"""Offline tests for IMSLP category fetching (mocked HTTP)."""

import pytest

import common
import imslp


class FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append(dict(params or {}))
        return FakeResponse(self.payloads.pop(0))


@pytest.fixture(autouse=True)
def tmp_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "CACHE_DIR", tmp_path)
    return tmp_path


def _cats(*names):
    return [{"ns": 14, "title": f"Category:{n}"} for n in names]


def test_follows_legacy_query_continue_and_merges_pages():
    session = FakeSession(
        [
            {
                "query": {
                    "pages": {
                        "1": {"pageid": 1, "title": "A", "categories": _cats("For piano", "Sonatas")},
                        "2": {"pageid": 2, "title": "B", "categories": _cats("For flute")},
                        "3": {"pageid": 3, "title": "C"},
                    }
                },
                "query-continue": {"categories": {"clcontinue": "2|Romantic style"}},
            },
            {
                "query": {
                    "pages": {
                        "1": {"pageid": 1, "title": "A"},
                        "2": {"pageid": 2, "title": "B", "categories": _cats("Romantic style")},
                        "3": {"pageid": 3, "title": "C", "categories": _cats("For organ")},
                    }
                }
            },
        ]
    )
    out = imslp.fetch_page_categories([1, 2, 3], session, sleep_s=0)

    assert len(session.calls) == 2
    assert session.calls[1]["clcontinue"] == "2|Romantic style"
    assert out[1]["categories"] == ["For piano", "Sonatas"]
    assert out[2]["categories"] == ["For flute", "Romantic style"]
    assert out[3]["categories"] == ["For organ"]
    assert common.cache_get(imslp.PAGE_CATS_NAMESPACE, "2")["categories"] == [
        "For flute",
        "Romantic style",
    ]


def test_follows_modern_continue():
    session = FakeSession(
        [
            {
                "query": {"pages": {"5": {"pageid": 5, "title": "E", "categories": _cats("X")}}},
                "continue": {"clcontinue": "5|Y", "continue": "||"},
            },
            {"query": {"pages": {"5": {"pageid": 5, "title": "E", "categories": _cats("Y")}}}},
        ]
    )
    out = imslp.fetch_page_categories([5], session, sleep_s=0)
    assert out[5]["categories"] == ["X", "Y"]


def test_missing_page_is_flagged_not_silently_empty():
    session = FakeSession([{"query": {"pages": {"9": {"pageid": 9, "missing": ""}}}}])
    out = imslp.fetch_page_categories([9], session, sleep_s=0)
    assert out[9]["missing"] is True
    assert out[9]["categories"] == []


def test_api_error_raises_and_caches_nothing():
    session = FakeSession([{"error": {"code": "internal", "info": "boom"}}])
    with pytest.raises(RuntimeError, match="IMSLP API error"):
        imslp.fetch_page_categories([7], session, sleep_s=0)
    assert common.cache_get(imslp.PAGE_CATS_NAMESPACE, "7") is None


def test_cached_pages_are_not_refetched():
    common.cache_set(imslp.PAGE_CATS_NAMESPACE, "4", {"pageid": 4, "categories": ["For piano"]})
    session = FakeSession([])
    out = imslp.fetch_page_categories([4], session, sleep_s=0)
    assert out[4]["categories"] == ["For piano"]
    assert session.calls == []
