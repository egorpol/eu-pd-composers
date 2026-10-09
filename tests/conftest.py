"""Make scripts/ importable as top-level modules (they import each other that way)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_politeness_delay(monkeypatch):
    """Unit tests use fake sessions; the real per-host cap is tested explicitly."""
    import common

    monkeypatch.setitem(common.MIN_INTERVAL_BY_HOST, "imslp.org", 0.0)
    monkeypatch.setattr(common, "_last_request_at", {})
