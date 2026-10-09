#!/usr/bin/env python3
"""Add IMSLP work evidence columns from cached page categories.

Offline only. Writes a new revision without overwriting inputs; --dry-run
prints the complete audit report without writing any output files.
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

from common import (  # noqa: E402
    SCHEMA_VERSION,
    TOOL_VERSION,
    cache_get,
    dump_meta_path,
    dump_tsv_path,
    next_revision_id,
    pipe_split,
    write_dump_meta,
    write_tsv_dump,
)
from imslp import PAGE_CATS_NAMESPACE  # noqa: E402
from imslp_work_evidence import (  # noqa: E402
    EVIDENCE_COLUMNS,
    unmapped_copyright_categories,
    work_evidence,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("remap_work_evidence")

EMPTY_EVIDENCE = {col: "" for col in EVIDENCE_COLUMNS}


def _insert_evidence_columns(works: pd.DataFrame) -> pd.DataFrame:
    """Place evidence columns after composition_year (or at end); recompute if present."""
    out = works.copy()
    present = [col for col in EVIDENCE_COLUMNS if col in out.columns]
    if present:
        out = out.drop(columns=present)
    columns = list(out.columns)
    if "composition_year" in columns:
        insert_at = columns.index("composition_year") + 1
    else:
        insert_at = len(columns)
    for offset, col in enumerate(EVIDENCE_COLUMNS):
        columns.insert(insert_at + offset, col)
        out[col] = ""
    return out.reindex(columns=columns)


def remap_work_evidence(
    composers: pd.DataFrame,
    works: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Fill evidence cells from cache; composers are returned unchanged."""
    out = _insert_evidence_columns(works)
    n = len(out)
    style_counts: Counter[str] = Counter()
    copyright_token_counts: Counter[str] = Counter()
    unmapped_counts: Counter[str] = Counter()
    librettist_names: set[str] = set()
    coverage = {col: 0 for col in EVIDENCE_COLUMNS}
    missing_cache = 0
    redirect_or_missing_page = 0

    for idx, row in out.iterrows():
        pageid = str(row.get("imslp_pageid") or "").strip()
        if not pageid:
            missing_cache += 1
            for col, value in EMPTY_EVIDENCE.items():
                out.at[idx, col] = value
            continue

        cached = cache_get(PAGE_CATS_NAMESPACE, pageid)
        if cached is None:
            missing_cache += 1
            for col, value in EMPTY_EVIDENCE.items():
                out.at[idx, col] = value
            continue

        if cached.get("redirect") or cached.get("missing"):
            redirect_or_missing_page += 1
            categories: list[str] = []
        else:
            categories = list(cached.get("categories") or [])

        evidence = work_evidence(categories)
        for col, value in evidence.items():
            out.at[idx, col] = value
            if value:
                coverage[col] += 1

        for style in pipe_split(evidence["imslp_style"]):
            style_counts[style] += 1
        for token in pipe_split(evidence["imslp_copyright_flags"]):
            copyright_token_counts[token] += 1
        for name in pipe_split(evidence["imslp_librettists"]):
            librettist_names.add(name)
        for cat in unmapped_copyright_categories(categories):
            unmapped_counts[cat] += 1

    status_by_id = {
        str(row["composer_id"]): str(row.get("eu_pd_status") or "")
        for _, row in composers.iterrows()
    }
    name_by_id = {
        str(row["composer_id"]): str(row.get("name_display") or "")
        for _, row in composers.iterrows()
    }
    crosstab: Counter[str] = Counter()
    pd_nonpd_eu_examples: list[dict[str, str]] = []
    for _, row in out.iterrows():
        status = status_by_id.get(str(row["composer_id"]), "")
        has_nonpd_eu = "nonpd_eu" in pipe_split(row["imslp_copyright_flags"])
        key = f"{status or '(empty)'} × {'nonpd_eu' if has_nonpd_eu else 'no_nonpd_eu'}"
        crosstab[key] += 1
        if status == "pd" and has_nonpd_eu and len(pd_nonpd_eu_examples) < 15:
            pd_nonpd_eu_examples.append(
                {
                    "title": str(row.get("title") or ""),
                    "composer": name_by_id.get(str(row["composer_id"]), ""),
                    "composer_id": str(row["composer_id"]),
                    "imslp_first_published": str(row["imslp_first_published"]),
                    "imslp_librettists": str(row["imslp_librettists"]),
                }
            )

    report: dict[str, Any] = {
        "coverage": {
            col: {
                "count": coverage[col],
                "pct": round(100.0 * coverage[col] / n, 2) if n else 0.0,
            }
            for col in EVIDENCE_COLUMNS
        },
        "style_counts": dict(sorted(style_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
        "copyright_token_counts": dict(sorted(copyright_token_counts.items())),
        "unmapped_copyright_categories": dict(
            sorted(unmapped_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ),
        "distinct_librettists": len(librettist_names),
        "eu_pd_status_x_nonpd_eu": dict(sorted(crosstab.items())),
        "pd_composer_nonpd_eu_examples": pd_nonpd_eu_examples,
        "missing_cache": missing_cache,
        "redirect_or_missing_page": redirect_or_missing_page,
        "works_total": n,
    }
    return out, report


def print_report(report: dict[str, Any], src: str, out_id: str) -> None:
    print(f"Work evidence remap {src} → {out_id} (offline)")
    print(f"Works: {report['works_total']}")
    print("Coverage:")
    for col, stats in report["coverage"].items():
        print(f"  {col}: {stats['count']} ({stats['pct']}%)")
    print("Style counts:")
    for style, count in report["style_counts"].items():
        print(f"  {style}: {count}")
    if not report["style_counts"]:
        print("  none")
    print("Copyright token counts:")
    for token, count in report["copyright_token_counts"].items():
        print(f"  {token}: {count}")
    if not report["copyright_token_counts"]:
        print("  none")
    print("Unmapped copyright categories:")
    for cat, count in report["unmapped_copyright_categories"].items():
        print(f"  {cat}: {count}")
    if not report["unmapped_copyright_categories"]:
        print("  none")
    print(f"Distinct librettists: {report['distinct_librettists']}")
    print(f"Missing cache entries: {report['missing_cache']}")
    print(f"Redirect / missing pages: {report['redirect_or_missing_page']}")
    print("Composer eu_pd_status × nonpd_eu:")
    for key, count in report["eu_pd_status_x_nonpd_eu"].items():
        print(f"  {key}: {count}")
    print(
        "Examples (composer eu_pd_status=pd, work has nonpd_eu) "
        f"[{len(report['pd_composer_nonpd_eu_examples'])}]:"
    )
    for ex in report["pd_composer_nonpd_eu_examples"]:
        print(
            f"  {ex['title']} — {ex['composer']} ({ex['composer_id']}); "
            f"first published {ex['imslp_first_published'] or '(none)'}; "
            f"librettists {ex['imslp_librettists'] or '(none)'}"
        )
    if not report["pd_composer_nonpd_eu_examples"]:
        print("  none")


def run(args: argparse.Namespace) -> None:
    src = args.from_dump
    out_id = args.to or next_revision_id()
    composers = pd.read_csv(
        dump_tsv_path("composers", src), sep="\t", dtype=str, keep_default_na=False,
    )
    works = pd.read_csv(
        dump_tsv_path("works", src), sep="\t", dtype=str, keep_default_na=False,
    )
    log.info("Loaded %d composers, %d works from %s", len(composers), len(works), src)
    works_out, report = remap_work_evidence(composers, works)
    if getattr(args, "preserve_schema", False):
        works_out = works_out.reindex(columns=works.columns)
    composers_out = composers.copy()
    if "dump_date" in composers_out.columns:
        composers_out["dump_date"] = out_id
    if "dump_date" in works_out.columns:
        works_out["dump_date"] = out_id
    print_report(report, src, out_id)
    if args.dry_run:
        return

    for path in (
        dump_tsv_path("composers", out_id),
        dump_tsv_path("works", out_id),
        dump_meta_path(out_id),
    ):
        if path.exists():
            raise FileExistsError(f"Refusing to overwrite existing dump: {path}")
    c_path = write_tsv_dump(composers_out, "composers", out_id)
    w_path = write_tsv_dump(works_out, "works", out_id)
    write_dump_meta(
        out_id,
        {
            "dump_id": out_id,
            "tool_version": TOOL_VERSION,
            "schema_version": SCHEMA_VERSION,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "derived_from_dump_id": src,
            "enrichment": "remap_work_evidence",
            "output_files": {"composers": c_path.name, "works": w_path.name},
            "row_counts": {"composers": len(composers_out), "works": len(works_out)},
            "coverage": report["coverage"],
            "style_counts": report["style_counts"],
            "copyright_token_counts": report["copyright_token_counts"],
            "unmapped_copyright_categories": report["unmapped_copyright_categories"],
            "distinct_librettists": report["distinct_librettists"],
            "eu_pd_status_x_nonpd_eu": report["eu_pd_status_x_nonpd_eu"],
            "pd_composer_nonpd_eu_examples": report["pd_composer_nonpd_eu_examples"],
            "missing_cache": report["missing_cache"],
            "redirect_or_missing_page": report["redirect_or_missing_page"],
            "notes": [
                "Offline parse of cached IMSLP work-page categories (imslp_page_cats)",
                "Added imslp_style, imslp_first_published, imslp_copyright_flags, imslp_librettists after composition_year",
                "Style names strip the trailing ' style' suffix; first published uses YYYY-only categories",
                "Copyright categories map to normalised tokens; unmapped NonPD/RoST/copyright cats are reported",
                "Redirect and missing pages yield empty evidence cells",
                "Composers copied unchanged except dump_date; works dump_date stamped only when present",
            ],
        },
    )
    log.info("Wrote %s → %s, %s", out_id, c_path.name, w_path.name)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-dump", required=True)
    parser.add_argument("--to", help="Output revision id (default: next rNNN)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--preserve-schema", action="store_true", help="Refresh existing evidence columns without adding columns")
    run(parser.parse_args())


if __name__ == "__main__":
    main()
