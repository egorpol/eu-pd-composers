"""Shared progress heartbeat for long-running scrape jobs."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Heartbeat:
    """Background logger that proves a long job is still alive.

    Call ``tick()`` (or use as a tqdm callback via ``update``) from the worker
    thread; a daemon thread prints status every ``interval_s`` seconds.
    """

    name: str
    total: Optional[int] = None
    interval_s: float = 30.0
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("heartbeat"))
    done: int = 0
    _started: float = field(default_factory=time.monotonic, init=False, repr=False)
    _last_tick: float = field(default_factory=time.monotonic, init=False, repr=False)
    _stop: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    _thread: Optional[threading.Thread] = field(default=None, init=False, repr=False)

    def start(self) -> "Heartbeat":
        if self._thread and self._thread.is_alive():
            return self
        self._stop.clear()
        self._started = time.monotonic()
        self._last_tick = self._started
        self._thread = threading.Thread(
            target=self._loop,
            name=f"heartbeat-{self.name}",
            daemon=True,
        )
        self._thread.start()
        self.logger.info(
            "heartbeat start [%s] total=%s interval=%.0fs",
            self.name,
            self.total if self.total is not None else "?",
            self.interval_s,
        )
        return self

    def tick(self, n: int = 1) -> None:
        self.done += n
        self._last_tick = time.monotonic()

    def update(self, n: int = 1) -> None:
        """Alias for tqdm-style callbacks."""
        self.tick(n)

    def stop(self, final: bool = True) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=self.interval_s + 1)
        if final:
            self._emit(prefix="heartbeat done")

    def __enter__(self) -> "Heartbeat":
        return self.start()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop(final=True)

    def _loop(self) -> None:
        while not self._stop.wait(self.interval_s):
            self._emit(prefix="heartbeat")

    def _emit(self, prefix: str) -> None:
        now = time.monotonic()
        elapsed = now - self._started
        since_tick = now - self._last_tick
        rate = self.done / elapsed if elapsed > 0 else 0.0
        parts = [
            f"{prefix} [{self.name}]",
            f"done={self.done}",
        ]
        if self.total is not None and self.total > 0:
            pct = 100.0 * self.done / self.total
            remaining = max(self.total - self.done, 0)
            eta_s = remaining / rate if rate > 0 else float("inf")
            parts.append(f"total={self.total}")
            parts.append(f"pct={pct:.1f}%")
            parts.append(f"eta={_fmt_secs(eta_s)}")
        parts.append(f"rate={rate:.2f}/s")
        parts.append(f"elapsed={_fmt_secs(elapsed)}")
        parts.append(f"since_tick={_fmt_secs(since_tick)}")
        if since_tick > self.interval_s * 2:
            parts.append("STALL?")
        self.logger.info(" ".join(parts))


def _fmt_secs(seconds: float) -> str:
    if seconds == float("inf"):
        return "?"
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m{s:02d}s"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"
