#!/usr/bin/env python3
"""Inventory who needs style-tag rechecking (no LLM calls).

Writes data/cache/style_review_queue.json with:
  - unmapped Wikidata P135/P136 QIDs seen in the entity cache
  - classical_core composers with empty style_tags (priority = pageviews)

Use this to decide LLM batch size before running a style pass.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import CACHE_DIR, DATA_DIR  # noqa: E402
from wikidata_enrich import STYLE_QID_TO_TAG  # noqa: E402


def main() -> None:
    dump = sys.argv[1] if len(sys.argv) > 1 else "2026-09-30"
    composers = pd.read_csv(DATA_DIR / f"composers_{dump}.tsv", sep="\t", low_memory=False)

    # Unmapped QIDs from cached Wikidata entities
    unmapped: Counter = Counter()
    mapped_hits = Counter()
    cache = CACHE_DIR / "wikidata_entity"
    if cache.exists():
        for path in cache.glob("*.json"):
            try:
                ent = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            claims = ent.get("claims") or {}
            for pid in ("P135", "P136"):
                for claim in claims.get(pid, []):
                    snak = claim.get("mainsnak") or {}
                    if snak.get("snaktype") != "value":
                        continue
                    val = (snak.get("datavalue") or {}).get("value") or {}
                    qid = val.get("id") if isinstance(val, dict) else None
                    if not qid:
                        continue
                    if qid in STYLE_QID_TO_TAG:
                        mapped_hits[STYLE_QID_TO_TAG[qid]] += 1
                    else:
                        unmapped[qid] += 1

    empty = composers[
        (composers["style_tags"].fillna("").astype(str).str.strip() == "")
        & (composers["scope_class"].fillna("") == "classical_core")
    ].copy()
    empty["pageviews_enwiki"] = pd.to_numeric(empty["pageviews_enwiki"], errors="coerce").fillna(0)
    empty = empty.sort_values("pageviews_enwiki", ascending=False)

    queue = [
        {
            "composer_id": str(r.composer_id),
            "name_display": r.name_display,
            "birth_year": r.birth_year if pd.notna(r.birth_year) else None,
            "death_year": r.death_year if pd.notna(r.death_year) else None,
            "occupations": r.occupations if pd.notna(r.occupations) else "",
            "notable_works": (str(r.notable_works)[:200] if pd.notna(r.notable_works) else ""),
            "pageviews": int(r.pageviews_enwiki),
        }
        for r in empty.head(2000).itertuples()
    ]

    out = {
        "dump_id": dump,
        "composers_total": int(len(composers)),
        "classical_core_empty_styles": int(len(empty)),
        "mapped_tag_hits_in_cache": dict(mapped_hits),
        "unmapped_p135_p136_qids_top": unmapped.most_common(40),
        "llm_queue_top": queue[:500],
        "recommendation": [
            "1) Expand STYLE_QID_TO_TAG for frequent unmapped QIDs (free, deterministic).",
            "2) Drop/narrow over-broad QIDs (e.g. Q9734 atonality → atonal_modernism).",
            "3) LLM only classical_core with empty styles after step 1, ordered by pageviews.",
            "4) LLM inputs: name, years, occupations, notable_works, optional 5 IMSLP titles — not full works.",
            "5) force_family_src-style provenance: style_tags_src=llm; never overwrite wikidata tags.",
        ],
    }
    dest = CACHE_DIR / "style_review_queue.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {dest}")
    print(f"classical_core with empty styles: {len(empty)}")
    print(f"top unmapped QIDs: {unmapped.most_common(10)}")


if __name__ == "__main__":
    main()
