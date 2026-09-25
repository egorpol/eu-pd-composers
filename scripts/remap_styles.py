#!/usr/bin/env python3
"""Re-apply STYLE_QID_TO_TAG from cached Wikidata entities onto a dump.

Only fills empty style_tags (or --overwrite-wikidata-empty). Sets
style_tags_src=wikidata when tags come from the map. Writes a new revision.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    CACHE_DIR,
    SCHEMA_VERSION,
    TOOL_VERSION,
    dump_tsv_path,
    next_revision_id,
    pipe_join,
    write_dump_meta,
    write_tsv_dump,
)
from wikidata_enrich import STYLE_QID_TO_TAG  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("remap_styles")


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


def styles_from_entity(ent: dict) -> list[str]:
    claims = ent.get("claims") or {}
    tags: list[str] = []
    for pid in ("P135", "P136"):
        for claim in claims.get(pid, []):
            snak = claim.get("mainsnak") or {}
            if snak.get("snaktype") != "value":
                continue
            val = (snak.get("datavalue") or {}).get("value") or {}
            qid = val.get("id") if isinstance(val, dict) else None
            if not qid:
                continue
            tag = STYLE_QID_TO_TAG.get(qid)
            if tag and tag not in tags:
                tags.append(tag)
    return tags


def run(args: argparse.Namespace) -> None:
    src = args.from_dump
    out_id = args.to or next_revision_id()
    composers = pd.read_csv(dump_tsv_path("composers", src), sep="\t", low_memory=False)
    works = pd.read_csv(dump_tsv_path("works", src), sep="\t", low_memory=False)

    if "style_tags_src" not in composers.columns:
        composers["style_tags_src"] = ""

    cache = CACHE_DIR / "wikidata_entity"
    filled = 0
    already = 0
    no_entity = 0
    still_empty = 0

    for idx, row in composers.iterrows():
        qid = _as_str(row.get("composer_id"))
        if not qid.startswith("Q"):
            # Some rows may use other ids; try wikidata_qid column
            qid = _as_str(row.get("wikidata_qid")) or qid
        existing = _as_str(row.get("style_tags"))
        src_tag = _as_str(row.get("style_tags_src"))
        # Never clobber LLM styles unless --overwrite
        if src_tag.startswith("llm") and not args.overwrite:
            already += 1
            continue
        # Skip non-empty non-wikidata unless refreshing wikidata rows / overwrite
        if existing and src_tag not in {"", "wikidata"} and not args.overwrite:
            already += 1
            continue
        if existing and src_tag == "wikidata" and not (args.overwrite or args.refresh_wikidata):
            already += 1
            continue

        path = cache / f"{qid}.json"
        if not path.exists():
            no_entity += 1
            if not existing:
                still_empty += 1
            continue
        try:
            ent = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            no_entity += 1
            continue
        tags = styles_from_entity(ent)
        if tags:
            composers.at[idx, "style_tags"] = pipe_join(tags)
            composers.at[idx, "style_tags_src"] = "wikidata"
            filled += 1
        else:
            # Drop stale wikidata tags that no longer map (e.g. bad Q1338153→electroacoustic)
            if src_tag == "wikidata" or (args.refresh_wikidata and existing):
                composers.at[idx, "style_tags"] = ""
                composers.at[idx, "style_tags_src"] = ""
                filled += 1  # counted as corrected
            elif not existing:
                still_empty += 1

    composers["dump_date"] = out_id
    composers["schema_version"] = SCHEMA_VERSION

    log.info(
        "filled=%d already_had=%d no_cache=%d still_empty=%d → %s",
        filled,
        already,
        no_entity,
        still_empty,
        out_id,
    )

    if args.dry_run:
        nonempty = composers["style_tags"].map(_as_str) != ""
        print(composers.loc[nonempty, "style_tags"].value_counts().head(20).to_string())
        return

    c_path = write_tsv_dump(composers, "composers", out_id)
    w_path = write_tsv_dump(works, "works", out_id)
    nonempty = int((composers["style_tags"].map(_as_str) != "").sum())
    write_dump_meta(
        out_id,
        {
            "dump_id": out_id,
            "tool_version": TOOL_VERSION,
            "schema_version": SCHEMA_VERSION,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "derived_from_dump_id": src,
            "enrichment": "remap_styles_wikidata",
            "output_files": {"composers": c_path.name, "works": w_path.name},
            "row_counts": {
                "composers": int(len(composers)),
                "works": int(len(works)),
                "style_tags_filled": filled,
                "style_tags_nonempty": nonempty,
                "style_tags_still_empty": still_empty,
            },
            "style_qid_map_size": len(STYLE_QID_TO_TAG),
            "notes": [
                "Re-applied STYLE_QID_TO_TAG from data/cache/wikidata_entity",
                "Does not overwrite existing non-empty style_tags unless --overwrite",
            ],
        },
    )
    log.info("Wrote %s, %s", c_path.name, w_path.name)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--from-dump", required=True)
    p.add_argument("--to", help="Output revision id (default: next rNNN)")
    p.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace all existing style_tags from Wikidata map (including llm)",
    )
    p.add_argument(
        "--refresh-wikidata",
        action="store_true",
        help="Re-apply map to rows with style_tags_src=wikidata (fix bad QIDs; keep llm tags)",
    )
    p.add_argument("--dry-run", action="store_true")
    run(p.parse_args())


if __name__ == "__main__":
    main()
