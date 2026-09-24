"""Shared constants and helpers for dump builders."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import unquote

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"

TOOL_VERSION = "3.0.0-dev"
SCHEMA_VERSION = 3

USER_AGENT = (
    f"eu-pd-composers/{TOOL_VERSION} "
    "(https://github.com/egorpol/eu-pd-composers; research dump builder)"
)
HEADERS = {"User-Agent": USER_AGENT}

WIKI_LIST_URL = (
    "https://en.wikipedia.org/wiki/List_of_20th-century_classical_composers"
)
WIKI_BASE = "https://en.wikipedia.org"
WIKI_API = "https://en.wikipedia.org/w/api.php"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
IMSLP_API = "https://imslp.org/api.php"
IMSLP_WIKI = "https://imslp.org/wiki/"
PAGEVIEWS_API = (
    "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
    "en.wikipedia.org/all-access/all-agents"
)

log = logging.getLogger("eu_pd")


def make_session() -> requests.Session:
    """HTTP session that ignores broken proxy env (common local failure mode)."""
    session = requests.Session()
    session.headers.update(HEADERS)
    session.trust_env = False
    return session


def pipe_join(values: Iterable[Any]) -> str:
    parts = [str(v).strip() for v in values if v is not None and str(v).strip()]
    # Deduplicate while preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for p in parts:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return "|".join(out)


def pipe_split(value: Any) -> list[str]:
    if value is None or (isinstance(value, float) and str(value) == "nan"):
        return []
    text = str(value).strip()
    if not text:
        return []
    return [p for p in text.split("|") if p]


def cache_path(namespace: str, key: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in key)[:180]
    folder = CACHE_DIR / namespace
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{safe}.json"


def cache_get(namespace: str, key: str) -> Optional[Any]:
    path = cache_path(namespace, key)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def cache_set(namespace: str, key: str, payload: Any) -> None:
    path = cache_path(namespace, key)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def request_json(
    session: requests.Session,
    url: str,
    *,
    params: Optional[dict] = None,
    timeout: float = 30,
    max_retries: int = 5,
    sleep_s: float = 0.05,
) -> dict:
    last_exc: Optional[Exception] = None
    for attempt in range(max_retries):
        try:
            response = session.get(url, params=params, timeout=timeout)
            if response.status_code == 429:
                wait = min(60.0, 2.0**attempt + 1.0)
                log.warning("429 from %s — sleep %.1fs", url, wait)
                time.sleep(wait)
                continue
            response.raise_for_status()
            time.sleep(sleep_s)
            return response.json()
        except (requests.RequestException, json.JSONDecodeError) as exc:
            last_exc = exc
            wait = min(30.0, 1.5**attempt)
            log.warning(
                "request failed %s (%s) — retry in %.1fs [%d/%d]",
                url,
                exc,
                wait,
                attempt + 1,
                max_retries,
            )
            time.sleep(wait)
    raise RuntimeError(f"Giving up on {url}: {last_exc}")


def wikipedia_title_from_url(url: Any) -> Optional[str]:
    if url is None or (isinstance(url, float) and str(url) == "nan"):
        return None
    text = str(url)
    if "/wiki/" not in text:
        return None
    title = text.split("/wiki/", 1)[-1].split("#", 1)[0].split("?", 1)[0]
    return unquote(title.replace("_", " "))


def imslp_category_to_url(category: str) -> str:
    """Build an IMSLP category URL from a Category:… title (underscores OK)."""
    title = category.strip()
    if not title.startswith("Category:"):
        title = f"Category:{title}"
    # MediaWiki accepts spaces or underscores in path.
    return IMSLP_WIKI + title.replace(" ", "_")


def strip_imslp_composer_suffix(work_title: str) -> str:
    """'Bassoon Sonata (Hindemith, Paul)' → 'Bassoon Sonata'."""
    if "(" in work_title and work_title.endswith(")"):
        return work_title[: work_title.rfind("(")].strip()
    return work_title.strip()
