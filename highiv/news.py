"""Recent Yahoo headlines with publication dates and links retained for evidence review."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import yfinance as yf

from . import config

MARKET_TZ = ZoneInfo("America/Toronto")


def _iso_day(stamp) -> str | None:
    if not stamp:
        return None
    if isinstance(stamp, (int, float)):
        try:
            return datetime.fromtimestamp(int(stamp), tz=MARKET_TZ).date().isoformat()
        except (OSError, ValueError, OverflowError):
            return None
    text = str(stamp).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if len(text) == 10:
            return parsed.date().isoformat()
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(MARKET_TZ).date().isoformat()
    except ValueError:
        return None


def _normalize(item: dict) -> dict | None:
    body = item.get("content") if isinstance(item.get("content"), dict) else item
    title = (body.get("title") or "").strip()
    if not title:
        return None
    url = None
    for key in ("clickThroughUrl", "canonicalUrl"):
        node = body.get(key)
        if isinstance(node, dict) and node.get("url"):
            url = node["url"]
            break
        if isinstance(node, str) and node.startswith("http"):
            url = node
            break
    url = url or body.get("link") or item.get("link")
    provider = body.get("provider")
    source = None
    if isinstance(provider, dict):
        source = provider.get("displayName")
    source = source or body.get("publisher") or item.get("publisher")
    return {
        "title": title,
        "summary": (body.get("summary") or body.get("description") or "").strip() or None,
        "date": _iso_day(body.get("pubDate") or body.get("displayTime") or item.get("providerPublishTime")),
        "source": source,
        "url": url,
        "type": (body.get("contentType") or item.get("type") or "STORY").upper(),
    }


def fetch(yahoo_symbol: str) -> list[dict] | None:
    """An empty list means no usable headlines; None means the provider could not be read."""
    raw: list = []
    available = False
    for attempt in range(3):
        try:
            ticker = yf.Ticker(yahoo_symbol)
            raw = ticker.get_news(count=config.NEWS_FETCH_COUNT)
            if not isinstance(raw, list):
                raise ValueError("Unexpected news response")
            available = True
            break
        except Exception:
            time.sleep(5 * (attempt + 1))
    time.sleep(config.YAHOO_MIN_INTERVAL_S)
    if not available:
        return None
    out: list[dict] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            row = _normalize(item)
        except (AttributeError, TypeError, ValueError):
            continue
        if not row or row["title"] in seen:
            continue
        seen.add(row["title"])
        out.append(row)
    return out
