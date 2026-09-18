"""HTTP helpers: a shared client and polite, rate-limited GETs."""
from __future__ import annotations

import math
import time
from collections import deque
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx

from . import config


class FetchError(RuntimeError):
    """A provider request failed and should be retried on the next scan."""


def _retry_delay(value: str | None) -> float:
    """Accept seconds or an HTTP date, falling back to a minute for invalid headers."""
    try:
        delay = float(value)
    except (TypeError, ValueError):
        try:
            when = parsedate_to_datetime(value)
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            delay = (when - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            delay = 60.0
    if not math.isfinite(delay):
        delay = 60.0
    return max(0.0, delay) + 1


class RateLimiter:
    """Allow at most `max_calls` in any rolling `window` seconds, spaced by `min_interval`."""

    def __init__(self, max_calls: int = 10**9, window: float = 60.0, min_interval: float = 0.0):
        self.max_calls = max_calls
        self.window = window
        self.min_interval = min_interval
        self._calls: deque[float] = deque()

    def wait(self) -> None:
        now = time.monotonic()
        while self._calls and now - self._calls[0] >= self.window:
            self._calls.popleft()
        if len(self._calls) >= self.max_calls:
            time.sleep(self.window - (now - self._calls[0]) + 0.1)
        if self._calls and self.min_interval:
            gap = time.monotonic() - self._calls[-1]
            if gap < self.min_interval:
                time.sleep(self.min_interval - gap)
        self._calls.append(time.monotonic())

    def pause(self, seconds: float) -> None:
        """Back off after a 429, then start a fresh window."""
        time.sleep(seconds)
        self._calls.clear()


def make_client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": config.USER_AGENT, "Accept": "application/json, text/html;q=0.9, */*;q=0.8"},
        timeout=30.0,
        follow_redirects=True,
    )


def get(
    client: httpx.Client,
    url: str,
    limiter: RateLimiter | None = None,
    params: dict | None = None,
    retries: int = 4,
) -> httpx.Response | None:
    """GET with rate limiting and retries on 429/5xx/network errors. None if every attempt failed."""
    for attempt in range(retries):
        if limiter:
            limiter.wait()
        try:
            resp = client.get(url, params=params)
        except httpx.TransportError:
            time.sleep(2**attempt)
            continue
        if resp.status_code == 429:
            delay = _retry_delay(resp.headers.get("retry-after"))
            if limiter:
                limiter.pause(delay)
            else:
                time.sleep(delay)
            continue
        if resp.status_code >= 500:
            time.sleep(2**attempt)
            continue
        return resp
    return None
