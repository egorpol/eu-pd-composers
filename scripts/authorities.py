"""Authority ids for composers: GND (P227) and VIAF (P214) from Wikidata, GND records from lobid.

Network helpers fill two caches; everything else reads them only.

- `lobid_gnd/<gnd id>`: a trimmed lobid record (name, life dates, professions), or
  `{"gndIdentifier": id, "missing": true}` when lobid returned nothing for the id.
- `lobid_gnd_search/<composer id>`: trimmed person candidates from a lobid name search,
  used only for composers whose Wikidata item has no GND id.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional

import requests

from common import cache_get, cache_set, request_json

LOBID_SEARCH_URL = "https://lobid.org/gnd/search"
RECORD_NAMESPACE = "lobid_gnd"
SEARCH_NAMESPACE = "lobid_gnd_search"
AUTHORITY_COLUMNS = ("gnd_id", "viaf_id")
# QA flags this stage owns (recomputed on every run).
GND_FLAGS = ("gnd_from_lobid", "gnd_death_conflict")
# Wikidata's format constraints for P227 and P214.
GND_ID_RE = re.compile(r"^(?:1[01]?\d{7}[0-9X]|[47]\d{6}-\d|[1-9]\d{0,7}-[0-9X]|3\d{7}[0-9X])$")
VIAF_ID_RE = re.compile(r"^[1-9]\d(?:\d{0,7}|\d{17,20})$")
# GND professions that make a name match a composer ("Komponist", "Komponistin").
COMPOSER_PROFESSIONS = {"https://d-nb.info/gnd/4032009-1", "https://d-nb.info/gnd/4032010-8"}
_YEAR_RE = re.compile(r"^-?(\d{1,4})")


def claim_strings(claims: dict, pid: str) -> list[str]:
    """String values of a property: preferred rank first, deprecated claims skipped."""
    ranked: list[tuple[int, str]] = []
    for claim in claims.get(pid, []):
        rank = claim.get("rank", "normal")
        if rank == "deprecated":
            continue
        snak = claim.get("mainsnak", {})
        if snak.get("snaktype") != "value":
            continue
        value = snak.get("datavalue", {}).get("value")
        if isinstance(value, str) and value.strip():
            ranked.append((0 if rank == "preferred" else 1, value.strip()))
    seen: list[str] = []
    for _, value in sorted(ranked, key=lambda item: item[0]):
        if value not in seen:
            seen.append(value)
    return seen


def wikidata_authority_ids(entity: dict) -> dict[str, str]:
    """First valid GND and VIAF id on a Wikidata entity ("" when none)."""
    claims = entity.get("claims", {})
    gnd = next((v for v in claim_strings(claims, "P227") if GND_ID_RE.match(v)), "")
    viaf = next((v for v in claim_strings(claims, "P214") if VIAF_ID_RE.match(v)), "")
    return {"gnd_id": gnd, "viaf_id": viaf}


def year_of(values: Any) -> Optional[int]:
    """Year of the first lobid date ("1942", "1865-05-22"); None for empty or BCE values."""
    if isinstance(values, list):
        values = values[0] if values else None
    if not isinstance(values, str):
        return None
    text = values.strip()
    if text.startswith("-"):
        return None
    match = _YEAR_RE.match(text)
    return int(match.group(1)) if match else None


def trim_record(member: dict) -> dict[str, Any]:
    """Keep the fields the stage reads; lobid records are large."""
    return {
        "gndIdentifier": member.get("gndIdentifier"),
        "preferredName": member.get("preferredName"),
        "type": member.get("type") or [],
        "dateOfBirth": member.get("dateOfBirth") or [],
        "dateOfDeath": member.get("dateOfDeath") or [],
        "professions": [p.get("id") for p in member.get("professionOrOccupation") or [] if p.get("id")],
        "profession_labels": [p.get("label") for p in member.get("professionOrOccupation") or [] if p.get("label")],
    }


def fetch_records(
    gnd_ids: Iterable[str], session: requests.Session, *, batch_size: int = 50, refresh: bool = False,
) -> dict[str, int]:
    """Fill the record cache for GND ids, `batch_size` ids per lobid search request."""
    pending = sorted({i for i in gnd_ids if i and (refresh or cache_get(RECORD_NAMESPACE, i) is None)})
    stats = {"requested": len(pending), "found": 0, "missing": 0}
    for start in range(0, len(pending), batch_size):
        batch = pending[start:start + batch_size]
        query = " OR ".join(f'gndIdentifier:"{i}"' for i in batch)
        payload = request_json(session, LOBID_SEARCH_URL, params={
            "q": query, "format": "json", "size": str(len(batch) * 2),
        })
        found = {m.get("gndIdentifier"): m for m in payload.get("member") or []}
        for gnd_id in batch:
            member = found.get(gnd_id)
            if member is None:
                cache_set(RECORD_NAMESPACE, gnd_id, {"gndIdentifier": gnd_id, "missing": True})
                stats["missing"] += 1
            else:
                cache_set(RECORD_NAMESPACE, gnd_id, trim_record(member))
                stats["found"] += 1
    return stats


def search_people(
    composer_id: str, name: str, session: requests.Session, *, size: int = 20, refresh: bool = False,
) -> list[dict[str, Any]]:
    """Cached lobid name search for differentiated persons."""
    cached = None if refresh else cache_get(SEARCH_NAMESPACE, composer_id)
    if cached is not None and cached.get("name") == name:
        return cached["candidates"]
    payload = request_json(session, LOBID_SEARCH_URL, params={
        "q": name, "filter": "type:DifferentiatedPerson", "format": "json", "size": str(size),
    })
    candidates = [trim_record(m) for m in payload.get("member") or []]
    cache_set(SEARCH_NAMESPACE, composer_id, {"name": name, "candidates": candidates})
    return candidates


def match_candidates(
    candidates: list[dict[str, Any]], birth_year: Optional[int], death_year: Optional[int],
) -> Optional[dict[str, Any]]:
    """The single candidate whose life dates agree with the composer's, else None.

    Birth years must be equal. Death years must be equal when both sides have one;
    a candidate without a matching death year needs a composer profession instead.
    Two or more agreeing candidates are ambiguous and give no match.
    """
    if birth_year is None:
        return None
    matches = []
    for cand in candidates:
        if not GND_ID_RE.match(str(cand.get("gndIdentifier") or "")):
            continue
        born, died = year_of(cand.get("dateOfBirth")), year_of(cand.get("dateOfDeath"))
        if born != birth_year:
            continue
        if died is not None and death_year is not None and died != death_year:
            continue
        death_agrees = died is not None and died == death_year
        composer = bool(COMPOSER_PROFESSIONS & set(cand.get("professions") or []))
        if death_agrees or composer:
            matches.append(cand)
    return matches[0] if len(matches) == 1 else None
