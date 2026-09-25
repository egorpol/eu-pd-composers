#!/usr/bin/env python3
"""Build residual LLM queues for force_family + composer style (no Codex calls).

Writes data/cache/llm_residual_queue.json for a later gpt-6-luna xhigh pass.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import CACHE_DIR, dump_tsv_path  # noqa: E402
from force_family import FORCE_FAMILIES  # noqa: E402

ALLOWED_STYLES = (
    "impressionism",
    "expressionism",
    "neoclassicism",
    "serialism",
    "minimalism",
    "postminimalism",
    "spectralism",
    "electroacoustic",
    "avant_garde",
    "national_folk",
    "late_romantic",
    "atonal_modernism",
    "polystylism",
)


def _as_str(value) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    if text.lower() in {"nan", "none"}:
        return ""
    return text


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dump", required=True, help="dump_id after geninfo + style remap")
    p.add_argument("--works-limit", type=int, default=50)
    p.add_argument("--composers-limit", type=int, default=50)
    args = p.parse_args()

    composers = pd.read_csv(dump_tsv_path("composers", args.dump), sep="\t", low_memory=False)
    works = pd.read_csv(dump_tsv_path("works", args.dump), sep="\t", low_memory=False)

    # Works still other/unclassified after geninfo
    w_todo = works[
        works["force_family"].map(_as_str).isin({"other", "unclassified"})
    ].copy()
    # Prefer rows that already have geninfo context
    if "instrumentation_raw" in w_todo.columns:
        w_todo["_has_gi"] = (w_todo["instrumentation_raw"].map(_as_str) != "").astype(int)
    else:
        w_todo["_has_gi"] = 0
    w_todo = w_todo.sort_values(["_has_gi", "work_id"], ascending=[False, True])

    work_items = []
    for r in w_todo.head(args.works_limit).itertuples():
        work_items.append(
            {
                "id": str(r.work_id),
                "title": _as_str(getattr(r, "title", "")),
                "url": _as_str(getattr(r, "imslp_work_url", "")),
                "instrumentation_raw": _as_str(getattr(r, "instrumentation_raw", "")),
                "piece_style_raw": _as_str(getattr(r, "piece_style_raw", "")),
                "current_force": _as_str(getattr(r, "force_family", "")),
                "current_src": _as_str(getattr(r, "force_family_src", "")),
            }
        )

    # Composers: classical_core + empty styles, pageviews desc
    c_todo = composers[
        (composers["scope_class"].fillna("") == "classical_core")
        & (composers["style_tags"].map(_as_str) == "")
    ].copy()
    c_todo["pageviews_enwiki"] = pd.to_numeric(
        c_todo.get("pageviews_enwiki"), errors="coerce"
    ).fillna(0)
    c_todo = c_todo.sort_values("pageviews_enwiki", ascending=False)

    # Top IMSLP titles per composer (for prompt context)
    titles_by_c: dict[str, list[str]] = {}
    for r in works.itertuples():
        cid = str(r.composer_id)
        titles_by_c.setdefault(cid, [])
        if len(titles_by_c[cid]) < 5:
            titles_by_c[cid].append(_as_str(getattr(r, "title", "")))

    composer_items = []
    for r in c_todo.head(args.composers_limit).itertuples():
        cid = str(r.composer_id)
        composer_items.append(
            {
                "id": cid,
                "name": _as_str(getattr(r, "name_display", "")),
                "birth_year": getattr(r, "birth_year", None)
                if pd.notna(getattr(r, "birth_year", None))
                else None,
                "death_year": getattr(r, "death_year", None)
                if pd.notna(getattr(r, "death_year", None))
                else None,
                "occupations": _as_str(getattr(r, "occupations", "")),
                "notable_works": _as_str(getattr(r, "notable_works", ""))[:240],
                "pageviews": int(r.pageviews_enwiki),
                "top_imslp_titles": titles_by_c.get(cid, []),
            }
        )

    out = {
        "dump_id": args.dump,
        "model_plan": {
            "cli": "codex",
            "model": "gpt-6-luna",
            "model_reasoning_effort": "xhigh",
            "do_not_run_until_approved": True,
        },
        "allowed_force_families": list(FORCE_FAMILIES),
        "allowed_style_tags": list(ALLOWED_STYLES),
        "works_residual_total": int(len(w_todo)),
        "composers_empty_style_total": int(len(c_todo)),
        "works_pilot": work_items,
        "composers_pilot": composer_items,
        "provenance_rules": [
            "force_family_src=llm_luna_xhigh; never overwrite imslp_tags or imslp_geninfo",
            "style_tags_src=llm_luna_xhigh; never overwrite wikidata style_tags",
            "Put geninfo / wiki facts in the prompt; do not rely on live web search",
        ],
    }
    dest = CACHE_DIR / "llm_residual_queue.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {dest}")
    print(
        f"works residual={len(w_todo)} (pilot {len(work_items)}); "
        f"composers empty style={len(c_todo)} (pilot {len(composer_items)})"
    )


if __name__ == "__main__":
    main()
