"""Offline tests for the composer-homepage open-licence scanner."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pandas as pd
import pytest

import common
import homepage_licences as hl
import licences

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "homepages"


@pytest.fixture(autouse=True)
def tmp_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "CACHE_DIR", tmp_path)
    return tmp_path


def _html(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _resp(
    url: str,
    html: str = "",
    status: int = 200,
    content_type: str = "text/html",
    content: bytes | None = None,
    headers: dict | None = None,
) -> hl.RawResponse:
    hdrs = {"Content-Type": content_type}
    if headers:
        hdrs.update(headers)
    body = content if content is not None else html.encode("utf-8")
    return hl.RawResponse(status_code=status, url=url, headers=hdrs, content=body)


def _robots(url: str, body: str = "", status: int = 404) -> hl.RawResponse:
    return hl.RawResponse(
        status_code=status,
        url=url,
        headers={"Content-Type": "text/plain"},
        content=body.encode("utf-8"),
    )


class MapFetcher:
    """URL → RawResponse map; records request order and concurrent hosts."""

    def __init__(self, mapping: dict[str, hl.RawResponse], default_error: str = "error:Timeout"):
        self.mapping = dict(mapping)
        self.default_error = default_error
        self.calls: list[str] = []
        self._lock = threading.Lock()
        self._active: set[str] = set()
        self.overlap: list[str] = []
        self._hold: dict[str, float] = {}

    def set_hold(self, host: str, seconds: float) -> None:
        self._hold[host] = seconds

    def __call__(self, url: str) -> hl.RawResponse:
        host = hl.request_host(url)
        with self._lock:
            self.calls.append(url)
            if host in self._active:
                self.overlap.append(host)
            self._active.add(host)
        try:
            hold = self._hold.get(host, 0.0)
            if hold:
                time.sleep(hold)
            if url in self.mapping:
                return self.mapping[url]
            if url.rstrip("/").endswith("robots.txt"):
                return _robots(url, status=404)
            return hl.RawResponse(0, url, error=self.default_error)
        finally:
            with self._lock:
                self._active.discard(host)


def _job(cid="Q1795042", name="Kyle Gann", url="http://www.kylegann.com/", **kw):
    base = {
        "composer_id": cid,
        "name_display": name,
        "birth_year": 1955,
        "death_year": "",
        "eu_pd_status": "not_pd",
        "homepage_url": url,
        "source": "p856",
        "search_rank": "",
        "search_title": "",
    }
    base.update(kw)
    return base


def _entity_p856(*urls: str, ranks: list[str] | None = None) -> dict:
    claims = []
    for i, u in enumerate(urls):
        rank = (ranks or ["normal"] * len(urls))[i]
        claims.append(
            {
                "rank": rank,
                "mainsnak": {"datavalue": {"value": u, "type": "string"}},
            }
        )
    return {"type": "item", "id": "Q1", "claims": {"P856": claims}}


def test_gann_two_page_flow_finds_cc_by_nc_and_pdfs():
    home = "http://www.kylegann.com/"
    music = "http://www.kylegann.com/Gannmusic.html"
    fake = MapFetcher(
        {
            home: _resp(home, _html("kylegann_index.html")),
            music: _resp(music, _html("kylegann_Gannmusic.html")),
        }
    )
    fetcher = hl.Fetcher(get_fn=fake, host_interval=0.0)
    row = hl.scan_homepage(_job(url=home), fetcher, max_hops=8)

    assert row["fetch_status"] == "ok"
    assert row["licences"] == "CC-BY-NC-3.0"
    assert row["n_pdf"] == 12
    assert row["candidate"] == "true"
    assert row["source"] == "p856"
    assert row["name_on_page"] == "true"
    assert music in row["hop_urls"]
    assert any(
        e.startswith(f"rel_license@{music}=") and "by-nc/3.0" in e
        for e in row["licence_evidence"].split("|")
    )


def test_negative_hops_only_werke_not_pdf_or_offsite():
    home = "https://www.beispiel.example/"
    werke = "https://www.beispiel.example/werke/"
    fake = MapFetcher(
        {
            home: _resp(home, _html("negative_index.html")),
            werke: _resp(werke, _html("negative_werke.html")),
        }
    )
    fetcher = hl.Fetcher(get_fn=fake, host_interval=0.0)
    row = hl.scan_homepage(
        _job(cid="Q1", name="Beispiel", url=home),
        fetcher,
        max_hops=8,
    )

    hops = [h for h in row["hop_urls"].split("|") if h]
    assert hops == [werke]
    assert row["licences"] == ""
    assert row["n_pdf"] == 1
    assert row["candidate"] == "false"
    page_calls = [c for c in fake.calls if not c.endswith("robots.txt")]
    assert page_calls == [home, werke]


def test_robots_disallow_sets_status_and_skips_page_fetch():
    home = "https://blocked.example/"
    robots_body = "User-agent: *\nDisallow: /\n"
    fake = MapFetcher(
        {
            "https://blocked.example/robots.txt": _robots(
                "https://blocked.example/robots.txt", body=robots_body, status=200
            ),
            home: _resp(home, "<html><body>secret</body></html>"),
        }
    )
    fetcher = hl.Fetcher(get_fn=fake, host_interval=0.0)
    row = hl.scan_homepage(_job(cid="Q9", name="Blocked", url=home), fetcher)

    assert row["fetch_status"] == "robots_disallowed"
    assert row["pages_fetched"] == 0
    assert home not in fake.calls


def test_robots_401_disallows_and_skips_page():
    home = "https://auth.example/"
    fake = MapFetcher(
        {
            "https://auth.example/robots.txt": _robots(
                "https://auth.example/robots.txt", status=401
            ),
            home: _resp(home, "<html><body>nope</body></html>"),
        }
    )
    fetcher = hl.Fetcher(get_fn=fake, host_interval=0.0)
    row = hl.scan_homepage(_job(cid="Q401", name="Auth", url=home), fetcher)
    assert row["fetch_status"] == "robots_disallowed"
    assert home not in fake.calls


def test_robots_unreachable_is_a_retryable_error_not_a_disallow():
    home = "https://down.example/"
    robots = "https://down.example/robots.txt"
    fake = MapFetcher({robots: hl.RawResponse(0, robots, error="error:ConnectionError")})
    row = hl.scan_homepage(
        _job(cid="Qdown", name="Down", url=home), hl.Fetcher(get_fn=fake, host_interval=0.0)
    )
    assert row["fetch_status"] == "error:robots_unreachable"
    assert home not in fake.calls

    up = MapFetcher(
        {
            "https://down.example/robots.txt": _robots("https://down.example/robots.txt"),
            home: _resp(home, "<html><body>back</body></html>"),
        }
    )
    reused = hl.scan_homepage(
        _job(cid="Qdown", name="Down", url=home), hl.Fetcher(get_fn=up, host_interval=0.0)
    )
    assert reused["fetch_status"] == "error:robots_unreachable"
    assert up.calls == []
    retried = hl.scan_homepage(
        _job(cid="Qdown", name="Down", url=home),
        hl.Fetcher(get_fn=up, host_interval=0.0, retry_errors=True),
    )
    assert retried["fetch_status"] == "ok"
    assert home in up.calls


def test_robots_403_disallows_and_skips_page():
    home = "https://forbid.example/"
    fake = MapFetcher(
        {
            "https://forbid.example/robots.txt": _robots(
                "https://forbid.example/robots.txt", status=403
            ),
            home: _resp(home, "<html><body>nope</body></html>"),
        }
    )
    fetcher = hl.Fetcher(get_fn=fake, host_interval=0.0)
    row = hl.scan_homepage(_job(cid="Q403", name="Forbid", url=home), fetcher)
    assert row["fetch_status"] == "robots_disallowed"
    assert home not in fake.calls


def test_fetch_error_row_and_sweep_continues():
    ok_home = "https://ok.example/"
    bad_home = "https://bad.example/"
    fake = MapFetcher(
        {
            ok_home: _resp(
                ok_home,
                '<html><body><a rel="license" href="https://creativecommons.org/licenses/by/4.0/">x</a>'
                '<a href="a.pdf">pdf</a></body></html>',
            ),
            bad_home: hl.RawResponse(0, bad_home, error="error:ConnectionError"),
        }
    )
    fetcher = hl.Fetcher(get_fn=fake, host_interval=0.0)
    rows = hl.run_jobs(
        [_job(cid="Qbad", name="Bad", url=bad_home), _job(cid="Qok", name="Ok", url=ok_home)],
        fetcher,
        max_hops=0,
        workers=2,
    )
    by_id = {r["composer_id"]: r for r in rows}
    assert by_id["Qbad"]["fetch_status"] == "error:ConnectionError"
    assert by_id["Qok"]["candidate"] == "true"


def test_same_host_never_fetched_concurrently():
    home_a = "https://shared.example/a/"
    home_b = "https://shared.example/b/"
    fake = MapFetcher(
        {
            home_a: _resp(home_a, "<html><body>a</body></html>"),
            home_b: _resp(home_b, "<html><body>b</body></html>"),
        }
    )
    fake.set_hold("shared.example", 0.05)
    fetcher = hl.Fetcher(get_fn=fake, host_interval=0.0)
    rows = hl.run_jobs(
        [_job(cid="Qa", name="A", url=home_a), _job(cid="Qb", name="B", url=home_b)],
        fetcher,
        max_hops=0,
        workers=4,
    )
    assert len(rows) == 2
    assert fake.overlap == []
    assert fetcher.overlap_events == []


def test_redirect_checks_robots_on_each_host_and_serialises():
    start = "https://start.example/"
    dest = "https://dest.example/"
    fake = MapFetcher(
        {
            start: hl.RawResponse(
                302,
                start,
                headers={"Location": dest, "Content-Type": "text/html"},
                content=b"",
            ),
            dest: _resp(dest, "<html><body>Gann landed</body></html>"),
            "https://dest.example/robots.txt": _robots(
                "https://dest.example/robots.txt",
                body="User-agent: *\nDisallow: /\n",
                status=200,
            ),
        }
    )
    fetcher = hl.Fetcher(get_fn=fake, host_interval=0.0)
    row = hl.scan_homepage(_job(cid="Qr", name="Gann", url=start), fetcher, max_hops=0)
    assert row["fetch_status"] == "robots_disallowed"
    assert dest not in fake.calls or fake.calls.count(dest) == 0
    assert "https://dest.example/robots.txt" in fake.calls


def test_cc_badge_via_licences_module():
    assert (
        licences.spdx_from_cc_url("http://i.creativecommons.org/l/by-nc/3.0/88x31.png")
        == "CC-BY-NC-3.0"
    )
    home = "https://badge.example/"
    html = (
        '<html><body><img src="http://i.creativecommons.org/l/by-nc/3.0/88x31.png">'
        '<a href="a.pdf">pdf</a></body></html>'
    )
    fake = MapFetcher({home: _resp(home, html)})
    row = hl.scan_homepage(_job(cid="Qb", name="Badge", url=home), hl.Fetcher(get_fn=fake, host_interval=0.0), max_hops=0)
    assert row["licences"] == "CC-BY-NC-3.0"
    assert any(e.startswith("cc_badge@") for e in row["licence_evidence"].split("|"))


def test_cc0_publicdomain_zero_link():
    home = "https://cc0.example/"
    fake = MapFetcher({home: _resp(home, _html("cc0_scores.html"))})
    row = hl.scan_homepage(
        _job(cid="Q0", name="Zero", url=home),
        hl.Fetcher(get_fn=fake, host_interval=0.0),
        max_hops=0,
    )
    assert row["licences"] == "CC0-1.0"
    assert row["candidate"] == "true"


def test_text_only_cc_by_sa_detection():
    home = "https://text.example/"
    fake = MapFetcher({home: _resp(home, _html("text_only_cc.html"))})
    row = hl.scan_homepage(
        _job(cid="Qt", name="Text", url=home),
        hl.Fetcher(get_fn=fake, host_interval=0.0),
        max_hops=0,
    )
    assert row["licences"] == "CC-BY-SA-4.0"
    assert "cc_text@" in row["licence_evidence"]


def test_cc_hyphen_trigger_in_visible_text():
    home = "https://hyphen.example/"
    html = "<html><body><p>Released as CC-BY-NC 3.0 for all scores.</p><a href='x.pdf'>x</a></body></html>"
    fake = MapFetcher({home: _resp(home, html)})
    row = hl.scan_homepage(
        _job(cid="Qh", name="Hyphen", url=home),
        hl.Fetcher(get_fn=fake, host_interval=0.0),
        max_hops=0,
    )
    assert row["licences"] == "CC-BY-NC-3.0"
    assert "cc_text@" in row["licence_evidence"]


def test_link_rel_license_element():
    home = "https://linkrel.example/"
    fake = MapFetcher({home: _resp(home, _html("link_rel_license.html"))})
    row = hl.scan_homepage(
        _job(cid="Ql", name="Link", url=home),
        hl.Fetcher(get_fn=fake, host_interval=0.0),
        max_hops=0,
    )
    assert row["licences"] == "CC-BY-4.0"
    assert any(e.startswith("rel_license@") for e in row["licence_evidence"].split("|"))


def test_offline_miss_not_cached_then_online_fetches(tmp_path):
    home = "https://later.example/"
    offline = hl.Fetcher(get_fn=MapFetcher({}), host_interval=0.0, offline=True)
    row = hl.scan_homepage(_job(cid="Qo", name="Later", url=home), offline, max_hops=0)
    assert row["fetch_status"] == "error:not_cached"
    assert hl.cache_get_url(hl.PAGES_NAMESPACE, home) is None
    assert hl.cache_get_url(hl.ROBOTS_NAMESPACE, "https://later.example/robots.txt") is None

    fake = MapFetcher({home: _resp(home, "<html><body>ok</body></html>")})
    online = hl.Fetcher(get_fn=fake, host_interval=0.0, offline=False)
    row2 = hl.scan_homepage(_job(cid="Qo", name="Later", url=home), online, max_hops=0)
    assert row2["fetch_status"] == "ok"
    assert hl.cache_get_url(hl.PAGES_NAMESPACE, home) is not None


def test_offline_rerun_matches_online_rows():
    home = "http://www.kylegann.com/"
    music = "http://www.kylegann.com/Gannmusic.html"
    mapping = {
        home: _resp(home, _html("kylegann_index.html")),
        music: _resp(music, _html("kylegann_Gannmusic.html")),
    }
    online = hl.Fetcher(get_fn=MapFetcher(mapping), host_interval=0.0, offline=False)
    row_online = hl.scan_homepage(_job(url=home), online, max_hops=8)
    offline = hl.Fetcher(get_fn=MapFetcher({}), host_interval=0.0, offline=True)
    row_offline = hl.scan_homepage(_job(url=home), offline, max_hops=8)
    keys = ["fetch_status", "pages_fetched", "licences", "n_pdf", "candidate", "hop_urls"]
    assert {k: row_offline[k] for k in keys} == {k: row_online[k] for k in keys}


def test_same_site_registrable_domain():
    assert hl.same_site("https://composer.example/", "https://scores.composer.example/list")
    assert hl.same_site("https://www.composer.example/", "https://composer.example/x")
    assert not hl.same_site("https://composer.example/", "https://other.example/")
    assert hl.registrable_domain("www.foo.co.uk") == "foo.co.uk"
    assert hl.same_site("https://a.foo.co.uk/", "https://b.foo.co.uk/x")


def test_shared_hosting_tenants_are_distinct():
    assert hl.registrable_domain("alice.wordpress.com") == "alice.wordpress.com"
    assert hl.registrable_domain("bob.wordpress.com") == "bob.wordpress.com"
    assert not hl.same_site("https://alice.wordpress.com/", "https://bob.wordpress.com/")
    assert hl.same_site("https://www.alice.wordpress.com/", "https://alice.wordpress.com/x")
    assert hl.registrable_domain("x.co.uk") == "x.co.uk"
    assert hl.registrable_domain("me.blogspot.de") == "me.blogspot.de"


def test_musicxml_rules():
    assert hl.classify_score_link("https://x.example/a.musicxml") == "musicxml"
    assert hl.classify_score_link("https://x.example/a.mxl") == "musicxml"
    assert hl.classify_score_link("https://x.example/score-export.xml", "download") == "musicxml"
    assert hl.classify_score_link("https://x.example/piece.xml", "MusicXML file") == "musicxml"
    assert hl.classify_score_link("https://x.example/feed.xml") is None
    assert hl.classify_score_link("https://x.example/sitemap.xml") is None
    assert hl.classify_score_link("https://x.example/data.xml", "notes") is None


def test_windows_1250_charset_decodes_muvek_keyword():
    home = "https://hu.example/"
    hop = "https://hu.example/oldal/"
    # Anchor text "művek" only; path has no ASCII keyword cue.
    html = (
        "<html><head><meta http-equiv=\"Content-Type\" "
        "content=\"text/html; charset=windows-1250\"></head>"
        "<body><a href=\"/oldal/\">művek</a></body></html>"
    )
    content = html.encode("windows-1250")
    fake = MapFetcher(
        {
            home: _resp(
                home,
                content=content,
                content_type="text/html; charset=windows-1250",
            ),
            hop: _resp(hop, "<html><body>lista</body></html>"),
        }
    )
    row = hl.scan_homepage(
        _job(cid="Qhu", name="Magyar", url=home),
        hl.Fetcher(get_fn=fake, host_interval=0.0),
        max_hops=8,
    )
    assert hop in row["hop_urls"]


def test_collect_targets_uses_synthetic_cache(tmp_path):
    composers = pd.DataFrame(
        [
            {
                "composer_id": "Q1",
                "name_display": "A",
                "birth_year": 1900,
                "death_year": "",
                "eu_pd_status": "not_pd",
            },
            {
                "composer_id": "Q2",
                "name_display": "B",
                "birth_year": 1800,
                "death_year": 1850,
                "eu_pd_status": "pd",
            },
            {
                "composer_id": "Q3",
                "name_display": "C",
                "birth_year": 1950,
                "death_year": "",
                "eu_pd_status": "not_pd",
            },
        ]
    )
    common.cache_set(
        "wikidata_entity",
        "Q1",
        _entity_p856("https://a.example/", "https://a.example/alt"),
    )
    # Q3: no cached entity
    jobs, stats = hl.collect_targets(composers)
    assert stats["composers_considered"] == 2
    assert stats["with_cached_entity"] == 1
    assert stats["with_p856"] == 1
    assert stats["urls"] == 2
    assert stats["skipped_no_entity"] == 1
    assert all(j["source"] == "p856" for j in jobs)


def test_search_targets_gann_wikipedia_pdf_and_site_root():
    results = pd.DataFrame(
        [
            {
                "composer_id": "Q1795042",
                "name_display": "Kyle Gann",
                "query": "kyle gann scores",
                "rank": 1,
                "url": "http://www.kylegann.com/FugitiveObjects.html",
                "title": "Fugitive Objects",
                "batch": 1,
            },
            {
                "composer_id": "Q1795042",
                "name_display": "Kyle Gann",
                "query": "kyle gann scores",
                "rank": 2,
                "url": "https://en.wikipedia.org/wiki/Kyle_Gann",
                "title": "Kyle Gann - Wikipedia",
                "batch": 1,
            },
            {
                "composer_id": "Q1795042",
                "name_display": "Kyle Gann",
                "query": "kyle gann scores",
                "rank": 3,
                "url": "http://www.kylegann.com/SomePiece.pdf",
                "title": "PDF",
                "batch": 1,
            },
            {
                "composer_id": "Q999",
                "name_display": "Nobody",
                "query": "nobody",
                "rank": 0,
                "url": "",
                "title": "",
                "batch": 1,
            },
        ]
    )
    jobs, stats = hl.collect_search_targets(results)
    urls = {(j["homepage_url"], j["source"]) for j in jobs}
    assert ("http://www.kylegann.com/FugitiveObjects.html", "search_result") in urls
    assert ("http://www.kylegann.com/SomePiece.pdf", "search_result") in urls
    assert ("http://www.kylegann.com/", "search_site_root") in urls
    assert not any("wikipedia.org" in j["homepage_url"] for j in jobs)
    assert not any(j["composer_id"] == "Q999" for j in jobs)
    assert stats["skip_reasons"].get("denied:wikipedia.org", 0) >= 1
    assert stats["skip_reasons"].get("rank0_no_results", 0) >= 1

    piece = "http://www.kylegann.com/FugitiveObjects.html"
    root = "http://www.kylegann.com/"
    music = "http://www.kylegann.com/Gannmusic.html"
    pdf = "http://www.kylegann.com/SomePiece.pdf"
    fake = MapFetcher(
        {
            piece: _resp(piece, "<html><body><p>Kyle Gann — Fugitive Objects</p></body></html>"),
            root: _resp(root, _html("kylegann_index.html")),
            music: _resp(music, _html("kylegann_Gannmusic.html")),
            pdf: _resp(pdf, content=b"%PDF-1.4", content_type="application/pdf"),
        }
    )
    fetcher = hl.Fetcher(get_fn=fake, host_interval=0.0)
    rows = hl.run_jobs(jobs, fetcher, max_hops=8, workers=1)
    by_url = {r["homepage_url"]: r for r in rows}
    assert by_url[pdf]["fetch_status"] == "not_html"
    root_row = by_url[root]
    assert root_row["source"] == "search_site_root"
    assert root_row["licences"] == "CC-BY-NC-3.0"
    assert root_row["candidate"] == "true"
    assert root_row["name_on_page"] == "true"
    assert music in root_row["hop_urls"]


def test_search_per_host_cap_of_three():
    rows = []
    for i in range(5):
        rows.append(
            {
                "composer_id": "Q1",
                "name_display": "Test Composer",
                "query": "q",
                "rank": i + 1,
                "url": f"https://scores.example/p{i}.html",
                "title": f"P{i}",
                "batch": 1,
            }
        )
    jobs, stats = hl.collect_search_targets(pd.DataFrame(rows))
    result_jobs = [j for j in jobs if j["source"] == "search_result"]
    assert len(result_jobs) == 3
    assert stats["skip_reasons"].get("per_host_cap", 0) == 2
    assert any(j["source"] == "search_site_root" for j in jobs)


def test_name_on_page_false_for_homonym():
    home = "https://homonym.example/"
    html = "<html><body><p>Works by Jane Doe for piano and choir.</p><a href='a.pdf'>a</a></body></html>"
    fake = MapFetcher({home: _resp(home, html)})
    row = hl.scan_homepage(
        _job(cid="Qm", name="John Mitchell", url=home),
        hl.Fetcher(get_fn=fake, host_interval=0.0),
        max_hops=0,
    )
    assert row["name_on_page"] == "false"


def test_write_output_columns(tmp_path):
    out = tmp_path / "homepages_test.tsv"
    row = hl.scan_homepage(
        _job(url="https://x.example/"),
        hl.Fetcher(
            get_fn=MapFetcher({"https://x.example/": _resp("https://x.example/", "<html></html>")}),
            host_interval=0.0,
        ),
        max_hops=0,
    )
    df = hl.write_output([row], out)
    assert list(df.columns) == hl.OUTPUT_COLUMNS
    for col in ("source", "search_rank", "search_title", "name_on_page"):
        assert col in df.columns


def test_robots_redirect_followed_and_disallow_honoured():
    home = "https://redir-robots.example/"
    robots = "https://redir-robots.example/robots.txt"
    rules = "https://redir-robots.example/robots-rules.txt"
    fake = MapFetcher(
        {
            robots: hl.RawResponse(
                301, robots, headers={"Location": "/robots-rules.txt"}, content=b""
            ),
            rules: _robots(rules, body="User-agent: *\nDisallow: /\n", status=200),
            home: _resp(home, "<html><body>secret</body></html>"),
        }
    )
    row = hl.scan_homepage(
        _job(cid="Qrr", name="RobotsRedir", url=home),
        hl.Fetcher(get_fn=fake, host_interval=0.0),
        max_hops=0,
    )
    assert row["fetch_status"] == "robots_disallowed"
    assert home not in fake.calls
    assert rules in fake.calls


def test_cache_key_no_collision_between_slash_and_underscore_paths():
    a = "https://scores.example/scores/a/b.html"
    b = "https://scores.example/scores/a_b.html"
    fake = MapFetcher(
        {
            a: _resp(
                a,
                '<html><body><a rel="license" href="https://creativecommons.org/licenses/by/4.0/">x</a>'
                '<a href="a.pdf">p</a></body></html>',
            ),
            b: _resp(
                b,
                '<html><body><a rel="license" href="https://creativecommons.org/licenses/by-nc/3.0/">x</a>'
                '<a href="b.pdf">p</a></body></html>',
            ),
        }
    )
    fetcher = hl.Fetcher(get_fn=fake, host_interval=0.0)
    ra = hl.scan_homepage(_job(cid="Qa", name="A", url=a), fetcher, max_hops=0)
    rb = hl.scan_homepage(_job(cid="Qb", name="B", url=b), fetcher, max_hops=0)
    assert ra["licences"] == "CC-BY-4.0"
    assert rb["licences"] == "CC-BY-NC-3.0"
    assert a in fake.calls and b in fake.calls


def test_case_insensitive_content_type_header():
    home = "https://ctype.example/"
    html = "<html><body>café</body></html>"
    # windows-1252 encoding of the page body
    content = html.encode("windows-1252")
    fake = MapFetcher(
        {
            home: _resp(
                home,
                content=content,
                headers={"Content-type": "text/html; charset=windows-1252"},
            ),
        }
    )
    # Drop the default Content-Type from _resp by rebuilding
    fake.mapping[home] = hl.RawResponse(
        200,
        home,
        headers={"Content-type": "text/html; charset=windows-1252"},
        content=content,
    )
    row = hl.scan_homepage(
        _job(cid="Qc", name="Ctype", url=home),
        hl.Fetcher(get_fn=fake, host_interval=0.0),
        max_hops=0,
    )
    assert row["fetch_status"] == "ok"
    assert "café" in (hl.cache_get_url(hl.PAGES_NAMESPACE, home) or {}).get("html", "")


def test_concurrent_shared_hop_fetched_once():
    shared = "https://shared.site.test/scores.html"
    a = "https://a.site.test/"
    b = "https://b.site.test/"
    page = (
        f'<html><body><a href="{shared}">scores catalogue</a></body></html>'
    )
    shared_html = (
        '<html><body><a rel="license" href="https://creativecommons.org/licenses/by/4.0/">x</a>'
        '<a href="x.pdf">pdf</a></body></html>'
    )
    fake = MapFetcher(
        {
            a: _resp(a, page),
            b: _resp(b, page),
            shared: _resp(shared, shared_html),
        }
    )
    fake.set_hold("shared.site.test", 0.05)
    fetcher = hl.Fetcher(get_fn=fake, host_interval=0.0)
    rows = hl.run_jobs(
        [
            _job(cid="Qa", name="A", url=a),
            _job(cid="Qb", name="B", url=b),
        ],
        fetcher,
        max_hops=8,
        workers=2,
    )
    assert len(rows) == 2
    assert fake.calls.count(shared) == 1


def test_text_licence_windows_do_not_mask_later_declaration():
    home = "https://two-cc.example/"
    html = (
        "<html><body><p>CC BY 4.0. Some works are under CC BY-NC 3.0.</p>"
        "<a href='a.pdf'>a</a></body></html>"
    )
    fake = MapFetcher({home: _resp(home, html)})
    row = hl.scan_homepage(
        _job(cid="Qt2", name="Two", url=home),
        hl.Fetcher(get_fn=fake, host_interval=0.0),
        max_hops=0,
    )
    assert "CC-BY-4.0" in row["licences"]
    assert "CC-BY-NC-3.0" in row["licences"]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("CC BY 4.0. Some works are under CC0.", {"CC-BY-4.0", "CC0-1.0"}),
        (
            "Scores under Creative Commons Attribution 4.0. Older works under "
            "Creative Commons Attribution-NonCommercial 3.0.",
            {"CC-BY-4.0", "CC-BY-NC-3.0"},
        ),
        ("Creative Commons Attribution-NonCommercial (CC BY-NC) 4.0", {"CC-BY-NC-4.0"}),
    ],
)
def test_text_licence_windows_stop_at_next_declaration(text, expected):
    found = hl.extract_licence_evidence("https://x.example/", f"<p>{text}</p>")
    assert {spdx for spdx, kind, _ in found if kind == "cc_text"} == expected


def test_real_transport_never_reads_non_html_body():
    class Resp:
        status_code = 200
        headers = {"Content-Type": "application/pdf"}
        read = False

        def iter_content(self, chunk_size=0):
            Resp.read = True
            yield b"%PDF-1.4"

        def close(self):
            pass

    class Session:
        def get(self, url, **kw):
            return Resp()

    raw = hl._default_get_once(Session(), "https://x.example/a")
    assert raw.headers["content-type"] == "application/pdf"
    assert not Resp.read


def test_creative_commons_attribution_noncommercial_still_parses():
    home = "https://gann-text.example/"
    html = (
        "<html><body><p>licensed under a Creative Commons "
        "Attribution-NonCommercial 3.0 Unported License</p>"
        "<a href='a.pdf'>a</a></body></html>"
    )
    fake = MapFetcher({home: _resp(home, html)})
    row = hl.scan_homepage(
        _job(cid="Qg", name="Gann", url=home),
        hl.Fetcher(get_fn=fake, host_interval=0.0),
        max_hops=0,
    )
    assert row["licences"] == "CC-BY-NC-3.0"


def test_rel_copyright_is_licence_hop():
    home = "https://copy.example/"
    legal = "https://copy.example/legal"
    html = (
        '<html><head><link rel="copyright" href="/legal"></head>'
        "<body><p>home</p></body></html>"
    )
    fake = MapFetcher(
        {
            home: _resp(home, html),
            legal: _resp(legal, "<html><body>terms</body></html>"),
        }
    )
    row = hl.scan_homepage(
        _job(cid="Qcr", name="Copy", url=home),
        hl.Fetcher(get_fn=fake, host_interval=0.0),
        max_hops=8,
    )
    assert legal in row["hop_urls"]


def test_marketplace_deny_label_equality():
    assert hl.denied_host_reason("amazon.com.be") == "amazon"
    assert hl.denied_host_reason("www.amazon.com.sa") == "amazon"
    assert hl.denied_host_reason("ebay.com.my") == "ebay"
    assert hl.denied_host_reason("s3.amazonaws.com") is None


def test_retry_errors_refetches_cached_network_error():
    home = "https://flaky.example/"
    # Seed a cached network error.
    hl.cache_set_url(
        hl.PAGES_NAMESPACE,
        home,
        {
            "url": home,
            "final_url": home,
            "status_code": 0,
            "content_type": "",
            "html": "",
            "error": "error:ConnectionError",
            "fetched_at": "2020-01-01T00:00:00Z",
        },
    )
    fake = MapFetcher({home: _resp(home, "<html><body>recovered</body></html>")})

    reused = hl.Fetcher(get_fn=fake, host_interval=0.0, retry_errors=False)
    row1 = hl.scan_homepage(_job(cid="Qf", name="Flaky", url=home), reused, max_hops=0)
    assert row1["fetch_status"] == "error:ConnectionError"
    assert home not in fake.calls

    retried = hl.Fetcher(get_fn=fake, host_interval=0.0, retry_errors=True)
    row2 = hl.scan_homepage(_job(cid="Qf", name="Flaky", url=home), retried, max_hops=0)
    assert row2["fetch_status"] == "ok"
    assert home in fake.calls


def test_missing_content_type_sniff_html_and_reject():
    ok = "https://noct.example/ok"
    bad = "https://noct.example/bad"
    fake = MapFetcher(
        {
            ok: hl.RawResponse(
                200,
                ok,
                headers={},  # no Content-Type
                content=b"<!DOCTYPE html><html><head></head><body>hi</body></html>",
            ),
            bad: hl.RawResponse(
                200,
                bad,
                headers={},
                content=b"%PDF-1.4 binary junk",
            ),
        }
    )
    fetcher = hl.Fetcher(get_fn=fake, host_interval=0.0)
    assert hl.scan_homepage(_job(cid="Q1", name="Ok", url=ok), fetcher, max_hops=0)[
        "fetch_status"
    ] == "ok"
    assert hl.scan_homepage(_job(cid="Q2", name="Bad", url=bad), fetcher, max_hops=0)[
        "fetch_status"
    ] == "not_html"


def test_search_results_tsv_ingestion(tmp_path):
    import csv

    path = tmp_path / "search_results_r019_test.tsv"
    # Real header; CSV-quoted query (`"Kyle Gann" score`); rank-0 empty url.
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter="\t", quoting=csv.QUOTE_MINIMAL)
        w.writerow(
            ["composer_id", "name_display", "query", "rank", "url", "title", "batch"]
        )
        w.writerow(
            [
                "Q1795042",
                "Kyle Gann",
                '"Kyle Gann" score',
                1,
                "http://www.kylegann.com/FugitiveObjects.html",
                "Fugitive",
                1,
            ]
        )
        w.writerow(["Q1795042", "Kyle Gann", "nothing", 0, "", "", 1])
    results = pd.read_csv(path, sep="\t", low_memory=False)
    jobs, stats = hl.collect_search_targets(results)
    assert any(
        j["homepage_url"] == "http://www.kylegann.com/FugitiveObjects.html"
        and j["source"] == "search_result"
        for j in jobs
    )
    assert any(j["source"] == "search_site_root" for j in jobs)
    assert stats["skip_reasons"].get("rank0_no_results", 0) >= 1
    assert any('"Kyle Gann" score' == str(q) for q in results["query"].astype(str))
