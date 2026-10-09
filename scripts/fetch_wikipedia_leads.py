#!/usr/bin/env python3
"""Fetch plain-text Wikipedia lead paragraphs for composers (cached).

Uses the MediaWiki API (prop=extracts&exintro&explaintext) in batches of 20
titles. Cache namespace: wikipedia_lead, keyed by the title derived from
wikipedia_url. Stored text is truncated to 1,200 characters at a sentence
boundary when possible.

Example:
  python scripts/fetch_wikipedia_leads.py --dump r013 --ids data/llm_ledger/pilot_ids.txt
  python scripts/fetch_wikipedia_leads.py --dump r013 --limit 100
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Iterable, Optional

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

import common  # noqa: E402
from common import (  # noqa: E402
    WIKI_API,
    cache_set,
    dump_tsv_path,
    make_session,
    request_json,
    wikipedia_title_from_url,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("fetch_wikipedia_leads")

CACHE_NS = "wikipedia_lead"
BATCH_SIZE = 20
MAX_LEAD_CHARS = 1200


def _as_str(value: Any) -> str:
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


def truncate_at_sentence(text: str, max_len: int = MAX_LEAD_CHARS) -> str:
    """Truncate to max_len, preferring a sentence boundary in the second half."""
    text = (text or "").strip()
    if len(text) <= max_len:
        return text
    chunk = text[:max_len]
    best = -1
    for i, ch in enumerate(chunk):
        if ch in ".!?" and i >= max_len // 2:
            # Prefer end of sentence if followed by space/end or closing quote.
            nxt = chunk[i + 1 : i + 2]
            if nxt in {"", " ", "\n", '"', "'"} or (nxt and nxt.isupper()):
                best = i
    if best >= 0:
        return chunk[: best + 1].strip()
    # Fall back to last whitespace so we do not cut mid-word.
    sp = chunk.rfind(" ")
    if sp > max_len // 2:
        return chunk[:sp].rstrip()
    return chunk.rstrip()


def load_id_file(path: Path) -> list[str]:
    ids: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        ids.append(line)
    return ids


def cache_get_readonly(namespace: str, key: str) -> Any | None:
    """Read cache without mkdir (worktree data/cache may be a read-only symlink)."""
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in key)[:180]
    path = common.CACHE_DIR / namespace / f"{safe}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def select_composers(
    composers: pd.DataFrame,
    *,
    ids: Optional[list[str]],
    limit: Optional[int],
) -> pd.DataFrame:
    df = composers.copy()
    if ids is not None:
        want = set(ids)
        df = df[df["composer_id"].astype(str).isin(want)].copy()
        # Preserve file order
        order = {cid: i for i, cid in enumerate(ids)}
        df["_ord"] = df["composer_id"].astype(str).map(order)
        df = df.sort_values("_ord").drop(columns=["_ord"])
    df = df[df["wikipedia_url"].map(_as_str) != ""].copy()
    if limit is not None and limit >= 0:
        df = df.head(limit)
    return df


def _resolve_title_map(query: dict) -> dict[str, str]:
    """Map requested title → canonical page title after normalised/redirects."""
    mapping: dict[str, str] = {}
    for item in query.get("normalized") or []:
        frm, to = item.get("from"), item.get("to")
        if frm and to:
            mapping[frm] = to
    for item in query.get("redirects") or []:
        frm, to = item.get("from"), item.get("to")
        if not frm or not to:
            continue
        # Redirect may start from an already-normalised title.
        sources = [frm] + [k for k, v in mapping.items() if v == frm]
        for src in sources:
            mapping[src] = to
        mapping[frm] = to
    return mapping


def fetch_batch(session, titles: list[str]) -> dict[str, str]:
    """Return requested_title → extract text (may be empty)."""
    if not titles:
        return {}
    params = {
        "action": "query",
        "format": "json",
        "prop": "extracts",
        "exintro": 1,
        "explaintext": 1,
        "redirects": 1,
        "titles": "|".join(titles),
    }
    data = request_json(session, WIKI_API, params=params)
    query = data.get("query") or {}
    title_map = _resolve_title_map(query)
    pages = query.get("pages") or {}
    by_canonical: dict[str, str] = {}
    for page in pages.values():
        title = page.get("title") or ""
        extract = page.get("extract") or ""
        if page.get("missing") is not None:
            by_canonical[title] = ""
        else:
            by_canonical[title] = extract

    out: dict[str, str] = {}
    for req in titles:
        canon = title_map.get(req, req)
        # Normalisation may be chained; follow once more if needed.
        canon = title_map.get(canon, canon)
        out[req] = by_canonical.get(canon, "")
    return out


def chunks(items: list[str], size: int) -> Iterable[list[str]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def run(args: argparse.Namespace) -> int:
    composers = pd.read_csv(dump_tsv_path("composers", args.dump), sep="\t", low_memory=False)
    ids = load_id_file(Path(args.ids)) if args.ids else None
    todo = select_composers(composers, ids=ids, limit=args.limit)
    log.info("Selected %d composers with wikipedia_url", len(todo))

    # title → list of composer_ids that share it
    pending: list[str] = []
    title_for: dict[str, str] = {}
    cached_hits = 0
    for row in todo.itertuples(index=False):
        cid = str(row.composer_id)
        title = wikipedia_title_from_url(_as_str(getattr(row, "wikipedia_url", "")))
        if not title:
            log.warning("No title for %s", cid)
            continue
        title_for[cid] = title
        hit = cache_get_readonly(CACHE_NS, title)
        if hit is not None and isinstance(hit, dict) and "extract" in hit:
            cached_hits += 1
            continue
        if title not in pending:
            pending.append(title)

    log.info("Cache hits=%d titles to fetch=%d", cached_hits, len(pending))
    if args.dry_run:
        print(f"composers={len(todo)} cache_hits={cached_hits} to_fetch={len(pending)}")
        if pending:
            print("first_titles:", pending[:5])
        return 0

    session = make_session()
    fetched = 0
    empty = 0
    for batch in chunks(pending, BATCH_SIZE):
        results = fetch_batch(session, batch)
        for title, extract in results.items():
            text = truncate_at_sentence(extract or "", MAX_LEAD_CHARS)
            cache_set(
                CACHE_NS,
                title,
                {
                    "title": title,
                    "extract": text,
                    "chars": len(text),
                },
            )
            fetched += 1
            if not text:
                empty += 1
        log.info("Fetched batch of %d (running fetched=%d)", len(batch), fetched)

    log.info("Done fetched=%d empty_extracts=%d cache_hits=%d", fetched, empty, cached_hits)
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dump", required=True, help="Dump id (e.g. r013)")
    p.add_argument("--ids", help="File with one composer_id per line")
    p.add_argument("--limit", type=int, default=None, help="Max composers to consider")
    p.add_argument("--dry-run", action="store_true", help="Report counts only; no network")
    raise SystemExit(run(p.parse_args()))


if __name__ == "__main__":
    main()
