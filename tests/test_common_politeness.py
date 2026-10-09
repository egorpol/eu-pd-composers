"""The per-host cap keeps IMSLP at <= 1 request/s on every code path."""

import common


class Resp:
    def __init__(self, status=200, headers=None):
        self.status_code = status
        self.headers = headers or {}

    def raise_for_status(self):
        pass

    def json(self):
        return {}


class Session:
    def __init__(self, responses):
        self.responses = list(responses)

    def get(self, url, params=None, timeout=None):
        return self.responses.pop(0)


def fake_clock(monkeypatch):
    now = [100.0]
    sleeps = []
    monkeypatch.setattr(common.time, "monotonic", lambda: now[0])

    def sleep(seconds):
        sleeps.append(round(seconds, 3))
        now[0] += seconds

    monkeypatch.setattr(common.time, "sleep", sleep)
    return now, sleeps


def test_imslp_requests_are_spaced_by_min_interval(monkeypatch):
    monkeypatch.setitem(common.MIN_INTERVAL_BY_HOST, "imslp.org", 1.0)
    _, sleeps = fake_clock(monkeypatch)
    session = Session([Resp(), Resp(), Resp()])
    for _ in range(3):
        common.request_json(session, common.IMSLP_API, sleep_s=0.0)
    # first request free, then each waits up to the 1 s cap
    assert sleeps == [0.0, 1.0, 0.0, 1.0, 0.0]


def test_other_hosts_are_not_throttled(monkeypatch):
    monkeypatch.setitem(common.MIN_INTERVAL_BY_HOST, "imslp.org", 1.0)
    _, sleeps = fake_clock(monkeypatch)
    session = Session([Resp(), Resp()])
    for _ in range(2):
        common.request_json(session, common.WIKIDATA_API, sleep_s=0.0)
    assert sleeps == [0.0, 0.0]


def test_retry_after_is_honoured(monkeypatch):
    monkeypatch.setitem(common.MIN_INTERVAL_BY_HOST, "imslp.org", 0.0)
    _, sleeps = fake_clock(monkeypatch)
    session = Session([Resp(503, {"Retry-After": "7"}), Resp()])
    common.request_json(session, common.IMSLP_API, sleep_s=0.0)
    assert 7.0 in sleeps
