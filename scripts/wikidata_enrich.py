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
    14: "second",
    13: "minute",
    12: "hour",
    11: "day",
    10: "month",
    9: "year",
    8: "decade",
    7: "century",
    6: "millennium",
    5: "ten_thousand_years",
    4: "hundred_thousand_years",
    3: "million_years",
    2: "ten_million_years",
    1: "hundred_million_years",
    0: "billion_years",
}

# P135 / P136 → controlled style_tags (verified Wikidata labels 2026-09-25).
# Only music-genre / technique items — never form QIDs (symphony, chamber music, …).
STYLE_QID_TO_TAG: dict[str, str] = {
    "Q837182": "impressionism",  # impressionism in music
    "Q613707": "expressionism",  # expressionist music
    "Q535611": "neoclassicism",  # 20th-c neoclassicism in music
    "Q106576145": "neoclassicism",  # neoclassical music
    "Q507246": "serialism",
    "Q221686": "serialism",  # twelve-tone technique
    "Q572901": "minimalism",  # minimalist music
    "Q2394116": "postminimalism",
    "Q98528143": "postminimalism",  # post-minimalism (music genre)
    "Q1245902": "spectralism",  # spectral music
    "Q1326777": "electroacoustic",
    "Q823560": "electroacoustic",  # musique concrète
    "Q2332751": "avant_garde",  # avant-garde music
    "Q1640319": "avant_garde",  # experimental music
    "Q102932": "avant_garde",
    "Q108908": "avant_garde",  # Fluxus
    "Q1196170": "national_folk",  # musical nationalism
    "Q235858": "national_folk",  # traditional folk music
    "Q1413570": "national_folk",  # sardana
    "Q148163": "national_folk",  # zarzuela
    "Q207591": "late_romantic",  # Romantic music
    "Q136774417": "late_romantic",  # late romantic music
    "Q1125039": "late_romantic",  # Post-romanticism
    "Q37068": "late_romantic",  # Romanticism (broad)
    "Q211745": "atonal_modernism",  # atonality
    "Q3269212": "polystylism",
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
QA_FLAGS = (
    "birth_rank_conflict",
    "death_rank_conflict",
    "birth_imprecise",
    "death_imprecise",
    "implausible_lifespan",
    "death_before_birth",
    "not_human",
    "no_composer_occupation",
    "birth_from_list",
    "death_from_list",
)
# Any of these makes a Wikidata item plausibly "a composer" for identity checks.
COMPOSING_OCC = {"composer", "classical_composer", "film_composer", "songwriter"}


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


def _parse_time_value(value: Any) -> Optional[tuple[Optional[str], Optional[int], str, bool]]:
    if not isinstance(value, dict) or not isinstance(value.get("time"), str):
        return None
    try:
        precision = int(value.get("precision", 9))
    except (TypeError, ValueError):
        return None
    if precision not in _PRECISION_MAP:
        return None
    m = re.match(r"^([+-])(\d+)-(\d{2})-(\d{2})", value["time"])
    if not m:
        return None
    sign, year_s, month, day = m.groups()
    year = int(year_s) * (1 if sign == "+" else -1)
    if precision == 8:
        year = year // 10 * 10 + 9
    elif precision < 8:
        span = 10 ** (9 - precision)
        year = (year - 1) // span * span + span
    if precision >= 11:
        iso = f"{year:04d}-{month}-{day}" if year >= 0 else None
    elif precision == 10:
        iso = f"{year:04d}-{month}" if year >= 0 else None
    else:
        iso = f"{year:04d}" if year >= 0 else None
    return iso, year if year > 0 else None, _PRECISION_MAP[precision], precision < 9


def _parse_time_claim(
    claims: dict, pid: str,
) -> tuple[Optional[str], Optional[int], str, bool, bool]:
    """Return (iso_date_or_year, year, precision_label, conflict, imprecise).

    Ignore deprecated claims; use the first usable preferred value, otherwise
    the first usable normal value. Conflicts compare years within that rank.
    For conservative PD math, coarse dates use the interval's final year.
    Wikidata decades span years ending 0–9 (1855 → 1859); centuries and larger
    units start at year 1 (1801–1900 → 1900, 1901–2000 → 2000). The encoded
    year can lie anywhere in the interval, including its final year.
    """
    by_rank: dict[str, list[tuple[Optional[str], Optional[int], str, bool]]] = {
        "preferred": [], "normal": [],
    }
    for claim in claims.get(pid, []):
        rank = claim.get("rank", "normal")
        if rank not in by_rank:
            continue
        snak = claim.get("mainsnak", {})
        if snak.get("snaktype") != "value":
            continue
        value = snak.get("datavalue", {}).get("value")
        parsed = _parse_time_value(value)
        if parsed is not None:
            by_rank[rank].append(parsed)
    candidates = by_rank["preferred"] or by_rank["normal"]
    if not candidates:
        return None, None, "unknown", False, False
    iso, year, prec_label, imprecise = candidates[0]
    conflict = len({c[1] for c in candidates}) > 1
    return iso, year, prec_label, conflict, imprecise


def lifespan_flags(birth_year: Optional[int], death_year: Optional[int]) -> list[str]:
    if birth_year is None or death_year is None:
        return []
    lifespan = death_year - birth_year
    flags = []
    if lifespan < 15 or lifespan > 110:
        flags.append("implausible_lifespan")
    if lifespan < 0:
        flags.append("death_before_birth")
    return flags


def deprecated_years(claims: dict, pid: str) -> set[int]:
    """Years asserted only by deprecated claims — never valid fallbacks."""
    years: set[int] = set()
    for claim in claims.get(pid, []):
        if claim.get("rank") != "deprecated":
            continue
        snak = claim.get("mainsnak", {})
        if snak.get("snaktype") != "value":
            continue
        parsed = _parse_time_value(snak.get("datavalue", {}).get("value"))
        if parsed and parsed[1] is not None:
            years.add(parsed[1])
    return years


def apply_year_fallbacks(
    wd: dict[str, Any],
    claims: dict,
    *,
    fallback_birth: Optional[int],
    fallback_death: Optional[int],
) -> dict[str, Any]:
    """Fill years Wikidata lacks from the Wikipedia list (or a prior dump).

    A fallback equal to a deprecated Wikidata year is rejected: that value was
    explicitly marked wrong. Returns birth_year, death_year, date_precision and
    qa_flags with `*_from_list` and lifespan flags recomputed on final years.
    """
    birth, death = wd.get("birth_year"), wd.get("death_year")
    flags = [f for f in str(wd.get("qa_flags") or "").split("|") if f]
    flags = [f for f in flags if f not in {"implausible_lifespan", "death_before_birth"}]
    precision = wd.get("date_precision") or "unknown"
    if birth is None and fallback_birth is not None and fallback_birth not in deprecated_years(claims, "P569"):
        birth = fallback_birth
        flags.append("birth_from_list")
        if death is None and precision == "unknown":
            precision = "year"
    if death is None and fallback_death is not None and fallback_death not in deprecated_years(claims, "P570"):
        death = fallback_death
        flags.append("death_from_list")
        precision = "year"
    flags.extend(lifespan_flags(birth, death))
    ordered = [f for f in QA_FLAGS if f in flags]
    return {
        "birth_year": birth,
        "death_year": death,
        "date_precision": precision,
        "qa_flags": pipe_join(ordered),
    }


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

    birth_iso, birth_year, birth_prec, birth_conflict, birth_imprecise = _parse_time_claim(
        claims, "P569"
    )
    death_iso, death_year, death_prec, death_conflict, death_imprecise = _parse_time_claim(
        claims, "P570"
    )
    qa_flags: list[str] = []
    for flag, present in (
        ("birth_rank_conflict", birth_conflict),
        ("death_rank_conflict", death_conflict),
        ("birth_imprecise", birth_imprecise),
        ("death_imprecise", death_imprecise),
    ):
        if present:
            qa_flags.append(flag)
    qa_flags.extend(lifespan_flags(birth_year, death_year))
    if "Q5" not in _claim_entity_ids(claims, "P31"):
        qa_flags.append("not_human")
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
    if not set(occupations) & COMPOSING_OCC:
        qa_flags.append("no_composer_occupation")

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
        "is_film_composer": "true" if "film_composer" in occupations else "false",
        "qa_flags": pipe_join(qa_flags),
        "notable_works_qids": pipe_join(notable_qids),
        "imslp_category_p839": imslp_category,
    }


def infer_scope_class(
    occupations: list[str],
    style_tags: list[str],
) -> tuple[str, str]:
    occ = set(occupations)
    is_composer = "composer" in occ or "classical_composer" in occ
    # Film work is a flag (is_film_composer), not a scope, for anyone who is
    # also a composer — otherwise Prokofiev/Gershwin vanish from classical_core.
    if occ & FILM_MEDIA_OCC and not is_composer:
        return "film_media", "occupations"
    if occ & POPULAR_OCC and not is_composer:
        return "popular", "occupations"
    if "crossover_popular" in style_tags:
        return "crossover", "style_tags"
    if is_composer or not occ:
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
