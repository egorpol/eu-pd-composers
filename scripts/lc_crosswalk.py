#!/usr/bin/env python3
"""Crosswalk from force_family / genre_form to Library of Congress vocabularies.

`data/vocab/lc_crosswalk.tsv` maps each value to LCMPT (Medium of Performance
Thesaurus for Music) or LCGFT (Genre/Form Terms) with a SKOS mapping relation,
read as "<value> <relation> <LC term>": broadMatch means the LC term is broader.
A value with no LC equivalent has one row with empty scheme, relation and uri.

    python scripts/lc_crosswalk.py check    # offline: coverage and well-formed rows
    python scripts/lc_crosswalk.py verify   # online: every label still resolves to its uri
"""

from __future__ import annotations

import argparse
import csv
import logging
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import REPO_ROOT, make_session  # noqa: E402
from force_family import FORCE_FAMILIES, GENRE_FORMS  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("lc_crosswalk")

CROSSWALK_PATH = REPO_ROOT / "data" / "vocab" / "lc_crosswalk.tsv"
COLUMNS = ("facet", "value", "scheme", "relation", "label", "uri", "note")
SCHEMES = {
    "lcmpt": "http://id.loc.gov/authorities/performanceMediums/",
    "lcgft": "http://id.loc.gov/authorities/genreForms/",
}
RELATIONS = {"exactMatch", "closeMatch", "broadMatch", "narrowMatch", "relatedMatch"}
# Values the crosswalk must cover; "unclassified" and an empty genre_form mean "no signal".
FACET_VALUES = {
    "force_family": tuple(f for f in FORCE_FAMILIES if f != "unclassified"),
    "genre_form": GENRE_FORMS,
}
_ID_RE = re.compile(r"^(mp|gf)\d{10}$")


def load_crosswalk(path: Path = CROSSWALK_PATH) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if tuple(reader.fieldnames or ()) != COLUMNS:
            raise ValueError(f"{path}: expected columns {COLUMNS}, got {reader.fieldnames}")
        return [dict(row) for row in reader]


def crosswalk_errors(rows: list[dict[str, str]]) -> list[str]:
    errors: list[str] = []
    seen: dict[str, set[str]] = {facet: set() for facet in FACET_VALUES}
    for number, row in enumerate(rows, start=2):
        facet, value, scheme = row["facet"], row["value"], row["scheme"]
        where = f"line {number} ({facet}={value})"
        if facet not in FACET_VALUES:
            errors.append(f"{where}: unknown facet")
            continue
        if value not in FACET_VALUES[facet]:
            errors.append(f"{where}: unknown value")
        seen[facet].add(value)
        if not scheme:
            if row["relation"] or row["label"] or row["uri"]:
                errors.append(f"{where}: relation, label and uri must be empty without a scheme")
            continue
        if scheme not in SCHEMES:
            errors.append(f"{where}: unknown scheme {scheme!r}")
            continue
        if row["relation"] not in RELATIONS:
            errors.append(f"{where}: relation {row['relation']!r} is not a SKOS mapping relation")
        uri = row["uri"]
        if not uri.startswith(SCHEMES[scheme]) or not _ID_RE.match(uri.removeprefix(SCHEMES[scheme])):
            errors.append(f"{where}: uri {uri!r} is not a {scheme} uri")
        if not row["label"]:
            errors.append(f"{where}: empty label")
    for facet, values in FACET_VALUES.items():
        missing = [v for v in values if v not in seen[facet]]
        if missing:
            errors.append(f"{facet} values missing from the crosswalk: " + ", ".join(missing))
    keys = [(r["facet"], r["value"], r["uri"]) for r in rows]
    if len(keys) != len(set(keys)):
        errors.append("duplicate (facet, value, uri) rows")
    return errors


def verify_online(rows: list[dict[str, str]], pause: float = 0.5) -> list[str]:
    """Resolve each label with id.loc.gov's label service; it must redirect to the row's uri."""
    session = make_session()
    errors: list[str] = []
    terms = sorted({(r["scheme"], r["label"], r["uri"]) for r in rows if r["scheme"]})
    for scheme, label, uri in terms:
        base = SCHEMES[scheme].replace("http://", "https://")
        response = session.get(base + "label/" + quote(label), allow_redirects=False, timeout=30)
        location = response.headers.get("Location", "").replace("https://", "http://")
        pref = response.headers.get("X-PrefLabel", "")
        if location != uri or pref != label:
            errors.append(f"{scheme} {label!r}: expected {uri}, id.loc.gov gives {location or response.status_code} ({pref!r})")
        else:
            log.info("ok  %s  %s", uri, label)
        time.sleep(pause)
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("check", "verify"))
    parser.add_argument("--path", type=Path, default=CROSSWALK_PATH)
    args = parser.parse_args()
    rows = load_crosswalk(args.path)
    errors = crosswalk_errors(rows)
    if not errors and args.command == "verify":
        errors = verify_online(rows)
    for error in errors:
        log.error(error)
    if errors:
        sys.exit(1)
    log.info("%d rows, %s", len(rows), "verified against id.loc.gov" if args.command == "verify" else "well-formed")


if __name__ == "__main__":
    main()
