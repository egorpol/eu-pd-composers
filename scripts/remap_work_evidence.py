#!/usr/bin/env python3
"""Add IMSLP work evidence columns from cached page categories and page files.

Offline only. Writes a new revision without overwriting inputs; --dry-run
prints the complete audit report without writing any output files. Works whose
page is not in a local cache (imslp_page_cats, imslp_page_files) keep their prior
cells from that cache, so an incomplete cache never erases published evidence.
"""

from __future__ import annotations

import argparse
import json
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
from imslp import PAGE_CATS_NAMESPACE, PAGE_FILES_NAMESPACE  # noqa: E402
from imslp_work_evidence import (  # noqa: E402
    EVIDENCE_COLUMNS,
    FILE_HOSTS,
    FILE_HOSTS_COLUMN,
    file_evidence,
    score_files,
    unmapped_copyright_categories,
    work_evidence,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("remap_work_evidence")

EMPTY_EVIDENCE = {col: "" for col in EVIDENCE_COLUMNS}
FILE_COLUMNS = ("has_files", FILE_HOSTS_COLUMN)
EMPTY_FILES = {col: "" for col in FILE_COLUMNS}


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


def _insert_file_columns(works: pd.DataFrame) -> pd.DataFrame:
    """Place imslp_file_hosts right after has_files (appending either when absent)."""
    out = works.drop(columns=[FILE_HOSTS_COLUMN], errors="ignore")
    if "has_files" not in out.columns:
        out["has_files"] = ""
    columns = list(out.columns)
    columns.insert(columns.index("has_files") + 1, FILE_HOSTS_COLUMN)
    out[FILE_HOSTS_COLUMN] = ""
    return out.reindex(columns=columns)


def _prior_cells(works: pd.DataFrame, idx: Any, columns: tuple[str, ...]) -> dict[str, str]:
    """Cells the input row already carries (empty when the column is absent)."""
    prior = {}
    for col in columns:
        value = works.at[idx, col] if col in works.columns else ""
        prior[col] = "" if pd.isna(value) else str(value)
    return prior


def remap_work_evidence(
    composers: pd.DataFrame,
    works: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Fill evidence and file cells from cache; composers are returned unchanged.

    Uncached pages keep their prior cells; rows without a page id get empty cells.
    """
    out = _insert_file_columns(_insert_evidence_columns(works))
    n = len(out)
    style_counts: Counter[str] = Counter()
    copyright_token_counts: Counter[str] = Counter()
    unmapped_counts: Counter[str] = Counter()
    librettist_names: set[str] = set()
    coverage = {col: 0 for col in EVIDENCE_COLUMNS}
    no_pageid = 0
    missing_cache = 0
    kept_prior = 0
    redirect_or_missing_page = 0
    has_files_counts: Counter[str] = Counter()
    file_host_counts: Counter[str] = Counter()
    files_missing_cache = 0
    files_kept_prior = 0
    files_redirect_or_missing_page = 0
    score_files_total = 0

    for idx, row in out.iterrows():
        pageid = str(row.get("imslp_pageid") or "").strip()
        categories: list[str] = []
        if not pageid:
            no_pageid += 1
            evidence = dict(EMPTY_EVIDENCE)
        elif (cached := cache_get(PAGE_CATS_NAMESPACE, pageid)) is None:
            missing_cache += 1
            evidence = _prior_cells(works, idx, EVIDENCE_COLUMNS)
            if any(evidence.values()):
                kept_prior += 1
        else:
            if cached.get("redirect") or cached.get("missing"):
                redirect_or_missing_page += 1
            else:
                categories = list(cached.get("categories") or [])
            evidence = work_evidence(categories)

        if not pageid:
            files = dict(EMPTY_FILES)
        elif (cached_files := cache_get(PAGE_FILES_NAMESPACE, pageid)) is None:
            files_missing_cache += 1
            files = _prior_cells(works, idx, FILE_COLUMNS)
            if any(files.values()):
                files_kept_prior += 1
        elif cached_files.get("redirect") or cached_files.get("missing"):
            files_redirect_or_missing_page += 1
            files = dict(EMPTY_FILES)
        else:
            linked = list(cached_files.get("images") or [])
            score_files_total += len(score_files(linked))
            files = file_evidence(linked)

        for col, value in evidence.items():
            out.at[idx, col] = value
            if value:
                coverage[col] += 1
        for col, value in files.items():
            out.at[idx, col] = value
        has_files_counts[files["has_files"] or "(empty)"] += 1
        for host in pipe_split(files[FILE_HOSTS_COLUMN]):
            file_host_counts[host] += 1

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
        "no_pageid": no_pageid,
        "missing_cache": missing_cache,
        "kept_prior": kept_prior,
        "redirect_or_missing_page": redirect_or_missing_page,
        "has_files_counts": dict(sorted(has_files_counts.items())),
        "file_host_counts": dict(sorted(file_host_counts.items())),
        "unknown_file_hosts": sorted(set(file_host_counts) - FILE_HOSTS),
        "score_files_total": score_files_total,
        "files_missing_cache": files_missing_cache,
        "files_kept_prior": files_kept_prior,
        "files_redirect_or_missing_page": files_redirect_or_missing_page,
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
    print(f"Works without page id: {report['no_pageid']}")
    print(
        f"Missing cache entries: {report['missing_cache']} "
        f"(prior evidence kept: {report['kept_prior']})"
    )
    print(f"Redirect / missing pages: {report['redirect_or_missing_page']}")
    print("has_files:")
    for value, count in report["has_files_counts"].items():
        print(f"  {value}: {count}")
    print(f"Works per file host ({report['score_files_total']} score/audio files):")
    for host, count in report["file_host_counts"].items():
        print(f"  {host}: {count}")
    if report["unknown_file_hosts"]:
        print("Unknown file hosts: " + ", ".join(report["unknown_file_hosts"]))
    print(
        f"Missing file cache entries: {report['files_missing_cache']} "
        f"(prior file cells kept: {report['files_kept_prior']})"
    )
    print(f"Redirect / missing pages (files): {report['files_redirect_or_missing_page']}")
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
    if report["missing_cache"]:
        log.warning(
            "%d works have no cached page categories; kept their prior evidence "
            "(fill the cache with refetch_work_categories.py --dump %s)",
            report["missing_cache"], src,
        )
    if report["files_missing_cache"]:
        log.warning(
            "%d works have no cached page files; kept their prior file cells "
            "(fill the cache with refetch_work_files.py --dump %s)",
            report["files_missing_cache"], src,
        )
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
    # Keep the source's provenance (reference year, stages, LLM policy) when run as a single-stage replay.
    source_meta = dump_meta_path(src)
    meta = json.loads(source_meta.read_text(encoding="utf-8")) if source_meta.exists() else {}
    meta.update(
        {
            "dump_id": out_id,
            "tool_version": TOOL_VERSION,
            "schema_version": SCHEMA_VERSION,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "derived_from_dump_id": src,
            "enrichment": "remap_work_evidence",
            "output_files": {"composers": c_path.name, "works": w_path.name},
            "row_counts": {"composers": len(composers_out), "works": len(works_out)},
            "composer_columns": list(composers_out.columns),
            "work_columns": list(works_out.columns),
            "coverage": report["coverage"],
            "style_counts": report["style_counts"],
            "copyright_token_counts": report["copyright_token_counts"],
            "unmapped_copyright_categories": report["unmapped_copyright_categories"],
            "distinct_librettists": report["distinct_librettists"],
            "eu_pd_status_x_nonpd_eu": report["eu_pd_status_x_nonpd_eu"],
            "pd_composer_nonpd_eu_examples": report["pd_composer_nonpd_eu_examples"],
            "no_pageid": report["no_pageid"],
            "missing_cache": report["missing_cache"],
            "kept_prior": report["kept_prior"],
            "redirect_or_missing_page": report["redirect_or_missing_page"],
            "has_files_counts": report["has_files_counts"],
            "file_host_counts": report["file_host_counts"],
            "unknown_file_hosts": report["unknown_file_hosts"],
            "score_files_total": report["score_files_total"],
            "files_missing_cache": report["files_missing_cache"],
            "files_kept_prior": report["files_kept_prior"],
            "files_redirect_or_missing_page": report["files_redirect_or_missing_page"],
            "notes": [
                "Offline parse of cached IMSLP work-page categories (imslp_page_cats)",
                "Added imslp_style, imslp_first_published, imslp_copyright_flags, imslp_librettists after composition_year",
                "Style names strip the trailing ' style' suffix; first published uses YYYY-only categories",
                "Copyright categories map to normalised tokens; unmapped rights-like categories are reported",
                "has_files / imslp_file_hosts from cached page files (imslp_page_files): non-image files only; "
                "hosts ca (main server), us (PMLUS), asia (PMLASIA) from file name prefixes",
                "Redirect and missing pages yield empty evidence cells",
                "Uncached pages keep their prior cells per cache; rows without a page id are empty",
                "Composers copied unchanged except dump_date; works dump_date stamped only when present",
            ],
        }
    )
    write_dump_meta(out_id, meta)
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
