#!/usr/bin/env python3
"""Rights-basis report: IMSLP's own rights evidence vs the composer life+70 rule.

Offline. Assigns each work one rights basis from `imslp_copyright_flags` (first
match wins) and crosses it with the composer's death-year band, then explains
the not-yet-PD works that carry no rights category by their file hosts
(`imslp_file_hosts`). Writes Markdown (optional --json). IMSLP's categories are
its own hosting judgements, not legal determinations.

  python scripts/rights_basis.py --dump r016 --out paper/notes/rights_basis_r016.md
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import dump_meta_path, dump_tsv_path, pipe_split  # noqa: E402

# First match wins; nonpd_us alone is a US warning, not a hosting basis.
BASIS_RULES: tuple[tuple[str, str, frozenset[str]], ...] = (
    ("eu_warning", "EU warning (PD in Canada)", frozenset({"nonpd_eu"})),
    ("us_only", "PD in the US only", frozenset({"pd_us_only"})),
    ("licensed", "Not PD anywhere; permission, CC or PRO licence",
     frozenset({"nonpd_licensed", "permission_granted", "pro_licensed"})),
    ("wima", "WIMA files", frozenset({"wima"})),
    ("us_routes", "US-only PD routes (not renewed, no notice)",
     frozenset({"pd_us_notrenewed", "pd_us_no_notice"})),
)
NO_BASIS = ("none", "No rights category")
BASIS_LABELS = dict([(key, label) for key, label, _ in BASIS_RULES] + [NO_BASIS])

# Canada's life+70 (30 Dec 2022) was not retroactive: deaths up to 1971 stay PD there.
CANADA_LAST_PD_DEATH = 1971


def rights_basis(flags: str) -> str:
    tokens = set(pipe_split(flags))
    for key, _, match in BASIS_RULES:
        if tokens & match:
            return key
    return NO_BASIS[0]


def death_band(death_year: str, reference_year: int) -> str:
    """Death-year band: PD now under life+70, PD in Canada only, or later."""
    if not str(death_year).strip().isdigit():
        return "unknown"
    year = int(death_year)
    last_pd = reference_year - 71
    if year <= last_pd:
        return f"≤ {last_pd}"
    if year <= CANADA_LAST_PD_DEATH:
        return f"{last_pd + 1}–{CANADA_LAST_PD_DEATH}"
    if year <= 1999:
        return f"{CANADA_LAST_PD_DEATH + 1}–1999"
    return "2000+"


def band_order(reference_year: int) -> list[str]:
    return [death_band(str(y), reference_year) for y in (reference_year - 71, CANADA_LAST_PD_DEATH, 1999, 2000)] + [
        "unknown"
    ]


def host_group(has_files: str, hosts: str) -> str:
    """Off-main-server hosts win; `ca` only means the main server alone."""
    found = set(pipe_split(hosts))
    if not found:
        return "no files" if has_files == "false" else "unknown"
    if "asia" in found:
        return "asia (life+50 server)"
    if "us" in found:
        return "us (US-only server)"
    return "ca (main server only)"


def analyse(composers: pd.DataFrame, works: pd.DataFrame, reference_year: int) -> dict[str, Any]:
    death = dict(zip(composers["composer_id"], composers["death_year"]))
    frame = works.assign(
        basis=works["imslp_copyright_flags"].map(rights_basis),
        band=works["composer_id"].map(lambda cid: death_band(death.get(cid, ""), reference_year)),
        hosts=[host_group(hf, h) for hf, h in zip(works.get("has_files", ""), works.get("imslp_file_hosts", ""))],
    )
    bands = band_order(reference_year)
    basis_keys = [key for key, _, _ in BASIS_RULES] + [NO_BASIS[0]]
    by_band = {b: Counter() for b in bands}
    for basis, band in zip(frame["basis"], frame["band"]):
        by_band[band][basis] += 1

    pd_now = bands[0]
    canada = bands[1]
    eu_warning = {b: (by_band[b]["eu_warning"], sum(by_band[b].values())) for b in bands}

    unflagged = frame[(frame["basis"] == "none") & ~frame["band"].isin([pd_now, "unknown"])]
    unflagged_groups = Counter(zip(unflagged["band"], unflagged["hosts"]))

    host_deaths: dict[str, Counter] = {}
    for hosts, band in zip(frame["hosts"], frame["band"]):
        host_deaths.setdefault(hosts, Counter())[band] += 1

    return {
        "reference_year": reference_year,
        "works": len(frame),
        "bands": bands,
        "basis_keys": basis_keys,
        "basis_by_band": {b: {k: by_band[b][k] for k in basis_keys} for b in bands},
        "eu_warning_share": {b: {"warned": w, "works": n} for b, (w, n) in eu_warning.items()},
        "pd_now_band": pd_now,
        "canada_band": canada,
        "unflagged_not_pd": len(unflagged),
        "unflagged_by_band_host": {f"{b} · {h}": n for (b, h), n in sorted(
            unflagged_groups.items(), key=lambda kv: (bands.index(kv[0][0]), kv[0][1]))},
        "hosts_by_band": {h: {b: c[b] for b in bands} for h, c in sorted(host_deaths.items())},
    }


def _pct(part: int, whole: int) -> str:
    return f"{100 * part / whole:.1f}%" if whole else "—"


def to_markdown(report: dict[str, Any], dump_id: str) -> str:
    bands = report["bands"]
    lines = [
        f"# Rights basis by death year ({dump_id}, reference year {report['reference_year']})",
        "",
        f"{report['works']:,} works. Basis from IMSLP's own rights categories, first match wins; "
        "IMSLP's categories are hosting judgements, not legal advice.",
        "",
        "| Basis | " + " | ".join(f"died {b}" for b in bands) + " |",
        "|---|" + "---:|" * len(bands),
    ]
    for key in report["basis_keys"]:
        row = [f"{report['basis_by_band'][b][key]:,}" for b in bands]
        lines.append(f"| {BASIS_LABELS[key]} | " + " | ".join(row) + " |")
    totals = [f"{sum(report['basis_by_band'][b].values()):,}" for b in bands]
    lines += ["| **All works** | " + " | ".join(totals) + " |", ""]

    lines += ["## IMSLP's EU warning by band", "", "| Death band | Works | With EU warning | Share |",
              "|---|---:|---:|---:|"]
    for b in bands:
        share = report["eu_warning_share"][b]
        lines.append(f"| {b} | {share['works']:,} | {share['warned']:,} | {_pct(share['warned'], share['works'])} |")

    lines += ["", f"## Not-yet-PD works with no rights category ({report['unflagged_not_pd']:,}), by file host", "",
              "| Death band · host | Works |", "|---|---:|"]
    for key, n in report["unflagged_by_band_host"].items():
        lines.append(f"| {key} | {n:,} |")

    lines += ["", "## File hosts by death band (all works)", "",
              "| Host | " + " | ".join(f"died {b}" for b in bands) + " |", "|---|" + "---:|" * len(bands)]
    for host, counts in report["hosts_by_band"].items():
        lines.append(f"| {host} | " + " | ".join(f"{counts[b]:,}" for b in bands) + " |")
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace) -> None:
    read = dict(sep="\t", dtype=str, keep_default_na=False)
    composers = pd.read_csv(dump_tsv_path("composers", args.dump), **read)
    works = pd.read_csv(dump_tsv_path("works", args.dump), **read)
    meta_path = dump_meta_path(args.dump)
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    reference_year = int(args.reference_year or meta.get("pd_reference_year") or 2026)
    report = analyse(composers, works, reference_year)
    markdown = to_markdown(report, args.dump)
    if args.out:
        Path(args.out).write_text(markdown, encoding="utf-8")
    else:
        print(markdown, end="")
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dump", required=True)
    parser.add_argument("--reference-year", type=int, help="Default: the dump's pd_reference_year")
    parser.add_argument("--out", help="Markdown path (default: stdout)")
    parser.add_argument("--json", help="Optional JSON report path")
    run(parser.parse_args())


if __name__ == "__main__":
    main()
