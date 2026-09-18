"""Implied-volatility sources.

- CBOE delayed quotes: a standardized 30-day IV (`iv30`) for US-listed stocks.
- Montreal Exchange quote pages: per-series IV for TSX stocks, turned into an at-the-money 30-day IV.
- AlphaQuery: ~3 months of daily 30-day IV, used once per symbol to bootstrap IV rank.
"""
from __future__ import annotations

import html
import json
import math
import re
from datetime import date, datetime
from statistics import mean

import httpx

from . import net

CBOE_QUOTE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/quotes/{symbol}.json"
MX_QUOTES_URL = "https://www.m-x.ca/en/trading/data/quotes"
ALPHAQUERY_URL = "https://www.alphaquery.com/data/option-statistic-chart"

_MX_ROW = re.compile(r"data-row='(\{.*?\})'")
_TAGS = re.compile(r"<script.*?</script>|<[^>]+>", re.S)


def cboe_iv30(client: httpx.Client, limiter: net.RateLimiter, symbol: str) -> dict | None:
    resp = net.get(client, CBOE_QUOTE_URL.format(symbol=symbol), limiter)
    if resp is not None and resp.status_code == 404:
        return None
    if resp is None or resp.status_code != 200:
        raise net.FetchError(f"Cboe quote unavailable for {symbol}")
    try:
        payload = resp.json()
    except ValueError as exc:
        raise net.FetchError(f"Invalid Cboe quote for {symbol}") from exc
    data = payload.get("data") or {}
    iv = data.get("iv30")
    if not iv or iv <= 0:
        return None
    session = (data.get("last_trade_time") or payload.get("timestamp") or "")[:10]
    return {
        "iv30": round(float(iv), 3),
        "iv30_change": data.get("iv30_change"),
        "price": data.get("current_price") or data.get("close"),
        "quote_date": session or None,
    }


def _number(pattern: str, text: str) -> float | None:
    match = re.search(pattern, text)
    return float(match.group(1).replace(",", "")) if match else None


def _atm_iv(strikes: dict[float, list[float]], spot: float) -> float:
    """IV at the spot price, interpolated between the strikes on either side (calls and puts averaged)."""
    below = [k for k in strikes if k <= spot]
    above = [k for k in strikes if k >= spot]
    if below and above:
        k1, k2 = max(below), min(above)
        v1, v2 = mean(strikes[k1]), mean(strikes[k2])
        return v1 if k1 == k2 else v1 + (v2 - v1) * (spot - k1) / (k2 - k1)
    nearest = min(strikes, key=lambda k: abs(k - spot))
    return mean(strikes[nearest])


def _iv_at_30_days(points: list[tuple[int, float]]) -> float | None:
    """Interpolate total variance between the expiries bracketing 30 days."""
    if not points:
        return None
    lower = [p for p in points if p[0] <= 30]
    upper = [p for p in points if p[0] > 30]
    if lower and upper:
        (t1, v1), (t2, v2) = lower[-1], upper[0]
        variance = v1**2 * t1 + (v2**2 * t2 - v1**2 * t1) * (30 - t1) / (t2 - t1)
        return math.sqrt(variance / 30)
    return (upper[0] if upper else lower[-1])[1]


def mx_iv30(client: httpx.Client, limiter: net.RateLimiter, root: str) -> dict | None:
    resp = net.get(client, MX_QUOTES_URL, limiter, params={"symbol": root})
    if resp is not None and resp.status_code == 404:
        return None
    if resp is None or resp.status_code != 200:
        raise net.FetchError(f"Montréal Exchange quote unavailable for {root}")
    page = resp.text
    rows = _MX_ROW.findall(page)
    if not rows:
        return None  # no listed options for this root
    text = re.sub(r"\s+", " ", html.unescape(_TAGS.sub(" ", page)))
    spot = _number(r"Last price:\s*([\d,.]+)", text)
    if not spot:
        bid, ask = _number(r"Bid price:\s*([\d,.]+)", text), _number(r"Ask price:\s*([\d,.]+)", text)
        spot = (bid + ask) / 2 if bid and ask else None
    if not spot:
        return None
    updated = re.search(r"Last update:\s*([A-Z][a-z]+ \d{1,2}, \d{4})", text)
    session = datetime.strptime(updated.group(1), "%B %d, %Y").date() if updated else date.today()

    chain: dict[str, dict[float, list[float]]] = {}
    for raw in rows:
        pair = json.loads(html.unescape(raw))
        for side in ("call", "put"):
            option = pair.get(side) or {}
            vol = option.get("volatility")
            if vol and 1 < vol < 500:
                strikes = chain.setdefault(option["expiry_date"], {})
                strikes.setdefault(float(option["strike_price"]), []).append(float(vol))

    points = []
    for expiry, strikes in chain.items():
        days = (date.fromisoformat(expiry) - session).days
        if days >= 7:  # the last week before expiry is too noisy
            points.append((days, _atm_iv(strikes, spot)))
    iv30 = _iv_at_30_days(sorted(points))
    if iv30 is None:
        return None
    return {"iv30": round(iv30, 3), "iv30_change": None, "price": spot, "quote_date": session.isoformat()}


def alphaquery_history(client: httpx.Client, limiter: net.RateLimiter, symbol: str) -> list[tuple[str, float]]:
    params = {"ticker": symbol, "perType": "30-Day", "identifier": "iv-mean"}
    resp = net.get(client, ALPHAQUERY_URL, limiter, params=params)
    if resp is None or resp.status_code != 200:
        return []
    try:
        points = resp.json()
    except ValueError:
        return []
    return [(p["x"][:10], round(p["value"] * 100, 3)) for p in points if p.get("value")]
