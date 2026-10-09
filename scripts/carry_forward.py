#!/usr/bin/env python3
"""Carry approved force, GenInfo and style decisions into a new dump, offline."""

from __future__ import annotations

import argparse
import logging
from datetime import datetime, timezone
from typing import Any

import pandas as pd

import common
from remap_force import dump_year_from_meta, rollup_composers

log = logging.getLogger("carry_forward")
GENINFO_COLUMNS = ("instrumentation_raw", "piece_style_raw", "composition_year")
WORK_KEY = ("composer_id", "imslp_pageid")


def _force_decision(row: dict[str, str]) -> bool:
    src = row.get("force_family_src", "")
    return src.startswith("llm") or src == "imslp_geninfo"


def _index(df: pd.DataFrame, keys: tuple[str, ...]) -> dict[tuple[str, ...], dict[str, str]]:
    if df.duplicated(subset=list(keys)).any():
        raise ValueError(f"Duplicate carry-forward keys: {keys}")
    return {tuple(row[k] for k in keys): row for row in df.to_dict("records")}


def carry_forward(
    composers: pd.DataFrame, works: pd.DataFrame,
    base_composers: pd.DataFrame, base_works: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    out_c, out_w = composers.copy(), works.copy()
    prev_w = _index(base_works, WORK_KEY)
    prev_c = _index(base_composers, ("composer_id",))
    new_w = _index(works, WORK_KEY)
    new_c = _index(composers, ("composer_id",))
    counts = {"force_family": 0, **{col: 0 for col in GENINFO_COLUMNS}, "style_tags": 0}
    for col in GENINFO_COLUMNS:
        if col in base_works and col not in out_w:
            out_w[col] = ""
    for col in ("style_tags", "style_tags_src"):
        if col in base_composers and col not in out_c:
            out_c[col] = ""

    for idx, row in works.iterrows():
        prior = prev_w.get(tuple(row[k] for k in WORK_KEY))
        if prior is None:
            continue
        src = row.get("force_family_src", "")
        weak = row.get("force_family", "") in {"unclassified", "other"} or src not in {
            "imslp_tags", "imslp_geninfo",
        }
        if _force_decision(prior) and weak and src != "imslp_tags":
            if any(row.get(col, "") != prior.get(col, "")
                   for col in ("force_family", "force_family_src")):
                for col in ("force_family", "force_family_src"):
                    out_w.at[idx, col] = prior.get(col, "")
                counts["force_family"] += 1
        for col in GENINFO_COLUMNS:
            if not row.get(col, "") and prior.get(col, ""):
                out_w.at[idx, col] = prior[col]
                counts[col] += 1

    for idx, row in composers.iterrows():
        prior = prev_c.get((row["composer_id"],))
        if prior and prior.get("style_tags_src", "").startswith("llm") and not row.get("style_tags", ""):
            for col in ("style_tags", "style_tags_src"):
                out_c.at[idx, col] = prior.get(col, "")
            counts["style_tags"] += 1

    lost_w = [key for key in prev_w if key not in new_w]
    lost_c = [key for key in prev_c if key not in new_c]
    lost_decisions = {
        "force_family": [list(k) for k in lost_w if _force_decision(prev_w[k])],
        **{col: [list(k) for k in lost_w if prev_w[k].get(col, "")] for col in GENINFO_COLUMNS},
        "style_tags": [k[0] for k in lost_c if prev_c[k].get("style_tags_src", "").startswith("llm")],
    }
    report = {
        "carried_forward": counts,
        "lost_rows": {"works": [list(k) for k in lost_w], "composers": [k[0] for k in lost_c]},
        "lost_decisions": lost_decisions,
        "lost_decision_counts": {rule: len(keys) for rule, keys in lost_decisions.items()},
    }
    return rollup_composers(out_c, out_w), out_w, report


def run(args: argparse.Namespace) -> None:
    def read(stem: str, dump_id: str) -> pd.DataFrame:
        return pd.read_csv(common.dump_tsv_path(stem, dump_id), sep="\t", dtype=str, keep_default_na=False)

    composers, works, report = carry_forward(
        read("composers", args.from_dump), read("works", args.from_dump),
        read("composers", args.base), read("works", args.base),
    )
    for rule, count in report["carried_forward"].items():
        print(f"{rule}: carried={count}, lost={report['lost_decision_counts'][rule]}")
    print(f"Missing base rows: composers={len(report['lost_rows']['composers'])}, works={len(report['lost_rows']['works'])}")
    for composer_id, pageid in report["lost_rows"]["works"]:
        print(f"  lost work: {composer_id}, {pageid}")
    for composer_id in report["lost_rows"]["composers"]:
        print(f"  lost composer: {composer_id}")
    if args.dry_run:
        return
    for path in (common.dump_tsv_path("composers", args.to), common.dump_tsv_path("works", args.to),
                 common.dump_meta_path(args.to)):
        if path.exists():
            raise FileExistsError(f"Refusing to overwrite existing dump: {path}")
    for stem, df in (("composers", composers), ("works", works)):
        if "dump_date" in df:
            df["dump_date"] = args.to
        common.write_tsv_dump(df, stem, args.to)
    common.write_dump_meta(args.to, {
        "dump_id": args.to, "tool_version": common.TOOL_VERSION, "schema_version": common.SCHEMA_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "derived_from_dump_id": args.from_dump, "base_dump_id": args.base,
        "enrichment": "carry_forward", "row_counts": {"composers": len(composers), "works": len(works)},
        "pd_reference_year": dump_year_from_meta(args.from_dump),
        "output_files": {stem: common.dump_tsv_path(stem, args.to).name for stem in ("composers", "works")},
        **report,
    })


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-dump", required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--to", required=True)
    parser.add_argument("--dry-run", action="store_true")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run(parser.parse_args())


if __name__ == "__main__":
    main()
