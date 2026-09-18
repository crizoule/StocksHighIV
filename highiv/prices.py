"""Price history for the row charts: 1H, 4H, 1D and 1W closes from Yahoo Finance.

Yahoo serves hourly and daily bars; 4H and 1W are resampled from them. Each frame keeps a
window long enough to read a trend without bloating the page.
"""
from __future__ import annotations

import math
import statistics
import time
from datetime import datetime

import yfinance as yf

from . import config

FRAMES = ("1H", "4H", "1D", "1W", "1M")
# visible bars per frame: ~1 week, ~1 month, ~6 months, ~2 years, ~5 years
BARS = {"1H": 35, "4H": 42, "1D": 126, "1W": 104, "1M": 60}


def _series(closes) -> dict | None:
    """A pandas Series with a tz-aware index -> parallel arrays of epoch seconds and closes."""
    if len(closes) < 2:
        return None
    return {
        "t": [int(stamp.timestamp()) for stamp in closes.index],
        "c": [round(float(v), 4 if abs(float(v)) < 10 else 2) for v in closes.values],
    }


def _frame(closes, bars: int) -> dict | None:
    """One timeframe: the visible window of closes, with MACD trimmed to the same window."""
    series = _series(closes.tail(bars))
    if series is None:
        return None
    lines = macd([float(v) for v in closes.values])
    if lines:
        line, signal = lines
        series["macd"] = [round(v, 4) if v is not None else None for v in line[-bars:]]
        series["signal"] = [round(v, 4) if v is not None else None for v in signal[-bars:]]
    return series


def _ema(values: list[float], span: int) -> list[float | None]:
    """Exponential moving average, seeded with the simple average of the first `span` values."""
    if len(values) < span:
        return [None] * len(values)
    weight = 2 / (span + 1)
    average = statistics.fmean(values[:span])
    out: list[float | None] = [None] * (span - 1) + [average]
    for value in values[span:]:
        average = value * weight + average * (1 - weight)
        out.append(average)
    return out


def macd(closes: list[float], fast: int = 12, slow: int = 26, smoothing: int = 9):
    """MACD line (fast EMA minus slow EMA) and its signal line, aligned with `closes`.

    Computed over the whole history so the EMAs are warmed up before the visible window starts.
    """
    if len(closes) < slow + smoothing:
        return None
    fast_ema, slow_ema = _ema(closes, fast), _ema(closes, slow)
    line = [f - s if f is not None and s is not None else None for f, s in zip(fast_ema, slow_ema)]
    warm = [v for v in line if v is not None]
    signal = [None] * (len(line) - len(warm)) + _ema(warm, smoothing)
    return line, signal


def realized_vol(closes: list[float], window: int = 30) -> float | None:
    """Historical (realized) volatility: annualized standard deviation of daily log returns."""
    if not closes:
        return None
    tail = closes[-(window + 1):]
    returns = [math.log(b / a) for a, b in zip(tail, tail[1:]) if a > 0 and b > 0]
    if len(returns) < 10:
        return None
    return round(statistics.stdev(returns) * math.sqrt(252) * 100, 1)


def rsi(closes: list[float], period: int = 14) -> float | None:
    """Wilder's relative strength index: above 70 is overbought, below 30 oversold."""
    if len(closes) < period + 1:
        return None
    gains = [max(b - a, 0.0) for a, b in zip(closes, closes[1:])]
    losses = [max(a - b, 0.0) for a, b in zip(closes, closes[1:])]
    avg_gain = statistics.fmean(gains[:period])
    avg_loss = statistics.fmean(losses[:period])
    for gain, loss in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0
    return round(100 - 100 / (1 + avg_gain / avg_loss), 1)


def volume_surge(volumes: list[float], window: int = 20) -> float | None:
    """Latest session's volume against the previous `window` sessions: 2.0 means twice normal."""
    if len(volumes) < window + 1:
        return None
    baseline = statistics.fmean(volumes[-(window + 1):-1])
    return round(volumes[-1] / baseline, 2) if baseline > 0 else None


def fetch(yahoo_symbol: str) -> tuple[dict | None, dict]:
    try:
        ticker = yf.Ticker(yahoo_symbol)
        # Three months of hourly bars: the visible windows stay short, but MACD's EMAs warm up first.
        intraday = ticker.history(period="3mo", interval="1h", auto_adjust=False)
        # Ten years of daily bars: monthly MACD needs 35 monthly closes before it has a signal line.
        daily = ticker.history(period="10y", interval="1d", auto_adjust=False)
    except Exception:  # yfinance raises assorted errors when throttled or for dead symbols
        return None, {}
    time.sleep(config.YAHOO_MIN_INTERVAL_S)

    frames = {}
    stats = {"hv30": None, "rsi14": None, "volume_surge": None}
    if len(intraday):
        closes = intraday["Close"].dropna()
        frames["1H"] = _frame(closes, BARS["1H"])
        frames["4H"] = _frame(closes.resample("4h").last().dropna(), BARS["4H"])
    if len(daily):
        closes = daily["Close"].dropna()
        frames["1D"] = _frame(closes, BARS["1D"])
        frames["1W"] = _frame(closes.resample("W-FRI").last().dropna(), BARS["1W"])
        frames["1M"] = _frame(closes.resample("ME").last().dropna(), BARS["1M"])
        # A session still in progress would understate volume and skew RSI, so measure completed days.
        settled = daily
        last = daily.index[-1]
        now = datetime.now(last.tz) if getattr(last, "tz", None) else datetime.now()
        if last.date() == now.date() and now.hour < 16:
            settled = daily.iloc[:-1]
        stats["hv30"] = realized_vol([float(v) for v in settled["Close"].dropna().values])
        stats["rsi14"] = rsi([float(v) for v in settled["Close"].dropna().values])
        stats["volume_surge"] = volume_surge([float(v) for v in settled["Volume"].dropna().values])
    return {frame: series for frame, series in frames.items() if series} or None, stats
