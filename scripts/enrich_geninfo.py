#!/usr/bin/env python3
"""Scrape IMSLP General Information and remap force_family (geninfo pilot/batch).

Queue: empty imslp_genre_categories OR (force_family=other and src in llm/llm_grok/title).
Writes a new revision dump; never overwrites. Caches under data/cache/imslp_geninfo/.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    CACHE_DIR,
    SCHEMA_VERSION,
    TOOL_VERSION,
    dump_tsv_path,
    make_session,
    next_revision_id,
    pipe_join,
    write_dump_meta,
    write_tsv_dump,
)
from force_family import FORCE_FAMILIES, map_force_family_with_geninfo  # noqa: E402
from imslp import fetch_work_geninfo  # noqa: E402
from remap_styles import styles_from_entity  # noqa: E402
from wikidata_enrich import STYLE_QID_TO_TAG  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("enrich_geninfo")

WEAK_SRC = {"llm", "llm_grok", "title", "title_unmapped", "empty"}
GOLD_WORK_IDS = {
    "A Farewell to Land, S.248 (Ives, Charles)",
}

EXTRA_WORK_COLS = [
    "instrumentation_raw",
    "piece_style_raw",
    "composition_year",
]


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


def select_queue(works: pd.DataFrame) -> pd.DataFrame:
    empty_genre = works["imslp_genre_categories"].map(_as_str) == ""
    weak_other = (works["force_family"].map(_as_str) == "other") & (
        works["force_family_src"].map(_as_str).isin(WEAK_SRC)
    )
    mask = empty_genre | weak_other
    # Always include gold checks when present
    gold = works["work_id"].astype(str).isin(GOLD_WORK_IDS)
    return works[mask | gold].copy()


def rollup_composers(composers: pd.DataFrame, works: pd.DataFrame) -> pd.DataFrame:
    by_composer: dict[str, Counter] = {}
    for cid, fam in zip(works["composer_id"].astype(str), works["force_family"]):
        by_composer.setdefault(str(cid), Counter())[_as_str(fam)] += 1

    present = []
    by_cat_json = []
    totals = []
    for cid in composers["composer_id"].astype(str):
        ctr = by_composer.get(cid, Counter())
        ordered = [f for f in FORCE_FAMILIES if ctr.get(f)]
        present.append(pipe_join(ordered))
        payload = {f: int(ctr[f]) for f in ordered}
        by_cat_json.append(json.dumps(payload, ensure_ascii=False) if payload else "")
        totals.append(int(sum(ctr.values())))

    out = composers.copy()
    out["work_categories_present"] = present
    out["works_count_by_category"] = by_cat_json
    out["works_count_total"] = totals
    if "imslp_works_count" in out.columns:
        out["imslp_works_count"] = totals
    out["schema_version"] = SCHEMA_VERSION
    return out


def apply_style_remap(composers: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Fill empty style_tags from Wikidata entity cache + STYLE_QID_TO_TAG."""
    out = composers.copy()
    if "style_tags_src" not in out.columns:
        out["style_tags_src"] = ""
    cache = CACHE_DIR / "wikidata_entity"
    filled = 0
    still_empty = 0
    for idx, row in out.iterrows():
        existing = _as_str(row.get("style_tags"))
        if existing:
            continue
        qid = _as_str(row.get("composer_id"))
        if not qid.startswith("Q"):
            qid = _as_str(row.get("wikidata_qid")) or qid
        path = cache / f"{qid}.json"
        if not path.exists():
            still_empty += 1
            continue
        try:
            ent = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            still_empty += 1
            continue
        tags = styles_from_entity(ent)
        if tags:
            out.at[idx, "style_tags"] = pipe_join(tags)
            out.at[idx, "style_tags_src"] = "wikidata"
            filled += 1
        else:
            still_empty += 1
    stats = {
        "style_tags_filled": filled,
        "style_tags_still_empty_classical_unchecked": still_empty,
        "style_qid_map_size": len(STYLE_QID_TO_TAG),
    }
    return out, stats


def run(args: argparse.Namespace) -> None:
    src = args.from_dump
    out_id = args.to or next_revision_id()
    composers = pd.read_csv(dump_tsv_path("composers", src), sep="\t", low_memory=False)
    works = pd.read_csv(dump_tsv_path("works", src), sep="\t", low_memory=False)
    for col in EXTRA_WORK_COLS:
        if col not in works.columns:
            works[col] = ""

    queue = select_queue(works)
    # Prefer gold rows first, then stable work_id order
    queue["_gold"] = queue["work_id"].astype(str).isin(GOLD_WORK_IDS).astype(int)
    queue = queue.sort_values(["_gold", "work_id"], ascending=[False, True])
    if args.limit and args.limit > 0:
        queue = queue.head(args.limit)
    log.info(
        "Queue %d / %d works (limit=%s) → dump %s",
        len(queue),
        len(works),
        args.limit,
        out_id,
    )

    session = make_session()
    changed = 0
    fetched_ok = 0
    ives_check = None

    for i, (idx, row) in enumerate(queue.iterrows(), start=1):
        wid = _as_str(row.get("work_id"))
        info = fetch_work_geninfo(wid, session, use_cache=not args.no_cache)
        if info.get("ok"):
            fetched_ok += 1
        instr = _as_str(info.get("instrumentation_raw"))
        style = _as_str(info.get("piece_style_raw"))
        year = _as_str(info.get("composition_year"))

        works.at[idx, "instrumentation_raw"] = instr
        works.at[idx, "piece_style_raw"] = style
        works.at[idx, "composition_year"] = year

        old_fam = _as_str(row.get("force_family"))
        old_src = _as_str(row.get("force_family_src"))
        new_fam, new_src = map_force_family_with_geninfo(
            _as_str(row.get("imslp_genre_categories")),
            _as_str(row.get("title")),
            instr,
            current_family=old_fam,
            current_src=old_src,
        )
        if new_src == "imslp_geninfo" and (new_fam != old_fam or old_src != new_src):
            works.at[idx, "force_family"] = new_fam
            works.at[idx, "force_family_src"] = new_src
            changed += 1

        if wid in GOLD_WORK_IDS:
            ives_check = {
                "work_id": wid,
                "instrumentation_raw": instr,
                "force_family": works.at[idx, "force_family"],
                "force_family_src": works.at[idx, "force_family_src"],
            }
            log.info("Gold check Ives: %s", ives_check)

        if i % 25 == 0 or i == len(queue):
            log.info("Progress %d/%d fetched_ok=%d changed=%d", i, len(queue), fetched_ok, changed)

    composers = rollup_composers(composers, works)
    style_stats: dict = {}
    if args.remap_styles:
        composers, style_stats = apply_style_remap(composers)
        log.info("Style remap: %s", style_stats)
    composers["dump_date"] = out_id

    if args.dry_run:
        log.info("Dry run — not writing (changed=%d)", changed)
        print(works["force_family_src"].value_counts().head(20).to_string())
        if ives_check:
            print("IVES", json.dumps(ives_check, ensure_ascii=False))
        return

    c_path = write_tsv_dump(composers, "composers", out_id)
    w_path = write_tsv_dump(works, "works", out_id)
    fam_counts = Counter(works["force_family"].map(_as_str).tolist())
    src_counts = Counter(works["force_family_src"].map(_as_str).tolist())
    meta = {
        "dump_id": out_id,
        "tool_version": TOOL_VERSION,
        "schema_version": SCHEMA_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "derived_from_dump_id": src,
        "enrichment": "imslp_geninfo"
        + ("+remap_styles" if args.remap_styles else ""),
        "output_files": {"composers": c_path.name, "works": w_path.name},
        "row_counts": {
            "composers": int(len(composers)),
            "works": int(len(works)),
            "geninfo_queue": int(len(queue)),
            "geninfo_fetched_ok": fetched_ok,
            "force_family_changed": changed,
            "force_family": dict(fam_counts),
            "force_family_src": dict(src_counts),
            **style_stats,
        },
        "gold_checks": {"ives_farewell_to_land": ives_check},
        "notes": [
            "IMSLP General Information Instrumentation → force_family_src=imslp_geninfo",
            "Never overwrites imslp_tags classifications",
            f"Pilot/batch limit={args.limit}",
            *(
                ["Re-applied expanded STYLE_QID_TO_TAG onto empty style_tags"]
                if args.remap_styles
                else []
            ),
        ],
    }
    write_dump_meta(out_id, meta)
    log.info("Wrote %s, %s (+ meta)", c_path.name, w_path.name)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--from-dump", required=True)
    p.add_argument("--to", help="Output revision id (default: next rNNN)")
    p.add_argument("--limit", type=int, default=200, help="Max works to scrape (0=all queue)")
    p.add_argument(
        "--remap-styles",
        action="store_true",
        help="Also fill empty composer style_tags from Wikidata cache map",
    )
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    run(p.parse_args())


if __name__ == "__main__":
    main()
