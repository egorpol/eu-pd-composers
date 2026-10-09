#!/usr/bin/env python3
"""Compare dump revisions by column name and write a Markdown PR report."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd

import common


def read_dump(dump_id: str, directory: Path | None = None) -> dict[str, pd.DataFrame]:
    directory = directory if directory is not None else common.DATA_DIR
    return {stem: pd.read_csv(directory / f"{stem}_{dump_id}.tsv", sep="\t", dtype=str, keep_default_na=False)
            for stem in ("composers", "works")}


def ignored_column(column: str) -> bool:
    return column in {"dump_date", "fetched_at"} or column.startswith("pageviews_")


def keyed_rows(frame: pd.DataFrame, keys: list[str]) -> dict[tuple[str, ...], dict[str, str]]:
    if frame.duplicated(subset=keys).any():
        raise ValueError(f"Duplicate dump keys: {keys}")
    return {tuple(row[k] for k in keys): row for row in frame.to_dict("records")}


def transition_counts(
    prev: dict, nxt: dict, column: str,
) -> dict[str, int]:
    counts = Counter(f"{prev[key].get(column, '') or '(empty)'} → {nxt[key].get(column, '') or '(empty)'}"
                     for key in prev.keys() & nxt.keys()
                     if prev[key].get(column, "") != nxt[key].get(column, ""))
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _text(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _label(row: dict[str, str]) -> str:
    return f"{_text(row.get('name_display', ''))} (`{_text(row['composer_id'])}`)"


def compare_dumps(
    previous: dict[str, pd.DataFrame], following: dict[str, pd.DataFrame],
    prev_id: str = "previous", next_id: str = "next",
) -> tuple[str, dict[str, Any]]:
    schema = []
    changed = False
    indexed: dict[str, tuple[dict, dict]] = {}
    cell_changes: dict[str, dict[str, int]] = {}
    for stem in ("composers", "works"):
        prev_df, next_df = previous[stem], following[stem]
        added = [col for col in next_df if col not in prev_df]
        removed = [col for col in prev_df if col not in next_df]
        if added or removed:
            schema.append({"file": stem, "added": added, "removed": removed})
        changed |= any(not ignored_column(col) for col in added + removed)
        keys = ["composer_id"]
        if stem == "works":
            keys.append("imslp_pageid" if "imslp_pageid" in prev_df and "imslp_pageid" in next_df else "work_id")
        prev, nxt = keyed_rows(prev_df, keys), keyed_rows(next_df, keys)
        indexed[stem] = prev, nxt
        changed |= prev.keys() != nxt.keys()
        counts = {}
        for col in prev_df:
            if col not in next_df or ignored_column(col):
                continue
            count = sum(prev[key][col] != nxt[key][col] for key in prev.keys() & nxt.keys())
            if count:
                counts[col] = count
                changed = True
        cell_changes[stem] = counts

    prev_c, next_c = indexed["composers"]
    prev_w, next_w = indexed["works"]
    common_c = sorted(prev_c.keys() & next_c.keys())
    pd_flips = [key for key in common_c if prev_c[key].get("eu_pd_status", "") != next_c[key].get("eu_pd_status", "")]
    summary = {
        "has_data_changes": bool(changed),
        "composers": {"prev": len(previous["composers"]), "next": len(following["composers"])},
        "works": {"prev": len(previous["works"]), "next": len(following["works"])},
        "pd_flips": len(pd_flips), "schema_changes": schema,
    }
    lines = [f"# Dump diff: {prev_id} → {next_id}", "",
             f"Data changes: **{'yes' if changed else 'no'}** (snapshot dates, fetch timestamps, pageviews and metadata ignored).", "",
             f"Composers: {summary['composers']['prev']} → {summary['composers']['next']}. "
             f"Works: {summary['works']['prev']} → {summary['works']['next']}.", "", "## Schema", ""]
    for entry in schema:
        lines.append(f"- {entry['file']}: added {', '.join(entry['added']) or 'none'}; removed {', '.join(entry['removed']) or 'none'}.")
    if not schema:
        lines.append("No column additions or removals.")

    for title, keys, rows in (
        ("Composers added", next_c.keys() - prev_c.keys(), next_c),
        ("Composers removed", prev_c.keys() - next_c.keys(), prev_c),
    ):
        lines.extend(["", f"## {title}: {len(keys)}", ""])
        lines.extend(f"- {_label(rows[key])}" for key in sorted(keys))
        if not keys:
            lines.append("None.")
    lines.extend(["", f"## EU PD status flips: {len(pd_flips)}", ""])
    for key in pd_flips:
        old, new = prev_c[key], next_c[key]
        lines.append(f"- {_label(new)}: {old.get('eu_pd_status', '') or '(empty)'} → {new.get('eu_pd_status', '') or '(empty)'}; "
                     f"birth {old.get('birth_year', '') or '?'} → {new.get('birth_year', '') or '?'}; "
                     f"death {old.get('death_year', '') or '?'} → {new.get('death_year', '') or '?'}; "
                     f"EU PD year {old.get('eu_pd_year', '') or '?'} → {new.get('eu_pd_year', '') or '?'}.")
    if not pd_flips:
        lines.append("None.")
    lines.extend(["", "## Birth/death year changes", ""])
    year_changes = 0
    for key in common_c:
        parts = [f"{col}: {prev_c[key].get(col, '') or '?'} → {next_c[key].get(col, '') or '?'}"
                 for col in ("birth_year", "death_year") if prev_c[key].get(col, "") != next_c[key].get(col, "")]
        if parts:
            lines.append(f"- {_label(next_c[key])}: {'; '.join(parts)}")
            year_changes += 1
    if not year_changes:
        lines.append("None.")

    for title, prev, nxt, col in (
        ("IMSLP match status transitions", prev_c, next_c, "imslp_match_status"),
        ("Force family transitions", prev_w, next_w, "force_family"),
        ("Force source transitions", prev_w, next_w, "force_family_src"),
    ):
        lines.extend(["", f"## {title}", ""])
        transitions = transition_counts(prev, nxt, col)
        lines.extend(f"- {_text(transition)}: {count}" for transition, count in transitions.items())
        if not transitions:
            lines.append("None.")

    lines.extend(["", "## Works added/removed", ""])
    for label, keys, rows in (("Added", next_w.keys() - prev_w.keys(), next_w),
                              ("Removed", prev_w.keys() - next_w.keys(), prev_w)):
        lines.append(f"{label}: **{len(keys)}**; top 20 composers:")
        counts = Counter(rows[key]["composer_id"] for key in keys)
        lines.append("")
        for cid, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:20]:
            composer = next_c.get((cid,)) or prev_c.get((cid,)) or {"composer_id": cid}
            lines.append(f"- {_label(composer)}: {count}")
        if not counts:
            lines.append("None.")
        lines.append("")
    lines.extend(["## LLM share", ""])
    for label, rows in ((prev_id, prev_w), (next_id, next_w)):
        count = sum(row.get("force_family_src", "").startswith("llm") for row in rows.values())
        lines.append(f"- {label} force labels: {count}/{len(rows)} ({count / max(len(rows), 1):.2%}).")
    for label, rows in ((prev_id, prev_c), (next_id, next_c)):
        count = sum(row.get("style_tags_src", "").startswith("llm") for row in rows.values())
        lines.append(f"- {label} composer styles: {count}/{len(rows)} ({count / max(len(rows), 1):.2%}).")
    added_flags: Counter[str] = Counter()
    removed_flags: Counter[str] = Counter()
    for key in prev_c.keys() | next_c.keys():
        old_flags = set(common.pipe_split(prev_c.get(key, {}).get("qa_flags", "")))
        new_flags = set(common.pipe_split(next_c.get(key, {}).get("qa_flags", "")))
        added_flags.update(new_flags - old_flags)
        removed_flags.update(old_flags - new_flags)
    lines.extend(["", "## QA flags added/removed", ""])
    for flag in sorted(added_flags.keys() | removed_flags.keys()):
        lines.append(f"- {flag}: +{added_flags[flag]}, −{removed_flags[flag]}")
    if not added_flags and not removed_flags:
        lines.append("None.")
    lines.extend(["", "## Changed cells on retained rows", ""])
    for stem, counts in cell_changes.items():
        lines.append(f"- {stem}: " + (", ".join(f"{col}={count}" for col, count in counts.items()) or "none"))
    return "\n".join(lines) + "\n", summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("previous")
    parser.add_argument("following")
    parser.add_argument("--prev-dir", type=Path)
    parser.add_argument("--next-dir", type=Path, help="Directory containing the staged next dump")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--summary-json", type=Path)
    args = parser.parse_args()
    report, summary = compare_dumps(read_dump(args.previous, args.prev_dir), read_dump(args.following, args.next_dir),
                                    args.previous, args.following)
    if args.out:
        args.out.write_text(report, encoding="utf-8")
    else:
        print(report, end="")
    if args.summary_json:
        args.summary_json.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
