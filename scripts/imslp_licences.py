#!/usr/bin/env python3
"""Per-file licences of IMSLP work pages (offline, from the `imslp_page_wikitext` cache).

Every score or recording on a work page sits in a {{#fte:imslpfile …}} or
{{#fte:imslpaudio …}} block whose `|Copyright=` field names the licence of that
block's files: "Public Domain", a Creative Commons licence, "Public Domain
(dedicated)", IMSLP's own "Performance Restricted" licences, and a few others.
This script lists the blocks with a normalised licence id and summarises them per
work. Research output for the open-licence sweep; writes no dump.

  python scripts/imslp_licences.py --dump r020
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import REPO_ROOT, cache_get, dump_tsv_path, pipe_join  # noqa: E402
from imslp import PAGE_WIKITEXT_NAMESPACE  # noqa: E402
from licences import is_cc_family, spdx_from_cc_text  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("imslp_licences")

_BLOCK_START_RE = re.compile(r"\{\{\s*#fte:\s*(imslpfile|imslpaudio)\b", re.I)
_HEADING_RE = re.compile(r"^(={2,6})\s*(.*?)\s*\1\s*$", re.M)
_FIELD_RE = re.compile(r"^\|[ \t]*([^=\n|{}]+?)[ \t]*=[ \t]*(.*)$")
_FILE_NAME_RE = re.compile(r"^File Name(?: \d+)?$")

# Notation sources and machine-readable formats among file names (by extension).
FORMAT_BY_EXT = {
    "pdf": "pdf",
    "mid": "midi",
    "midi": "midi",
    "mxl": "musicxml",
    "musicxml": "musicxml",
    "xml": "musicxml",
    "mscz": "musescore",
    "mscx": "musescore",
    "ly": "lilypond",
    "mei": "mei",
    "sib": "sibelius",
    "mus": "finale",
    "musx": "finale",
    "zip": "zip",
    "brf": "braille",
    "mp3": "audio",
    "flac": "audio",
    "ogg": "audio",
    "m4a": "audio",
    "wav": "audio",
    "mp4": "video",
}


def _block_end(text: str, start: int) -> int:
    """Index just past the `}}` that closes the template opened at `start`."""
    depth = 0
    i = start
    while i < len(text) - 1:
        pair = text[i : i + 2]
        if pair == "{{":
            depth += 1
            i += 2
        elif pair == "}}":
            depth -= 1
            i += 2
            if depth == 0:
                return i
        else:
            i += 1
    return len(text)


def _block_fields(body: str) -> dict[str, str]:
    """`|Field=value` lines of one block; continuation lines join the previous field."""
    fields: dict[str, str] = {}
    key = None
    for line in body.splitlines()[1:]:
        m = _FIELD_RE.match(line)
        if m:
            key = m.group(1).strip()
            fields[key] = m.group(2).strip()
        elif key is not None and line.strip() not in ("", "}}"):
            fields[key] = (fields[key] + " " + line.strip()).strip()
    return fields


def iter_blocks(wikitext: str) -> list[dict[str, Any]]:
    """File and audio blocks of a work page, in page order, with the nearest heading above each."""
    headings = [(m.start(), m.group(2)) for m in _HEADING_RE.finditer(wikitext or "")]
    out = []
    for m in _BLOCK_START_RE.finditer(wikitext or ""):
        end = _block_end(wikitext, m.start())
        fields = _block_fields(wikitext[m.start() : end])
        section = next((h for pos, h in reversed(headings) if pos < m.start()), "")
        out.append({"kind": "score" if m.group(1).lower() == "imslpfile" else "audio", "fields": fields, "section": section})
    return out


def licence_id(raw: str) -> str:
    """Normalised licence of one block's `|Copyright=` value.

    SPDX ids for Creative Commons; `PD` (IMSLP's plain "Public Domain"),
    `PD-dedicated` (uploader's dedication), `PD-note` (a rights note such as
    {{RoST}} written into the field); `LicenseRef-…` for IMSLP Performance
    Restricted, CPDL and EFF licences; `unparsed` otherwise; empty when blank.
    """
    s = re.sub(r"\s+", " ", raw or "").strip()
    low = s.lower()
    if not s:
        return ""
    if low == "public domain":
        return "PD"
    if low.startswith("public domain (dedicated)"):
        return "PD-dedicated"
    if low.startswith("performance restricted"):
        cc = spdx_from_cc_text("creative commons " + s[len("performance restricted") :])
        return "LicenseRef-IMSLP-PR-" + cc[len("CC-") :] if cc else "LicenseRef-IMSLP-PR"
    cc = spdx_from_cc_text(s)
    if cc:
        return cc
    if "choral public domain library license" in low:
        ver = re.search(r"v(?:ersion)?\s*(\d+)", low)
        return "LicenseRef-CPDL" + (f"-{ver.group(1)}" if ver else "")
    if "open audio license" in low:
        return "LicenseRef-EFF-OAL"
    if "rost" in low or "public domain" in low or "pma" in low:
        return "PD-note"
    return "unparsed"


def is_open_licence(lid: str) -> bool:
    """CC family (incl. CC0 and the PD mark), an uploader's PD dedication, CPDL and EFF OAL.

    IMSLP Performance Restricted licences are not open (they restrict performance).
    """
    return is_cc_family(lid) or lid == "PD-dedicated" or lid.startswith(("LicenseRef-CPDL", "LicenseRef-EFF-OAL"))


def file_formats(names: list[str]) -> list[str]:
    out = []
    for name in names:
        ext = name.strip().rsplit(".", 1)[-1].lower() if "." in name else ""
        fmt = FORMAT_BY_EXT.get(re.sub(r"[^a-z0-9].*$", "", ext), "other")
        if fmt not in out:
            out.append(fmt)
    return out


def block_rows(work: dict[str, Any], entry: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for i, block in enumerate(iter_blocks(entry.get("wikitext", "")), start=1):
        f = block["fields"]
        names = [v for k, v in f.items() if _FILE_NAME_RE.match(k) and v]
        raw = f.get("Copyright", "")
        lid = licence_id(raw)
        rows.append(
            {
                "work_id": work["work_id"],
                "composer_id": work["composer_id"],
                "imslp_pageid": int(entry["pageid"]),
                "block": i,
                "kind": block["kind"],
                "section": block["section"],
                "n_files": len(names),
                "formats": pipe_join(file_formats(names)),
                "image_type": f.get("Image Type", ""),
                "copyright_raw": raw,
                "licence": lid,
                "open_licence": is_open_licence(lid),
                "editor": f.get("Editor", ""),
                "arranger": f.get("Arranger", ""),
                "uploader": f.get("Uploader", ""),
                "date_submitted": f.get("Date Submitted", ""),
                "file_names": pipe_join(names),
                "revid": entry.get("revid"),
            }
        )
    return rows


def work_summary(blocks: pd.DataFrame) -> pd.DataFrame:
    """One row per work: licences over its score blocks, open-licence score blocks and their formats."""
    scores = blocks[blocks["kind"] == "score"]
    open_scores = scores[scores["open_licence"]]
    g = scores.groupby("work_id")
    out = pd.DataFrame(
        {
            "n_score_blocks": g.size(),
            "score_licences": g["licence"].agg(lambda s: pipe_join(sorted(set(s) - {""}))),
        }
    )
    og = open_scores.groupby("work_id")
    out["n_open_score_blocks"] = og.size()
    out["open_score_licences"] = og["licence"].agg(lambda s: pipe_join(sorted(set(s))))
    out["open_score_formats"] = og["formats"].agg(lambda s: pipe_join(sorted({x for v in s for x in v.split("|") if x})))
    out["open_score_typeset"] = og["image_type"].agg(lambda s: bool(s.str.lower().str.contains("typeset|new").any()))
    out["n_open_score_blocks"] = out["n_open_score_blocks"].fillna(0).astype(int)
    return out.reset_index()


def run(args: argparse.Namespace) -> None:
    works = pd.read_csv(dump_tsv_path("works", args.dump), sep="\t", low_memory=False, dtype=str)
    composers = pd.read_csv(dump_tsv_path("composers", args.dump), sep="\t", low_memory=False, dtype=str)
    rows: list[dict[str, Any]] = []
    state: Counter = Counter()
    for work in works.to_dict("records"):
        pid = str(work.get("imslp_pageid") or "").split(".")[0]
        entry = cache_get(PAGE_WIKITEXT_NAMESPACE, pid) if pid else None
        if entry is None:
            state["not_cached"] += 1
        elif entry.get("missing"):
            state["missing_on_imslp"] += 1
        elif entry.get("redirect"):
            state["redirect_on_imslp"] += 1
        else:
            state["parsed"] += 1
            rows.extend(block_rows(work, entry))
    log.info("%s: %d works; page state %s", args.dump, len(works), dict(state))

    blocks = pd.DataFrame(rows)
    summary = work_summary(blocks)
    status = composers.set_index("composer_id")["eu_pd_status"]
    summary["composer_id"] = summary["work_id"].map(works.set_index("work_id")["composer_id"])
    summary["eu_pd_status"] = summary["composer_id"].map(status)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    blocks.to_csv(out_dir / f"imslp_file_blocks_{args.dump}.tsv", sep="\t", index=False)
    summary.to_csv(out_dir / f"imslp_work_licences_{args.dump}.tsv", sep="\t", index=False)

    log.info("blocks by kind × licence: %s", blocks.groupby(["kind", "licence"]).size().sort_values(ascending=False).head(40).to_dict())
    unparsed = blocks.loc[blocks["licence"] == "unparsed", "copyright_raw"].value_counts().head(15)
    log.info("unparsed copyright values: %s", unparsed.to_dict())
    with_open = summary[summary["n_open_score_blocks"] > 0]
    log.info(
        "works with ≥1 open-licence score block: %d (by composer eu_pd_status %s); composers: %d",
        len(with_open),
        with_open["eu_pd_status"].value_counts().to_dict(),
        with_open["composer_id"].nunique(),
    )


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dump", required=True, help="Dump id whose works to read (e.g. r020)")
    p.add_argument("--out-dir", default=str(REPO_ROOT / "build" / "open_licence"))
    run(p.parse_args())


if __name__ == "__main__":
    main()
