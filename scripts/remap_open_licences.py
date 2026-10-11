#!/usr/bin/env python3
"""Add open-licence columns from cached work wikitext and a composer catalogue registry.

Offline only. Writes a new revision without overwriting inputs; --dry-run prints
the audit report without writing. Works whose page is not in the local
`imslp_page_wikitext` cache keep their prior open-licence cells, so an incomplete
cache never erases published evidence. Redirect / missing pages get empty cells.

  python scripts/remap_open_licences.py --from-dump r019 --to r020
  python scripts/remap_open_licences.py --from-dump r019 --to r999 --dry-run
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
from urllib.parse import urlparse

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    REPO_ROOT,
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
from imslp import PAGE_WIKITEXT_NAMESPACE  # noqa: E402
from imslp_licences import is_open_licence  # noqa: E402
from open_licences import (  # noqa: E402
    EMPTY_OPEN_COMPOSER,
    EMPTY_OPEN_WORK,
    OPEN_COMPOSER_COLUMNS,
    OPEN_WORK_COLUMNS,
    open_work_cells,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("remap_open_licences")

REGISTRY_COLUMNS = (
    "composer_id",
    "name_display",
    "catalogue_url",
    "licence",
    "rights_holder",
    "n_score_links",
    "evidence_url",
    "evidence_quote",
    "verified_on",
    "note",
)


def default_registry_path() -> Path:
    return REPO_ROOT / "data" / "overrides" / "open_catalogues.tsv"


def _insert_work_columns(works: pd.DataFrame) -> pd.DataFrame:
    """Place open-licence columns after imslp_librettists (or other evidence / at end)."""
    out = works.copy()
    present = [col for col in OPEN_WORK_COLUMNS if col in out.columns]
    if present:
        out = out.drop(columns=present)
    columns = list(out.columns)
    if "imslp_librettists" in columns:
        insert_at = columns.index("imslp_librettists") + 1
    elif "imslp_copyright_flags" in columns:
        insert_at = columns.index("imslp_copyright_flags") + 1
    elif "composition_year" in columns:
        insert_at = columns.index("composition_year") + 1
    else:
        insert_at = len(columns)
    for offset, col in enumerate(OPEN_WORK_COLUMNS):
        columns.insert(insert_at + offset, col)
        out[col] = ""
    return out.reindex(columns=columns)


def _insert_composer_columns(composers: pd.DataFrame) -> pd.DataFrame:
    """Place open-catalogue columns after years_until_eu_pd (or eu_pd_status / end)."""
    out = composers.copy()
    present = [col for col in OPEN_COMPOSER_COLUMNS if col in out.columns]
    if present:
        out = out.drop(columns=present)
    columns = list(out.columns)
    if "years_until_eu_pd" in columns:
        insert_at = columns.index("years_until_eu_pd") + 1
    elif "eu_pd_status" in columns:
        insert_at = columns.index("eu_pd_status") + 1
    else:
        insert_at = len(columns)
    for offset, col in enumerate(OPEN_COMPOSER_COLUMNS):
        columns.insert(insert_at + offset, col)
        out[col] = ""
    return out.reindex(columns=columns)


def _prior_cells(frame: pd.DataFrame, idx: Any, columns: tuple[str, ...]) -> dict[str, str]:
    prior = {}
    for col in columns:
        value = frame.at[idx, col] if col in frame.columns else ""
        prior[col] = "" if pd.isna(value) else str(value)
    return prior


def _http_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def load_registry(path: Path) -> pd.DataFrame:
    """Load and validate the open-catalogues registry; raise ValueError on bad rows."""
    if not path.is_file():
        raise FileNotFoundError(path)
    df = pd.read_csv(path, sep="\t", dtype=str, keep_default_na=False)
    missing_cols = [c for c in REGISTRY_COLUMNS if c not in df.columns]
    if missing_cols:
        raise ValueError(f"open catalogues registry missing columns: {missing_cols}")
    df = df[list(REGISTRY_COLUMNS)].copy()

    errors: list[str] = []
    seen: dict[str, int] = {}
    for i, row in df.iterrows():
        line = int(i) + 2  # header is line 1
        cid = str(row["composer_id"]).strip()
        if not cid:
            errors.append(f"line {line}: missing composer_id")
            continue
        if cid in seen:
            errors.append(f"line {line}: duplicate composer_id {cid} (also line {seen[cid]})")
        else:
            seen[cid] = line
        for col in REGISTRY_COLUMNS:
            if not str(row[col]).strip():
                errors.append(f"line {line} ({cid}): missing required field {col}")
        lid = str(row["licence"]).strip()
        if lid and not is_open_licence(lid):
            errors.append(f"line {line} ({cid}): licence {lid!r} is not an open licence")
        for url_col in ("catalogue_url", "evidence_url"):
            url = str(row[url_col]).strip()
            if url and not _http_url(url):
                errors.append(f"line {line} ({cid}): {url_col} must be http(s): {url!r}")
        n_links = str(row["n_score_links"]).strip()
        if n_links and not n_links.isdigit():
            errors.append(f"line {line} ({cid}): n_score_links must be a non-negative integer")

    if errors:
        raise ValueError(
            "open catalogues registry validation failed:\n  " + "\n  ".join(errors)
        )
    return df


def remap_open_licences(
    composers: pd.DataFrame,
    works: pd.DataFrame,
    registry: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Fill open-licence work cells from cache and composer catalogue cells from registry."""
    works_out = _insert_work_columns(works)
    composers_out = _insert_composer_columns(composers)

    status_by_id = {
        str(row["composer_id"]): str(row.get("eu_pd_status") or "")
        for _, row in composers.iterrows()
    }

    page_state: Counter[str] = Counter()
    licence_counts: Counter[str] = Counter()
    format_counts: Counter[str] = Counter()
    scope_counts: Counter[str] = Counter()
    composition_composers: set[str] = set()
    works_with_open = 0
    kept_prior = 0
    no_pageid = 0

    for idx, row in works_out.iterrows():
        pageid = str(row.get("imslp_pageid") or "").strip().split(".")[0]
        cid = str(row.get("composer_id") or "")
        eu_status = status_by_id.get(cid, "")
        if not pageid:
            no_pageid += 1
            page_state["no_pageid"] += 1
            cells = dict(EMPTY_OPEN_WORK)
        elif (cached := cache_get(PAGE_WIKITEXT_NAMESPACE, pageid)) is None:
            page_state["not_cached"] += 1
            cells = _prior_cells(works, idx, OPEN_WORK_COLUMNS)
            if any(cells.values()):
                kept_prior += 1
        elif cached.get("redirect") or cached.get("missing"):
            page_state["redirect_or_missing"] += 1
            cells = dict(EMPTY_OPEN_WORK)
        else:
            page_state["parsed"] += 1
            cells = open_work_cells(
                cached.get("wikitext") or "", eu_pd_status=eu_status,
            )

        for col, value in cells.items():
            works_out.at[idx, col] = value
        if cells["imslp_open_licences"]:
            works_with_open += 1
            scope_counts[cells["imslp_open_scope"] or "(empty)"] += 1
            if cells["imslp_open_scope"] == "composition" and cid:
                composition_composers.add(cid)
            for lid in pipe_split(cells["imslp_open_licences"]):
                licence_counts[lid] += 1
            for fmt in pipe_split(cells["imslp_open_formats"]):
                format_counts[fmt] += 1

    dump_ids = {str(row["composer_id"]) for _, row in composers_out.iterrows()}
    registry_by_id = {
        str(row["composer_id"]).strip(): row for _, row in registry.iterrows()
    }
    not_in_dump = sorted(cid for cid in registry_by_id if cid not in dump_ids)
    composers_with_catalogue = 0

    for idx, row in composers_out.iterrows():
        cid = str(row["composer_id"])
        reg = registry_by_id.get(cid)
        if reg is None:
            for col, value in EMPTY_OPEN_COMPOSER.items():
                composers_out.at[idx, col] = value
            continue
        composers_out.at[idx, "open_catalogue_url"] = str(reg["catalogue_url"]).strip()
        composers_out.at[idx, "open_catalogue_licence"] = str(reg["licence"]).strip()
        composers_with_catalogue += 1

    report: dict[str, Any] = {
        "works_total": len(works_out),
        "works_with_open_licences": works_with_open,
        "scope_counts": dict(sorted(scope_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
        "licence_id_counts": dict(
            sorted(licence_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ),
        "format_counts": dict(
            sorted(format_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ),
        "composers_with_composition_scope": len(composition_composers),
        "composers_with_open_catalogue": composers_with_catalogue,
        "registry_rows": len(registry),
        "registry_not_in_dump": not_in_dump,
        "page_state": dict(page_state),
        "no_pageid": no_pageid,
        "kept_prior": kept_prior,
        "missing_cache": int(page_state.get("not_cached", 0)),
        "redirect_or_missing": int(page_state.get("redirect_or_missing", 0)),
        "parsed": int(page_state.get("parsed", 0)),
    }
    return composers_out, works_out, report


def print_report(report: dict[str, Any], src: str, out_id: str) -> None:
    print(f"Open-licence remap {src} → {out_id} (offline)")
    print(f"Works: {report['works_total']}")
    print(
        f"Works with open licences: {report['works_with_open_licences']} "
        f"(of {report['works_total']})"
    )
    print("Scope:")
    for scope, count in report["scope_counts"].items():
        print(f"  {scope}: {count}")
    if not report["scope_counts"]:
        print("  none")
    print("Licence ids (works counted once per distinct id):")
    for lid, count in report["licence_id_counts"].items():
        print(f"  {lid}: {count}")
    if not report["licence_id_counts"]:
        print("  none")
    print("Formats:")
    for fmt, count in report["format_counts"].items():
        print(f"  {fmt}: {count}")
    if not report["format_counts"]:
        print("  none")
    print(
        f"Composers with ≥1 composition-scope work: "
        f"{report['composers_with_composition_scope']}"
    )
    print(
        f"Composers with an open catalogue: {report['composers_with_open_catalogue']} "
        f"(registry rows: {report['registry_rows']})"
    )
    if report["registry_not_in_dump"]:
        print(
            "Registry rows not in dump: "
            + ", ".join(report["registry_not_in_dump"])
        )
    else:
        print("Registry rows not in dump: 0")
    ps = report["page_state"]
    print(
        "Pages: "
        f"parsed {ps.get('parsed', 0)}, "
        f"redirect/missing {ps.get('redirect_or_missing', 0)}, "
        f"not cached {ps.get('not_cached', 0)} "
        f"(prior kept: {report['kept_prior']}), "
        f"no pageid {ps.get('no_pageid', 0)}"
    )


def run(args: argparse.Namespace) -> None:
    src = args.from_dump
    out_id = args.to or next_revision_id()
    registry_path = Path(args.registry) if args.registry else default_registry_path()
    registry = load_registry(registry_path)
    composers = pd.read_csv(
        dump_tsv_path("composers", src), sep="\t", dtype=str, keep_default_na=False,
    )
    works = pd.read_csv(
        dump_tsv_path("works", src), sep="\t", dtype=str, keep_default_na=False,
    )
    log.info(
        "Loaded %d composers, %d works from %s; registry %s (%d rows)",
        len(composers), len(works), src, registry_path, len(registry),
    )
    composers_out, works_out, report = remap_open_licences(composers, works, registry)
    if report["missing_cache"]:
        log.warning(
            "%d works have no cached page wikitext; kept their prior open-licence cells "
            "(fill the cache with refetch_work_wikitext.py --dump %s)",
            report["missing_cache"], src,
        )
    if report["registry_not_in_dump"]:
        log.warning(
            "Registry composers not in dump: %s",
            ", ".join(report["registry_not_in_dump"]),
        )

    if getattr(args, "preserve_schema", False):
        composers_out = composers_out.reindex(columns=composers.columns)
        works_out = works_out.reindex(columns=works.columns)

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
    source_meta = dump_meta_path(src)
    meta = json.loads(source_meta.read_text(encoding="utf-8")) if source_meta.exists() else {}
    meta.update(
        {
            "dump_id": out_id,
            "tool_version": TOOL_VERSION,
            "schema_version": SCHEMA_VERSION,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "derived_from_dump_id": src,
            "enrichment": "remap_open_licences",
            "output_files": {"composers": c_path.name, "works": w_path.name},
            "row_counts": {"composers": len(composers_out), "works": len(works_out)},
            "composer_columns": list(composers_out.columns),
            "work_columns": list(works_out.columns),
            "open_licences": {
                "works_with_open_licences": report["works_with_open_licences"],
                "scope_counts": report["scope_counts"],
                "licence_id_counts": report["licence_id_counts"],
                "format_counts": report["format_counts"],
                "composers_with_composition_scope": report[
                    "composers_with_composition_scope"
                ],
                "composers_with_open_catalogue": report["composers_with_open_catalogue"],
                "registry_not_in_dump": report["registry_not_in_dump"],
                "page_state": report["page_state"],
                "kept_prior": report["kept_prior"],
                "registry": str(
                    registry_path.relative_to(REPO_ROOT)
                    if registry_path.is_absolute() and REPO_ROOT in registry_path.parents
                    else registry_path
                ),
            },
            "notes": [
                "Offline parse of cached IMSLP work-page wikitext (imslp_page_wikitext)",
                "Added imslp_open_licences, imslp_open_scope, imslp_open_formats after imslp evidence",
                "Open score blocks only ({{#fte:imslpfile}} with is_open_licence); audio ignored",
                "Scope composition = New Composition + composer eu_pd_status≠pd + Arranger empty/Composer",
                "Else edition when open scores exist (covers PD works and third-party arrangements)",
                "Composer open_catalogue_url / open_catalogue_licence from open_catalogues.tsv",
                "Redirect and missing pages yield empty open-licence cells",
                "Uncached pages keep their prior open-licence cells",
            ],
        }
    )
    write_dump_meta(out_id, meta)
    log.info("Wrote %s → %s, %s", out_id, c_path.name, w_path.name)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-dump", required=True)
    parser.add_argument("--to", help="Output revision id (default: next rNNN)")
    parser.add_argument(
        "--registry",
        default=None,
        help="Open catalogues TSV (default: data/overrides/open_catalogues.tsv)",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--preserve-schema",
        action="store_true",
        help="Refresh existing open-licence columns without adding columns",
    )
    run(parser.parse_args())


if __name__ == "__main__":
    main()
