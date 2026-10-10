#!/usr/bin/env python3
"""Fill the lobid GND caches for a dump's composers (network).

GND ids come from each composer's cached Wikidata entity (P227). Composers
without one get a lobid name search (fallback candidates); GND records are then
fetched for both, batched by id. Cached ids and searches are skipped, so reruns
are cheap. Writes no dump.

  python scripts/refetch_authorities.py --dump r016
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from authorities import fetch_records, search_people, wikidata_authority_ids  # noqa: E402
from common import cache_get, dump_tsv_path, make_session  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("refetch_authorities")


def run(args: argparse.Namespace) -> None:
    composers = pd.read_csv(
        dump_tsv_path("composers", args.dump), sep="\t", dtype=str, keep_default_na=False
    )
    session = make_session()
    gnd_ids: list[str] = []
    searches = no_entity = 0
    for _, row in composers.iterrows():
        entity = cache_get("wikidata_entity", row["composer_id"])
        if entity is None:
            no_entity += 1
            continue
        gnd = wikidata_authority_ids(entity)["gnd_id"]
        if gnd:
            gnd_ids.append(gnd)
            continue
        name = (entity.get("labels", {}).get("en") or {}).get("value") or row["name_display"]
        search_people(row["composer_id"], name, session, refresh=args.refresh)
        searches += 1
    log.info("%s: %d GND ids from Wikidata, %d name searches, %d rows without a cached entity",
             args.dump, len(gnd_ids), searches, no_entity)
    stats = fetch_records(gnd_ids, session, refresh=args.refresh)
    log.info("GND records: %s", stats)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dump", required=True)
    p.add_argument("--refresh", action="store_true", help="Refetch cached records and searches")
    run(p.parse_args())


if __name__ == "__main__":
    main()
