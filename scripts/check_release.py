#!/usr/bin/env python3
"""Pre-merge / release checks for a dump + optional viewer JSON export.

Exit 0 on success; non-zero with printed failures otherwise.

  python scripts/check_release.py --dump r008
  python scripts/check_release.py --dump r008 --viewer-data viewer/data
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import DATA_DIR, dump_meta_path, dump_tsv_path  # noqa: E402
from wikidata_enrich import STYLE_QID_TO_TAG  # noqa: E402

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


def check(dump_id: str, viewer_data: Path | None) -> list[str]:
    errors: list[str] = []
    c_path = dump_tsv_path("composers", dump_id)
    w_path = dump_tsv_path("works", dump_id)
    m_path = dump_meta_path(dump_id)
    if not c_path.exists() or not w_path.exists():
        return [f"missing dump files for {dump_id}"]
    composers = pd.read_csv(c_path, sep="\t", low_memory=False)
    works = pd.read_csv(w_path, sep="\t", low_memory=False)

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
    cache = DATA_DIR / "cache" / "wikidata_entity"
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
    if g in works.columns:
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
                return errors
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

    return errors


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dump", required=True)
    p.add_argument(
        "--viewer-data",
        type=Path,
        help="Optional viewer/data dir to cross-check JSON export",
    )
    args = p.parse_args()
    errors = check(args.dump, args.viewer_data)
    if errors:
        print("FAIL")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    print(f"OK {args.dump}")
    sys.exit(0)


if __name__ == "__main__":
    main()
