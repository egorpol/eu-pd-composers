#!/usr/bin/env python3
"""Recompute force_family (rule fixes), merge duplicate QIDs, fix PD labels.

Writes a new revision. Never overwrites. Used for Sol pre-main dump r008.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_dump import eu_pd_fields  # noqa: E402
from common import (  # noqa: E402
    SCHEMA_VERSION,
    TOOL_VERSION,
    dump_meta_path,
    dump_tsv_path,
    next_revision_id,
    pipe_join,
    pipe_split,
    write_dump_meta,
    write_tsv_dump,
)
from force_family import (  # noqa: E402
    FORCE_FAMILIES,
    map_force_family_with_geninfo,
    map_genre_form,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("remap_force")


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


def _as_int(value):
    text = _as_str(value)
    if not text:
        return None
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return None


def _as_views(value) -> int:
    n = _as_int(value)
    return n if n is not None else -1


def merge_composers(composers: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    """One row per composer_id; fold aliases; prefer richer IMSLP / pageviews."""
    stats = {"duplicate_rows_removed": 0, "ids_merged": 0}
    if composers["composer_id"].duplicated().sum() == 0:
        return composers.copy(), stats

    merged_rows: list[dict] = []
    for cid, group in composers.groupby("composer_id", sort=False):
        if len(group) == 1:
            merged_rows.append(group.iloc[0].to_dict())
            continue
        stats["ids_merged"] += 1
        stats["duplicate_rows_removed"] += len(group) - 1
        scored = group.copy()
        scored["_views"] = (
            scored["pageviews_enwiki"].map(_as_views)
            if "pageviews_enwiki" in scored
            else -1
        )
        scored["_has_imslp"] = (
            scored["imslp_category"].map(_as_str).ne("")
            if "imslp_category" in scored
            else False
        )
        scored = scored.sort_values(["_has_imslp", "_views"], ascending=[False, False])
        primary = scored.iloc[0].to_dict()
        aliases = set(pipe_split(_as_str(primary.get("name_aliases"))))
        for _, r in scored.iterrows():
            n = _as_str(r.get("name_display"))
            if n and n != _as_str(primary.get("name_display")):
                aliases.add(n)
            for a in pipe_split(_as_str(r.get("name_aliases"))):
                aliases.add(a)
            url = _as_str(r.get("wikipedia_url"))
            if "and_" in url.lower() or "_and_" in url.lower():
                primary["wikipedia_url"] = url
                if _as_str(r.get("name_display")):
                    primary["name_display"] = _as_str(r.get("name_display"))
        primary["name_aliases"] = pipe_join(
            sorted(a for a in aliases if a and a != _as_str(primary.get("name_display")))
        )
        births = [
            y
            for y in (_as_int(r.get("birth_year")) for _, r in scored.iterrows())
            if y is not None
        ]
        deaths = [
            y
            for y in (_as_int(r.get("death_year")) for _, r in scored.iterrows())
            if y is not None
        ]
        if births:
            primary["birth_year"] = min(births)
        if deaths:
            primary["death_year"] = max(deaths)
        for k in list(primary):
            if str(k).startswith("_"):
                del primary[k]
        merged_rows.append(primary)

    out = pd.DataFrame(merged_rows)
    # Preserve column order from input where possible.
    cols = [c for c in composers.columns if c in out.columns]
    cols += [c for c in out.columns if c not in cols]
    return out.reindex(columns=cols), stats


def dedupe_works(works: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    before = len(works)
    key_cols = ["composer_id"]
    if "imslp_pageid" in works.columns:
        key_cols.append("imslp_pageid")
    else:
        key_cols.append("work_id")
    out = works.drop_duplicates(subset=key_cols, keep="first").copy()
    return out.reset_index(drop=True), before - len(out)


def rollup_composers(composers: pd.DataFrame, works: pd.DataFrame) -> pd.DataFrame:
    by_composer: dict[str, Counter] = {}
    for cid, fam in zip(works["composer_id"].astype(str), works["force_family"].astype(str)):
        by_composer.setdefault(cid, Counter())[fam] += 1

    present, by_cat_json, totals = [], [], []
    for cid in composers["composer_id"].astype(str):
        ctr = by_composer.get(cid, Counter())
        ordered = [f for f in FORCE_FAMILIES if ctr.get(f)]
        present.append(pipe_join(ordered))
        payload = {f: int(ctr[f]) for f in ordered}
        by_cat_json.append(json.dumps(payload, ensure_ascii=False) if payload else "")
        totals.append(int(sum(ctr.values())))

    out = composers.copy()
    out["work_categories_present"] = present
    out["works_count_by_category"] = by_cat_json
    out["works_count_total"] = totals
    if "imslp_works_count" in out.columns:
        out["imslp_works_count"] = totals
    return out


def dump_year_from_meta(src: str) -> int:
    path = dump_meta_path(src)
    if path.exists():
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
            created = meta.get("created_at_utc") or ""
            if created[:4].isdigit():
                return int(created[:4])
        except (json.JSONDecodeError, ValueError, TypeError):
            pass
    return datetime.now(timezone.utc).year


def run(args: argparse.Namespace) -> None:
    src = args.from_dump
    out_id = args.to or next_revision_id()
    composers = pd.read_csv(dump_tsv_path("composers", src), sep="\t", low_memory=False)
    works = pd.read_csv(dump_tsv_path("works", src), sep="\t", low_memory=False)
    log.info("Loaded %d composers, %d works from %s", len(composers), len(works), src)

    composers, merge_stats = merge_composers(composers)
    works, works_dropped = dedupe_works(works)
    log.info(
        "ID merge: %s; works deduped=%d → %d composers, %d works",
        merge_stats,
        works_dropped,
        len(composers),
        len(works),
    )

    dump_year = dump_year_from_meta(src)
    changed_force = 0
    for idx, row in works.iterrows():
        genre = _as_str(row.get("imslp_genre_categories"))
        title = _as_str(row.get("title"))
        instru = _as_str(row.get("instrumentation_raw"))
        old_f = _as_str(row.get("force_family"))
        old_s = _as_str(row.get("force_family_src"))
        family, src_tag = map_force_family_with_geninfo(
            genre,
            title,
            instru,
            current_family=old_f,
            current_src=old_s,
            recompute=True,
        )
        form = map_genre_form(genre, title)
        if family != old_f or src_tag != old_s:
            changed_force += 1
        works.at[idx, "force_family"] = family
        works.at[idx, "force_family_src"] = src_tag
        works.at[idx, "genre_form"] = form

    log.info("force_family rows changed: %d / %d", changed_force, len(works))

    for col in ("eu_pd_year", "eu_pd_status", "years_until_eu_pd"):
        composers[col] = composers[col].astype(object)

    pd_fixed = 0
    for idx, row in composers.iterrows():
        death = _as_int(row.get("death_year"))
        fields = eu_pd_fields(death, dump_year)
        if _as_str(row.get("eu_pd_status")) != str(fields["eu_pd_status"]):
            pd_fixed += 1
        composers.at[idx, "eu_pd_year"] = (
            "" if fields["eu_pd_year"] == "" else fields["eu_pd_year"]
        )
        composers.at[idx, "eu_pd_status"] = fields["eu_pd_status"]
        composers.at[idx, "years_until_eu_pd"] = (
            "" if fields["years_until_eu_pd"] == "" else fields["years_until_eu_pd"]
        )

    log.info("eu_pd_status rows changed: %d", pd_fixed)

    composers = rollup_composers(composers, works)
    composers["dump_date"] = out_id
    composers["schema_version"] = SCHEMA_VERSION
    if "dump_date" in works.columns:
        works["dump_date"] = out_id

    fam_counts = Counter(works["force_family"].tolist())
    for fam in FORCE_FAMILIES:
        if fam_counts.get(fam):
            log.info("  %5d  %s", fam_counts[fam], fam)

    if args.dry_run:
        g = "imslp_genre_categories"
        ops = works[
            works[g].fillna("").str.contains("Operas", regex=False)
            & (works["force_family"] == "concerto")
        ]
        orch = works[
            works[g].fillna("").str.contains("For orchestra", regex=False)
            & ~works[g].fillna("").str.contains("For orchestra (arr)", regex=False)
            & (works["force_family"] == "piano_ensemble")
        ]
        print("Operas→concerto", len(ops))
        print("For orchestra→piano_ensemble (non-arr orch token)", len(orch))
        print("unique composer_id", composers["composer_id"].nunique(), "/", len(composers))
        print(composers["eu_pd_status"].value_counts().to_string())
        return

    c_path = write_tsv_dump(composers, "composers", out_id)
    w_path = write_tsv_dump(works, "works", out_id)
    write_dump_meta(
        out_id,
        {
            "dump_id": out_id,
            "tool_version": TOOL_VERSION,
            "schema_version": SCHEMA_VERSION,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "derived_from_dump_id": src,
            "enrichment": "remap_force_ids_pd",
            "output_files": {"composers": c_path.name, "works": w_path.name},
            "row_counts": {
                "composers": int(len(composers)),
                "works": int(len(works)),
                "force_rows_changed": changed_force,
                "pd_rows_changed": pd_fixed,
                "duplicate_composer_rows_removed": merge_stats["duplicate_rows_removed"],
                "work_rows_deduped": works_dropped,
            },
            "notes": [
                "Recomputed force_family ignoring (arr) tokens; opera/voice beat concerto",
                "Merged duplicate composer_id rows; deduped works by imslp_pageid",
                "Missing death_year → eu_pd_status=unknown_death",
            ],
        },
    )
    log.info("Wrote %s → %s, %s", out_id, c_path.name, w_path.name)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--from-dump", required=True)
    p.add_argument("--to", help="Output revision id (default: next rNNN)")
    p.add_argument("--dry-run", action="store_true")
    run(p.parse_args())


if __name__ == "__main__":
    main()
