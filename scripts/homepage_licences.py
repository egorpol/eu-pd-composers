#!/usr/bin/env python3
"""Scan composer sites for open-licence score publishing (P856 homepages or search hits).

Stage-1 rule-based sweep: start page plus one hop to score/catalogue/licence pages.
Writes a review TSV; recall with evidence matters more than precision.

  python scripts/homepage_licences.py --dump r019 --list-only
  python scripts/homepage_licences.py --dump r019 --limit 20
  python scripts/homepage_licences.py --dump r019 --targets build/open_licence/search/search_results_r019.tsv
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import re
import sys
import threading
import time
import unicodedata
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional
from urllib.parse import urldefrag, urljoin, urlparse
from urllib.robotparser import RobotFileParser

import pandas as pd
from bs4 import BeautifulSoup, UnicodeDammit

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    REPO_ROOT,
    USER_AGENT,
    cache_get,
    cache_set,
    dump_tsv_path,
    make_session,
)
from heartbeat import Heartbeat  # noqa: E402
from licences import spdx_from_cc_text, spdx_from_cc_url  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("homepage_licences")

PAGES_NAMESPACE = "homepage_pages"
ROBOTS_NAMESPACE = "homepage_robots"
MAX_HTML_BYTES = 3 * 1024 * 1024
FETCH_TIMEOUT_S = 20
MAX_REDIRECTS = 5
HTML_TYPES = ("text/html", "application/xhtml+xml")
REDIRECT_STATUS = frozenset({301, 302, 303, 307, 308})
MAX_RESULTS_PER_HOST = 3

# Two-level public suffixes for registrable-domain same-site checks (no extra deps).
TWO_LEVEL_SUFFIXES: frozenset[str] = frozenset(
    {
        "co.uk",
        "org.uk",
        "ac.uk",
        "gov.uk",
        "com.au",
        "net.au",
        "org.au",
        "edu.au",
        "co.nz",
        "co.jp",
        "ne.jp",
        "or.jp",
        "ac.jp",
        "com.br",
        "com.ar",
        "com.mx",
        "co.za",
        "com.tr",
        "com.pl",
        "co.il",
        "co.kr",
        "com.cn",
        "com.tw",
        "com.hk",
        "com.sg",
    }
)

# Host suffixes skipped in --targets mode (platform / catalogue noise).
DENIED_HOST_SUFFIXES: tuple[str, ...] = (
    "wikipedia.org",
    "wikidata.org",
    "wikimedia.org",
    "imslp.org",
    "youtube.com",
    "youtu.be",
    "spotify.com",
    "apple.com",
    "facebook.com",
    "instagram.com",
    "twitter.com",
    "x.com",
    "tiktok.com",
    "linkedin.com",
    "discogs.com",
    "allmusic.com",
    "soundcloud.com",
    "bandcamp.com",
    "sheetmusicplus.com",
    "jwpepper.com",
    "prestomusic.com",
    "last.fm",
    "genius.com",
)
# Bare second-level names denied when any host label matches (amazon.com.be, ebay.com.my, …).
# Label equality only — amazonaws.com stays allowed.
DENIED_SLD_ANY_TLD: frozenset[str] = frozenset({"amazon", "ebay"})

# Shared-hosting platforms: tenant.platform is the registrable unit (not platform alone).
SHARED_HOSTING_SUFFIXES: frozenset[str] = frozenset(
    {
        "wordpress.com",
        "blogspot.com",
        "wixsite.com",
        "webs.com",
        "weebly.com",
        "github.io",
        "tumblr.com",
        "jimdofree.com",
        "jimdosite.com",
        "squarespace.com",
        "netlify.app",
        "neocities.org",
        "bandcamp.com",
    }
)

# Multilingual score / catalogue / download / licence path & anchor cues (stage 1).
SCORE_KEYWORDS: tuple[str, ...] = (
    "score",
    "scores",
    "sheet music",
    "sheetmusic",
    "works",
    "work",
    "compositions",
    "composition",
    "catalogue",
    "catalog",
    "download",
    "downloads",
    "pdf",
    "license",
    "licence",
    "werke",
    "werk",
    "noten",
    "partitur",
    "partituren",
    "katalog",
    "lizenz",
    "œuvres",
    "oeuvres",
    "œuvre",
    "oeuvre",
    "partitions",
    "partition",
    "opere",
    "opera",
    "partiture",
    "partitura",
    "catalogo",
    "licenza",
    "obras",
    "obra",
    "partituras",
    "catálogo",
    "descargas",
    "descarga",
    "licencia",
    "licença",
    "werken",
    "partituur",
    "catalogus",
    "licentie",
    "utwory",
    "utwór",
    "nuty",
    "partytura",
    "licencja",
    "skladby",
    "skladba",
    "noty",
    "verk",
    "verker",
    "værker",
    "noter",
    "licens",
    "lisens",
    "teokset",
    "teos",
    "nuotit",
    "nuotti",
    "luettelo",
    "lisenssi",
    "művek",
    "muvek",
    "mű",
    "kották",
    "kottak",
    "kotta",
    "katalógus",
    "katalogus",
    "licenc",
)

_LICENCE_ANCHOR_RE = re.compile(
    r"\b(licen[cs]e|copyright|lizenz|licenza|licencia|licença|licentie|"
    r"licencja|lisenssi|licens|lisens|licenc)\b",
    re.I,
)
# Hyphen or whitespace between CC and BY (CC-BY… / CC BY…).
_CC_TEXT_TRIGGER_RE = re.compile(
    r"creative\s+commons|\bcc[\s-]+by\b|\bcc0\b|public\s+domain\s+mark",
    re.I,
)
_MUSICXML_HINT_RE = re.compile(r"musicxml|\bscore\b|partitur|partition|partitura", re.I)
_CHARSET_RE = re.compile(r"charset\s*=\s*([^\s;]+)", re.I)

SCORE_EXTENSIONS: dict[str, tuple[str, ...]] = {
    "pdf": (".pdf",),
    "midi": (".mid", ".midi"),
    "musicxml": (".musicxml", ".mxl"),
    "mei": (".mei",),
    "lilypond": (".ly",),
    "musescore": (".mscz", ".mscx"),
    "other_notation": (".sib", ".mus", ".dorico", ".abc"),
}

OUTPUT_COLUMNS = [
    "composer_id",
    "name_display",
    "birth_year",
    "death_year",
    "eu_pd_status",
    "homepage_url",
    "final_url",
    "fetch_status",
    "pages_fetched",
    "licences",
    "licence_evidence",
    "n_pdf",
    "n_midi",
    "n_musicxml",
    "n_mei",
    "n_lilypond",
    "n_musescore",
    "n_other_notation",
    "score_pages",
    "hop_urls",
    "candidate",
    "source",
    "search_rank",
    "search_title",
    "name_on_page",
]


@dataclass
class RawResponse:
    """Minimal HTTP response for the injectable fetch layer (single hop, no auto-redirect)."""

    status_code: int
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    content: bytes = b""
    error: Optional[str] = None

    def __post_init__(self) -> None:
        # Case-insensitive header names (covers session responses and test fakes).
        self.headers = {str(k).lower(): v for k, v in (self.headers or {}).items()}


GetFn = Callable[[str], RawResponse]


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _cache_key(url: str) -> str:
    """Collision-resistant cache key; original URL is kept inside each record."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def cache_get_url(namespace: str, url: str) -> Optional[Any]:
    """Load a URL-keyed cache record; miss if absent or stored url mismatches."""
    rec = cache_get(namespace, _cache_key(url))
    if rec is None:
        return None
    if rec.get("url") != url:
        return None
    return rec


def cache_set_url(namespace: str, url: str, payload: dict[str, Any]) -> None:
    out = dict(payload)
    out["url"] = url
    cache_set(namespace, _cache_key(url), out)


def _strip_www(host: str) -> str:
    h = (host or "").lower().rstrip(".")
    if h.startswith("www."):
        h = h[4:]
    return h


def request_host(url: str) -> str:
    """Full hostname for robots.txt URLs and per-host request scheduling."""
    return (urlparse(url).hostname or "").lower().rstrip(".")


def host_key(url: str) -> str:
    """Grouping key for workers (full hostname; www kept distinct for pacing)."""
    return request_host(url)


def registrable_domain(host: Optional[str]) -> str:
    """eTLD+1 approximation, with shared-hosting tenants kept distinct."""
    h = _strip_www(host or "")
    if not h:
        return ""
    parts = h.split(".")
    # blogspot.<cc> / blogspot.com.<cc> (any country): tenant.blogspot…
    if "blogspot" in parts:
        idx = parts.index("blogspot")
        if idx >= 1:
            return ".".join(parts[idx - 1 :])
        return ".".join(parts[idx:])
    for suffix in SHARED_HOSTING_SUFFIXES:
        if h == suffix:
            return h
        if h.endswith("." + suffix):
            rest = h[: -(len(suffix) + 1)]
            tenant = rest.split(".")[-1] if rest else ""
            return f"{tenant}.{suffix}" if tenant else suffix
    if len(parts) < 2:
        return h
    last_two = ".".join(parts[-2:])
    if last_two in TWO_LEVEL_SUFFIXES and len(parts) >= 3:
        return ".".join(parts[-3:])
    return last_two


def same_site(base_url: str, other_url: str) -> bool:
    a = registrable_domain(urlparse(base_url).hostname)
    b = registrable_domain(urlparse(other_url).hostname)
    return bool(a and a == b)


def denied_host_reason(host: Optional[str]) -> Optional[str]:
    """Return deny-list reason key, or None if the host is allowed."""
    if not host:
        return "empty_host"
    h = host.lower().rstrip(".")
    bare = _strip_www(h)
    for suffix in DENIED_HOST_SUFFIXES:
        if bare == suffix or bare.endswith("." + suffix):
            return suffix
    for label in bare.split("."):
        if label in DENIED_SLD_ANY_TLD:
            return label
    return None


def site_root(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}/"


def _content_type(headers: dict[str, str]) -> str:
    raw = headers.get("content-type") or ""
    return raw.split(";", 1)[0].strip().lower()


def _header_charset(headers: dict[str, str]) -> Optional[str]:
    raw = headers.get("content-type") or ""
    m = _CHARSET_RE.search(raw)
    if not m:
        return None
    return m.group(1).strip().strip("\"'")


def _is_html_type(content_type: str) -> bool:
    return content_type in HTML_TYPES or content_type.startswith("text/html")


def looks_like_html(content: bytes) -> bool:
    """Sniff HTML when Content-Type is missing (first 1 KiB after BOM/whitespace)."""
    sample = content[:1024]
    for bom in (b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff"):
        if sample.startswith(bom):
            sample = sample[len(bom) :]
            break
    sample = sample.lstrip()
    lower = sample.lower()
    if lower.startswith(b"<!doctype html") or lower.startswith(b"<html"):
        return True
    return b"<html" in lower or b"<head" in lower


def decode_html(content: bytes, headers: Optional[dict[str, str]] = None) -> str:
    """Decode HTML using HTTP charset and/or meta charset (UnicodeDammit)."""
    if not content:
        return ""
    from_enc = _header_charset(headers or {})
    user_encodings = [from_enc] if from_enc else None
    dammit = UnicodeDammit(
        content[:MAX_HTML_BYTES],
        user_encodings=user_encodings,
        is_html=True,
    )
    if dammit.unicode_markup is not None:
        return dammit.unicode_markup
    return content[:MAX_HTML_BYTES].decode("utf-8", errors="replace")


def _clean_url(url: str) -> str:
    return urldefrag(url.strip())[0]


def _should_refetch(record: dict[str, Any], retry_errors: bool) -> bool:
    """Whether a cached record should be ignored when --retry-errors is set."""
    if not retry_errors:
        return False
    err = str(record.get("error") or "")
    status = int(record.get("status_code") or 0)
    if err.startswith("error:") and err != "error:not_cached":
        return True
    if status == 429 or status >= 500:
        return True
    return False


def _path_lower(url: str) -> str:
    return (urlparse(url).path or "").lower()


def _fold_accents(text: str) -> str:
    s = unicodedata.normalize("NFD", (text or "").lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def composer_surname(name_display: str) -> str:
    parts = str(name_display or "").split()
    return _fold_accents(parts[-1]) if parts else ""


def _keyword_score(path: str, text: str) -> int:
    blob = f"{path} {text}".lower()
    return sum(1 for kw in SCORE_KEYWORDS if kw in blob)


def _rel_list(rel: Any) -> list[str]:
    if not rel:
        return []
    if isinstance(rel, str):
        return rel.split()
    return list(rel)


def _is_licence_nav(text: str, rel: Iterable[str]) -> bool:
    rels = {r.lower() for r in rel}
    if "license" in rels or "licence" in rels or "copyright" in rels:
        return True
    return bool(_LICENCE_ANCHOR_RE.search(text or ""))


def extract_p856_urls(entity: dict[str, Any]) -> list[str]:
    """Non-deprecated P856 string values, order preserved, deduped."""
    out: list[str] = []
    seen: set[str] = set()
    for claim in (entity.get("claims") or {}).get("P856") or []:
        if claim.get("rank") == "deprecated":
            continue
        snak = claim.get("mainsnak") or {}
        dv = snak.get("datavalue") or {}
        val = dv.get("value")
        if not isinstance(val, str) or not val.strip():
            continue
        url = val.strip()
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def collect_targets(
    composers: pd.DataFrame,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Build P856 (composer, homepage_url) jobs and input-count stats."""
    non_pd = composers[composers["eu_pd_status"].astype(str) != "pd"].copy()
    stats = {
        "composers_considered": int(len(non_pd)),
        "with_cached_entity": 0,
        "with_p856": 0,
        "urls": 0,
        "skipped_no_entity": 0,
    }
    jobs: list[dict[str, Any]] = []
    seen_pair: set[tuple[str, str]] = set()
    for row in non_pd.itertuples(index=False):
        cid = str(getattr(row, "composer_id", "") or "")
        if not cid or cid == "nan":
            stats["skipped_no_entity"] += 1
            continue
        entity = cache_get("wikidata_entity", cid)
        if entity is None:
            stats["skipped_no_entity"] += 1
            continue
        stats["with_cached_entity"] += 1
        urls = extract_p856_urls(entity)
        if not urls:
            continue
        stats["with_p856"] += 1
        for url in urls:
            key = (cid, url)
            if key in seen_pair:
                continue
            seen_pair.add(key)
            stats["urls"] += 1
            jobs.append(
                {
                    "composer_id": cid,
                    "name_display": getattr(row, "name_display", ""),
                    "birth_year": getattr(row, "birth_year", ""),
                    "death_year": getattr(row, "death_year", ""),
                    "eu_pd_status": getattr(row, "eu_pd_status", ""),
                    "homepage_url": url,
                    "source": "p856",
                    "search_rank": "",
                    "search_title": "",
                }
            )
    return jobs, stats


def collect_search_targets(
    results: pd.DataFrame,
    composers: Optional[pd.DataFrame] = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Build jobs from a search_results TSV (result pages + site roots)."""
    meta: dict[str, dict[str, Any]] = {}
    if composers is not None and len(composers):
        for row in composers.itertuples(index=False):
            cid = str(getattr(row, "composer_id", "") or "")
            if cid and cid != "nan":
                meta[cid] = {
                    "birth_year": getattr(row, "birth_year", ""),
                    "death_year": getattr(row, "death_year", ""),
                    "eu_pd_status": getattr(row, "eu_pd_status", ""),
                    "name_display": getattr(row, "name_display", ""),
                }

    skip_reasons: Counter[str] = Counter()
    stats = {
        "result_rows": int(len(results)),
        "composers": 0,
        "result_jobs": 0,
        "site_root_jobs": 0,
        "urls": 0,
        "skipped": 0,
    }
    jobs: list[dict[str, Any]] = []

    if results is None or not len(results):
        return jobs, {**stats, "skip_reasons": dict(skip_reasons)}

    df = results.copy()
    if "rank" in df.columns:
        df["rank"] = pd.to_numeric(df["rank"], errors="coerce").fillna(0).astype(int)
    else:
        df["rank"] = 0

    for cid, group in df.groupby("composer_id", sort=False):
        cid_s = str(cid)
        stats["composers"] += 1
        m = meta.get(cid_s, {})
        name = ""
        if "name_display" in group.columns and len(group):
            name = str(group["name_display"].iloc[0] or "")
        name = name or str(m.get("name_display", "") or "")

        per_host = Counter()
        roots_needed: dict[str, str] = {}  # root_url -> first title
        seen_urls: set[str] = set()
        group_sorted = group.sort_values("rank", kind="mergesort")

        for row in group_sorted.itertuples(index=False):
            rank = int(getattr(row, "rank", 0) or 0)
            url_val = getattr(row, "url", "")
            if url_val is None or (isinstance(url_val, float) and pd.isna(url_val)):
                url = ""
            else:
                url = str(url_val).strip()
                if url.lower() in ("nan", "none"):
                    url = ""
            title_val = getattr(row, "title", "")
            if title_val is None or (isinstance(title_val, float) and pd.isna(title_val)):
                title = ""
            else:
                title = str(title_val)
            if rank == 0 and not url:
                skip_reasons["rank0_no_results"] += 1
                stats["skipped"] += 1
                continue
            if not url:
                skip_reasons["empty_url"] += 1
                stats["skipped"] += 1
                continue
            host = request_host(url)
            reason = denied_host_reason(host)
            if reason:
                skip_reasons[f"denied:{reason}"] += 1
                stats["skipped"] += 1
                continue
            if per_host[host] >= MAX_RESULTS_PER_HOST:
                skip_reasons["per_host_cap"] += 1
                stats["skipped"] += 1
                continue
            clean = _clean_url(url)
            if clean in seen_urls:
                skip_reasons["dup_url"] += 1
                stats["skipped"] += 1
                continue
            seen_urls.add(clean)
            per_host[host] += 1
            root = site_root(clean)
            if clean.rstrip("/") != root.rstrip("/"):
                roots_needed.setdefault(root, title)
            jobs.append(
                {
                    "composer_id": cid_s,
                    "name_display": name,
                    "birth_year": m.get("birth_year", ""),
                    "death_year": m.get("death_year", ""),
                    "eu_pd_status": m.get("eu_pd_status", ""),
                    "homepage_url": clean,
                    "source": "search_result",
                    "search_rank": rank,
                    "search_title": title,
                }
            )
            stats["result_jobs"] += 1

        for root, title in roots_needed.items():
            if root.rstrip("/") in {u.rstrip("/") for u in seen_urls} or root in seen_urls:
                continue
            # Root may equal a result after normalisation.
            if any(_clean_url(u).rstrip("/") == root.rstrip("/") for u in seen_urls):
                continue
            jobs.append(
                {
                    "composer_id": cid_s,
                    "name_display": name,
                    "birth_year": m.get("birth_year", ""),
                    "death_year": m.get("death_year", ""),
                    "eu_pd_status": m.get("eu_pd_status", ""),
                    "homepage_url": root,
                    "source": "search_site_root",
                    "search_rank": "",
                    "search_title": title,
                }
            )
            stats["site_root_jobs"] += 1

    stats["urls"] = stats["result_jobs"] + stats["site_root_jobs"]
    stats["skip_reasons"] = dict(skip_reasons)
    return jobs, stats


def _default_get_once(session: Any, url: str, *, purpose: str = "page") -> RawResponse:
    """Single HTTP GET without following redirects.

    ``purpose='page'``: read the body for HTML Content-Type, or when Content-Type is
    absent (sniffed later). Non-HTML Content-Types are rejected without reading.
    ``purpose='robots'``: read the body for any 2xx.
    """
    try:
        resp = session.get(
            url, timeout=FETCH_TIMEOUT_S, allow_redirects=False, stream=True
        )
        headers = {str(k).lower(): v for k, v in resp.headers.items()}
        status = int(resp.status_code)
        content = b""
        ctype = _content_type(headers)
        if status in REDIRECT_STATUS:
            resp.close()
            return RawResponse(status_code=status, url=url, headers=headers, content=b"")
        read_body = False
        if 200 <= status < 300:
            if purpose == "robots":
                read_body = True
            elif _is_html_type(ctype):
                read_body = True
            elif not ctype:
                read_body = True
        if read_body:
            chunks: list[bytes] = []
            size = 0
            for chunk in resp.iter_content(chunk_size=65536):
                if not chunk:
                    continue
                size += len(chunk)
                if size > MAX_HTML_BYTES:
                    chunks.append(chunk[: max(0, MAX_HTML_BYTES - (size - len(chunk)))])
                    break
                chunks.append(chunk)
            content = b"".join(chunks)
        resp.close()
        return RawResponse(status_code=status, url=url, headers=headers, content=content)
    except Exception as exc:  # noqa: BLE001 — recorded as fetch_status
        return RawResponse(
            status_code=0,
            url=url,
            headers={},
            content=b"",
            error=f"error:{type(exc).__name__}",
        )


class Fetcher:
    """Polite, cache-backed page fetcher. Network goes through ``get_fn`` only."""

    def __init__(
        self,
        session: Any = None,
        *,
        host_interval: float = 1.0,
        offline: bool = False,
        retry_errors: bool = False,
        get_fn: Optional[GetFn] = None,
        user_agent: str = USER_AGENT,
    ) -> None:
        self.session = session
        self.host_interval = float(host_interval)
        self.offline = offline
        self.retry_errors = bool(retry_errors)
        self.user_agent = user_agent
        self._get_fn = get_fn
        self._host_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
        self._last_start: dict[str, float] = {}
        self._active_hosts: set[str] = set()
        self._active_lock = threading.Lock()
        self.overlap_events: list[str] = []
        self._url_locks_guard = threading.Lock()
        self._url_locks: dict[str, threading.Lock] = {}

    def _url_lock(self, key: str) -> threading.Lock:
        with self._url_locks_guard:
            lock = self._url_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._url_locks[key] = lock
            return lock

    def _exchange(self, url: str, *, purpose: str = "page") -> RawResponse:
        """One request: hold per-host lock for throttle + in-flight duration."""
        host = request_host(url) or "_"
        lock = self._host_locks[host]
        with lock:
            wait = self._last_start.get(host, float("-inf")) + self.host_interval - time.monotonic()
            if wait > 0 and self.host_interval > 0:
                time.sleep(wait)
            self._last_start[host] = time.monotonic()
            with self._active_lock:
                if host in self._active_hosts:
                    self.overlap_events.append(host)
                self._active_hosts.add(host)
            try:
                if self._get_fn is not None:
                    return self._get_fn(url)
                if self.session is None:
                    return RawResponse(0, url, error="error:NoSession")
                return _default_get_once(self.session, url, purpose=purpose)
            finally:
                with self._active_lock:
                    self._active_hosts.discard(host)

    def _eval_robots_record(self, cached: dict[str, Any], page_url: str) -> bool:
        if cached.get("allowed_default") is False:
            return False
        if cached.get("allowed_default") is True and not (cached.get("html") or "").strip():
            return True
        body = cached.get("html") or ""
        rp = RobotFileParser()
        rp.parse(body.splitlines())
        try:
            return bool(rp.can_fetch(self.user_agent, page_url))
        except Exception:  # noqa: BLE001
            return False

    def _robots_terminal_payload(
        self, robots_url: str, current: str, raw: RawResponse
    ) -> dict[str, Any]:
        status = raw.status_code
        text = ""
        err = raw.error
        allowed_default: Optional[bool] = None
        if err:
            allowed_default = False
        elif status in REDIRECT_STATUS:
            # Unresolved redirect (no Location / too many) → disallow.
            allowed_default = False
        elif status in (401, 403):
            allowed_default = False
        elif status == 404:
            allowed_default = True
        elif status >= 500 or status == 0:
            allowed_default = False
        elif 200 <= status < 300:
            text = raw.content[:MAX_HTML_BYTES].decode("utf-8", errors="replace")
        else:
            allowed_default = True
        return {
            "url": robots_url,
            "final_url": current,
            "status_code": status,
            "content_type": _content_type(raw.headers) or "text/plain",
            "html": text[:MAX_HTML_BYTES],
            "error": err,
            "fetched_at": _utc_now(),
            "allowed_default": allowed_default,
        }

    def robots_allowed(self, url: str) -> bool:
        """False when robots forbids url, robots.txt is 401/403/5xx, or unreachable."""
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return False
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        lock = self._url_lock("robots:" + robots_url)
        with lock:
            cached = cache_get_url(ROBOTS_NAMESPACE, robots_url)
            if cached is not None and not _should_refetch(cached, self.retry_errors):
                return self._eval_robots_record(cached, url)

            if self.offline:
                return False

            current = robots_url
            for redirect_i in range(MAX_REDIRECTS + 1):
                raw = self._exchange(current, purpose="robots")
                if raw.error and not raw.status_code:
                    payload = self._robots_terminal_payload(robots_url, current, raw)
                    cache_set_url(ROBOTS_NAMESPACE, robots_url, payload)
                    return self._eval_robots_record(payload, url)

                if raw.status_code in REDIRECT_STATUS:
                    if redirect_i >= MAX_REDIRECTS:
                        payload = self._robots_terminal_payload(robots_url, current, raw)
                        cache_set_url(ROBOTS_NAMESPACE, robots_url, payload)
                        return False
                    loc = (raw.headers.get("location") or "").strip()
                    if not loc:
                        payload = self._robots_terminal_payload(robots_url, current, raw)
                        cache_set_url(ROBOTS_NAMESPACE, robots_url, payload)
                        return False
                    current = _clean_url(urljoin(current, loc))
                    continue

                payload = self._robots_terminal_payload(robots_url, current, raw)
                cache_set_url(ROBOTS_NAMESPACE, robots_url, payload)
                return self._eval_robots_record(payload, url)

            payload = {
                "url": robots_url,
                "final_url": current,
                "status_code": 0,
                "content_type": "text/plain",
                "html": "",
                "error": "error:TooManyRedirects",
                "fetched_at": _utc_now(),
                "allowed_default": False,
            }
            cache_set_url(ROBOTS_NAMESPACE, robots_url, payload)
            return False

    def robots_block_reason(self, url: str) -> str:
        """Why robots blocked ``url``: unreachable robots.txt (retryable) or a real disallow."""
        parsed = urlparse(url)
        rec = cache_get_url(ROBOTS_NAMESPACE, f"{parsed.scheme}://{parsed.netloc}/robots.txt")
        if rec is not None:
            status = int(rec.get("status_code") or 0)
            if rec.get("error") or status == 0 or status >= 500:
                return "error:robots_unreachable"
        return "robots_disallowed"

    def fetch_page(self, url: str) -> dict[str, Any]:
        """Fetch or reuse a cached HTML page record for ``url`` (manual redirects)."""
        url = _clean_url(url)
        lock = self._url_lock("page:" + url)
        with lock:
            cached = cache_get_url(PAGES_NAMESPACE, url)
            if cached is not None and not _should_refetch(cached, self.retry_errors):
                return cached

            if self.offline:
                return {
                    "url": url,
                    "final_url": url,
                    "status_code": 0,
                    "content_type": "",
                    "html": "",
                    "error": "error:not_cached",
                    "fetched_at": _utc_now(),
                }

            current = url
            for redirect_i in range(MAX_REDIRECTS + 1):
                if not self.robots_allowed(current):
                    payload = {
                        "url": url,
                        "final_url": current,
                        "status_code": 0,
                        "content_type": "",
                        "html": "",
                        "error": self.robots_block_reason(current),
                        "fetched_at": _utc_now(),
                    }
                    cache_set_url(PAGES_NAMESPACE, url, payload)
                    return payload

                raw = self._exchange(current, purpose="page")
                if raw.error and not raw.status_code:
                    payload = {
                        "url": url,
                        "final_url": raw.url or current,
                        "status_code": raw.status_code or 0,
                        "content_type": "",
                        "html": "",
                        "error": raw.error,
                        "fetched_at": _utc_now(),
                    }
                    cache_set_url(PAGES_NAMESPACE, url, payload)
                    return payload

                if raw.status_code in REDIRECT_STATUS:
                    if redirect_i >= MAX_REDIRECTS:
                        payload = {
                            "url": url,
                            "final_url": current,
                            "status_code": raw.status_code,
                            "content_type": _content_type(raw.headers),
                            "html": "",
                            "error": f"http_{raw.status_code}",
                            "fetched_at": _utc_now(),
                        }
                        cache_set_url(PAGES_NAMESPACE, url, payload)
                        return payload
                    loc = (raw.headers.get("location") or "").strip()
                    if not loc:
                        payload = {
                            "url": url,
                            "final_url": current,
                            "status_code": raw.status_code,
                            "content_type": _content_type(raw.headers),
                            "html": "",
                            "error": f"http_{raw.status_code}",
                            "fetched_at": _utc_now(),
                        }
                        cache_set_url(PAGES_NAMESPACE, url, payload)
                        return payload
                    current = _clean_url(urljoin(current, loc))
                    continue

                ctype = _content_type(raw.headers)
                if not (200 <= raw.status_code < 300):
                    payload = {
                        "url": url,
                        "final_url": current,
                        "status_code": raw.status_code,
                        "content_type": ctype,
                        "html": "",
                        "error": f"http_{raw.status_code}",
                        "fetched_at": _utc_now(),
                    }
                    cache_set_url(PAGES_NAMESPACE, url, payload)
                    return payload

                accept_html = False
                if _is_html_type(ctype):
                    accept_html = True
                elif not ctype:
                    accept_html = looks_like_html(raw.content)
                if not accept_html:
                    payload = {
                        "url": url,
                        "final_url": current,
                        "status_code": raw.status_code,
                        "content_type": ctype,
                        "html": "",
                        "error": "not_html",
                        "fetched_at": _utc_now(),
                    }
                    cache_set_url(PAGES_NAMESPACE, url, payload)
                    return payload

                html = decode_html(raw.content, raw.headers)
                payload = {
                    "url": url,
                    "final_url": current,
                    "status_code": raw.status_code,
                    "content_type": ctype or "text/html",
                    "html": html,
                    "error": None,
                    "fetched_at": _utc_now(),
                }
                cache_set_url(PAGES_NAMESPACE, url, payload)
                return payload

            payload = {
                "url": url,
                "final_url": current,
                "status_code": 0,
                "content_type": "",
                "html": "",
                "error": "error:TooManyRedirects",
                "fetched_at": _utc_now(),
            }
            cache_set_url(PAGES_NAMESPACE, url, payload)
            return payload


def is_score_format_url(url: str, anchor_text: str = "") -> bool:
    return classify_score_link(url, anchor_text) is not None


def select_hop_urls(homepage_final_url: str, html: str, max_hops: int) -> list[str]:
    """Same-site score/catalogue/licence links, best keyword matches first.

    Never selects score-format file URLs (pdf, midi, …) as hop targets.
    """
    if max_hops <= 0 or not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    scored: list[tuple[int, str]] = []
    licence_links: list[str] = []
    seen: set[str] = set()

    for tag in soup.find_all(href=True):
        href = (tag.get("href") or "").strip()
        if not href or href.startswith(("#", "mailto:", "javascript:", "tel:")):
            continue
        abs_url = _clean_url(urljoin(homepage_final_url, href))
        if not same_site(homepage_final_url, abs_url):
            continue
        if _clean_url(abs_url) == _clean_url(homepage_final_url):
            continue
        text = tag.get_text(" ", strip=True) if tag.name != "link" else ""
        rel = _rel_list(tag.get("rel"))
        if is_score_format_url(abs_url, text):
            continue
        path = _path_lower(abs_url)
        if _is_licence_nav(text, rel):
            if abs_url not in seen:
                licence_links.append(abs_url)
                seen.add(abs_url)
            continue
        # Keyword hops: prefer anchors / areas with visible or path cues.
        if tag.name not in ("a", "area"):
            continue
        score = _keyword_score(path, text)
        if score <= 0 or abs_url in seen:
            continue
        seen.add(abs_url)
        scored.append((score, abs_url))

    scored.sort(key=lambda t: (-t[0], t[1]))
    ordered = licence_links + [u for _, u in scored]
    out: list[str] = []
    seen_out: set[str] = set()
    for u in ordered:
        if u in seen_out:
            continue
        seen_out.add(u)
        out.append(u)
        if len(out) >= max_hops:
            break
    return out


def _evidence_detail_link(href: str) -> str:
    return href.replace("|", "%7C").replace("=", "%3D")


def _evidence_detail_text(snippet: str) -> str:
    s = " ".join(snippet.split())
    if len(s) > 200:
        s = s[:200]
    return s.replace("|", " ").replace("=", " ")


def extract_licence_evidence(page_url: str, html: str) -> list[tuple[str, str, str]]:
    """Return list of (spdx, kind, detail) for a page."""
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    found: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    def add(spdx: Optional[str], kind: str, detail: str) -> None:
        if not spdx:
            return
        key = (spdx, kind, detail)
        if key in seen:
            return
        seen.add(key)
        found.append((spdx, kind, detail))

    for tag in soup.find_all(href=True):
        href = (tag.get("href") or "").strip()
        if not href:
            continue
        abs_href = urljoin(page_url, href)
        spdx = spdx_from_cc_url(abs_href)
        if not spdx:
            continue
        rels = {r.lower() for r in _rel_list(tag.get("rel"))}
        kind = "rel_license" if ("license" in rels or "licence" in rels) else "cc_link"
        add(spdx, kind, _evidence_detail_link(abs_href))

    for img in soup.find_all("img", src=True):
        src = (img.get("src") or "").strip()
        if not src:
            continue
        abs_src = urljoin(page_url, src)
        add(spdx_from_cc_url(abs_src), "cc_badge", _evidence_detail_link(abs_src))

    text = soup.get_text(" ", strip=True)
    matches = list(_CC_TEXT_TRIGGER_RE.finditer(text))
    for i, m in enumerate(matches):
        # Parse from the match start up to the next declaration, so neighbouring licences
        # stay apart; the full window may only add a version ("… NonCommercial (CC BY-NC) 4.0").
        parse_end = min(len(text), m.end() + 160)
        next_start = matches[i + 1].start() if i + 1 < len(matches) else parse_end
        spdx = spdx_from_cc_text(text[m.start() : min(parse_end, next_start)])
        if not spdx or not re.search(r"\d", spdx):
            full = spdx_from_cc_text(text[m.start() : parse_end])
            if full and (not spdx or full.startswith(spdx)):
                spdx = full
        if spdx:
            detail_start = max(0, m.start() - 40)
            detail = text[detail_start:parse_end]
            if len(detail) > 200:
                detail = detail[:200]
            add(spdx, "cc_text", _evidence_detail_text(detail))

    return found


def classify_score_link(url: str, anchor_text: str = "") -> Optional[str]:
    """Return score-format kind for a URL, or None.

    ``.musicxml`` / ``.mxl`` always count. ``.xml`` only when the path *stem* or
    anchor text (not the ``.xml`` extension) mentions musicxml/score/partitur-like cues.
    ``feed.xml`` / ``sitemap.xml`` never count.
    """
    path = urlparse(url).path or ""
    lower = path.lower()
    name = lower.rsplit("/", 1)[-1]
    stem = name.rsplit(".", 1)[0] if "." in name else name
    if name in ("feed.xml", "sitemap.xml") or stem in ("feed", "sitemap"):
        if lower.endswith(".xml"):
            return None
    for kind, exts in SCORE_EXTENSIONS.items():
        for ext in exts:
            if lower.endswith(ext):
                return kind
    if lower.endswith(".xml"):
        blob = f"{stem} {anchor_text}"
        if _MUSICXML_HINT_RE.search(blob):
            return "musicxml"
    return None


def extract_score_counts(page_url: str, html: str) -> dict[str, set[str]]:
    counts: dict[str, set[str]] = {
        "pdf": set(),
        "midi": set(),
        "musicxml": set(),
        "mei": set(),
        "lilypond": set(),
        "musescore": set(),
        "other_notation": set(),
    }
    if not html:
        return counts
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("#", "mailto:", "javascript:")):
            continue
        abs_url = _clean_url(urljoin(page_url, href))
        text = a.get_text(" ", strip=True) or ""
        kind = classify_score_link(abs_url, text)
        if kind:
            counts[kind].add(abs_url)
    return counts


def _page_ok(record: dict[str, Any]) -> bool:
    return (
        not record.get("error")
        and 200 <= int(record.get("status_code") or 0) < 300
        and bool(record.get("html"))
    )


def _fetch_status_from_record(record: dict[str, Any]) -> str:
    err = record.get("error")
    if err:
        return str(err)
    code = int(record.get("status_code") or 0)
    if 200 <= code < 300:
        if not _is_html_type(str(record.get("content_type") or "")) and not record.get("html"):
            return "not_html"
        return "ok"
    if code:
        return f"http_{code}"
    return "error:Unknown"


def _name_on_page(name_display: str, pages: list[tuple[str, dict[str, Any]]]) -> str:
    surname = composer_surname(name_display)
    if not surname:
        return "false"
    for _url, rec in pages:
        text = _fold_accents(
            BeautifulSoup(rec.get("html") or "", "html.parser").get_text(" ", strip=True)
        )
        if surname in text:
            return "true"
    return "false"


def _job_meta_fields(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": job.get("source") or "p856",
        "search_rank": job.get("search_rank", ""),
        "search_title": job.get("search_title", ""),
    }


def scan_homepage(
    job: dict[str, Any],
    fetcher: Fetcher,
    *,
    max_hops: int = 8,
) -> dict[str, Any]:
    """Scan one start URL (homepage / search hit / site root); returns an output row."""
    homepage_url = job["homepage_url"]
    home = fetcher.fetch_page(homepage_url)
    fetch_status = _fetch_status_from_record(home)
    final_url = home.get("final_url") or homepage_url

    pages: list[tuple[str, dict[str, Any]]] = []
    hop_urls: list[str] = []

    if _page_ok(home):
        pages.append((final_url, home))
        for hop in select_hop_urls(final_url, home.get("html") or "", max_hops):
            hop_rec = fetcher.fetch_page(hop)
            hop_urls.append(hop_rec.get("final_url") or hop)
            if _page_ok(hop_rec):
                pages.append((hop_rec.get("final_url") or hop, hop_rec))

    licences: set[str] = set()
    evidence: list[str] = []
    score_urls: dict[str, set[str]] = {
        "pdf": set(),
        "midi": set(),
        "musicxml": set(),
        "mei": set(),
        "lilypond": set(),
        "musescore": set(),
        "other_notation": set(),
    }
    score_page_urls: list[str] = []

    for page_url, rec in pages:
        for spdx, kind, detail in extract_licence_evidence(page_url, rec.get("html") or ""):
            licences.add(spdx)
            evidence.append(f"{kind}@{page_url}={detail}")
        counts = extract_score_counts(page_url, rec.get("html") or "")
        page_has_score = False
        for kind, urls in counts.items():
            if urls:
                page_has_score = True
                score_urls[kind].update(urls)
        if page_has_score:
            score_page_urls.append(page_url)

    seen_ev: set[str] = set()
    ev_out: list[str] = []
    for e in evidence:
        if e not in seen_ev:
            seen_ev.add(e)
            ev_out.append(e)

    n_scores = sum(len(v) for v in score_urls.values())
    candidate = "true" if licences and n_scores > 0 else "false"
    meta = _job_meta_fields(job)

    return {
        "composer_id": job["composer_id"],
        "name_display": job.get("name_display", ""),
        "birth_year": job.get("birth_year", ""),
        "death_year": job.get("death_year", ""),
        "eu_pd_status": job.get("eu_pd_status", ""),
        "homepage_url": homepage_url,
        "final_url": final_url,
        "fetch_status": fetch_status,
        "pages_fetched": len(pages),
        "licences": "|".join(sorted(licences)),
        "licence_evidence": "|".join(ev_out),
        "n_pdf": len(score_urls["pdf"]),
        "n_midi": len(score_urls["midi"]),
        "n_musicxml": len(score_urls["musicxml"]),
        "n_mei": len(score_urls["mei"]),
        "n_lilypond": len(score_urls["lilypond"]),
        "n_musescore": len(score_urls["musescore"]),
        "n_other_notation": len(score_urls["other_notation"]),
        "score_pages": "|".join(score_page_urls),
        "hop_urls": "|".join(hop_urls),
        "candidate": candidate,
        "source": meta["source"],
        "search_rank": meta["search_rank"],
        "search_title": meta["search_title"],
        "name_on_page": _name_on_page(str(job.get("name_display", "")), pages),
    }


def _empty_error_row(job: dict[str, Any], status: str) -> dict[str, Any]:
    meta = _job_meta_fields(job)
    return {
        "composer_id": job["composer_id"],
        "name_display": job.get("name_display", ""),
        "birth_year": job.get("birth_year", ""),
        "death_year": job.get("death_year", ""),
        "eu_pd_status": job.get("eu_pd_status", ""),
        "homepage_url": job["homepage_url"],
        "final_url": job["homepage_url"],
        "fetch_status": status,
        "pages_fetched": 0,
        "licences": "",
        "licence_evidence": "",
        "n_pdf": 0,
        "n_midi": 0,
        "n_musicxml": 0,
        "n_mei": 0,
        "n_lilypond": 0,
        "n_musescore": 0,
        "n_other_notation": 0,
        "score_pages": "",
        "hop_urls": "",
        "candidate": "false",
        "source": meta["source"],
        "search_rank": meta["search_rank"],
        "search_title": meta["search_title"],
        "name_on_page": "false",
    }


def run_jobs(
    jobs: list[dict[str, Any]],
    fetcher: Fetcher,
    *,
    max_hops: int = 8,
    workers: int = 8,
    heartbeat_interval: float = 30.0,
) -> list[dict[str, Any]]:
    """Process jobs grouped by request host; one host never shared across workers."""
    by_host: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for job in jobs:
        by_host[host_key(job["homepage_url"]) or "_"].append(job)

    results: list[dict[str, Any]] = []
    results_lock = threading.Lock()

    def process_host(host_jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for job in host_jobs:
            try:
                rows.append(scan_homepage(job, fetcher, max_hops=max_hops))
            except Exception as exc:  # noqa: BLE001 — keep sweep going
                log.exception(
                    "scan failed for %s %s", job.get("composer_id"), job.get("homepage_url")
                )
                rows.append(_empty_error_row(job, f"error:{type(exc).__name__}"))
        return rows

    host_lists = list(by_host.values())
    workers = max(1, int(workers))

    with Heartbeat(
        name="homepage_licences", total=len(jobs), interval_s=heartbeat_interval
    ) as hb:
        if workers == 1 or len(host_lists) <= 1:
            for group in host_lists:
                rows = process_host(group)
                with results_lock:
                    results.extend(rows)
                hb.tick(len(rows))
        else:
            with ThreadPoolExecutor(max_workers=min(workers, len(host_lists))) as pool:
                futs = [pool.submit(process_host, group) for group in host_lists]
                for fut in as_completed(futs):
                    rows = fut.result()
                    with results_lock:
                        results.extend(rows)
                    hb.tick(len(rows))

    order = {
        (j["composer_id"], j["homepage_url"], j.get("source", ""), str(j.get("search_rank", ""))): i
        for i, j in enumerate(jobs)
    }
    results.sort(
        key=lambda r: order.get(
            (r["composer_id"], r["homepage_url"], r.get("source", ""), str(r.get("search_rank", ""))),
            10**9,
        )
    )
    return results


def default_out_path(dump_id: str, *, targets: bool = False, tag: str = "") -> Path:
    if targets:
        return REPO_ROOT / "build" / "open_licence" / f"search_scan_{dump_id}{tag}.tsv"
    return REPO_ROOT / "build" / "open_licence" / f"homepages_{dump_id}.tsv"


def write_output(rows: list[dict[str, Any]], path: Path) -> pd.DataFrame:
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows, columns=OUTPUT_COLUMNS)
    for col in OUTPUT_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    df = df[OUTPUT_COLUMNS]
    df.to_csv(path, sep="\t", index=False)
    return df


def log_summary(df: pd.DataFrame) -> None:
    status_counts = df["fetch_status"].value_counts().to_dict() if len(df) else {}
    n_cand = int((df["candidate"].astype(str) == "true").sum()) if len(df) else 0
    lic_counter: Counter[str] = Counter()
    for cell in df.get("licences", pd.Series(dtype=str)).fillna(""):
        for part in str(cell).split("|"):
            if part:
                lic_counter[part] += 1
    log.info(
        "summary: rows=%d fetch_status=%s candidates=%d licences=%s",
        len(df),
        status_counts,
        n_cand,
        dict(lic_counter),
    )


def run(args: argparse.Namespace) -> Optional[pd.DataFrame]:
    composers = pd.read_csv(dump_tsv_path("composers", args.dump), sep="\t", low_memory=False)
    tag = args.tag or ""

    if args.targets:
        results = pd.read_csv(args.targets, sep="\t", low_memory=False)
        jobs, stats = collect_search_targets(results, composers)
        log.info(
            "%s targets: %d result rows, %d composers, %d result jobs, %d site-root jobs, "
            "%d urls; skipped=%d reasons=%s",
            args.dump,
            stats.get("result_rows", 0),
            stats.get("composers", 0),
            stats.get("result_jobs", 0),
            stats.get("site_root_jobs", 0),
            stats.get("urls", 0),
            stats.get("skipped", 0),
            stats.get("skip_reasons", {}),
        )
        if args.list_only:
            return None
    else:
        jobs, stats = collect_targets(composers)
        log.info(
            "%s: %d non-PD composers, %d with a cached entity, %d with ≥ 1 non-deprecated P856, %d urls",
            args.dump,
            stats["composers_considered"],
            stats["with_cached_entity"],
            stats["with_p856"],
            stats["urls"],
        )
        if args.list_only:
            return None

    if args.limit:
        jobs = jobs[: int(args.limit)]

    session = None if args.offline else make_session()
    fetcher = Fetcher(
        session,
        host_interval=args.host_interval,
        offline=bool(args.offline),
        retry_errors=bool(getattr(args, "retry_errors", False)),
    )
    rows = run_jobs(
        jobs,
        fetcher,
        max_hops=args.max_hops,
        workers=args.workers,
        heartbeat_interval=args.heartbeat_interval,
    )
    out = (
        Path(args.out)
        if args.out
        else default_out_path(args.dump, targets=bool(args.targets), tag=tag)
    )
    df = write_output(rows, out)
    log.info("wrote %s (%d rows)", out, len(df))
    log_summary(df)
    return df


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dump", required=True, help="Dump id (e.g. r019)")
    p.add_argument("--limit", type=int, default=0, help="Process at most N start URLs")
    p.add_argument("--workers", type=int, default=8, help="Parallel hosts (default 8)")
    p.add_argument("--max-hops", type=int, default=8, help="Max same-site hop pages per start URL")
    p.add_argument(
        "--host-interval",
        type=float,
        default=1.0,
        help="Minimum seconds between request starts to the same host",
    )
    p.add_argument(
        "--offline",
        action="store_true",
        help="Read only homepage_pages / homepage_robots cache; fetch nothing",
    )
    p.add_argument(
        "--retry-errors",
        action="store_true",
        help="Refetch cached network errors / 429 / 5xx (not robots_disallowed, not_html, 4xx)",
    )
    p.add_argument(
        "--list-only",
        action="store_true",
        help="Print input counts and exit (no network)",
    )
    p.add_argument(
        "--targets",
        default="",
        help="Search-results TSV (enables search mode instead of P856)",
    )
    p.add_argument(
        "--tag",
        default="",
        help="Suffix for default search_scan output filename (search mode)",
    )
    p.add_argument("--out", default="", help="Output TSV path (default under build/open_licence/)")
    p.add_argument("--heartbeat-interval", type=float, default=30.0)
    run(p.parse_args())


if __name__ == "__main__":
    main()
