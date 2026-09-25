#!/usr/bin/env python3
"""Copy a calendar (or any) dump to the next / explicit revision id (rNNN).

Never overwrites. Historical dated files stay in place.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    DATA_DIR,
    SCHEMA_VERSION,
    TOOL_VERSION,
    dump_meta_path,
    dump_tsv_path,
    next_revision_id,
    write_dump_meta,
)


def promote(src: str, dest: str | None, notes: list[str]) -> str:
    out_id = dest or next_revision_id()
    c_src = dump_tsv_path("composers", src)
    w_src = dump_tsv_path("works", src)
    m_src = dump_meta_path(src)
    if not c_src.exists() or not w_src.exists():
        raise FileNotFoundError(f"Need {c_src.name} and {w_src.name}")

    c_out = dump_tsv_path("composers", out_id)
    w_out = dump_tsv_path("works", out_id)
    for p in (c_out, w_out, dump_meta_path(out_id)):
        if p.exists():
            raise FileExistsError(p)

    shutil.copy2(c_src, c_out)
    shutil.copy2(w_src, w_out)

    prev_meta: dict = {}
    if m_src.exists():
        prev_meta = json.loads(m_src.read_text(encoding="utf-8"))

    meta = {
        "dump_id": out_id,
        "tool_version": TOOL_VERSION,
        "schema_version": SCHEMA_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "derived_from_dump_id": src,
        "enrichment": "promote_revision",
        "output_files": {"composers": c_out.name, "works": w_out.name},
        "row_counts": prev_meta.get("row_counts")
        or {
            "composers": sum(1 for _ in c_out.open(encoding="utf-8")) - 1,
            "works": sum(1 for _ in w_out.open(encoding="utf-8")) - 1,
        },
        "notes": notes
        or [
            f"Canonical revision copy of {src}; calendar/history files retained.",
        ],
    }
    if "excluded" in prev_meta:
        meta["excluded"] = prev_meta["excluded"]
    write_dump_meta(out_id, meta)
    print(f"Promoted {src} → {out_id}")
    print(f"  {c_out.name}, {w_out.name}, {dump_meta_path(out_id).name}")
    return out_id


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--from-dump", required=True, help="Source dump_id")
    p.add_argument("--to", help="Destination revision id (default: next rNNN)")
    p.add_argument("--note", action="append", default=[], help="Meta note (repeatable)")
    args = p.parse_args()
    promote(args.from_dump, args.to, args.note)


if __name__ == "__main__":
    main()
