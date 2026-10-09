#!/usr/bin/env python3
"""Verify IMSLP identities and resolve category collisions from cached pages.

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
from typing import Any, Mapping

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    SCHEMA_VERSION, TOOL_VERSION, dump_meta_path, dump_tsv_path,
    next_revision_id, pipe_split, write_dump_meta, write_tsv_dump,
)
from imslp_identity import (  # noqa: E402
    ACTIVE_STATUSES, EVIDENCE_COLUMN, REJECTED_STATUSES, load_cached_pages, resolve_matches,
)
from remap_force import rollup_composers  # noqa: E402

log = logging.getLogger("remap_imslp_matches")


def remap_matches(
    composers: pd.DataFrame, works: pd.DataFrame, out_id: str,
    pages: Mapping[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Resolve identities, drop rejected composers' works, then refresh rollups."""
    out, decisions = resolve_matches(composers, pages)
    rejected_ids = set(out.loc[out["imslp_match_status"].isin(REJECTED_STATUSES), "composer_id"])
    dropped_mask = works["composer_id"].isin(rejected_ids)
    kept_works = works.loc[~dropped_mask].copy()
    dropped_counts = works.loc[dropped_mask].groupby("composer_id").size().to_dict()
    pd_ids = set(composers.loc[composers["eu_pd_status"].eq("pd"), "composer_id"])
    for decision in decisions:
        decision["works_dropped"] = int(dropped_counts.get(decision["qid"], 0))
    out = rollup_composers(out, kept_works)
    if "dump_date" in out.columns:
        out["dump_date"] = out_id
    if "dump_date" in kept_works.columns:
        kept_works["dump_date"] = out_id

    collisions = {}
    for decision in decisions:
        if decision["collision"]:
            collisions[decision["category"]] = decision["collision"]
    transitions = Counter(
        f"{old} → {new}"
        for old, new in zip(composers["imslp_match_status"], out["imslp_match_status"])
        if old != new
    )
    evidence = Counter(token for value in out[EVIDENCE_COLUMN] for token in pipe_split(value))
    report = {
        "status_transitions": dict(sorted(transitions.items())),
        "status_counts": dict(sorted(Counter(out["imslp_match_status"]).items())),
        "rejected_heuristic": [d for d in decisions if d["new_status"] == "rejected_heuristic"],
        "rejected_p839": [d for d in decisions if d["new_status"] == "rejected_p839"],
        "weak_conflicts_kept": [d for d in decisions if d["dates"] == "conflict_weak" and d["new_status"] in ACTIVE_STATUSES],
        "collisions": list(collisions.values()),
        "p839_dates_conflicts": [d for d in decisions if d["old_status"] == "matched" and d["method"] == "wikidata_p839" and d["dates"] in {"conflict_weak", "conflict_strong"}],
        "unverified_heuristic": [d for d in decisions if d["new_status"] == "unverified_heuristic"],
        "works_dropped": int(dropped_mask.sum()),
        "pd_works_dropped": int((dropped_mask & works["composer_id"].isin(pd_ids)).sum()),
        "evidence_counts": dict(sorted(evidence.items())),
    }
    return out, kept_works, report


def _years(birth: Any, death: Any) -> str:
    return f"{birth if birth is not None else '?'}–{death if death is not None else '?'}"


def _row_report(row: dict[str, Any]) -> str:
    dump = _years(row["birth_year"], row["death_year"])
    page = _years(row["imslp_born_year"], row["imslp_died_year"])
    return f"{row['name']} ({row['qid']}): dump {dump}; IMSLP {page}; {row['category']}"


def print_report(report: dict[str, Any], src: str, out_id: str) -> None:
    print(f"IMSLP identity remap {src} → {out_id} (offline)")
    print("Status transitions:")
    for transition, count in report["status_transitions"].items():
        print(f"  {transition}: {count}")
    if not report["status_transitions"]:
        print("  none")
    print(f"Rejected heuristic rows: {len(report['rejected_heuristic'])}")
    for row in report["rejected_heuristic"]:
        print(f"  {_row_report(row)}; works dropped {row['works_dropped']}; reason: {row['reason']}")
    print(f"Rejected P839 rows: {len(report['rejected_p839'])}")
    for row in report["rejected_p839"]:
        print(f"  {_row_report(row)}; works dropped {row['works_dropped']}; reason: {row['reason']}; imslp_p839_wrong")
    print(f"Weak conflicts kept (flagged imslp_dates_conflict): {len(report['weak_conflicts_kept'])}")
    for row in report["weak_conflicts_kept"]:
        print(f"  {_row_report(row)}; wikilink_{row['wikilink']}; status: {row['new_status']}; works kept")
    print(f"Collisions: {len(report['collisions'])}")
    for collision in report["collisions"]:
        winner = f"{collision['winner_name']} ({collision['winner']})" if collision["winner"] else "none"
        claimants = "; ".join(f"{d['name']} ({d['qid']}): dates_{d['dates']}, wikilink_{d['wikilink']}" for d in collision["claimants"])
        print(f"  {collision['category']}: {claimants}; winner: {winner}; reason: {collision['reason']}")
    print(f"P839 date conflicts: {len(report['p839_dates_conflicts'])}")
    for row in report["p839_dates_conflicts"]:
        flag = "imslp_p839_wrong" if row["new_status"] == "rejected_p839" else "imslp_dates_conflict"
        print(f"  {_row_report(row)}; dates_{row['dates']}; status: {row['new_status']}; {flag}")
    print(f"Heuristics left unverified: {len(report['unverified_heuristic'])}")
    for row in report["unverified_heuristic"]:
        print(f"  {_row_report(row)}; reason: {row['reason']}")
    print(f"Works dropped: {report['works_dropped']} (EU PD composers: {report['pd_works_dropped']})")
    print("Evidence counts:")
    for token, count in report["evidence_counts"].items():
        print(f"  {token}: {count}")


def run(args: argparse.Namespace) -> None:
    src, out_id = args.from_dump, args.to or next_revision_id()
    composers = pd.read_csv(dump_tsv_path("composers", src), sep="\t", dtype=str, keep_default_na=False)
    works = pd.read_csv(dump_tsv_path("works", src), sep="\t", dtype=str, keep_default_na=False)
    log.info("Loaded %d composers, %d works from %s", len(composers), len(works), src)
    pages = load_cached_pages(composers)
    composers, works, report = remap_matches(composers, works, out_id, pages)
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
        "derived_from_dump_id": src, "enrichment": "remap_imslp_identity",
        "output_files": {"composers": c_path.name, "works": w_path.name},
        "row_counts": {"composers": len(composers), "works": len(works)},
        **report,
        "notes": [
            "Offline verification against cached IMSLP category wikitext (redirects already followed)",
            "Known birth/death year pairs agree within one year",
            "Strong conflicts have no agreeing year pair and a largest difference over ten years; other conflicts are weak",
            "Wikipedia links provide evidence and collision tie-breaks, never rejection by themselves",
            "Weak conflicts retain works with imslp_dates_conflict; agreeing Wikipedia links promote weak heuristics",
            "P839 strong conflicts become rejected_p839 with imslp_p839_wrong; only other P839 holders win collisions",
            "Performer templates are recorded as evidence; identity uses the same date rules",
            "Both rejection statuses keep categories for audit, clear URLs and lose works",
            "Composer rollups recomputed; composers stamped; works dump_date stamped only when present; other fields preserved",
        ],
    })
    log.info("Wrote %s → %s, %s", out_id, c_path.name, w_path.name)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-dump", required=True)
    parser.add_argument("--to", help="Output revision id (default: next rNNN)")
    parser.add_argument("--dry-run", action="store_true")
    run(parser.parse_args())


if __name__ == "__main__":
    main()
