"""Shared constants and helpers for dump builders."""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import unquote

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"

TOOL_VERSION = "3.1.0-dev"
SCHEMA_VERSION = 3

# Product dumps use sequential revision ids (r001, r002, …). Calendar dates
# remain valid for historical files only.
_REV_ID_RE = re.compile(r"^r(\d+)$")
_CALENDAR_ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

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


def is_revision_dump_id(dump_id: str) -> bool:
    return bool(_REV_ID_RE.match(str(dump_id).strip()))


def is_calendar_dump_id(dump_id: str) -> bool:
    return bool(_CALENDAR_ID_RE.match(str(dump_id).strip()))


def dump_tsv_path(stem: str, dump_id: str) -> Path:
    return DATA_DIR / f"{stem}_{dump_id}.tsv"


def dump_meta_path(dump_id: str) -> Path:
    return DATA_DIR / f"dump_meta_{dump_id}.json"


def list_revision_numbers() -> list[int]:
    nums: list[int] = []
    for path in DATA_DIR.glob("composers_r*.tsv"):
        m = _REV_ID_RE.match(path.stem.removeprefix("composers_"))
        if m:
            nums.append(int(m.group(1)))
    return sorted(set(nums))


def next_revision_id() -> str:
    nums = list_revision_numbers()
    n = (max(nums) + 1) if nums else 1
    return f"r{n:03d}"


def format_viewer_dump_label(dump_id: str, created_at_utc: str | None = None) -> str:
    """Human label: `dump r002 · built 2026-09-25`."""
    built = ""
    if created_at_utc:
        try:
            dt = datetime.fromisoformat(str(created_at_utc).replace("Z", "+00:00"))
            built = dt.astimezone(timezone.utc).strftime("%Y-%m-%d")
        except ValueError:
            built = str(created_at_utc)[:10]
    if built:
        return f"dump {dump_id} · built {built}"
    return f"dump {dump_id}"


def write_tsv_dump(df: Any, stem: str, dump_id: str) -> Path:
    """Write `{stem}_{dump_id}.tsv`; refuse overwrite."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = dump_tsv_path(stem, dump_id)
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite existing dump: {path}")
    df.to_csv(path, sep="\t", index=False)
    return path


def write_dump_meta(dump_id: str, meta: dict[str, Any]) -> Path:
    path = dump_meta_path(dump_id)
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite existing meta: {path}")
    payload = dict(meta)
    payload.setdefault("dump_id", dump_id)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


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
