#!/usr/bin/env python3
"""Recompute composer dates, scope, QA flags and PD fields from cached Wikidata.

Never fetches entities or overwrites a dump. Unrelated composer fields and row
order are preserved; both files receive the new dump_date when that column exists.
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

import common  # noqa: E402
from build_dump import eu_pd_fields  # noqa: E402
from common import (  # noqa: E402
    SCHEMA_VERSION,
    TOOL_VERSION,
    dump_meta_path,
    dump_tsv_path,
    next_revision_id,
    pipe_split,
    write_dump_meta,
    write_tsv_dump,
)
from remap_force import dump_year_from_meta  # noqa: E402
from wikidata_enrich import QA_FLAGS, apply_year_fallbacks, enrich_from_entity  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("remap_composers")

NEW_COLUMNS = ("is_film_composer", "qa_flags")
ENRICH_FIELDS = (
    "birth_year",
    "death_year",
    "date_precision",
    "scope_class",
    "scope_class_src",
    *NEW_COLUMNS,
)
PD_FIELDS = ("eu_pd_year", "eu_pd_status", "years_until_eu_pd")
REMAP_FIELDS = (*ENRICH_FIELDS, *PD_FIELDS)
YEAR_FIELDS = {"birth_year", "death_year", "eu_pd_year", "years_until_eu_pd"}
REPORT_FIELDS = ("birth_year", "death_year", "eu_pd_status", "scope_class")


def _as_str(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def _as_int(value: Any) -> int | None:
    text = _as_str(value)
    if not text:
        return None
    try:
        return int(float(text))
    except (TypeError, ValueError, OverflowError):
        return None


def _field_value(field: str, value: Any) -> str:
    if field in YEAR_FIELDS:
        year = _as_int(value)
        return "" if year is None else str(year)
    return _as_str(value)


def remap_composers(
    composers: pd.DataFrame, dump_year: int,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Remap cached rows only; count semantic changes, excluding .0 formatting."""
    out = composers.copy()
    added = [col for col in NEW_COLUMNS if col not in out.columns]
    for col in added:
        if col == "is_film_composer":
            out[col] = out["occupations"].map(
                lambda value: "true" if "film_composer" in pipe_split(value) else "false"
            )
        else:
            out[col] = ""
    columns = [col for col in composers.columns]
    insert_at = columns.index("scope_class_src") + 1
    columns[insert_at:insert_at] = added
    out = out.reindex(columns=columns)
    for field in REMAP_FIELDS:
        out[field] = out[field].astype(object)

    changed_rows: list[dict[str, Any]] = []
    missing_entities = 0
    for idx, row in composers.iterrows():
        qid = _as_str(row.get("composer_id"))
        ent = common.cache_get("wikidata_entity", qid)
        if ent is None:
            missing_entities += 1
            continue
        enriched = enrich_from_entity(ent)
        # Years Wikidata cannot supply came from the Wikipedia list at build
        # time; keep them unless Wikidata deprecated exactly that value.
        enriched.update(
            apply_year_fallbacks(
                enriched,
                ent.get("claims", {}),
                fallback_birth=_as_int(row.get("birth_year")),
                fallback_death=_as_int(row.get("death_year")),
            )
        )
        fields = {field: enriched[field] for field in ENRICH_FIELDS}
        fields.update(eu_pd_fields(enriched["death_year"], dump_year))
        changes: dict[str, tuple[str, str]] = {}
        for field, value in fields.items():
            old = _field_value(field, row.get(field))
            new = _field_value(field, value)
            if old != new:
                if field in REPORT_FIELDS:
                    changes[field] = (old, new)
            out.at[idx, field] = new
        if changes:
            changed_rows.append(
                {
                    "name": _as_str(row.get("name_display")),
                    "qid": qid,
                    "changes": changes,
                }
            )

    # Normalize numeric spelling even for uncached rows, without changing years.
    for field in YEAR_FIELDS:
        out[field] = out[field].map(lambda value: _field_value(field, value))

    field_counts: dict[str, int] = {}
    for field in REMAP_FIELDS:
        old_values = composers[field] if field in composers else [""] * len(composers)
        field_counts[field] = sum(
            _field_value(field, old) != _field_value(field, new)
            for old, new in zip(old_values, out[field])
        )

    qa_counts: Counter[str] = Counter({flag: 0 for flag in QA_FLAGS})
    qa_examples: dict[str, list[str]] = {flag: [] for flag in QA_FLAGS}
    for _, row in out.iterrows():
        for flag in pipe_split(_as_str(row.get("qa_flags"))):
            qa_counts[flag] += 1
            examples = qa_examples.setdefault(flag, [])
            if len(examples) < 15:
                examples.append(_as_str(row.get("name_display")))
    film_names = out.loc[out["scope_class"] == "film_media", "name_display"].tolist()
    return out, {
        "field_change_counts": field_counts,
        "changed_rows": changed_rows,
        "qa_flags_counts": dict(sorted(qa_counts.items())),
        "qa_flags_examples": qa_examples,
        "missing_entities": missing_entities,
        "film_media_remaining": len(film_names),
        "film_media_examples": film_names[:10],
    }


def print_report(report: dict[str, Any], src: str, out_id: str, dump_year: int) -> None:
    print(f"Composer remap {src} → {out_id} (PD reference year {dump_year})")
    print("Field changes (numeric formatting excluded):")
    for field, count in report["field_change_counts"].items():
        print(f"  {field}: {count}")
    print(f"Changed dates / PD status / scope: {len(report['changed_rows'])} rows")
    for row in report["changed_rows"]:
        changes = "; ".join(
            f"{field}: {old or '(empty)'} → {new or '(empty)'}"
            for field, (old, new) in row["changes"].items()
        )
        print(f"  {row['name']} ({row['qid']}): {changes}")
    print("QA flags:")
    for flag, count in report["qa_flags_counts"].items():
        examples = ", ".join(report["qa_flags_examples"][flag])
        print(f"  {flag}: {count}" + (f" — {examples}" if examples else ""))
    print(f"Missing cached entities: {report['missing_entities']}")
    print(f"film_media remaining: {report['film_media_remaining']}")
    print("  Examples: " + (", ".join(report["film_media_examples"]) or "none"))


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
    dump_year = dump_year_from_meta(src)
    composers, report = remap_composers(composers, dump_year)
    for df in (composers, works):
        if "dump_date" in df.columns:
            df["dump_date"] = out_id
    print_report(report, src, out_id, dump_year)
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
    write_dump_meta(
        out_id,
        {
            "dump_id": out_id,
            "tool_version": TOOL_VERSION,
            "schema_version": SCHEMA_VERSION,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "derived_from_dump_id": src,
            "enrichment": "remap_composers_wikidata",
            "output_files": {"composers": c_path.name, "works": w_path.name},
            "row_counts": {"composers": len(composers), "works": len(works)},
            "field_change_counts": report["field_change_counts"],
            "qa_flags_counts": report["qa_flags_counts"],
            "missing_entities": report["missing_entities"],
            "pd_reference_year": dump_year,
            "notes": [
                "Recomputed dates, scope, QA and PD fields using cached Wikidata only",
                "Preferred usable time claims beat normal; deprecated claims ignored",
                "Coarse dates use the final year of the Wikidata precision interval",
                "QA flags are informational; no rows dropped or dates blanked for flags",
                "Years Wikidata lacks keep the prior (Wikipedia list) value unless deprecated",
                "Missing entities retained; year spelling normalized to integers",
                "New film flags on uncached rows use their retained occupations",
                "Other composer fields and row order preserved; dump_date set to this revision",
                "Works preserved except dump_date when present",
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
