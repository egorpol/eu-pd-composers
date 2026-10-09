#!/usr/bin/env python3
"""Fill the `imslp_cat_page` cache with composer category wikitext for a dump (network).

Covers every composer whose `imslp_category` is set (P839 and heuristic
matches). Cached categories are skipped, so reruns are cheap. Writes no dump.

  python scripts/refetch_composer_pages.py --dump r010
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import dump_tsv_path, make_session  # noqa: E402
from imslp import fetch_category_pages  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("refetch_composer_pages")


def run(args: argparse.Namespace) -> None:
    composers = pd.read_csv(
        dump_tsv_path("composers", args.dump), sep="\t", dtype=str, keep_default_na=False
    )
    categories = sorted(set(composers.loc[composers["imslp_category"].ne(""), "imslp_category"]))
    log.info("%s: %d composer categories", args.dump, len(categories))
    pages = fetch_category_pages(categories, make_session(), sleep_s=args.sleep)
    stats = Counter()
    for entry in pages.values():
        if entry["missing"]:
            stats["missing"] += 1
        elif "#fte:person" not in entry["wikitext"]:
            stats["no_person_template"] += 1
        else:
            stats["with_person_template"] += 1
        if entry["redirect"]:
            stats["redirect"] += 1
    log.info("category pages: %s", dict(stats))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dump", required=True)
    p.add_argument("--sleep", type=float, default=1.0, help="Seconds between API requests")
    run(p.parse_args())


if __name__ == "__main__":
    main()
