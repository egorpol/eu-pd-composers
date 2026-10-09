#!/usr/bin/env python3
"""Apply hand-reviewed composer overrides as the last dump transform.

Writes a new revision. Never overwrites. Empty overrides file is a no-op
apart from setting dump_date on the output.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

import common  # noqa: E402
from common import (  # noqa: E402
    SCHEMA_VERSION,
    TOOL_VERSION,
    dump_meta_path,
    dump_tsv_path,
    next_revision_id,
    write_dump_meta,
    write_tsv_dump,
)
from overrides import apply_composer_overrides, load_overrides  # noqa: E402
from remap_force import dump_year_from_meta  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("apply_overrides")


def default_overrides_path() -> Path:
    return common.DATA_DIR / "overrides" / "composers.tsv"


def overrides_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def print_report(
    report: dict[str, Any],
    src: str,
    out_id: str,
    overrides_path: Path,
    pd_year: int,
) -> None:
    print(
        f"Composer overrides {src} → {out_id} "
        f"(PD reference year {pd_year}; {overrides_path})"
    )
    print(f"Overrides applied: {report['overrides_applied']}")
    for rk in report["already_applied"]:
        print(f"  already_applied: {rk['old_composer_id']} → {rk['new_composer_id']}")
    if report["rekeys"]:
        print("Re-keys:")
        for rk in report["rekeys"]:
            extra = ""
            if rk.get("rederived"):
                imslp = (rk.get("imslp") or {}).get("action")
                extra = f", rederived, imslp={imslp}"
            print(
                f"  {rk['old_composer_id']} → {rk['new_composer_id']} "
                f"({rk['works_rekeyed']} works{extra})"
            )
    if report["value_changes"]:
        print("Value overrides:")
        for ch in report["value_changes"]:
            print(
                f"  {ch['composer_id']} {ch['field']}: "
                f"{ch['old'] or '(empty)'} → {ch['new'] or '(empty)'}"
            )
    if report["drops"]:
        print("Drops:")
        for dr in report["drops"]:
            print(
                f"  {dr['composer_id']} "
                f"({dr['works_dropped']} works dropped)"
            )
    if report.get("row_changes"):
        print("Touched row changes (all columns):")
        for row in report["row_changes"]:
            label = row["composer_id"]
            if row.get("old_composer_id") and row["old_composer_id"] != row["composer_id"]:
                label = f"{row['old_composer_id']} → {row['composer_id']}"
            print(f"  {label}:")
            for field, ch in row["changes"].items():
                print(
                    f"    {field}: {ch['old'] or '(empty)'} → {ch['new'] or '(empty)'}"
                )
    if report.get("citizenship_iso_skipped"):
        print(
            "Citizenship QIDs without cached P297: "
            + ", ".join(report["citizenship_iso_skipped"])
        )
    print(f"Works re-keyed: {report['works_rekeyed']}")
    print(f"Works dropped: {report['works_dropped']}")
    if report["overrides_applied"] == 0:
        print("No overrides; dump_date will be set to the output revision.")


def run(args: argparse.Namespace) -> None:
    src = args.from_dump
    out_id = args.to or next_revision_id()
    overrides_path = (
        Path(args.overrides) if args.overrides else default_overrides_path()
    )

    composers = pd.read_csv(
        dump_tsv_path("composers", src), sep="\t", dtype=str, keep_default_na=False,
    )
    works = pd.read_csv(
        dump_tsv_path("works", src), sep="\t", dtype=str, keep_default_na=False,
    )
    log.info("Loaded %d composers, %d works from %s", len(composers), len(works), src)

    overrides = load_overrides(overrides_path)
    pd_year = dump_year_from_meta(src)
    composers, works, report = apply_composer_overrides(
        composers, works, overrides, pd_year=pd_year,
    )
    for df in (composers, works):
        if "dump_date" in df.columns:
            df["dump_date"] = out_id

    print_report(report, src, out_id, overrides_path, pd_year)
    if args.dry_run:
        return

    for path in (
        dump_tsv_path("composers", out_id),
        dump_tsv_path("works", out_id),
        dump_meta_path(out_id),
    ):
        if path.exists():
            raise FileExistsError(f"Refusing to overwrite existing dump: {path}")

    c_path = write_tsv_dump(composers, "composers", out_id)
    w_path = write_tsv_dump(works, "works", out_id)
    sha = overrides_sha256(overrides_path)
    write_dump_meta(
        out_id,
        {
            "dump_id": out_id,
            "tool_version": TOOL_VERSION,
            "schema_version": SCHEMA_VERSION,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "derived_from_dump_id": src,
            "enrichment": "apply_composer_overrides",
            "output_files": {"composers": c_path.name, "works": w_path.name},
            "row_counts": {
                "composers": int(len(composers)),
                "works": int(len(works)),
            },
            "pd_reference_year": pd_year,
            "overrides": {
                "path": str(overrides_path),
                "sha256": sha,
                "rows_applied": report["overrides_applied"],
                "report": report,
            },
            "notes": [
                "Applied hand-reviewed composer overrides last; they win over automatic sources",
                "Order: re-keys, re-derive from cached Wikidata, value overrides, drops",
                "Then PD fields and rollups for touched rows; qa_flags+=manual_override",
                "Empty overrides file is a no-op apart from dump_date",
            ],
        },
    )
    log.info("Wrote %s → %s, %s", out_id, c_path.name, w_path.name)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--from-dump", required=True)
    p.add_argument("--to", help="Output revision id (default: next rNNN)")
    p.add_argument(
        "--overrides",
        default=None,
        help="Overrides TSV (default: data/overrides/composers.tsv)",
    )
    p.add_argument("--dry-run", action="store_true")
    run(p.parse_args())


if __name__ == "__main__":
    main()
