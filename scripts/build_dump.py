#!/usr/bin/env python3
"""Build schema-v3 composer + works dumps (Opus-aligned P0).

Pipeline:
  Wikipedia list → Wikidata enrichment → pageviews → IMSLP match + works list.

Does not overwrite existing dated dumps. LLM tagging is intentionally out of scope.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin

import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    DATA_DIR,
    SCHEMA_VERSION,
    TOOL_VERSION,
    WIKI_BASE,
    WIKI_LIST_URL,
    PAGEVIEWS_API,
    HEADERS,
    make_session,
    pipe_join,
    pipe_split,
    wikipedia_title_from_url,
)
from cohort import cohort_exclusion_reason  # noqa: E402
from heartbeat import Heartbeat  # noqa: E402
from force_family import map_work_row  # noqa: E402
from imslp import match_imslp, works_rows_for_composer  # noqa: E402
from wikidata_enrich import (  # noqa: E402
    enrich_from_entity,
    fetch_entities,
    iso_codes_for_countries,
    name_sort_from_display,
    name_sort_from_imslp_category,
    resolve_qids_for_titles,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("build_dump")

COMPOSER_COLUMNS = [
    "composer_id",
    "name_display",
    "name_sort",
    "name_aliases",
    "birth_year",
    "death_year",
    "date_precision",
    "wikipedia_url",
    "wikidata_url",
    "wikidata_qid",
    "nationality_label_wiki",
    "citizenship_qids",
    "citizenship_iso",
    "occupations",
    "scope_class",
    "scope_class_src",
    "eu_pd_year",
    "eu_pd_status",
    "years_until_eu_pd",
    "style_tags",
    "style_tags_src",
    "notable_works",
    "notable_works_qids",
    "imslp_category",
    "imslp_url",
    "imslp_match_status",
    "imslp_match_method",
    "imslp_works_count",
    "pageviews_enwiki",
    "pageviews_window",
    "schema_version",
    "dump_date",
    "legacy_notable_works_raw",
    "legacy_remarks_raw",
]

WORK_COLUMNS = [
    "work_id",
    "composer_id",
    "imslp_pageid",
    "imslp_work_url",
    "title",
    "imslp_genre_categories",
    "force_family",
    "force_family_src",
    "genre_form",
    "has_files",
    "fetched_at",
]


def get_soup(url: str, session: requests.Session, timeout: float = 20) -> BeautifulSoup:
    response = session.get(url, headers=HEADERS, timeout=timeout)
    response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser")


def parse_composer_table(soup: BeautifulSoup) -> list[dict[str, Any]]:
    table = soup.find("table", {"class": "wikitable sortable"})
    if table is None:
        raise RuntimeError("Could not find wikitable sortable on Wikipedia list page")

    rows = table.find_all("tr")
    headers = [th.get_text(strip=True) for th in rows[0].find_all("th")]
    headers = headers + ["URL"]
    expected = len(headers) - 1

    data: list[dict[str, Any]] = []
    for row in rows[1:]:
        cells = row.find_all("td")
        if not cells:
            continue
        values = [td.get_text(strip=True) for td in cells]
        if len(values) < expected:
            values.extend([""] * (expected - len(values)))
        elif len(values) > expected:
            values = values[:expected]
        link = cells[0].find("a")
        href = link.get("href") if link else None
        url = urljoin(WIKI_BASE, href) if href else None
        data.append(dict(zip(headers, values + [url])))
    return data


def clean_notable_titles(raw: Any) -> str:
    """Split glued wiki notable-works text into pipe-separated titles (best effort)."""
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return ""
    text = str(raw).strip()
    if not text:
        return ""
    # Normalize common separators first.
    text = text.replace(";", "|")
    # Fix glued "No. 2andNo. 3" / "Stone;Ecce" already handled; also "and" between caps.
    text = text.replace("andNo.", "|No.")
    parts = []
    for chunk in text.split("|"):
        chunk = chunk.strip(" .;")
        if chunk:
            parts.append(chunk)
    return pipe_join(parts)


def fetch_pageviews(
    article_url: str,
    start: str,
    end: str,
    session: requests.Session,
    sleep_s: float = 0.1,
    max_retries: int = 5,
) -> float:
    if pd.isna(article_url) or "/wiki/" not in str(article_url):
        return float("nan")
    title = str(article_url).split("/wiki/", 1)[-1]
    title_enc = quote(title, safe="()_,%-")
    url = f"{PAGEVIEWS_API}/{title_enc}/monthly/{start}00/{end}00"

    for attempt in range(max_retries):
        try:
            response = session.get(url, headers=HEADERS, timeout=20)
            if response.status_code == 404:
                time.sleep(sleep_s)
                return 0.0
            if response.status_code == 429:
                wait = min(60.0, 2.0**attempt + 1.0)
                log.warning("pageviews 429 — sleep %.1fs", wait)
                time.sleep(wait)
                continue
            response.raise_for_status()
            payload = response.json()
            total = sum(item.get("views", 0) for item in payload.get("items", []))
            time.sleep(sleep_s)
            return float(total)
        except (requests.RequestException, json.JSONDecodeError, KeyError) as exc:
            wait = min(30.0, 1.5**attempt)
            log.warning("pageviews failed: %s — retry %.1fs", exc, wait)
            time.sleep(wait)
    return float("nan")


def eu_pd_fields(death_year: Any, dump_year: int) -> dict[str, Any]:
    if death_year is None or (isinstance(death_year, float) and pd.isna(death_year)):
        return {
            "eu_pd_year": "",
            "eu_pd_status": "unknown_death",
            "years_until_eu_pd": "",
        }
    try:
        dy = int(death_year)
    except (TypeError, ValueError):
        return {
            "eu_pd_year": "",
            "eu_pd_status": "unknown_death",
            "years_until_eu_pd": "",
        }
    eu_year = dy + 71
    if eu_year <= dump_year:
        status = "pd"
        years_until = 0
    else:
        status = "not_pd"
        years_until = eu_year - dump_year
    return {
        "eu_pd_year": eu_year,
        "eu_pd_status": status,
        "years_until_eu_pd": years_until,
    }


def write_dump(df: pd.DataFrame, stem: str, dump_date: date) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / f"{stem}_{dump_date.isoformat()}.tsv"
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite existing dump: {path}")
    df.to_csv(path, sep="\t", index=False)
    return path


def _map_with_heartbeat(series: pd.Series, fn, name: str, interval_s: float) -> list:
    results: list = []
    with Heartbeat(name=name, total=len(series), interval_s=interval_s) as hb:
        for value in tqdm(series, desc=name):
            results.append(fn(value))
            hb.tick()
    return results


def build(args: argparse.Namespace) -> None:
    dump_date = date.fromisoformat(args.date) if args.date else date.today()
    dump_year = dump_date.year
    session = make_session()
    pageviews_window = f"{args.pageviews_start[:4]}-{args.pageviews_start[4:6]}..{args.pageviews_end[:4]}-{args.pageviews_end[4:6]}"

    log.info("Fetching Wikipedia list: %s", WIKI_LIST_URL)
    soup = get_soup(WIKI_LIST_URL, session)
    raw_rows = parse_composer_table(soup)
    raw_df = pd.DataFrame(raw_rows)
    log.info("Parsed %d composers from Wikipedia list", len(raw_df))

    if args.limit:
        raw_df = raw_df.head(args.limit).copy()
        log.info("Limited to first %d rows (smoke test)", len(raw_df))

    # --- Wikidata QIDs ---
    titles = [
        wikipedia_title_from_url(u) or str(n)
        for n, u in zip(raw_df["Name"], raw_df["URL"])
    ]
    log.info("Resolving Wikidata QIDs for %d titles", len(titles))
    title_to_qid = resolve_qids_for_titles(titles, session, use_cache=not args.no_cache)
    qids = [title_to_qid.get(t) for t in titles]
    unique_qids = sorted({q for q in qids if q})
    log.info("Fetching %d Wikidata entities", len(unique_qids))
    entities = fetch_entities(unique_qids, session, use_cache=not args.no_cache)

    # Citizenship ISO mapping
    all_citizenships: list[str] = []
    for qid in unique_qids:
        ent = entities.get(qid)
        if not ent:
            continue
        enriched = enrich_from_entity(ent)
        all_citizenships.extend(pipe_split(enriched.get("citizenship_qids")))
    cit_iso_map = iso_codes_for_countries(
        sorted(set(all_citizenships)), session, use_cache=not args.no_cache
    )

    # --- Pageviews ---
    if args.pageviews:
        log.info("Fetching pageviews %s → %s", args.pageviews_start, args.pageviews_end)
        pageviews = _map_with_heartbeat(
            series=raw_df["URL"],
            fn=lambda u: fetch_pageviews(
                u, args.pageviews_start, args.pageviews_end, session
            ),
            name="pageviews",
            interval_s=args.heartbeat_interval,
        )
    else:
        pageviews = [float("nan")] * len(raw_df)
        log.info("Skipping pageviews (--no-pageviews)")

    # --- Build composer rows + IMSLP works ---
    composer_rows: list[dict[str, Any]] = []
    work_rows: list[dict[str, Any]] = []
    seen_composer_ids: dict[str, int] = {}  # composer_id → index in composer_rows
    imslp_fetched: set[tuple[str, str]] = set()  # (qid_or_id, category)

    log.info(
        "Enriching rows + IMSLP match%s",
        " + works" if args.imslp_works else " (works skipped)",
    )
    raw_records = list(raw_df.to_dict(orient="records"))
    with Heartbeat(
        name="enrich+imslp", total=len(raw_records), interval_s=args.heartbeat_interval
    ) as hb:
        for i, raw in enumerate(tqdm(raw_records, desc="enrich")):
            name_display = str(raw.get("Name") or "")
            wiki_url = raw.get("URL")
            title = titles[i]
            qid = qids[i]
            ent = entities.get(qid) if qid else None
            wd = enrich_from_entity(ent) if ent else {}

            # Prefer Wikidata years; fall back to Wikipedia list cells.
            list_birth = pd.to_numeric(raw.get("Year of birth"), errors="coerce")
            list_death = pd.to_numeric(raw.get("Year of death"), errors="coerce")
            list_birth_i = None if pd.isna(list_birth) else int(list_birth)
            list_death_i = None if pd.isna(list_death) else int(list_death)

            birth_year = wd.get("birth_year")
            death_year = wd.get("death_year")
            if birth_year is None:
                birth_year = list_birth_i
            if death_year is None:
                death_year = list_death_i

            skip_reason = cohort_exclusion_reason(
                birth_year=birth_year,
                death_year=death_year,
                list_birth_year=list_birth_i,
                list_death_year=list_death_i,
            )
            if skip_reason:
                log.warning(
                    "Skipping %s — cohort gate: %s (WD/list years %s–%s / list %s–%s)",
                    name_display,
                    skip_reason,
                    birth_year,
                    death_year,
                    list_birth_i,
                    list_death_i,
                )
                hb.tick()
                continue

            composer_id = qid or f"wiki:{quote(title.replace(' ', '_'))}"

            # One row per Wikidata QID: fold later list aliases into the first row.
            if composer_id in seen_composer_ids:
                prev = composer_rows[seen_composer_ids[composer_id]]
                aliases = set(pipe_split(prev.get("name_aliases") or ""))
                if name_display and name_display != prev.get("name_display"):
                    aliases.add(name_display)
                for a in pipe_split(wd.get("name_aliases") or ""):
                    aliases.add(a)
                prev["name_aliases"] = pipe_join(
                    sorted(a for a in aliases if a and a != prev.get("name_display"))
                )
                if birth_year is not None:
                    pb = prev.get("birth_year")
                    if pb == "" or pb is None:
                        prev["birth_year"] = birth_year
                    else:
                        try:
                            prev["birth_year"] = min(int(pb), int(birth_year))
                        except (TypeError, ValueError):
                            pass
                if death_year is not None:
                    pd_ = prev.get("death_year")
                    if pd_ == "" or pd_ is None:
                        prev["death_year"] = death_year
                    else:
                        try:
                            prev["death_year"] = max(int(pd_), int(death_year))
                        except (TypeError, ValueError):
                            pass
                    prev.update(eu_pd_fields(prev.get("death_year") or None, dump_year))
                wiki_u = str(wiki_url or "")
                if "and_" in wiki_u.lower() or "_and_" in wiki_u.lower():
                    prev["wikipedia_url"] = wiki_u
                    prev["name_display"] = name_display or prev["name_display"]
                pv = pageviews[i]
                if pv is not None and not (isinstance(pv, float) and np.isnan(pv)):
                    try:
                        prev_pv = int(prev.get("pageviews_enwiki") or 0)
                    except (TypeError, ValueError):
                        prev_pv = 0
                    prev["pageviews_enwiki"] = max(prev_pv, int(pv))
                hb.tick()
                continue

            cit_qids = pipe_split(wd.get("citizenship_qids"))
            cit_iso = pipe_join(
                cit_iso_map.get(c) for c in cit_qids if cit_iso_map.get(c)
            )

            pd_fields = eu_pd_fields(death_year, dump_year)

            # IMSLP match
            if args.imslp:
                match = match_imslp(
                    display_name=name_display,
                    p839_category=wd.get("imslp_category_p839"),
                    session=session,
                )
            else:
                match = {
                    "imslp_category": "",
                    "imslp_url": "",
                    "imslp_match_status": "not_checked",
                    "imslp_match_method": "",
                }

            works_count = 0
            cat = match.get("imslp_category") or ""
            fetch_key = (composer_id, cat)
            if (
                args.imslp
                and args.imslp_works
                and match.get("imslp_match_status")
                in {"matched", "unverified_heuristic"}
                and cat
                and fetch_key not in imslp_fetched
            ):
                wrows = works_rows_for_composer(
                    composer_id=composer_id,
                    category=cat,
                    session=session,
                    fetch_categories=args.work_categories,
                    fetch_has_files=args.work_files,
                )
                for wr in wrows:
                    wr.update(
                        map_work_row(
                            wr.get("imslp_genre_categories") or "",
                            wr.get("title") or "",
                        )
                    )
                work_rows.extend(wrows)
                works_count = len(wrows)
                imslp_fetched.add(fetch_key)

            name_sort = (
                name_sort_from_imslp_category(match.get("imslp_category"))
                or name_sort_from_imslp_category(wd.get("imslp_category_p839"))
                or name_sort_from_display(name_display)
            )

            legacy_notable = raw.get("Notable 20th-century works") or ""
            notable_clean = clean_notable_titles(legacy_notable)

            pv = pageviews[i]
            pv_out: Any = ""
            if pv is not None and not (isinstance(pv, float) and np.isnan(pv)):
                pv_out = int(pv)

            seen_composer_ids[composer_id] = len(composer_rows)
            composer_rows.append(
                {
                    "composer_id": composer_id,
                    "name_display": name_display,
                    "name_sort": name_sort,
                    "name_aliases": wd.get("name_aliases") or "",
                    "birth_year": birth_year if birth_year is not None else "",
                    "death_year": death_year if death_year is not None else "",
                    "date_precision": wd.get("date_precision") or "unknown",
                    "wikipedia_url": wiki_url or "",
                    "wikidata_url": f"https://www.wikidata.org/wiki/{qid}" if qid else "",
                    "wikidata_qid": qid or "",
                    "nationality_label_wiki": raw.get("Nationality") or "",
                    "citizenship_qids": wd.get("citizenship_qids") or "",
                    "citizenship_iso": cit_iso,
                    "occupations": wd.get("occupations") or "",
                    "scope_class": wd.get("scope_class") or "classical_core",
                    "scope_class_src": wd.get("scope_class_src") or "default",
                    "eu_pd_year": pd_fields["eu_pd_year"],
                    "eu_pd_status": pd_fields["eu_pd_status"],
                    "years_until_eu_pd": pd_fields["years_until_eu_pd"],
                    "style_tags": wd.get("style_tags") or "",
                    "style_tags_src": wd.get("style_tags_src") or "",
                    "notable_works": notable_clean,
                    "notable_works_qids": wd.get("notable_works_qids") or "",
                    "imslp_category": match.get("imslp_category") or "",
                    "imslp_url": match.get("imslp_url") or "",
                    "imslp_match_status": match.get("imslp_match_status") or "",
                    "imslp_match_method": match.get("imslp_match_method") or "",
                    "imslp_works_count": works_count if args.imslp_works else "",
                    "pageviews_enwiki": pv_out,
                    "pageviews_window": pageviews_window if args.pageviews else "",
                    "schema_version": SCHEMA_VERSION,
                    "dump_date": dump_date.isoformat(),
                    "legacy_notable_works_raw": legacy_notable,
                    "legacy_remarks_raw": raw.get("Remarks") or "",
                }
            )
            hb.tick()

    composers_df = pd.DataFrame(composer_rows).reindex(columns=COMPOSER_COLUMNS)
    if args.pageviews:
        composers_df = composers_df.sort_values(
            by="pageviews_enwiki",
            ascending=False,
            na_position="last",
            key=lambda s: pd.to_numeric(s, errors="coerce"),
        )

    works_df = pd.DataFrame(work_rows)
    if not works_df.empty:
        works_df = works_df.reindex(columns=WORK_COLUMNS)
    else:
        works_df = pd.DataFrame(columns=WORK_COLUMNS)

    if args.dry_run:
        log.info(
            "Dry run — composers=%d works=%d cols=%s",
            len(composers_df),
            len(works_df),
            list(composers_df.columns),
        )
        print(composers_df.head(10).to_string(index=False))
        if not works_df.empty:
            print("\n--- works sample ---")
            print(works_df.head(15).to_string(index=False))
        return

    composers_path = write_dump(composers_df, "composers", dump_date)
    log.info(
        "Wrote %s (%d rows, %d cols)",
        composers_path,
        len(composers_df),
        len(composers_df.columns),
    )

    works_path = None
    if args.imslp_works:
        works_path = write_dump(works_df, "works", dump_date)
        log.info(
            "Wrote %s (%d rows, %d cols)",
            works_path,
            len(works_df),
            len(works_df.columns),
        )

    matched = int((composers_df["imslp_match_status"] == "matched").sum())
    heuristic = int(
        (composers_df["imslp_match_status"] == "unverified_heuristic").sum()
    )

    meta = {
        "dump_id": dump_date.isoformat(),
        "tool_version": TOOL_VERSION,
        "schema_version": SCHEMA_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "output_files": {
            "composers": composers_path.name,
            "works": works_path.name if works_path else None,
        },
        "composer_columns": COMPOSER_COLUMNS,
        "work_columns": WORK_COLUMNS,
        "wikipedia_list_url": WIKI_LIST_URL,
        "pageviews": bool(args.pageviews),
        "pageviews_range": [args.pageviews_start, args.pageviews_end]
        if args.pageviews
        else None,
        "imslp_match": bool(args.imslp),
        "imslp_works": bool(args.imslp_works),
        "work_categories": bool(args.work_categories),
        "work_files": bool(args.work_files),
        "row_counts": {
            "composers": int(len(composers_df)),
            "works": int(len(works_df)),
            "imslp_matched_p839": matched,
            "imslp_unverified_heuristic": heuristic,
        },
        "limit": args.limit,
        "eu_pd_year_note": (
            "death_year + 71 calendar heuristic (Directive-style life+70); "
            "not legal advice"
        ),
        "list_encoding": "pipe-separated (|); empty string = unknown/unqueried",
        "notes": [
            "force_family / genre_form mapping deferred (Phase C)",
            "style_tags currently Wikidata P135/P136 mapped only",
            "legacy_* columns transitional; prefer structured fields",
        ],
    }
    meta_path = DATA_DIR / f"dump_meta_{dump_date.isoformat()}.json"
    if meta_path.exists():
        raise FileExistsError(f"Refusing to overwrite existing meta: {meta_path}")
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    log.info("Wrote %s", meta_path)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--date", help="Dump date stamp YYYY-MM-DD (default: today)")
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only process first N composers (smoke test)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Build in memory and print a sample; do not write files",
    )
    p.add_argument(
        "--no-pageviews",
        dest="pageviews",
        action="store_false",
        help="Skip Wikimedia pageviews",
    )
    p.add_argument(
        "--no-imslp",
        dest="imslp",
        action="store_false",
        help="Skip IMSLP matching",
    )
    p.add_argument(
        "--no-imslp-works",
        dest="imslp_works",
        action="store_false",
        help="Match IMSLP categories but do not list works",
    )
    p.add_argument(
        "--no-work-categories",
        dest="work_categories",
        action="store_false",
        help="Skip per-work IMSLP category fetch (faster)",
    )
    p.add_argument(
        "--work-files",
        action="store_true",
        help="Parse each work page for has_files (slow; off by default)",
    )
    p.add_argument(
        "--no-cache",
        action="store_true",
        help="Ignore data/cache/ and refetch everything",
    )
    p.add_argument("--pageviews-start", default="20250101")
    p.add_argument("--pageviews-end", default="20251231")
    p.add_argument(
        "--heartbeat-interval",
        type=float,
        default=30.0,
        help="Seconds between alive/progress log lines (default: 30)",
    )
    p.set_defaults(
        pageviews=True,
        imslp=True,
        imslp_works=True,
        work_categories=True,
    )
    return p.parse_args()


if __name__ == "__main__":
    build(parse_args())
