"""Wikidata / Wikipedia enrichment helpers (schema v3)."""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

import requests

from common import (
    WIKI_API,
    WIKIDATA_API,
    cache_get,
    cache_set,
    pipe_join,
    request_json,
)

log = logging.getLogger("eu_pd.wikidata")

# Wikidata precision: 11=day, 10=month, 9=year, 8=decade, 7=century…
_PRECISION_MAP = {
    11: "day",
    10: "month",
    9: "year",
    8: "decade",
    7: "century",
}

# P135 / P136 → controlled style_tags (Opus-ish, P0 subset)
STYLE_QID_TO_TAG: dict[str, str] = {
    "Q207338": "impressionism",
    "Q164800": "impressionism",
    "Q317557": "expressionism",
    "Q189201": "neoclassicism",
    "Q189268": "serialism",
    "Q131433": "serialism",  # twelve-tone technique
    "Q206466": "minimalism",
    "Q1168885": "postminimalism",
    "Q189212": "spectralism",
    "Q1338153": "electroacoustic",
    "Q243370": "avant_garde",
    "Q1770252": "national_folk",
    "Q426816": "late_romantic",
    "Q9734": "atonal_modernism",
    "Q1168883": "polystylism",
    # Intentionally omit broad QIDs like jazz (Q9730) — too many false positives.
}

# P106 → occupation slug
OCCUPATION_QID_TO_SLUG: dict[str, str] = {
    "Q36834": "composer",
    "Q158852": "conductor",
    "Q486748": "pianist",
    "Q765778": "organist",
    "Q1259917": "violinist",
    "Q1415090": "film_composer",
    "Q753110": "songwriter",
    "Q14915627": "musicologist",
    "Q16145150": "music_teacher",
    "Q21680663": "classical_composer",
    "Q15981151": "jazz_musician",
}

FILM_MEDIA_OCC = {"film_composer"}
POPULAR_OCC = {"songwriter", "jazz_musician"}


def resolve_qids_for_titles(
    titles: list[str],
    session: requests.Session,
    *,
    use_cache: bool = True,
) -> dict[str, Optional[str]]:
    """Map Wikipedia article titles → Wikidata QIDs."""
    out: dict[str, Optional[str]] = {}
    pending: list[str] = []
    for title in titles:
        if not title:
            out[title] = None
            continue
        if use_cache:
            cached = cache_get("wiki_qid", title)
            if cached is not None:
                out[title] = cached.get("qid")
                continue
        pending.append(title)

    for i in range(0, len(pending), 40):
        batch = pending[i : i + 40]
        data = request_json(
            session,
            WIKI_API,
            params={
                "action": "query",
                "titles": "|".join(batch),
                "prop": "pageprops",
                "format": "json",
                "redirects": 1,
            },
        )
        # Map normalized / redirected titles back.
        redirects = {
            r["from"]: r["to"] for r in data.get("query", {}).get("redirects", [])
        }
        normalized = {
            n["from"]: n["to"] for n in data.get("query", {}).get("normalized", [])
        }
        pages = data.get("query", {}).get("pages", {})
        title_to_page: dict[str, dict] = {}
        for page in pages.values():
            if "title" in page:
                title_to_page[page["title"]] = page

        for original in batch:
            resolved = original
            if resolved in normalized:
                resolved = normalized[resolved]
            if resolved in redirects:
                resolved = redirects[resolved]
            page = title_to_page.get(resolved)
            qid = None
            if page and "pageprops" in page:
                qid = page["pageprops"].get("wikibase_item")
            out[original] = qid
            if use_cache:
                cache_set("wiki_qid", original, {"qid": qid, "resolved": resolved})

    return out


def _claim_entity_ids(claims: dict, pid: str) -> list[str]:
    ids: list[str] = []
    for claim in claims.get(pid, []):
        snak = claim.get("mainsnak", {})
        if snak.get("snaktype") != "value":
            continue
        value = snak.get("datavalue", {}).get("value")
        if isinstance(value, dict) and "id" in value:
            ids.append(value["id"])
        elif isinstance(value, str):
            ids.append(value)
    return ids


def _parse_time_claim(claims: dict, pid: str) -> tuple[Optional[str], Optional[int], str]:
    """Return (iso_date_or_year, year, precision_label)."""
    for claim in claims.get(pid, []):
        snak = claim.get("mainsnak", {})
        if snak.get("snaktype") != "value":
            continue
        value = snak.get("datavalue", {}).get("value")
        if not isinstance(value, dict) or "time" not in value:
            continue
        raw = value["time"]  # e.g. +1895-11-16T00:00:00Z
        precision = int(value.get("precision") or 9)
        prec_label = _PRECISION_MAP.get(precision, "unknown")
        m = re.match(r"^([+-])(\d+)-(\d{2})-(\d{2})", raw)
        if not m:
            continue
        sign, year_s, month, day = m.groups()
        year = int(year_s) * (1 if sign == "+" else -1)
        if precision >= 11:
            iso = f"{year:04d}-{month}-{day}" if year >= 0 else None
        elif precision == 10:
            iso = f"{year:04d}-{month}" if year >= 0 else None
        else:
            iso = f"{year:04d}" if year >= 0 else None
        return iso, year if year > 0 else None, prec_label
    return None, None, "unknown"


def fetch_entities(
    qids: list[str],
    session: requests.Session,
    *,
    use_cache: bool = True,
) -> dict[str, dict]:
    """Fetch wbgetentities payloads keyed by QID."""
    out: dict[str, dict] = {}
    pending: list[str] = []
    for qid in qids:
        if not qid:
            continue
        if use_cache:
            cached = cache_get("wikidata_entity", qid)
            if cached is not None:
                out[qid] = cached
                continue
        pending.append(qid)

    for i in range(0, len(pending), 40):
        batch = pending[i : i + 40]
        data = request_json(
            session,
            WIKIDATA_API,
            params={
                "action": "wbgetentities",
                "ids": "|".join(batch),
                "props": "claims|labels|aliases",
                "languages": "en",
                "format": "json",
            },
            timeout=60,
        )
        for qid, ent in data.get("entities", {}).items():
            if "missing" in ent:
                continue
            out[qid] = ent
            if use_cache:
                cache_set("wikidata_entity", qid, ent)
    return out


def iso_codes_for_countries(
    country_qids: list[str],
    session: requests.Session,
    *,
    use_cache: bool = True,
) -> dict[str, Optional[str]]:
    """Map country QIDs → ISO 3166-1 alpha-2 via P297 (None if unavailable)."""
    entities = fetch_entities(country_qids, session, use_cache=use_cache)
    out: dict[str, Optional[str]] = {}
    for qid in country_qids:
        ent = entities.get(qid, {})
        codes = _claim_entity_ids(ent.get("claims", {}), "P297")
        # P297 stores strings, not entities — handle both.
        iso = None
        for claim in ent.get("claims", {}).get("P297", []):
            snak = claim.get("mainsnak", {})
            if snak.get("snaktype") != "value":
                continue
            val = snak.get("datavalue", {}).get("value")
            if isinstance(val, str) and len(val) == 2:
                iso = val.upper()
                break
        out[qid] = iso
    return out


def enrich_from_entity(ent: dict) -> dict[str, Any]:
    """Flatten a Wikidata entity into schema-v3 composer fields."""
    claims = ent.get("claims", {})
    qid = ent.get("id")
    label = (ent.get("labels", {}).get("en") or {}).get("value")
    aliases = [
        a.get("value")
        for a in (ent.get("aliases", {}).get("en") or [])
        if a.get("value")
    ]

    birth_iso, birth_year, birth_prec = _parse_time_claim(claims, "P569")
    death_iso, death_year, death_prec = _parse_time_claim(claims, "P570")
    # Prefer death precision for PD; if living, birth precision is secondary.
    if death_year is not None:
        date_precision = death_prec
    elif birth_year is not None:
        date_precision = birth_prec
    else:
        date_precision = "unknown"

    citizenship_qids = _claim_entity_ids(claims, "P27")
    style_qids = _claim_entity_ids(claims, "P135") + _claim_entity_ids(claims, "P136")
    occupation_qids = _claim_entity_ids(claims, "P106")
    notable_qids = _claim_entity_ids(claims, "P800")
    imslp_ids = _claim_entity_ids(claims, "P839")

    style_tags: list[str] = []
    for sq in style_qids:
        tag = STYLE_QID_TO_TAG.get(sq)
        if tag and tag not in style_tags:
            style_tags.append(tag)

    occupations: list[str] = []
    for oq in occupation_qids:
        slug = OCCUPATION_QID_TO_SLUG.get(oq)
        if slug and slug not in occupations:
            occupations.append(slug)
        elif not slug and "other" not in occupations and len(occupations) < 8:
            # Keep unknown occupation QIDs out of slug list; skip.
            pass

    scope_class, scope_src = infer_scope_class(occupations, style_tags)

    imslp_category = None
    if imslp_ids:
        # P839 values look like "Category:Hindemith,_Paul"
        imslp_category = imslp_ids[0].replace(" ", "_")
        if not imslp_category.startswith("Category:"):
            imslp_category = f"Category:{imslp_category}"

    return {
        "wikidata_qid": qid,
        "wikidata_label": label,
        "name_aliases": pipe_join(aliases[:20]),
        "birth_date": birth_iso,
        "birth_year": birth_year,
        "death_date": death_iso,
        "death_year": death_year,
        "date_precision": date_precision,
        "citizenship_qids": pipe_join(citizenship_qids),
        "style_tags": pipe_join(style_tags),
        "style_tags_src": "wikidata" if style_tags else "",
        "style_raw_qids": pipe_join(style_qids),
        "occupations": pipe_join(occupations),
        "scope_class": scope_class,
        "scope_class_src": scope_src,
        "notable_works_qids": pipe_join(notable_qids),
        "imslp_category_p839": imslp_category,
    }


def infer_scope_class(
    occupations: list[str],
    style_tags: list[str],
) -> tuple[str, str]:
    occ = set(occupations)
    if occ & FILM_MEDIA_OCC:
        return "film_media", "occupations"
    if occ & POPULAR_OCC and "composer" not in occ and "classical_composer" not in occ:
        return "popular", "occupations"
    if "crossover_popular" in style_tags:
        return "crossover", "style_tags"
    if "composer" in occ or "classical_composer" in occ or not occ:
        return "classical_core", "occupations" if occ else "default"
    return "crossover", "occupations"


def name_sort_from_display(name: str) -> str:
    parts = str(name).split()
    if len(parts) >= 2:
        return f"{parts[-1]}, {' '.join(parts[:-1])}"
    return str(name)


def name_sort_from_imslp_category(category: Optional[str]) -> Optional[str]:
    if not category:
        return None
    text = category
    if text.startswith("Category:"):
        text = text[len("Category:") :]
    return text.replace("_", " ").strip()
