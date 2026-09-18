"""Float, short interest, 52-week range and next earnings date from Yahoo Finance."""
from __future__ import annotations

import time
from datetime import datetime, timezone

import yfinance as yf

from . import config


def _date(timestamp) -> str | None:
    if not timestamp:
        return None
    try:
        return datetime.fromtimestamp(int(timestamp), tz=timezone.utc).date().isoformat()
    except (TypeError, ValueError, OSError):
        return None


def _info(yahoo_symbol: str) -> dict:
    for attempt in range(3):
        try:
            return yf.Ticker(yahoo_symbol).info or {}
        except Exception:  # yfinance raises assorted errors when throttled
            time.sleep(5 * (attempt + 1))
    return {}


def fetch(yahoo_symbol: str) -> dict | None:
    info = _info(yahoo_symbol)
    time.sleep(config.YAHOO_MIN_INTERVAL_S)
    if not info.get("quoteType"):
        return None

    float_shares = info.get("floatShares")
    shares_out = info.get("sharesOutstanding") or info.get("impliedSharesOutstanding")
    # Yahoo sometimes reports another share class's float; drop values that cannot be right.
    if float_shares and shares_out and not 0.02 <= float_shares / shares_out <= 1.05:
        float_shares = None
    shares_short = info.get("sharesShort")
    short_pct = info.get("shortPercentOfFloat")
    if short_pct is None and shares_short and float_shares:
        short_pct = shares_short / float_shares

    return {
        "details_fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "name": info.get("longName") or info.get("shortName"),
        "country": info.get("country"),
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "currency": info.get("currency"),
        "market_cap": info.get("marketCap"),
        "price": info.get("currentPrice") or info.get("regularMarketPrice"),
        "low_52w": info.get("fiftyTwoWeekLow"),
        "high_52w": info.get("fiftyTwoWeekHigh"),
        "float_shares": float_shares,
        "shares_outstanding": shares_out,
        "shares_short": shares_short,
        "shares_short_prior": info.get("sharesShortPriorMonth"),
        "short_pct_float": short_pct,
        "days_to_cover": info.get("shortRatio"),
        "short_date": _date(info.get("dateShortInterest")),
        "avg_volume": info.get("averageVolume"),
        "summary": (info.get("longBusinessSummary") or "").strip() or None,
        # Raw stamps: report.py turns them into a date, an hour and a before/after-the-bell label
        "earnings_ts": info.get("earningsTimestampStart") or info.get("earningsTimestamp"),
        "earnings_ts_end": info.get("earningsTimestampEnd"),
        "earnings_estimate": info.get("isEarningsDateEstimate"),
    }
