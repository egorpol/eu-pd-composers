#!/usr/bin/env python3
"""Fill the `imslp_page_cats` cache for every work page in a dump (network, resumable).

Fetches complete raw IMSLP category lists by page id. Already-cached pages are
skipped, so an interrupted run resumes where it stopped. Writes no dump; use
`remap_force.py --refresh-categories` afterwards to derive a new revision.

  python scripts/refetch_work_categories.py --dump r008 --limit 200
  python scripts/refetch_work_categories.py --dump r008 --sleep 1.0
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import cache_get, dump_tsv_path, make_session  # noqa: E402
from heartbeat import Heartbeat  # noqa: E402
from imslp import PAGE_CATS_NAMESPACE, fetch_page_categories  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("refetch_work_categories")


def run(args: argparse.Namespace) -> None:
    works = pd.read_csv(dump_tsv_path("works", args.dump), sep="\t", low_memory=False)
    pageids = (
        pd.to_numeric(works["imslp_pageid"], errors="coerce").dropna().astype(int).drop_duplicates()
    ).tolist()
    pending = [p for p in pageids if cache_get(PAGE_CATS_NAMESPACE, str(p)) is None]
    cached_n = len(pageids) - len(pending)
    if args.limit:
        pending = pending[: args.limit]
    log.info(
        "%s: %d unique page ids, %d cached, %d to fetch",
        args.dump,
        len(pageids),
        cached_n,
        len(pending),
    )

    session = make_session()
    chunk = args.batch_size * 10
    with Heartbeat(name="page_cats", total=len(pending), interval_s=args.heartbeat_interval) as hb:
        for i in range(0, len(pending), chunk):
            part = pending[i : i + chunk]
            fetch_page_categories(part, session, batch_size=args.batch_size, sleep_s=args.sleep)
            hb.tick(len(part))

    stats: Counter = Counter()
    for pid in pageids:
        entry = cache_get(PAGE_CATS_NAMESPACE, str(pid))
        if entry is None:
            stats["not_fetched"] += 1
        elif entry.get("missing"):
            stats["missing_on_imslp"] += 1
        elif entry.get("redirect"):
            stats["redirect_on_imslp"] += 1
        elif not entry.get("categories"):
            stats["empty_categories"] += 1
        else:
            stats["with_categories"] += 1
    log.info("cache state for %s: %s", args.dump, dict(stats))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dump", required=True, help="Dump id whose works to fetch (e.g. r008)")
    p.add_argument("--limit", type=int, default=0, help="Fetch at most N uncached pages")
    p.add_argument("--batch-size", type=int, default=50, help="Page ids per API request")
    p.add_argument("--sleep", type=float, default=1.0, help="Seconds between API requests")
    p.add_argument("--heartbeat-interval", type=float, default=30.0)
    run(p.parse_args())


if __name__ == "__main__":
    main()
