#!/usr/bin/env python3
"""Build a versioned composer dump from Wikipedia (+ optional pageviews / IMSLP checks).

Reference implementation of the pipeline that produced data/*.tsv.
Does not overwrite existing dumps — writes dated files and prints manifest fields.
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
from heartbeat import Heartbeat

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"

WIKI_LIST_URL = (
    "https://en.wikipedia.org/wiki/List_of_20th-century_classical_composers"
)
WIKI_BASE = "https://en.wikipedia.org"
PAGEVIEWS_API = (
    "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
    "en.wikipedia.org/all-access/all-agents"
)

# Identify the client; Wikimedia / IMSLP expect a contactable UA.
USER_AGENT = (
    "eu-pd-composers/2.0 "
    "(https://github.com/egorpol/eu-pd-composers; research dump builder)"
)
HEADERS = {"User-Agent": USER_AGENT}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("build_dump")


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


def clean_years(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in ("Year of birth", "Year of death"):
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
        else:
            out[col] = pd.NA
    # EU life+70: work enters PD on 1 Jan of (death_year + 71) in most life+70 places.
    out["eu_pd_year"] = out["Year of death"] + 71
    return out


def fetch_pageviews(
    article_url: str,
    start: str,
    end: str,
    session: requests.Session,
    sleep_s: float = 0.05,
) -> float:
    if pd.isna(article_url) or "/wiki/" not in str(article_url):
        return float("nan")
    title = str(article_url).split("/wiki/", 1)[-1]
    url = f"{PAGEVIEWS_API}/{title}/monthly/{start}00/{end}00"
    try:
        response = session.get(url, headers=HEADERS, timeout=15)
        if response.status_code == 404:
            return 0.0
        response.raise_for_status()
        payload = response.json()
        total = sum(item.get("views", 0) for item in payload.get("items", []))
        time.sleep(sleep_s)
        return float(total)
    except (requests.RequestException, json.JSONDecodeError, KeyError) as exc:
        log.warning("pageviews failed for %s: %s", article_url, exc)
        return float("nan")


def imslp_category_url(name: str) -> str:
    parts = str(name).split()
    if len(parts) > 1:
        imslp_name = f"{parts[-1]},_{'_'.join(parts[:-1])}"
    else:
        imslp_name = parts[0]
    return f"https://imslp.org/wiki/Category:{quote(imslp_name)}"


def page_exists(url: str, session: requests.Session, sleep_s: float = 0.1) -> bool:
    try:
        time.sleep(sleep_s)
        response = session.get(url, headers=HEADERS, timeout=15)
        return response.status_code == 200
    except requests.RequestException:
        return False


def write_dump(df: pd.DataFrame, stem: str, dump_date: date) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / f"{stem}_{dump_date.isoformat()}.tsv"
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite existing dump: {path}")
    df.to_csv(path, sep="\t", index=False)
    return path


def _map_with_heartbeat(
    series: pd.Series,
    fn,
    name: str,
    interval_s: float,
) -> list:
    """Apply ``fn`` over ``series`` with tqdm + periodic heartbeat logs."""
    results: list = []
    with Heartbeat(name=name, total=len(series), interval_s=interval_s) as hb:
        for value in tqdm(series, desc=name):
            results.append(fn(value))
            hb.tick()
    return results


def build(args: argparse.Namespace) -> None:
    dump_date = date.fromisoformat(args.date) if args.date else date.today()
    session = requests.Session()

    log.info("Fetching Wikipedia list: %s", WIKI_LIST_URL)
    soup = get_soup(WIKI_LIST_URL, session)
    rows = parse_composer_table(soup)
    df = clean_years(pd.DataFrame(rows))
    log.info("Parsed %d composers", len(df))

    if args.limit:
        df = df.head(args.limit).copy()
        log.info("Limited to first %d rows (smoke test)", len(df))

    if args.pageviews:
        start, end = args.pageviews_start, args.pageviews_end
        log.info("Fetching pageviews %s → %s", start, end)
        df["Pageviews"] = _map_with_heartbeat(
            series=df["URL"],
            fn=lambda u: fetch_pageviews(u, start, end, session),
            name="pageviews",
            interval_s=args.heartbeat_interval,
        )
        df = df.sort_values(by="Pageviews", ascending=False, na_position="last")
    else:
        df["Pageviews"] = np.nan
        log.info("Skipping pageviews (--no-pageviews)")

    if args.imslp:
        log.info("Checking IMSLP category pages (heuristic Last,_First URLs)")
        df["IMSLP_URL"] = df["Name"].map(imslp_category_url)
        df["IMSLP_Exists"] = _map_with_heartbeat(
            series=df["IMSLP_URL"],
            fn=lambda u: page_exists(u, session),
            name="imslp",
            interval_s=args.heartbeat_interval,
        )
    else:
        df["IMSLP_URL"] = df["Name"].map(imslp_category_url)
        df["IMSLP_Exists"] = pd.NA
        log.info("Skipping IMSLP existence checks (--no-imslp); URLs still filled")

    # Stable column order for schema_version 2 (single TSV).
    column_order = [
        "Name",
        "Year of birth",
        "Year of death",
        "Nationality",
        "Notable 20th-century works",
        "Remarks",
        "URL",
        "eu_pd_year",
        "Pageviews",
        "IMSLP_URL",
        "IMSLP_Exists",
    ]
    df = df.reindex(columns=column_order)

    if args.dry_run:
        log.info("Dry run — not writing files. Columns: %s", list(df.columns))
        print(df.head(10).to_string(index=False))
        return

    composers_path = write_dump(df, "composers", dump_date)
    log.info("Wrote %s (%d rows, %d cols)", composers_path, len(df), len(df.columns))

    meta = {
        "dump_id": dump_date.isoformat(),
        "tool_version": "2.0.0-dev",
        "schema_version": 2,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "output_file": composers_path.name,
        "columns": column_order,
        "wikipedia_list_url": WIKI_LIST_URL,
        "pageviews": bool(args.pageviews),
        "pageviews_range": [args.pageviews_start, args.pageviews_end]
        if args.pageviews
        else None,
        "imslp_checks": bool(args.imslp),
        "row_count": int(len(df)),
        "eu_pd_year_note": "Year of death + 71 (calendar-year heuristic, not legal advice)",
    }
    meta_path = DATA_DIR / f"dump_meta_{dump_date.isoformat()}.json"
    if meta_path.exists():
        raise FileExistsError(f"Refusing to overwrite existing meta: {meta_path}")
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    log.info("Wrote %s — append a row to data/README.md for this dump", meta_path)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--date",
        help="Dump date stamp YYYY-MM-DD (default: today)",
    )
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
        help="Skip Wikimedia pageviews (much faster)",
    )
    p.add_argument(
        "--no-imslp",
        dest="imslp",
        action="store_false",
        help="Skip IMSLP existence checks",
    )
    p.add_argument("--pageviews-start", default="20250101")
    p.add_argument("--pageviews-end", default="20251231")
    p.add_argument(
        "--heartbeat-interval",
        type=float,
        default=30.0,
        help="Seconds between alive/progress log lines during long scrapes (default: 30)",
    )
    p.set_defaults(pageviews=True, imslp=True)
    return p.parse_args()


if __name__ == "__main__":
    build(parse_args())
