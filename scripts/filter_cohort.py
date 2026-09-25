#!/usr/bin/env python3
"""Filter an existing dump through cohort gates (no network).

Drops pre-20th-century / list-mismatch rows (e.g. Gletle) and their works.
Writes a new dated dump; never overwrites.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cohort import cohort_exclusion_reason  # noqa: E402
from common import DATA_DIR, SCHEMA_VERSION, TOOL_VERSION  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("filter_cohort")


def run(args: argparse.Namespace) -> None:
    src = args.from_dump
    out = date.fromisoformat(args.date) if args.date else date.today()
    c_path = DATA_DIR / f"composers_{src}.tsv"
    w_path = DATA_DIR / f"works_{src}.tsv"
    composers = pd.read_csv(c_path, sep="\t", low_memory=False)
    works = pd.read_csv(w_path, sep="\t", low_memory=False)

    drop_ids = []
    reasons = []
    for _, row in composers.iterrows():
        # Prefer Wikidata years already stored; list years only in legacy raw if present.
        reason = cohort_exclusion_reason(
            birth_year=row.get("birth_year"),
            death_year=row.get("death_year"),
        )
        if reason:
            drop_ids.append(str(row["composer_id"]))
            reasons.append((row.get("name_display"), row.get("composer_id"), reason,
                            row.get("birth_year"), row.get("death_year")))

    log.info("Excluding %d composers:", len(drop_ids))
    for name, cid, reason, b, d in reasons:
        log.info("  %s (%s) %s–%s → %s", name, cid, b, d, reason)

    drop_set = set(drop_ids)
    kept_c = composers[~composers["composer_id"].astype(str).isin(drop_set)].copy()
    kept_w = works[~works["composer_id"].astype(str).isin(drop_set)].copy()
    kept_c["dump_date"] = out.isoformat()
    kept_c["schema_version"] = SCHEMA_VERSION

    if args.dry_run:
        log.info("Dry run — would write %d composers, %d works", len(kept_c), len(kept_w))
        return

    out_c = DATA_DIR / f"composers_{out.isoformat()}.tsv"
    out_w = DATA_DIR / f"works_{out.isoformat()}.tsv"
    out_m = DATA_DIR / f"dump_meta_{out.isoformat()}.json"
    for p in (out_c, out_w, out_m):
        if p.exists():
            raise FileExistsError(p)
    kept_c.to_csv(out_c, sep="\t", index=False)
    kept_w.to_csv(out_w, sep="\t", index=False)
    meta = {
        "dump_id": out.isoformat(),
        "tool_version": TOOL_VERSION,
        "schema_version": SCHEMA_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "derived_from_dump_id": src,
        "enrichment": "cohort_filter",
        "excluded": [
            {"name": n, "composer_id": cid, "reason": r, "birth_year": b, "death_year": d}
            for n, cid, r, b, d in reasons
        ],
        "row_counts": {
            "composers": int(len(kept_c)),
            "works": int(len(kept_w)),
            "excluded_composers": len(drop_ids),
        },
        "output_files": {"composers": out_c.name, "works": out_w.name},
        "notes": [
            "Dropped rows failing 20th-century cohort gates (see scripts/cohort.py)",
            "Typical cause: Wikipedia list links wrong person with fabricated years",
        ],
    }
    out_m.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    log.info("Wrote %s (%d), %s (%d), %s", out_c.name, len(kept_c), out_w.name, len(kept_w), out_m.name)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--from-dump", required=True)
    p.add_argument("--date", help="Output dump_id")
    p.add_argument("--dry-run", action="store_true")
    run(p.parse_args())


if __name__ == "__main__":
    main()
