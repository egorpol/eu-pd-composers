#!/usr/bin/env python3
"""Add GND and VIAF ids to composers and check GND death years, from cached data only.

`gnd_id` and `viaf_id` come from the composer's Wikidata entity (P227, P214).
A composer without a Wikidata GND id gets one from a lobid name search when
exactly one GND person agrees on life dates (`gnd_from_lobid`). A GND death
year that differs from the dump's flags `gnd_death_conflict`. Rows whose caches
are missing keep their prior cells. Writes a new revision; --dry-run prints the
report only.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from authorities import (  # noqa: E402
    AUTHORITY_COLUMNS, GND_FLAGS, RECORD_NAMESPACE, SEARCH_NAMESPACE,
    match_candidates, wikidata_authority_ids, year_of,
)
from common import (  # noqa: E402
    SCHEMA_VERSION, TOOL_VERSION, cache_get, dump_meta_path, dump_tsv_path,
    next_revision_id, pipe_join, pipe_split, write_dump_meta, write_tsv_dump,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("remap_authorities")


def _int(value: Any) -> int | None:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError, OverflowError):
        return None


def add_columns(composers: pd.DataFrame) -> pd.DataFrame:
    """Insert empty authority columns after `wikidata_qid` when absent."""
    columns = list(composers.columns)
    added = [c for c in AUTHORITY_COLUMNS if c not in columns]
    if not added:
        return composers.copy()
    anchor = columns.index("wikidata_qid") + 1 if "wikidata_qid" in columns else len(columns)
    columns[anchor:anchor] = added
    return composers.reindex(columns=columns, fill_value="")


def remap_authorities(composers: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    out = add_columns(composers)
    counts: Counter[str] = Counter()
    conflicts: list[dict[str, Any]] = []
    lobid_matches: list[dict[str, Any]] = []
    unknown_ids: list[dict[str, Any]] = []
    for idx, row in out.iterrows():
        qid = str(row["composer_id"])
        prior_flags = pipe_split(row.get("qa_flags"))
        flags = [f for f in prior_flags if f not in GND_FLAGS]
        entity = cache_get("wikidata_entity", qid)
        if entity is None:
            counts["missing_entity"] += 1
            continue
        ids = wikidata_authority_ids(entity)
        birth, death = _int(row.get("birth_year")), _int(row.get("death_year"))
        record = None
        if ids["gnd_id"]:
            counts["gnd_wikidata"] += 1
            record = cache_get(RECORD_NAMESPACE, ids["gnd_id"])
            if record is None:
                counts["missing_record"] += 1
                if ids["gnd_id"] == row.get("gnd_id") and "gnd_death_conflict" in prior_flags:
                    flags.append("gnd_death_conflict")
            elif record.get("missing"):
                counts["gnd_not_in_lobid"] += 1
                unknown_ids.append({"name": row["name_display"], "qid": qid, "gnd_id": ids["gnd_id"]})
                record = None
        else:
            search = cache_get(SEARCH_NAMESPACE, qid)
            if search is None:
                counts["missing_search"] += 1
                if "gnd_from_lobid" in prior_flags and row.get("gnd_id"):
                    ids["gnd_id"] = row["gnd_id"]
                    flags.extend(f for f in GND_FLAGS if f in prior_flags)
                    counts["kept_prior"] += 1
            else:
                record = match_candidates(search.get("candidates") or [], birth, death)
                if record is not None:
                    ids["gnd_id"] = record["gndIdentifier"]
                    flags.append("gnd_from_lobid")
                    counts["gnd_lobid"] += 1
                    lobid_matches.append({
                        "name": row["name_display"], "qid": qid, "gnd_id": ids["gnd_id"],
                        "gnd_name": record.get("preferredName"), "years": f"{birth}–{death or ''}",
                    })
        if record is not None:
            gnd_death = year_of(record.get("dateOfDeath"))
            if gnd_death is not None and death is not None and gnd_death != death:
                flags.append("gnd_death_conflict")
                conflicts.append({
                    "name": row["name_display"], "qid": qid, "gnd_id": ids["gnd_id"],
                    "death_year": death, "gnd_death_year": gnd_death,
                    "eu_pd_status": row.get("eu_pd_status", ""),
                })
        if ids["viaf_id"]:
            counts["viaf"] += 1
        if not ids["gnd_id"] and not ids["viaf_id"]:
            counts["no_gnd_or_viaf"] += 1
        out.at[idx, "gnd_id"] = ids["gnd_id"]
        out.at[idx, "viaf_id"] = ids["viaf_id"]
        out.at[idx, "qa_flags"] = pipe_join(flags)
    counts["rows"] = len(out)
    counts["gnd_id"] = int(out["gnd_id"].ne("").sum())
    counts["gnd_death_conflict"] = len(conflicts)
    return out, {
        "counts": dict(sorted(counts.items())),
        "gnd_death_conflicts": conflicts,
        "gnd_lobid_matches": lobid_matches,
        "gnd_not_in_lobid": unknown_ids,
    }


def print_report(report: dict[str, Any], src: str, out_id: str) -> None:
    print(f"Authority ids {src} → {out_id} (offline)")
    for key, value in report["counts"].items():
        print(f"  {key}: {value}")
    print(f"GND death-year conflicts: {len(report['gnd_death_conflicts'])}")
    for row in report["gnd_death_conflicts"]:
        print(f"  {row['name']} ({row['qid']}): dump †{row['death_year']}, GND {row['gnd_id']} "
              f"†{row['gnd_death_year']}; {row['eu_pd_status']}")
    print(f"GND ids from lobid name search: {len(report['gnd_lobid_matches'])}")
    for row in report["gnd_lobid_matches"]:
        print(f"  {row['name']} ({row['qid']}, {row['years']}): {row['gnd_id']} {row['gnd_name']}")
    print(f"Wikidata GND ids lobid does not know: {len(report['gnd_not_in_lobid'])}")
    for row in report["gnd_not_in_lobid"]:
        print(f"  {row['name']} ({row['qid']}): {row['gnd_id']}")


def run(args: argparse.Namespace) -> None:
    src, out_id = args.from_dump, args.to or next_revision_id()
    composers = pd.read_csv(dump_tsv_path("composers", src), sep="\t", dtype=str, keep_default_na=False)
    works = pd.read_csv(dump_tsv_path("works", src), sep="\t", dtype=str, keep_default_na=False)
    log.info("Loaded %d composers, %d works from %s", len(composers), len(works), src)
    if args.preserve_schema and not set(AUTHORITY_COLUMNS) <= set(composers.columns):
        log.info("--preserve-schema and no authority columns in %s: composers unchanged", src)
        report = {"counts": {"skipped_preserve_schema": len(composers)},
                  "gnd_death_conflicts": [], "gnd_lobid_matches": [], "gnd_not_in_lobid": []}
    else:
        composers, report = remap_authorities(composers)
    for df in (composers, works):
        if "dump_date" in df.columns:
            df["dump_date"] = out_id
    print_report(report, src, out_id)
    if args.dry_run:
        return

    for path in (dump_tsv_path("composers", out_id), dump_tsv_path("works", out_id), dump_meta_path(out_id)):
        if path.exists():
            raise FileExistsError(f"Refusing to overwrite existing dump: {path}")
    c_path = write_tsv_dump(composers, "composers", out_id)
    w_path = write_tsv_dump(works, "works", out_id)
    write_dump_meta(out_id, {
        "dump_id": out_id, "tool_version": TOOL_VERSION, "schema_version": SCHEMA_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "derived_from_dump_id": src, "enrichment": "remap_authorities",
        "output_files": {"composers": c_path.name, "works": w_path.name},
        "row_counts": {"composers": len(composers), "works": len(works)},
        "authorities": report,
        "notes": [
            "gnd_id / viaf_id: first non-deprecated Wikidata P227 / P214 value (preferred rank first) that passes the property's format",
            "Without a Wikidata GND id, a lobid name search supplies one only when exactly one GND person has the same birth year and the same death year (or no death year and a composer profession); flagged gnd_from_lobid",
            "gnd_death_conflict: the GND record's death year differs from death_year",
            "Rows without a cached entity, search or record keep their prior cells and flags",
            "Other composer fields, works and row order preserved; dump_date stamped where present",
        ],
    })
    log.info("Wrote %s → %s, %s", out_id, c_path.name, w_path.name)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-dump", required=True)
    parser.add_argument("--to", help="Output revision id (default: next rNNN)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--preserve-schema", action="store_true",
                        help="Do not add the authority columns to a dump that lacks them")
    run(parser.parse_args())


if __name__ == "__main__":
    main()
