"""IMSLP matching and work-list extraction (schema v3)."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import quote

import requests

from common import (
    IMSLP_API,
    cache_get,
    cache_set,
    imslp_category_to_url,
    pipe_join,
    request_json,
    strip_imslp_composer_suffix,
)

log = logging.getLogger("eu_pd.imslp")


def heuristic_category_title(display_name: str) -> str:
    parts = str(display_name).split()
    if len(parts) > 1:
        body = f"{parts[-1]},_{'_'.join(parts[:-1])}"
    else:
        body = parts[0]
    return f"Category:{body}"


def category_exists(category: str, session: requests.Session) -> bool:
    cache_key = category
    cached = cache_get("imslp_cat_exists", cache_key)
    if cached is not None:
        return bool(cached.get("exists"))

    title = category if category.startswith("Category:") else f"Category:{category}"
    data = request_json(
        session,
        IMSLP_API,
        params={
            "action": "query",
            "titles": title.replace("_", " "),
            "format": "json",
        },
        sleep_s=0.1,
    )
    pages = data.get("query", {}).get("pages", {})
    exists = bool(pages) and all(int(pid) > 0 for pid in pages.keys())
    # Missing pages use pageid -1
    for page in pages.values():
        if page.get("missing") is not None or page.get("pageid", -1) < 0:
            exists = False
            break
    cache_set("imslp_cat_exists", cache_key, {"exists": exists})
    return exists


def match_imslp(
    *,
    display_name: str,
    p839_category: Optional[str],
    session: requests.Session,
) -> dict[str, Any]:
    """Resolve IMSLP composer category with provenance."""
    if p839_category:
        cat = p839_category if p839_category.startswith("Category:") else f"Category:{p839_category}"
        cat = cat.replace(" ", "_")
        # Trust Wikidata P839; still verify the page exists.
        if category_exists(cat, session):
            return {
                "imslp_category": cat,
                "imslp_url": imslp_category_to_url(cat),
                "imslp_match_status": "matched",
                "imslp_match_method": "wikidata_p839",
            }
        return {
            "imslp_category": cat,
            "imslp_url": "",
            "imslp_match_status": "not_found",
            "imslp_match_method": "wikidata_p839",
        }

    heuristic = heuristic_category_title(display_name)
    if category_exists(heuristic, session):
        return {
            "imslp_category": heuristic,
            "imslp_url": imslp_category_to_url(heuristic),
            "imslp_match_status": "unverified_heuristic",
            "imslp_match_method": "exact_name",
        }

    return {
        "imslp_category": "",
        "imslp_url": "",
        "imslp_match_status": "not_found",
        "imslp_match_method": "exact_name",
    }


def list_category_works(
    category: str,
    session: requests.Session,
    *,
    use_cache: bool = True,
) -> list[dict[str, Any]]:
    """Return IMSLP work pages in a composer category (paginated)."""
    cat = category if category.startswith("Category:") else f"Category:{category}"
    cat_api = cat.replace("_", " ")
    if use_cache:
        cached = cache_get("imslp_works", cat)
        if cached is not None:
            return cached.get("works", [])

    works: list[dict[str, Any]] = []
    cont: Optional[str] = None
    while True:
        params: dict[str, Any] = {
            "action": "query",
            "list": "categorymembers",
            "cmtitle": cat_api,
            "cmlimit": 500,
            "cmtype": "page",
            "format": "json",
        }
        if cont:
            params["cmcontinue"] = cont
        data = request_json(session, IMSLP_API, params=params, timeout=60, sleep_s=0.15)
        for member in data.get("query", {}).get("categorymembers", []):
            title = member.get("title") or ""
            # Skip talk / non-main if any slip through
            if member.get("ns", 0) != 0:
                continue
            works.append(
                {
                    "pageid": member.get("pageid"),
                    "title": title,
                }
            )
        cont = (data.get("continue") or {}).get("cmcontinue")
        if not cont:
            # older MW
            cont = (
                (data.get("query-continue") or {})
                .get("categorymembers", {})
                .get("cmcontinue")
            )
        if not cont:
            break

    if use_cache:
        cache_set("imslp_works", cat, {"works": works})
    return works


def fetch_work_categories_batch(
    titles: list[str],
    session: requests.Session,
    *,
    use_cache: bool = True,
) -> dict[str, list[str]]:
    """Map work titles → IMSLP category titles (without Category: prefix)."""
    out: dict[str, list[str]] = {}
    pending: list[str] = []
    for title in titles:
        if use_cache:
            cached = cache_get("imslp_work_cats", title)
            if cached is not None:
                out[title] = cached.get("categories", [])
                continue
        pending.append(title)

    for i in range(0, len(pending), 20):
        batch = pending[i : i + 20]
        data = request_json(
            session,
            IMSLP_API,
            params={
                "action": "query",
                "titles": "|".join(batch),
                "prop": "categories",
                "cllimit": 100,
                "format": "json",
            },
            timeout=60,
            sleep_s=0.15,
        )
        pages = data.get("query", {}).get("pages", {})
        for page in pages.values():
            title = page.get("title")
            if not title:
                continue
            cats = []
            for c in page.get("categories", []):
                ctitle = c.get("title", "")
                if ctitle.startswith("Category:"):
                    ctitle = ctitle[len("Category:") :]
                cats.append(ctitle)
            out[title] = cats
            if use_cache:
                cache_set("imslp_work_cats", title, {"categories": cats})
    return out


def work_has_files(
    title: str,
    session: requests.Session,
    *,
    use_cache: bool = True,
) -> Optional[bool]:
    """Best-effort: parse work page HTML for File: links / pdf mentions."""
    if use_cache:
        cached = cache_get("imslp_has_files", title)
        if cached is not None:
            return cached.get("has_files")

    try:
        data = request_json(
            session,
            IMSLP_API,
            params={
                "action": "parse",
                "page": title,
                "prop": "text",
                "format": "json",
            },
            timeout=60,
            sleep_s=0.2,
        )
        html = (data.get("parse") or {}).get("text", {}).get("*", "")
        has = bool(re.search(r"/wiki/File:", html, re.I)) or (
            ".pdf" in html.lower() and "file" in html.lower()
        )
    except Exception as exc:  # noqa: BLE001 — keep dump moving
        log.warning("has_files failed for %s: %s", title, exc)
        has = None

    if use_cache and has is not None:
        cache_set("imslp_has_files", title, {"has_files": has})
    return has


# Keep force / form signal; drop publisher / score-hosting noise from IMSLP cats.
_FORM_ALLOW = {
    "Sonatas",
    "Symphonies",
    "Quartets",
    "Quintets",
    "Trios",
    "Duets",
    "Concertos",
    "Overtures",
    "Suites",
    "Variations",
    "Preludes",
    "Fugues",
    "Etudes",
    "Songs",
    "Lieder",
    "Masses",
    "Operas",
    "Ballets",
    "Cantatas",
    "Oratorios",
    "Pieces",
    "Waltzes",
    "Marches",
    "Nocturnes",
    "Rondos",
}


def filter_imslp_genre_categories(categories: list[str], composer_category: str) -> list[str]:
    self_name = composer_category.replace("Category:", "").replace("_", " ")
    keep: list[str] = []
    for g in categories:
        if g == self_name or g.replace(" ", "_") == self_name.replace(" ", "_"):
            continue
        if g.endswith("/Editor") or g.endswith("/Arranger"):
            continue
        if g.startswith(
            (
                "Scores",
                "Works first",
                "Pages with",
                "Submission",
                "Pieces based",
                "Intermediate ",
                "Pages using",
                "Wikipedia links",
            )
        ):
            continue
        if g.startswith("For ") or g in _FORM_ALLOW:
            keep.append(g)
    return keep


def works_rows_for_composer(
    *,
    composer_id: str,
    category: str,
    session: requests.Session,
    fetch_categories: bool = True,
    fetch_has_files: bool = False,
) -> list[dict[str, Any]]:
    members = list_category_works(category, session)
    titles = [m["title"] for m in members]
    cats_map: dict[str, list[str]] = {}
    if fetch_categories and titles:
        cats_map = fetch_work_categories_batch(titles, session)

    now = datetime.now(timezone.utc).isoformat()
    rows: list[dict[str, Any]] = []
    for member in members:
        title = member["title"]
        pageid = member.get("pageid")
        genres = filter_imslp_genre_categories(cats_map.get(title, []), category)
        has_files = ""
        if fetch_has_files:
            hf = work_has_files(title, session)
            has_files = "" if hf is None else ("true" if hf else "false")

        rows.append(
            {
                "work_id": title,
                "composer_id": composer_id,
                "imslp_pageid": pageid if pageid is not None else "",
                "imslp_work_url": f"https://imslp.org/wiki/{quote(title.replace(' ', '_'))}",
                "title": strip_imslp_composer_suffix(title),
                "imslp_genre_categories": pipe_join(genres),
                "has_files": has_files,
                "fetched_at": now,
            }
        )
    return rows
