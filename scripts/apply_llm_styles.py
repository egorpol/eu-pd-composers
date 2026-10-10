#!/usr/bin/env python3
"""Replay the committed style ledger into a new dump, offline and without models."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

import common
from style_vocab import PRIMARY_PERIOD_ALLOWED, STYLE_SLUG_SET

log = logging.getLogger("apply_llm_styles")
POLICY_ID = "llm_consensus_grounded_v2"
POLICY_CONDITION = "grounded"
POLICY_PROMPT_VERSION = "style-v2"
# Order defines consensus ordering, vote serialisation and viewer model order.
POLICY_LABELLERS = (
    ("codex", "gpt-6.1-sol", "low", "GPT-6.1 Sol"),
    ("cursor", "grok-4.7-low", "", "Grok 4.7"),
)
LLM_STYLE_COLUMNS = ("llm_style_tags", "llm_style_period", "llm_style_votes", "llm_style_src")


def default_ledger_path() -> Path:
    return common.REPO_ROOT / "data" / "llm_ledger" / "style_labels.jsonl"


def vote_key(model: str, effort: str) -> str:
    return f"{model}@{effort}" if effort else model


def policy_metadata() -> dict[str, Any]:
    return {
        "id": POLICY_ID, "condition": POLICY_CONDITION, "prompt_version": POLICY_PROMPT_VERSION,
        "labellers": [
            {"backend": backend, "model": model, "effort": effort,
             "key": vote_key(model, effort), "name": name}
            for backend, model, effort, name in POLICY_LABELLERS
        ],
    }


def load_ledger(path: Path) -> dict[tuple[str, str, str, str], dict[str, Any]]:
    selected = {}
    timestamps = {}
    labellers = {labeller[:3] for labeller in POLICY_LABELLERS}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        identity = (row.get("backend"), row.get("model"), row.get("effort"))
        if (row.get("condition") != POLICY_CONDITION
                or row.get("prompt_version") != POLICY_PROMPT_VERSION or identity not in labellers):
            continue
        styles = row.get("styles")
        if (not isinstance(styles, list) or any(s not in STYLE_SLUG_SET for s in styles)
                or row.get("primary_period") not in PRIMARY_PERIOD_ALLOWED):
            raise ValueError(f"Invalid policy styles/period at {path}:{line_number}")
        timestamp = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            raise ValueError(f"Missing timestamp timezone at {path}:{line_number}")
        key = (row["composer_id"], *identity)
        # Equal timestamps deliberately replace the earlier line.
        if key not in timestamps or timestamp >= timestamps[key]:
            timestamps[key] = timestamp
            selected[key] = row
    return selected


def apply_llm_styles(
    composers: pd.DataFrame, ledger: dict[tuple[str, str, str, str], dict[str, Any]],
) -> tuple[pd.DataFrame, dict[str, int]]:
    out = composers.copy()
    legacy = out["style_tags_src"].str.startswith("llm", na=False)
    out.loc[legacy, ["style_tags", "style_tags_src"]] = ""
    columns = [col for col in out.columns if col not in LLM_STYLE_COLUMNS]
    position = columns.index("style_tags_src") + 1
    out = out.reindex(columns=columns[:position] + list(LLM_STYLE_COLUMNS) + columns[position:])
    out[list(LLM_STYLE_COLUMNS)] = ""
    counts = {"consensus_nonempty": 0, "both_labelled_no_consensus": 0, "missing_labellers": 0,
              "period_nonempty": 0, "both_labelled": 0, "legacy_retired": int(legacy.sum())}
    for idx, composer in out.iterrows():
        rows = [ledger.get((composer["composer_id"], *labeller[:3])) for labeller in POLICY_LABELLERS]
        votes = [f"{vote_key(model, effort)}={'|'.join(row['styles'])}"
                 for (_, model, effort, _), row in zip(POLICY_LABELLERS, rows) if row is not None]
        out.at[idx, "llm_style_votes"] = ";".join(votes)
        if any(row is None for row in rows):
            counts["missing_labellers"] += 1
            continue
        first, second = rows
        consensus = [s for s in first["styles"] if s in second["styles"]]
        period = first["primary_period"]
        if period != second["primary_period"] or period == "unknown":
            period = ""
        out.at[idx, "llm_style_tags"] = "|".join(consensus)
        out.at[idx, "llm_style_period"] = period
        out.at[idx, "llm_style_src"] = POLICY_ID
        counts["both_labelled"] += 1
        counts["consensus_nonempty" if consensus else "both_labelled_no_consensus"] += 1
        counts["period_nonempty"] += bool(period)
    return out, counts


def run(args: argparse.Namespace) -> None:
    output = args.to or common.next_revision_id()
    ledger_path = Path(args.ledger) if args.ledger else default_ledger_path()
    composers = pd.read_csv(common.dump_tsv_path("composers", args.from_dump),
                            sep="\t", dtype=str, keep_default_na=False)
    works = pd.read_csv(common.dump_tsv_path("works", args.from_dump),
                        sep="\t", dtype=str, keep_default_na=False)
    composers, counts = apply_llm_styles(composers, load_ledger(ledger_path))
    if "dump_date" in composers:
        composers["dump_date"] = output
    print(f"LLM styles {args.from_dump} → {output}: "
          + ", ".join(f"{key}={value}" for key, value in counts.items()))
    if args.dry_run:
        return
    for path in (common.dump_tsv_path("composers", output), common.dump_tsv_path("works", output),
                 common.dump_meta_path(output)):
        if path.exists():
            raise FileExistsError(f"Refusing to overwrite existing dump: {path}")
    for stem, frame in (("composers", composers), ("works", works)):
        common.write_tsv_dump(frame, stem, output)
    meta = json.loads(common.dump_meta_path(args.from_dump).read_text(encoding="utf-8"))
    meta.update({
        "dump_id": output, "derived_from_dump_id": args.from_dump,
        "tool_version": common.TOOL_VERSION, "schema_version": common.SCHEMA_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(), "enrichment": "apply_llm_styles",
        "row_counts": {"composers": len(composers), "works": len(works)},
        "output_files": {stem: common.dump_tsv_path(stem, output).name for stem in ("composers", "works")},
        "composer_columns": list(composers.columns), "work_columns": list(works.columns),
        "llm_styles": {"policy": policy_metadata(), "ledger_path": str(ledger_path),
                       "ledger_sha256": hashlib.sha256(ledger_path.read_bytes()).hexdigest(), "counts": counts},
    })
    common.write_dump_meta(output, meta)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-dump", required=True)
    parser.add_argument("--to", help="Output revision id (default: next rNNN)")
    parser.add_argument("--ledger", help="Ledger JSONL (default: committed data/llm_ledger/style_labels.jsonl)")
    parser.add_argument("--dry-run", action="store_true")
    run(parser.parse_args())


if __name__ == "__main__":
    main()
