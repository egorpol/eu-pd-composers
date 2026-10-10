#!/usr/bin/env python3
"""Pre-merge / release checks for a dump + optional viewer JSON export.

Exit 0 on success; non-zero with printed failures otherwise.

  python scripts/check_release.py --dump r013
  python scripts/check_release.py --dump r013 --viewer-data viewer/data
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

import common  # noqa: E402
from common import dump_meta_path, dump_tsv_path  # noqa: E402
from force_family import force_family_from_categories  # noqa: E402
from style_vocab import ERA_SLUG_SET, STYLE_SLUG_SET  # noqa: E402
from wikidata_enrich import STYLE_QID_TO_TAG  # noqa: E402

# Share of works without any IMSLP genre/force category. r008 shipped 62.8%
# because of a truncated category fetch; a complete crawl is well under 5%.
MAX_EMPTY_CATEGORY_SHARE = 0.05

# Known form / non-genre QIDs that must never appear in STYLE_QID_TO_TAG.
STYLE_QID_DENYLIST = {
    "Q9734",  # symphony
    "Q189201",  # chamber music
    "Q207338",  # string quartet
    "Q1344",  # opera (form)
    "Q11401",  # concerto (form)
}


def _as_str(value) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    if text.lower() in {"nan", "none"}:
        return ""
    return text


def _as_int(value) -> int | None:
    text = _as_str(value)
    try:
        return int(float(text)) if text else None
    except ValueError:
        return None


def check_data_quality(
    composers: pd.DataFrame, works: pd.DataFrame, meta: dict
) -> tuple[list[str], list[str]]:
    """Invariants that would have caught the r008 crawl / rule / PD defects."""
    errors: list[str] = []
    warnings: list[str] = []

    # Older revisions predate ledger replay; enforce retirement on the new schema.
    if "llm_style_tags" in composers:
        invalid = {s for value in composers["llm_style_tags"].map(_as_str)
                   for s in common.pipe_split(value) if s not in STYLE_SLUG_SET}
        if invalid:
            errors.append("invalid llm_style_tags slugs: " + ", ".join(sorted(invalid)))
        if "llm_style_period" in composers:
            periods = set(composers["llm_style_period"].map(_as_str)) - {""}
            if periods - ERA_SLUG_SET:
                errors.append("invalid llm_style_period slugs: " + ", ".join(sorted(periods - ERA_SLUG_SET)))
        if "style_tags_src" in composers:
            legacy = composers["style_tags_src"].map(_as_str).str.startswith("llm").sum()
            if legacy:
                errors.append(f"legacy LLM style_tags_src rows: {int(legacy)}")

    # --- IMSLP category completeness ---
    if "imslp_genre_categories" in works.columns and len(works):
        empty = works["imslp_genre_categories"].map(_as_str).eq("").sum()
        share = empty / len(works)
        if share > MAX_EMPTY_CATEGORY_SHARE:
            errors.append(
                f"works with empty imslp_genre_categories: {empty} ({share:.1%}) "
                f"> {MAX_EMPTY_CATEGORY_SHARE:.0%} — incomplete category crawl?"
            )

    # --- Stored imslp_tags labels match current category rules ---
    if {"force_family", "force_family_src", "imslp_genre_categories"} <= set(works.columns):
        tagged = works[works["force_family_src"].map(_as_str) == "imslp_tags"]
        stale = [
            (getattr(r, "work_id", "(unknown work)"), r.force_family, expected)
            for r in tagged.itertuples()
            for expected in [force_family_from_categories(_as_str(r.imslp_genre_categories))[0]]
            if expected != _as_str(r.force_family)
        ]
        if stale:
            examples = "; ".join(f"{w}: {got}≠{exp}" for w, got, exp in stale[:5])
            errors.append(
                f"imslp_tags labels disagree with current rules: {len(stale)} (e.g. {examples})"
            )

    # --- Composer rollups match works ---
    if "works_count_total" in composers.columns:
        actual = works.groupby(works["composer_id"].astype(str)).size()
        stored = composers.set_index(composers["composer_id"].astype(str))["works_count_total"]
        diff = (stored.map(_as_int).fillna(0) - actual.reindex(stored.index).fillna(0)).ne(0).sum()
        if diff:
            errors.append(f"works_count_total disagrees with works rows for {int(diff)} composers")
    orphans = ~works["composer_id"].astype(str).isin(composers["composer_id"].astype(str))
    if orphans.any():
        errors.append(f"works rows with unknown composer_id: {int(orphans.sum())}")

    # --- IMSLP identity / category ownership ---
    if "imslp_match_status" in composers.columns:
        active = composers["imslp_match_status"].map(_as_str).isin({"matched", "unverified_heuristic"})
        if "imslp_category" in composers.columns:
            holders = composers.loc[active].copy()
            holders["imslp_category"] = holders["imslp_category"].map(_as_str)
            shared = holders[holders["imslp_category"].ne("")].groupby("imslp_category")["composer_id"].nunique()
            shared = shared[shared > 1]
            if len(shared):
                examples = "; ".join(f"{category} ({count} composers)" for category, count in shared.items())
                errors.append(f"IMSLP categories held by multiple active composers: {len(shared)} — {examples}")
        inactive_ids = composers.loc[~active, "composer_id"].astype(str)
        invalid_works = works["composer_id"].astype(str).isin(inactive_ids)
        if invalid_works.any():
            errors.append(
                f"works rows belonging to composers without matched/unverified_heuristic status: {int(invalid_works.sum())}"
            )

    # --- PD arithmetic (death_year + 71 vs. dump year) ---
    created = str(meta.get("pd_reference_year") or meta.get("created_at_utc") or "")
    if created[:4].isdigit() and {"death_year", "eu_pd_year", "eu_pd_status"} <= set(composers.columns):
        year = int(created[:4])
        bad = 0
        for r in composers.itertuples():
            death = _as_int(r.death_year)
            if death is None:
                ok = _as_str(r.eu_pd_status) == "unknown_death"
            else:
                expected = "pd" if death + 71 <= year else "not_pd"
                ok = _as_int(r.eu_pd_year) == death + 71 and _as_str(r.eu_pd_status) == expected
            bad += not ok
        if bad:
            errors.append(f"eu_pd_year/eu_pd_status inconsistent with death_year: {bad}")

    # --- Review items (do not fail the release) ---
    if "imslp_pageid" in works.columns:
        identified = works.loc[works["imslp_pageid"].map(_as_str).ne("")]
        shared = identified.groupby("imslp_pageid")["composer_id"].nunique()
        n_shared = int((shared > 1).sum())
        if n_shared:
            warnings.append(f"IMSLP pages listed under more than one composer: {n_shared}")
    if "qa_flags" in composers.columns:
        flags = composers["qa_flags"].map(_as_str).str.split("|").explode()
        counts = flags[flags.ne("")].value_counts()
        if len(counts):
            warnings.append("composer qa_flags: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    if "force_family_src" in works.columns:
        llm = works["force_family_src"].map(_as_str).str.startswith("llm").sum()
        warnings.append(f"LLM-derived force labels: {int(llm)} ({llm / max(len(works), 1):.1%})")

    return errors, warnings


def check_regressions(
    composers: pd.DataFrame, works: pd.DataFrame,
    previous_composers: pd.DataFrame, previous_works: pd.DataFrame,
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    prev_n = len(previous_composers)
    if abs(len(composers) - prev_n) > prev_n * 0.03:
        errors.append(f"composers changed by more than 3%: {prev_n} → {len(composers)}")
    if len(previous_works) - len(works) > len(previous_works) * 0.05:
        errors.append(f"works dropped by more than 5%: {len(previous_works)} → {len(works)}")
    for stem, previous, following in (("composers", previous_composers, composers),
                                      ("works", previous_works, works)):
        missing = [col for col in previous if col not in following]
        if missing:
            errors.append(f"schema drift in {stem}: missing columns {', '.join(missing)}")
    if "composer_id" not in composers or "composer_id" not in previous_composers:
        return errors, ["EU PD status flips and composer additions/removals unavailable: missing composer_id"]
    old = previous_composers.set_index("composer_id")
    new = composers.set_index("composer_id")
    added = set(new.index) - set(old.index)
    removed = set(old.index) - set(new.index)
    flips = 0
    if "eu_pd_status" in old and "eu_pd_status" in new and old.index.is_unique and new.index.is_unique:
        shared = sorted(set(old.index) & set(new.index))
        flips = int((old.loc[shared, "eu_pd_status"] != new.loc[shared, "eu_pd_status"]).sum())
    warnings = [f"EU PD status flips: {flips}",
                f"composers added: {len(added)}; removed: {len(removed)}"]
    if added:
        warnings.append("composer IDs added: " + ", ".join(sorted(added)))
    if removed:
        warnings.append("composer IDs removed: " + ", ".join(sorted(removed)))
    return errors, warnings


def check(dump_id: str, viewer_data: Path | None, against: str | None = None) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    c_path = dump_tsv_path("composers", dump_id)
    w_path = dump_tsv_path("works", dump_id)
    m_path = dump_meta_path(dump_id)
    if not c_path.exists() or not w_path.exists():
        return [f"missing dump files for {dump_id}"], warnings
    composers = pd.read_csv(c_path, sep="\t", dtype=str, keep_default_na=False)
    works = pd.read_csv(w_path, sep="\t", dtype=str, keep_default_na=False)
    meta = json.loads(m_path.read_text(encoding="utf-8")) if m_path.exists() else {}
    if against:
        prev_c_path = dump_tsv_path("composers", against)
        prev_w_path = dump_tsv_path("works", against)
        if not prev_c_path.exists() or not prev_w_path.exists():
            errors.append(f"missing baseline dump files for {against}")
        else:
            prev_c = pd.read_csv(prev_c_path, sep="\t", dtype=str, keep_default_na=False)
            prev_w = pd.read_csv(prev_w_path, sep="\t", dtype=str, keep_default_na=False)
            regression_errors, regression_warnings = check_regressions(composers, works, prev_c, prev_w)
            errors.extend(regression_errors)
            warnings.extend(regression_warnings)
    for stem, frame in (("composers", composers), ("works", works)):
        if "composer_id" not in frame:
            errors.append(f"missing required {stem} column: composer_id")
    if "imslp_pageid" not in works and "work_id" not in works:
        errors.append("missing required work key: imslp_pageid or work_id")
    if "composer_id" not in composers or "composer_id" not in works or not {"imslp_pageid", "work_id"} & set(works):
        return errors, warnings
    dq_errors, dq_warnings = check_data_quality(composers, works, meta)
    errors.extend(dq_errors)
    warnings.extend(dq_warnings)

    # --- IDs ---
    dup_c = composers["composer_id"].duplicated().sum()
    if dup_c:
        errors.append(f"duplicate composer_id rows: {int(dup_c)}")
    if "imslp_pageid" in works.columns:
        key = works.duplicated(subset=["composer_id", "imslp_pageid"]).sum()
    else:
        key = works.duplicated(subset=["composer_id", "work_id"]).sum()
    if key:
        errors.append(f"duplicate work join keys: {int(key)}")

    # --- Meta counts ---
    if m_path.exists():
        meta = json.loads(m_path.read_text(encoding="utf-8"))
        rc = meta.get("row_counts") or {}
        if int(rc.get("composers", -1)) != len(composers):
            errors.append(
                f"meta composers count {rc.get('composers')} != TSV {len(composers)}"
            )
        if int(rc.get("works", -1)) != len(works):
            errors.append(f"meta works count {rc.get('works')} != TSV {len(works)}")
    else:
        errors.append(f"missing {m_path.name}")

    # --- Style QIDs ---
    for qid in STYLE_QID_TO_TAG:
        if qid in STYLE_QID_DENYLIST:
            errors.append(f"STYLE_QID_TO_TAG contains denylisted form QID {qid}")
    if not STYLE_QID_TO_TAG:
        errors.append("STYLE_QID_TO_TAG is empty")

    # Offline label sanity via cached entities when present
    cache = common.CACHE_DIR / "wikidata_entity"
    formish = ("symphony", "chamber music", "string quartet", "concerto", "opera")
    if cache.is_dir():
        for qid, tag in STYLE_QID_TO_TAG.items():
            path = cache / f"{qid}.json"
            if not path.exists():
                continue
            try:
                ent = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                errors.append(f"bad cache JSON for {qid}")
                continue
            labels = (ent.get("labels") or {}).get("en") or {}
            label = (labels.get("value") or "").lower()
            if any(f == label for f in formish):
                errors.append(
                    f"STYLE map {qid}→{tag} has form-like en label {label!r}"
                )

    # --- Force spot rules ---
    g = "imslp_genre_categories"
    if g in works.columns and "force_family" in works.columns:
        ops = works[
            works[g].fillna("").astype(str).str.contains("Operas", regex=False)
            & (works["force_family"].astype(str) == "concerto")
        ]
        if len(ops):
            errors.append(f"Operas→concerto still present: {len(ops)}")
        orch = works[
            works[g].fillna("").astype(str).str.contains("For orchestra", regex=False)
            & ~works[g]
            .fillna("")
            .astype(str)
            .str.contains("For orchestra (arr)", regex=False)
            & works[g].fillna("").astype(str).str.contains("Symphonies|Overtures", regex=True)
            & (works["force_family"].astype(str) == "piano_ensemble")
        ]
        if len(orch):
            errors.append(
                f"orchestra+symphony/overture→piano_ensemble still present: {len(orch)}"
            )

    # --- PD ---
    if "eu_pd_status" in composers.columns and "death_year" in composers.columns:
        living_no_death = composers[
            (composers["eu_pd_status"].astype(str) == "living")
            & composers["death_year"].map(_as_str).eq("")
        ]
        if len(living_no_death):
            errors.append(
                f"eu_pd_status=living with empty death_year: {len(living_no_death)}"
            )

    # --- Viewer JSON ---
    if viewer_data is not None:
        manifest_path = viewer_data / "manifest.json"
        composers_json = viewer_data / "composers.json"
        works_json = viewer_data / "works_by_composer.json"
        for p in (manifest_path, composers_json, works_json):
            if not p.exists():
                errors.append(f"missing viewer file {p}")
                return errors, warnings
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("dump_id") != dump_id:
            errors.append(
                f"viewer manifest dump_id {manifest.get('dump_id')} != {dump_id}"
            )
        c_rows = json.loads(composers_json.read_text(encoding="utf-8"))
        w_map = json.loads(works_json.read_text(encoding="utf-8"))
        if len(c_rows) != len(composers):
            errors.append(
                f"viewer composers.json {len(c_rows)} != TSV {len(composers)}"
            )
        work_n = sum(len(v) for v in w_map.values())
        if work_n != len(works):
            errors.append(f"viewer works entries {work_n} != TSV {len(works)}")
        counts = manifest.get("counts") or {}
        if int(counts.get("composers", -1)) != len(composers):
            errors.append("manifest counts.composers mismatch")
        if int(counts.get("works", -1)) != len(works):
            errors.append("manifest counts.works mismatch")

    return errors, warnings


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dump", required=True)
    p.add_argument("--against", help="Previous approved revision for regression gates")
    p.add_argument(
        "--viewer-data",
        type=Path,
        help="Optional viewer/data dir to cross-check JSON export",
    )
    args = p.parse_args()
    errors, warnings = check(args.dump, args.viewer_data, args.against)
    for w in warnings:
        print(f"  warn: {w}")
    if errors:
        print("FAIL")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    print(f"OK {args.dump}")
    sys.exit(0)


if __name__ == "__main__":
    main()
