#!/usr/bin/env python3
"""Enrich an existing schema-v3 dump with force_family / genre_form (no network).

Reads composers_*.tsv + works_*.tsv for a source dump_id, maps IMSLP categories
(and title fallbacks) to Opus force_family, rolls aggregates onto composers, and
writes a new dated dump (never overwrites).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import DATA_DIR, SCHEMA_VERSION, TOOL_VERSION, pipe_join  # noqa: E402
from force_family import FORCE_FAMILIES, map_work_row  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("enrich_dump")

WORK_COLUMNS = [
    "work_id",
    "composer_id",
    "imslp_pageid",
    "imslp_work_url",
    "title",
    "imslp_genre_categories",
    "force_family",
    "force_family_src",
    "genre_form",
    "has_files",
    "fetched_at",
]

# Appended to composers (keep prior columns, add rollups).
NEW_COMPOSER_COLS = [
    "work_categories_present",
    "works_count_by_category",
    "works_count_total",
]


def write_dump(df: pd.DataFrame, stem: str, dump_date: date) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / f"{stem}_{dump_date.isoformat()}.tsv"
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite existing dump: {path}")
    df.to_csv(path, sep="\t", index=False)
    return path


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


def enrich(args: argparse.Namespace) -> None:
    src = args.from_dump
    out_date = date.fromisoformat(args.date) if args.date else date.today()

    composers_path = DATA_DIR / f"composers_{src}.tsv"
    works_path = DATA_DIR / f"works_{src}.tsv"
    if not composers_path.exists() or not works_path.exists():
        raise FileNotFoundError(f"Need {composers_path.name} and {works_path.name}")

    log.info("Reading %s + %s", composers_path.name, works_path.name)
    composers = pd.read_csv(composers_path, sep="\t", low_memory=False)
    works = pd.read_csv(works_path, sep="\t", low_memory=False)
    log.info("Loaded %d composers, %d works", len(composers), len(works))

    mapped = works.apply(
        lambda r: map_work_row(
            _as_str(r.get("imslp_genre_categories")),
            _as_str(r.get("title")),
        ),
        axis=1,
        result_type="expand",
    )
    works = pd.concat([works, mapped], axis=1)
    works = works.reindex(columns=WORK_COLUMNS)

    fam_counts = Counter(works["force_family"].tolist())
    log.info("force_family distribution:")
    for fam in FORCE_FAMILIES:
        if fam_counts.get(fam):
            log.info("  %5d  %s", fam_counts[fam], fam)

    # Composer rollups
    by_composer: dict[str, Counter] = {}
    for cid, fam in zip(works["composer_id"], works["force_family"]):
        by_composer.setdefault(str(cid), Counter())[fam] += 1

    present = []
    by_cat_json = []
    totals = []
    for cid in composers["composer_id"].astype(str):
        ctr = by_composer.get(cid, Counter())
        # stable order by FORCE_FAMILIES
        ordered = [f for f in FORCE_FAMILIES if ctr.get(f)]
        present.append(pipe_join(ordered))
        payload = {f: int(ctr[f]) for f in ordered}
        by_cat_json.append(json.dumps(payload, ensure_ascii=False) if payload else "")
        totals.append(int(sum(ctr.values())))

    composers = composers.copy()
    composers["work_categories_present"] = present
    composers["works_count_by_category"] = by_cat_json
    composers["works_count_total"] = totals
    # Keep imslp_works_count in sync with works file when present
    if "imslp_works_count" in composers.columns:
        composers["imslp_works_count"] = totals
    composers["schema_version"] = SCHEMA_VERSION
    composers["dump_date"] = out_date.isoformat()

    # Column order: previous cols + new rollups (dedupe)
    cols = [c for c in composers.columns if c not in NEW_COMPOSER_COLS]
    # Insert rollups after imslp_works_count when possible
    if "imslp_works_count" in cols:
        i = cols.index("imslp_works_count") + 1
        cols = cols[:i] + NEW_COMPOSER_COLS + cols[i:]
    else:
        cols = cols + NEW_COMPOSER_COLS
    # unique preserve order
    seen = set()
    ordered_cols = []
    for c in cols:
        if c not in seen:
            seen.add(c)
            ordered_cols.append(c)
    composers = composers.reindex(columns=ordered_cols)

    if args.dry_run:
        log.info("Dry run — not writing")
        print(works["force_family"].value_counts().head(20).to_string())
        print(composers[["name_display", "work_categories_present", "works_count_total"]].head(10).to_string(index=False))
        return

    c_path = write_dump(composers, "composers", out_date)
    w_path = write_dump(works, "works", out_date)
    log.info("Wrote %s (%d rows)", c_path, len(composers))
    log.info("Wrote %s (%d rows)", w_path, len(works))

    classified = int((works["force_family"] != "unclassified").sum())
    meta = {
        "dump_id": out_date.isoformat(),
        "tool_version": TOOL_VERSION,
        "schema_version": SCHEMA_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "derived_from_dump_id": src,
        "enrichment": "force_family+genre_form+composer_rollups",
        "output_files": {"composers": c_path.name, "works": w_path.name},
        "composer_columns": list(composers.columns),
        "work_columns": WORK_COLUMNS,
        "row_counts": {
            "composers": int(len(composers)),
            "works": int(len(works)),
            "works_classified": classified,
            "works_unclassified": int(len(works) - classified),
            "force_family": dict(fam_counts),
        },
        "force_families": list(FORCE_FAMILIES),
        "notes": [
            "force_family mapped from imslp_genre_categories with title fallback",
            "no network calls; IMSLP/Wikidata fields copied from source dump",
            "genre_form is best-effort secondary axis",
        ],
    }
    meta_path = DATA_DIR / f"dump_meta_{out_date.isoformat()}.json"
    if meta_path.exists():
        raise FileExistsError(f"Refusing to overwrite existing meta: {meta_path}")
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    log.info("Wrote %s", meta_path)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--from-dump",
        required=True,
        help="Source dump_id (e.g. 2026-09-26)",
    )
    p.add_argument(
        "--date",
        help="Output dump_id YYYY-MM-DD (default: today)",
    )
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


if __name__ == "__main__":
    enrich(parse_args())
