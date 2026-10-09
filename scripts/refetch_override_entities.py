#!/usr/bin/env python3
"""Fetch Wikidata entities that composer overrides re-key to (network, cached).

`apply_overrides.py` is offline and re-derives a re-keyed row from the cached
entity of the *correct* person. A cold crawl only caches the entities the
Wikipedia list links to (the wrong ones), so the pipeline runs this first, as
a preflight, before the long crawl. Also caches the targets' citizenship
countries so their ISO codes resolve offline.

  python scripts/refetch_override_entities.py --overrides data/overrides/composers.tsv
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import REPO_ROOT, make_session  # noqa: E402
from overrides import OP_REKEY, _is_qid, load_overrides  # noqa: E402
from wikidata_enrich import _claim_entity_ids, fetch_entities  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("refetch_override_entities")


def rekey_targets(overrides_path: Path) -> list[str]:
    overrides = load_overrides(overrides_path)
    values = overrides.loc[overrides["field"] == OP_REKEY, "value"].map(str).str.strip()
    return sorted({v for v in values if _is_qid(v)})


def run(args: argparse.Namespace) -> None:
    targets = rekey_targets(Path(args.overrides))
    if not targets:
        log.info("No QID re-key targets in %s", args.overrides)
        return
    session = make_session()
    entities = fetch_entities(targets, session)
    missing = [q for q in targets if q not in entities]
    if missing:
        raise SystemExit(f"Wikidata returned no entity for re-key targets: {', '.join(missing)}")
    countries = sorted({c for q in targets for c in _claim_entity_ids(entities[q].get("claims", {}), "P27")})
    fetch_entities(countries, session)
    log.info("Cached %d re-key target entities and %d citizenship countries", len(targets), len(countries))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--overrides", default=str(REPO_ROOT / "data" / "overrides" / "composers.tsv"))
    run(p.parse_args())


if __name__ == "__main__":
    main()
