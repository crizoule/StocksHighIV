"""Dated sentiment evidence. Missing inputs never become neutral observations.

Network collection is separate from scoring so saved reports can be rendered offline.
Scores are transparent screening heuristics, not forecasts or catalyst attribution.
"""
from __future__ import annotations

import csv
import io
import json
import math
import os
import re
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import quote

import httpx
import pandas as pd
import yfinance as yf

from . import aaii, config, context, fear_greed, market, net, progress

VIX_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
PC_URL = "https://www.cboe.com/us/options/market_statistics/daily/"
AAII_URL = "https://www.aaii.com/sentimentsurvey"
CNN_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
CNN_PAGE = "https://www.cnn.com/markets/fear-and-greed"
MACRO = {
    "vix": ("VIX", VIX_URL, 4),
    "put_call": ("Put/call ratios", PC_URL, 4),
    "aaii": ("AAII sentiment", AAII_URL, 10),
    "cnn": ("Fear & Greed", CNN_PAGE, 2),
    "cot": ("COT positioning", "https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm", 14),  # Tuesday data, out Friday
}
# CFTC's public reporting API: Traders in Financial Futures, futures only. No key; one request returns every week since 2006.
COT_URL = "https://publicreporting.cftc.gov/resource/gpe5-46if.json"
COT_CONTRACTS = {"13874A": "E-mini S&P 500", "1170E1": "VIX futures"}
COT_LOOKBACK = 156  # weeks: the usual three-year "COT index" window
HISTORY_DAYS = 3660               # the chart keeps daily points for 10 years, weekly points before that
EARLIEST = date(1987, 7, 1)       # AAII's survey starts in July 1987, the chart's longest range
PUT_CALL_ARCHIVES = (             # Cboe's discontinued daily files; the newer one wins where they overlap
    "https://cdn.cboe.com/resources/options/volume_and_call_put_ratios/equitypcarchive.csv",  # Oct 2003 – Jun 2012
    "https://cdn.cboe.com/resources/options/volume_and_call_put_ratios/equitypc.csv",         # Nov 2006 – Oct 2019
)
TTL_HOURS = {("history", "put_call_archive"): 24 * 30}  # Cboe no longer updates these files
COMPLETE = {  # readings saved before 1.6.0 hold only 10 years of history
    ("history", "spx"): lambda item: (item.get("points") or [["9999"]])[0][0] <= "1987-07-31" and bool(item.get("rsi")),
    ("macro", "vix"): lambda item: (item.get("history") or [["9999"]])[0][0] <= "1990-01-31",
}
SESSION_KINDS = {"macro", "history", "benchmarks"}  # news and social posts keep arriving while markets are closed
NEWS_URL = "https://www.alphavantage.co/query"
NEWS_WINDOW_DAYS = 7              # articles are kept this long, so thin coverage of small caps accumulates
NEWS_DAILY_REQUESTS = 25          # Alpha Vantage's free daily allowance: the shared feed plus per-ticker asks
NEWS_MIN_ARTICLES = 3             # a ticker the shared feed covers this well needs no request of its own
NEWS_MIN_INTERVAL_S = 13.0        # the same free plan allows about five requests a minute
NEWS_BUDGET_SECONDS = 120         # per refresh; the rest of the day's allowance goes to the next ones
RSI_PERIOD = 14                   # technical context on the S&P 500 itself, computed from the closes already downloaded
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
CNN_HISTORY_DAYS = 1826           # CNN's feed rejects start dates before its history (late 2020)
REPLICA_MAX_AGE = 4
REPLICA_HISTORY = "20y"           # 52-week highs, 20-session smoothing, 125-session z-scores, 500-session ranks, then history
PUT_CALL_BACKFILL_SECONDS = 180   # Cboe history fills over a few refreshes, never in one long burst
PUT_CALL_FILLED = 0.99            # share of sessions stored that counts as a complete history
SECTORS = {
    "technology": "XLK", "financial services": "XLF", "financials": "XLF",
    "healthcare": "XLV", "health care": "XLV", "consumer cyclical": "XLY",
    "consumer discretionary": "XLY", "consumer defensive": "XLP", "consumer staples": "XLP",
    "communication services": "XLC", "energy": "XLE", "industrials": "XLI",
    "basic materials": "XLB", "materials": "XLB", "real estate": "XLRE", "utilities": "XLU",
}
WEIGHTS = {"stock": 40, "sector": 30, "news": 20, "social": 10}
METHOD = (
    "0–100 screening score, not a probability or recommendation. Stock momentum 40%, sector momentum 30%, "
    "news 20%, social 10%; only fresh available components are reweighted. A rank requires both stock and "
    "sector prices. Below 40 = Negative, 40–60 = Mixed, above 60 = Positive. Compare coverage as well as score. "
    "Momentum is observed performance, not measured investor opinion. No score establishes why IV moved."
)


def number(value, low=-math.inf, high=math.inf):
    if isinstance(value, bool):
        raise ValueError("Boolean is not a reading")
    value = float(value)
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError("Invalid reading")
    return value


def iso_date(value):
    return date.fromisoformat(str(value)[:10])


def fresh(as_of, today, max_age=4):
    try:
        return 0 <= (today - iso_date(as_of)).days <= max_age
    except (TypeError, ValueError):
        return False


def label(score):
    return "Positive" if score > 60 else "Negative" if score < 40 else "Mixed"


def bounded(score):
    return round(max(0, min(100, score)), 1)


class PageText(HTMLParser):
    """Read visible text only; scripts and navigation dates must not become readings."""
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def page_text(html):
    parser = PageText()
    parser.feed(html)
    return re.sub(r"\s+", " ", " ".join(parser.parts))


def parse_vix(text, today):
    rows = []
    for item in csv.DictReader(io.StringIO(text)):
        when = datetime.strptime(item["DATE"], "%m/%d/%Y").date()
        if when <= today:
            rows.append((when, number(item["CLOSE"], 0.01, 200)))
    when, value = max(rows)
    return dict(as_of=when.isoformat(), value=value, reading=f"{value:.2f}",
                history=[[d.isoformat(), v] for d, v in thinned(sorted(rows), when - timedelta(days=HISTORY_DAYS)) if d >= EARLIEST],
                signal="Calm" if value < 20 else "Elevated uncertainty" if value < 30 else "High stress",
                direction=1 if value < 20 else 0 if value < 30 else -1,
                detail="Cboe daily close; expected 30-day S&P 500 volatility, not direction. Heuristic: <20 calm, 20–30 elevated, ≥30 stressed.")


def parse_put_call(html, today):
    # Cboe's Next.js page embeds a dated optionsData object; never use today's date as a substitute.
    decoded = html.replace('\\"', '"')
    dates = set(re.findall(r'"selectedDate"\s*:\s*"(\d{4}-\d{2}-\d{2})"', decoded))
    if len(dates) != 1:
        raise ValueError("Missing or ambiguous options session")
    values = {}
    for key, name in (("equity", "EQUITY"), ("total", "TOTAL"), ("index", "INDEX")):
        match = re.search(r'"name"\s*:\s*"' + name + r' PUT/CALL RATIO"\s*,\s*"value"\s*:\s*"([\d.]+)"', decoded)
        if match:
            values[key] = number(match[1], 0, 100)
    equity = values["equity"]
    return dict(as_of=dates.pop(), value=equity, ratios=values, reading=f"{equity:.2f} equity",
                signal="Call-heavy" if equity < 0.6 else "Put-heavy" if equity > 1 else "Balanced",
                direction=1 if equity < 0.6 else -1 if equity > 1 else 0,
                detail="Cboe daily volume ratios, not all-market order flow. Equity <0.60 = call-heavy, >1 = put-heavy (heuristic). Index/total shown separately; hedges, spreads and early exercise can distort direction.")


def parse_aaii(html, today):
    text = page_text(html)
    # Require all three percentages immediately after a dated observation, in the documented order.
    pattern = r"Week ending\s+([A-Za-z]+ \d{1,2}, \d{4})\s+Bullish\s+([\d.]+)%\s+(?:Avg [\d.]+%\s+)?Neutral\s+([\d.]+)%\s+(?:Avg [\d.]+%\s+)?Bearish\s+([\d.]+)%"
    match = re.search(pattern, text, re.I)
    if match:
        when = datetime.strptime(match[1], "%B %d, %Y").date()
        values = match.group(2, 3, 4)
    else:
        # Older page layout: explicitly labeled recent-results table, not historical averages.
        section = re.search(r"Recent weekly results.*?Bullish\s+Neutral\s+Bearish\s+(\d{1,2}/\d{1,2}/\d{4})\s+([\d.]+)%\s+([\d.]+)%\s+([\d.]+)%", text, re.I)
        if not section:
            raise ValueError("No dated AAII observation")
        when = datetime.strptime(section[1], "%m/%d/%Y").date()
        values = section.group(2, 3, 4)
    bull, neutral, bear = [number(v, 0, 100) for v in values]
    if abs(bull + neutral + bear - 100) > 0.3:
        raise ValueError("AAII percentages do not total 100")
    return aaii.summary(when, bull, neutral, bear)


def parse_cnn(text, today):
    data = json.loads(text)
    item = data["fear_and_greed"]
    value = number(item["score"], 0, 100)
    when = datetime.fromisoformat(item["timestamp"].replace("Z", "+00:00"))
    if when.tzinfo is None:
        raise ValueError("CNN timestamp has no timezone")
    history = {}
    for point in (data.get("fear_and_greed_historical") or {}).get("data", []):
        try:  # closed sessions are stamped at 00:00 UTC of the market date
            day = datetime.fromtimestamp(point["x"] / 1000, timezone.utc).date()
            history[day] = round(number(point["y"], 0, 100), 1)
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            continue
    return dict(as_of=when.date().isoformat(), observed_at=when.isoformat(), value=value, reading=f"{value:.1f}/100",
                history=[[d.isoformat(), history[d]] for d in sorted(history) if d <= today],
                signal=str(item["rating"]).title(), direction=1 if value >= 55 else -1 if value <= 45 else 0,
                detail="CNN’s seven-component Fear & Greed index. Includes volatility and options inputs already shown here, so it is not an independent confirmation. Public website feed may be unavailable; no official API guarantee.")


def request_text(client, url, errors="strict", **kwargs):
    # Bounded requests, no anti-bot workarounds and no leaking API keys in failure messages.
    with client.stream("GET", url, **kwargs) as response:
        if response.status_code != 200:
            raise ValueError(f"Provider unavailable (HTTP {response.status_code})")
        data = bytearray()
        for chunk in response.iter_bytes():
            data.extend(chunk)
            if len(data) > 3_000_000:
                raise ValueError("Provider response too large")
    return data.decode("utf-8", errors)


def session_of(item):
    """The market date a reading ends on: its as-of date, last history point, or last benchmark bar."""
    if item.get("as_of"):
        return str(item["as_of"])[:10]
    if item.get("points"):
        return item["points"][-1][0]
    stamps = ((item.get("prices") or {}).get("1D") or {}).get("t")
    return datetime.fromtimestamp(stamps[-1], context.MARKET_TZ).date().isoformat() if stamps else None


def settled(saved, now):
    """True while the market stays closed after the session this reading already holds.

    Nothing new trades until the next open, so a copy fetched after the close is reused overnight and over weekends.
    """
    session, next_open = market.last_session(now)
    fetched = datetime.fromisoformat(saved["fetched_at"])
    closed = market.settled_at(session)
    # A replica still filling its Cboe history keeps the usual expiry, so the backfill finishes over a few refreshes.
    filling = any(str(part.get("detail") or "").startswith("Building") for part in saved.get("components") or [])
    return closed <= fetched <= now < next_open and session_of(saved) == session.isoformat() and not filling


def cached_read(key, fetch, now, ttl_hours=6, complete=None, sessions=False):
    """Cache successful observations only. Keep provenance when refresh fails.

    `complete` rejects a fresh copy that holds less than this version needs (saved by an older version), so it is fetched again.
    `sessions` also reuses a copy that already holds the latest closed session until the next open (see `settled`).
    """
    path = config.DATA_DIR / "sentiment" / f"{key}.json"
    saved = None
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
        age = (now - datetime.fromisoformat(saved["fetched_at"])).total_seconds()
        if (complete is None or complete(saved)) and (0 <= age < ttl_hours * 3600 or sessions and settled(saved, now)):
            return saved
    except (OSError, ValueError, TypeError, KeyError):
        saved = None
    try:
        result = {**fetch(), "fetched_at": now.isoformat(), "status": "ok"}
    except Exception:
        return {**(saved or {}), "status": "cached" if saved else "unavailable",
                "checked_at": now.isoformat(), "error": "Provider blocked, unavailable, or returned an unrecognized response."}
    write_json(path, result)
    return result


def write_json(path, data):
    """Atomic replace, so an interrupted refresh never leaves a truncated file behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            temporary = handle.name
            json.dump(data, handle, allow_nan=False)
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def bar_chunks(symbols, *, actions=False, period=REPLICA_HISTORY, chunk=150):
    for start in range(0, len(symbols), chunk):
        frame = yf.download(list(symbols[start:start + chunk]), period=period, interval="1d", auto_adjust=False,
                            actions=actions, group_by="column", progress=False, threads=True, timeout=20)
        if frame is not None and not frame.empty:
            yield frame


def download_bars(symbols, *, actions=False, period=REPLICA_HISTORY, chunk=150):
    frames = list(bar_chunks(symbols, actions=actions, period=period, chunk=chunk))
    if not frames:
        raise ValueError("No price history")
    return pd.concat(frames, axis=1, sort=True)


def nyse_totals(symbols, today, market_hour, *, period=REPLICA_HISTORY, chunk=150):
    """Daily new highs, lows and up/down volume over the NYSE universe, one group of stocks at a time.

    Each group is reduced to counts and released, so twenty years of history costs about 280 MB rather than 900 MB.
    """
    totals = None
    for frame in bar_chunks(symbols, period=period, chunk=chunk):
        bars = fear_greed.completed(frame, today, market_hour)
        part = fear_greed.nyse_counts(bars["Close"], bars["High"], bars["Low"], bars["Volume"])
        totals = part if totals is None else totals.add(part, fill_value=0)
    if totals is None:
        raise ValueError("No price history")
    return totals


def nyse_symbols():
    data = json.loads((config.DATA_DIR / "universe.json").read_text(encoding="utf-8"))
    return sorted({s["yahoo"] for s in data["stocks"] if s.get("exchange") == "NYSE" and s.get("yahoo")})


def put_call_history(client, sessions, deadline, workers=4):
    """Stored Cboe volumes by session, filling missing sessions newest first until the deadline.

    Old sessions take Cboe several seconds to render, so a few run concurrently; the shared limiter
    still caps the request rate at the scan's Cboe politeness limit.
    """
    path = config.DATA_DIR / "sentiment" / "put_call_history.json"
    try:
        history = json.loads(path.read_text(encoding="utf-8"))["sessions"]
    except (OSError, ValueError, TypeError, KeyError):
        history = {}
    limiter = net.RateLimiter(config.CBOE_MAX_PER_MINUTE, 60.0, 60.0 / config.CBOE_MAX_PER_MINUTE)
    lock = threading.Lock()
    failures = []

    def fetch(day):
        if time.monotonic() > deadline or len(failures) >= 5:
            return day, None
        with lock:
            limiter.wait()
        try:
            when, volumes = fear_greed.parse_put_call_volumes(request_text(client, PC_URL, params={"dt": day}))
        except Exception:
            when = volumes = None
        if when != day:  # blocked, unparseable, or another session; never relabel it
            failures.append(day)
            return day, None
        failures.clear()
        return day, volumes

    added = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for day, volumes in pool.map(fetch, [d.isoformat() for d in reversed(sessions) if d.isoformat() not in history]):
            if volumes:
                history[day], added = volumes, added + 1
                if added % 25 == 0:
                    write_json(path, {"sessions": history})
    if added:
        write_json(path, {"sessions": history})
    return history


def put_call_archive():
    """Cboe's saved 2003–2019 daily ratios, so the replica's older years keep their put/call component."""
    try:
        return json.loads((config.DATA_DIR / "sentiment" / "history-put_call_archive.json").read_text(encoding="utf-8"))["daily"]
    except (OSError, ValueError, TypeError, KeyError):
        return []


def put_call_window(sessions, archive, rank_window):
    """Sessions to fetch from Cboe: every one after its archive files end, so the two sources join without a gap.

    Cboe's daily page starts on 2019-10-07, the session after the last archive file ends, and the app stores what it
    fetches, so the gap fills over a few refreshes. Without the archive, only the rank window is worth fetching.
    """
    if archive:
        end = iso_date(archive[-1][0])
        after = [day for day in sessions if day > end]
        if after:
            return after
    return sessions[-rank_window:]


def fear_greed_inputs(client, now, *, period=REPLICA_HISTORY, put_call_sessions=fear_greed.PUT_CALL_SESSIONS,
                      backfill_seconds=PUT_CALL_BACKFILL_SECONDS):
    local = now.astimezone(context.MARKET_TZ)
    bars = fear_greed.completed(download_bars(fear_greed.INDEX_SYMBOLS, actions=True, period=period), local.date(), local.hour)
    sessions = [d.date() for d in bars["Close"]["^GSPC"].dropna().index]
    archive = put_call_archive()
    recent = put_call_window(sessions, archive, put_call_sessions)  # fetched from Cboe; earlier ones come from its archive
    inputs = fear_greed.index_inputs(bars["Close"], bars["Dividends"])
    notes = {}
    with ThreadPoolExecutor(max_workers=1) as pool:
        # Cboe and Yahoo are separate hosts, so the history backfill overlaps the stock download.
        stored = pool.submit(put_call_history, client, recent, time.monotonic() + backfill_seconds)
        try:
            nyse, count = fear_greed.nyse_inputs(nyse_totals(nyse_symbols(), local.date(), local.hour, period=period))
            inputs.update(nyse)
            notes["strength"] = notes["breadth"] = f"{count} NYSE stocks with a year of daily bars (screen universe, ≥ $1B)."
        except (OSError, ValueError, KeyError, TypeError):
            notes["strength"] = notes["breadth"] = "NYSE stock history unavailable; run a scan to build the stock list."
        history = stored.result()
    inputs["put_call"] = fear_greed.put_call_input(history, sessions, archive)
    held = sum(d.isoformat() in history for d in recent)
    # A few sessions never parse (Cboe posts nothing for some half-days), so "complete" allows a small shortfall.
    notes["put_call"] = (f"Building Cboe history: {held}/{len(recent)} sessions stored; fills over the next refreshes."
                         if held < len(recent) * PUT_CALL_FILLED
                         else f"{held} Cboe sessions stored, and Cboe's 2003–2019 archive before them.")
    return inputs, notes


def fetch_fear_greed(client, now):
    progress.emit(activity="Building the Fear & Greed replica from public data")
    inputs, notes = fear_greed_inputs(client, now)
    item = fear_greed.replica(inputs, notes)
    scores = fear_greed.history(inputs, require=fear_greed.MIN_COMPONENTS)["score"].dropna()
    rows = [(d.date(), round(float(v), 1)) for d, v in scores.items()]
    points = thinned(rows, rows[-1][0] - timedelta(days=HISTORY_DAYS)) if rows else []
    return {**item, "history": [[d.isoformat(), v] for d, v in points]}


def fetch_cnn(client, today):
    try:
        text = request_text(client, f"{CNN_URL}/{(today - timedelta(days=CNN_HISTORY_DAYS)).isoformat()}")
    except ValueError:  # an unavailable history window: fall back to CNN's default year
        text = request_text(client, CNN_URL)
    return parse_cnn(text, today)


def rsi(closes, period=RSI_PERIOD):
    """Wilder's relative strength index over daily closes; the first `period` sessions warm the averages."""
    change = closes.diff()
    gain = change.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-change.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    return (100 - 100 / (1 + gain / loss.where(loss > 0))).fillna(100).iloc[period:]


def macd(closes, fast=MACD_FAST, slow=MACD_SLOW, signal=MACD_SIGNAL):
    """MACD and its signal line as a percentage of the index, so 1987 and today are on one scale.

    In index points the same crossover is worth 3 points at 300 and 80 points at 7,600; dividing by the close
    keeps the long ranges readable.
    """
    line = (closes.ewm(span=fast, adjust=False).mean() - closes.ewm(span=slow, adjust=False).mean()) / closes * 100
    return line.iloc[slow + signal:], line.ewm(span=signal, adjust=False).mean().iloc[slow + signal:]


def fetch_spx_history(now):
    local = now.astimezone(context.MARKET_TZ)
    frame = fear_greed.completed(yf.download("^GSPC", start=EARLIEST.isoformat(), interval="1d", auto_adjust=False, progress=False,
                                             multi_level_index=False, timeout=20), local.date(), local.hour)
    closes = frame["Close"].dropna()
    rows = [(d.date(), round(number(v, 1), 2)) for d, v in closes.items()]
    if len(rows) < 250:
        raise ValueError("Insufficient S&P 500 history")
    # Both are derived from these daily closes before thinning, so old weeks keep daily-based values.
    recent_from = rows[-1][0] - timedelta(days=HISTORY_DAYS)
    line, signal = macd(closes)
    series = lambda values, digits: [[d.isoformat(), v] for d, v in thinned(
        [(d.date(), round(float(v), digits)) for d, v in values.items()], recent_from)]
    return {"points": [[d.isoformat(), v] for d, v in thinned(rows, recent_from)],
            "rsi": series(rsi(closes), 1), "macd": series(line, 3), "macd_signal": series(signal, 3)}


def thinned(rows, recent_from):
    """(date, value) pairs, oldest first: every point from `recent_from`, before it only each week's last point."""
    out, week = [], None
    for day, value in rows:
        key = day + timedelta(days=(2 - day.weekday()) % 7)  # weeks end on Wednesday, like AAII's survey
        if day < recent_from and out and key == week:
            out[-1] = (day, value)
        else:
            out.append((day, value))
        week = key
    return out


def parse_put_call_archive(text):
    """Daily equity put/call ratios from one of Cboe's discontinued CSV files (disclaimer and headings first)."""
    daily = {}
    for row in csv.reader(io.StringIO(text)):
        try:
            day = datetime.strptime(row[0].strip(), "%m/%d/%Y").date()
            calls, puts = float(row[1]), float(row[2])
        except (IndexError, ValueError):
            continue
        if calls > 0 and puts >= 0:
            daily[day] = puts / calls
    if len(daily) < 100:
        raise ValueError("Unrecognized Cboe archive")
    return daily


def fetch_put_call_archive(client):
    daily = {}
    for url in PUT_CALL_ARCHIVES:
        daily.update(parse_put_call_archive(request_text(client, url, errors="replace")))  # the disclaimers carry stray bytes
    return {"daily": [[d.isoformat(), round(v, 4)] for d, v in sorted(daily.items())]}


def put_call_series(archive=()):
    """Five-session average of Cboe's daily equity put/call ratio: the 2003–2019 archive, then the stored history.

    Averages never span a gap between the two; points older than 10 years are thinned to one per week.
    """
    daily = {date.fromisoformat(day): ratio for day, ratio in archive}
    try:
        history = json.loads((config.DATA_DIR / "sentiment" / "put_call_history.json").read_text(encoding="utf-8"))["sessions"]
    except (OSError, ValueError, TypeError, KeyError):
        history = {}
    for day, volumes in history.items():
        try:
            calls, puts = volumes["equity"]
            daily[date.fromisoformat(day)] = puts / calls
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            continue
    days = sorted(daily)
    averaged = [(days[i], sum(daily[d] for d in days[i - 4:i + 1]) / 5) for i in range(4, len(days)) if (days[i] - days[i - 4]).days <= 10]
    if not averaged:
        return []
    return [[d.isoformat(), round(v, 3)] for d, v in thinned(averaged, averaged[-1][0] - timedelta(days=HISTORY_DAYS))]


def cot_index(values, current):
    """Percentile of the latest reading within the trailing window (the "COT index"), 0–100; None without a full window."""
    window = values[-COT_LOOKBACK:]
    if len(window) < COT_LOOKBACK:
        return None
    return round(sum(v < current for v in window[:-1]) / (len(window) - 1) * 100)


def parse_cot(rows, today):
    """Asset-manager and leveraged-fund net positions from CFTC rows; the S&P 500 E-mini leads, VIX futures in the details."""
    weeks = {code: {} for code in COT_CONTRACTS}
    for row in rows:
        try:
            code, when = row["cftc_contract_market_code"], date.fromisoformat(row["report_date_as_yyyy_mm_dd"][:10])
            interest = number(row["open_interest_all"], 1)
            lev = number(row["lev_money_positions_long"], 0) - number(row["lev_money_positions_short"], 0)
            asset = number(row["asset_mgr_positions_long"], 0) - number(row["asset_mgr_positions_short"], 0)
        except (KeyError, TypeError, ValueError):
            continue
        if code in weeks and when <= today:
            weeks[code][when] = (asset, lev, interest)
    groups = {}
    for code, name in COT_CONTRACTS.items():
        days = sorted(weeks[code])
        if len(days) < COT_LOOKBACK:
            raise ValueError(f"Too little COT history for {name}")
        asset = [weeks[code][d][0] for d in days]
        lev = [weeks[code][d][1] for d in days]
        last, (managers, net, interest) = days[-1], weeks[code][days[-1]]
        groups[code] = dict(name=name, as_of=last.isoformat(), asset_managers=round(managers), asset_managers_index=cot_index(asset, managers),
                            leveraged=round(net), leveraged_index=cot_index(lev, net), open_interest=round(interest),
                            asset_managers_pct_oi=round(managers / interest * 100, 1),
                            history=[[d.isoformat(), round(weeks[code][d][0] / weeks[code][d][2] * 100, 2)] for d in days])
    spx = groups.pop("13874A")
    index, share = spx["asset_managers_index"], spx["asset_managers_pct_oi"]
    return dict(as_of=spx["as_of"], value=share, reading=f"{share:+.1f}% of OI".replace("-", "−"),
                index=index, groups=[{k: v for k, v in spx.items() if k != "history"},
                                     *({k: v for k, v in g.items() if k != "history"} for g in groups.values())],
                history=spx["history"],
                signal="Institutions heavily long" if index >= 80 else "Institutions lightly long" if index <= 20 else "Typical positioning",
                direction=1 if index >= 80 else -1 if index <= 20 else 0,
                detail=("CFTC Traders in Financial Futures, E-mini S&P 500 futures only. Reading: asset managers' (pension funds, "
                        "mutual funds, insurers) net contracts as a share of open interest. The COT index ranks that net position within "
                        "the last three years (0 = least long, 100 = most long); ≥80 and ≤20 mark crowded or light positioning, which "
                        "traders often read contrarian at extremes. Asset managers' net position has moved with the index week to week, "
                        "while leveraged funds' has moved against it: much of theirs hedges cash holdings or arbitrages futures against "
                        "stocks, so it is shown for reference only. Positions are as of Tuesday and published the following Friday. "
                        "This is positioning, not a survey of opinion."))


def fetch_cot(client, today):
    codes = ", ".join(f"'{code}'" for code in COT_CONTRACTS)
    fields = ("report_date_as_yyyy_mm_dd, cftc_contract_market_code, open_interest_all, lev_money_positions_long, "
              "lev_money_positions_short, asset_mgr_positions_long, asset_mgr_positions_short")
    text = request_text(client, COT_URL, params={"$select": fields, "$where": f"cftc_contract_market_code in ({codes})",
                                                  "$order": "report_date_as_yyyy_mm_dd", "$limit": 10000})
    return parse_cot(json.loads(text), today)


def check_fear_greed(now=None, years=3):
    """Compare the replica with CNN's published history; needs CNN's feed and a filled Cboe history."""
    now = now or datetime.now(timezone.utc)
    start = (now - timedelta(days=365 * years)).date().isoformat()
    period = f"{years + 4}y"
    with httpx.Client(headers={"User-Agent": config.USER_AGENT}, timeout=30, follow_redirects=True) as client:
        cnn = json.loads(request_text(client, f"{CNN_URL}/{start}"))
        inputs, _ = fear_greed_inputs(client, now, period=period, backfill_seconds=0,
                                      put_call_sessions=252 * (years + 3))
    replica_scores = fear_greed.history(inputs)
    result = fear_greed.compare(fear_greed.cnn_series(cnn), replica_scores["score"])
    for key, (name, _) in fear_greed.COMPONENTS.items():
        print(f"{name:22s} {len(replica_scores[key].dropna()) if key in replica_scores else 0:5d} scored sessions")
    # The put/call component limits the window: its scores start 629 stored Cboe sessions in.
    print(f"Replica vs CNN on sessions with all seven components, {result['start']} → {result['end']} ({result['sessions']} sessions): "
          f"correlation {result['correlation']:.3f}, mean gap {result['mean_abs_gap']:.1f} points "
          f"(90% within {result['p90_abs_gap']:.1f}, max {result['max_abs_gap']:.1f}, bias {result['bias']:+.1f}), "
          f"same rating on {result['same_rating']:.0%} of sessions.")
    return result


def fetch_benchmark(symbol):
    frame = yf.Ticker(symbol).history(period="3mo", interval="1d", auto_adjust=False, timeout=12)
    closes = frame["Close"].dropna()
    if len(closes) < 21:
        raise ValueError("Insufficient benchmark history")
    return {"prices": {"1D": {"t": [int(t.timestamp()) for t in closes.index],
                               "c": [number(v, 0.00001) for v in closes]}}}


def parse_news(text):
    data = json.loads(text)
    if not isinstance(data.get("feed"), list):
        raise ValueError("News unavailable or quota exhausted")  # the provider explains a spent quota in prose
    return [a for a in data["feed"] if isinstance(a, dict) and a.get("url")]


def published(article):
    try:
        return datetime.strptime(article["time_published"], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
    except (KeyError, TypeError, ValueError):
        return None


def news_requests(rows, articles, remaining):
    """Leaders worth a request of their own: those the shared feed nearly covers, halved between cap bands.

    The shared feed carries whatever made national news, which is mostly the largest companies, so the quota
    goes to the screen's own leaders — evenly split, or the whole of it when only one band has names left.
    A ticker already carrying an article or two is asked about first: it is the one a single request can lift
    over the three-article threshold, while a company with no coverage at all usually has none to find.
    """
    covered = {}
    for article in articles:
        for item in article.get("ticker_sentiment") or []:
            covered[item.get("ticker")] = covered.get(item.get("ticker"), 0) + 1
    bands = {"mid": [], "large": []}
    ranked = sorted(rows, key=lambda r: (-min(covered.get(r.get("yahoo_symbol") or str(r.get("symbol") or "").replace(".", "-"), 0),
                                              NEWS_MIN_ARTICLES - 1), -(r.get("iv30") or 0)))
    for row in ranked:
        symbol = row.get("yahoo_symbol") or str(row.get("symbol") or "").replace(".", "-")
        if row.get("market") != "US" or not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,11}", symbol):
            continue
        if covered.get(symbol, 0) >= NEWS_MIN_ARTICLES or symbol in bands["mid"] or symbol in bands["large"]:
            continue
        bands["large" if (row.get("market_cap_usd") or 0) >= config.LARGE_MARKET_CAP_USD else "mid"].append(symbol)
    share = remaining // 2
    chosen = bands["mid"][:share] + bands["large"][:remaining - share]
    spare = bands["mid"][share:] + bands["large"][remaining - share:]
    return chosen + spare[:max(0, remaining - len(chosen))]


def fetch_news(client, api_key, rows, now, *, interval=NEWS_MIN_INTERVAL_S, budget=NEWS_BUDGET_SECONDS):
    """The shared Alpha Vantage feed plus a request for each uncovered leader, kept as a rolling window.

    The free allowance is NEWS_DAILY_REQUESTS a day and about five a minute, so per-ticker requests are spaced
    out and stop after `budget` seconds; the day's remaining allowance is spent over the next refreshes. One
    request goes to the shared feed and the rest are split between the cap bands. Articles are kept for
    NEWS_WINDOW_DAYS, so coverage of the smaller names builds up over a week instead of being thrown away.
    """
    try:
        saved = json.loads((config.DATA_DIR / "sentiment" / "news-feed.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    today = now.astimezone(context.MARKET_TZ).date().isoformat()
    used = (saved.get("requests") or {}).get(today, 0)
    ask = lambda extra: parse_news(request_text(client, NEWS_URL, params={
        "function": "NEWS_SENTIMENT", "sort": "LATEST", "apikey": api_key, **extra}))
    cutoff = now - timedelta(days=NEWS_WINDOW_DAYS)
    fresh = lambda items: {a["url"]: a for a in items if isinstance(a, dict) and a.get("url") and (published(a) or cutoff) > cutoff}
    articles = fresh(saved.get("feed") or [])
    for article in ask({"limit": 1000}):  # an unusable response raises before anything is stored
        articles[article["url"]] = article
    used += 1
    articles = fresh(articles.values())  # choose who to ask about from the window that actually scores
    asked, refused = [], 0
    limiter = net.RateLimiter(min_interval=interval)
    deadline = time.monotonic() + budget
    for symbol in news_requests(rows, articles.values(), max(0, NEWS_DAILY_REQUESTS - used)):
        if refused >= 3 or time.monotonic() > deadline:
            break  # the allowance is spent or this refresh has asked for long enough
        limiter.wait()
        try:
            for article in ask({"tickers": symbol, "limit": 50}):
                articles[article["url"]] = article
        except (ValueError, KeyError, OSError):
            refused += 1  # one ticker the provider would not answer for; the others are still worth asking
            continue
        refused = 0
        used += 1
        asked.append(symbol)
    return {"feed": list(fresh(articles.values()).values()), "requests": {today: used},
            "asked": asked, "window_days": NEWS_WINDOW_DAYS}


def parse_social(text):
    data = json.loads(text)["data"]
    # Use 24h, not the undocumented 'now' window; never turn a missing score into zero.
    item = data["sentiment"]["24h"]
    if item.get("labelNormalized") not in {"EXTREMELY_BEARISH", "BEARISH", "NEUTRAL", "BULLISH", "EXTREMELY_BULLISH"}:
        raise ValueError("Social sentiment unavailable")
    score = number(item["valueNormalized"], 0, 100)
    volume = data.get("messageVolume", {}).get("24h", {})
    return dict(score=score, reading=item["labelNormalized"].replace("_", " ").title(),
                buzz=volume.get("labelNormalized", "NA"))


def collect(rows, *, now=None):
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(context.MARKET_TZ).date()
    result = {"collected_at": now.isoformat(), "macro": {}, "benchmarks": {}, "social": {}, "history": {}}
    with httpx.Client(headers={"User-Agent": config.USER_AGENT}, timeout=12, follow_redirects=True) as client:
        jobs = {}
        parsers = {"vix": lambda: parse_vix(request_text(client, VIX_URL), today),
                   "put_call": lambda: parse_put_call(request_text(client, PC_URL), today),
                   "aaii": lambda: parse_aaii(request_text(client, AAII_URL), today),
                   "cnn": lambda: fetch_cnn(client, today),
                   "cot": lambda: fetch_cot(client, today)}
        for key, fetch in parsers.items():
            def macro_fetch(fetch=fetch):
                item = fetch()
                if iso_date(item["as_of"]) > today:
                    raise ValueError("Future observation")
                return item
            jobs[("macro", key)] = macro_fetch
        jobs[("macro", "fear_greed")] = lambda: fetch_fear_greed(client, now)
        jobs[("history", "spx")] = lambda: fetch_spx_history(now)
        jobs[("history", "put_call_archive")] = lambda: fetch_put_call_archive(client)
        symbols = {SECTORS.get(str(row.get("sector") or "").lower()) for row in rows} - {None}
        for symbol in sorted(symbols | {"SPY"}):
            jobs[("benchmarks", symbol)] = lambda symbol=symbol: fetch_benchmark(symbol)
        api_key = os.environ.get("ALPHAVANTAGE_API_KEY")
        if api_key:
            jobs[("news", "feed")] = lambda: fetch_news(client, api_key, rows, now)
        username, password = os.environ.get("STOCKTWITS_USERNAME"), os.environ.get("STOCKTWITS_PASSWORD")
        if username and password:
            for row in rows:
                # Avoid cross-market ticker collisions; this integration is explicitly US-only.
                symbol = row.get("yahoo_symbol") or row["symbol"].replace(".", "-")
                if row.get("market") == "US" and re.fullmatch(r"[A-Z0-9^-]{1,20}", symbol):
                    jobs[("social", symbol)] = lambda symbol=symbol: parse_social(request_text(client,
                        f"https://api-gw-prd.stocktwits.com/api-middleware/external/sentiment/v2/{quote(symbol, safe='')}/detail",
                        auth=(username, password)))
        result["news"] = {"feed": {"status": "not_configured"}}
        result["social_configured"] = bool(username and password)
        def run(job):
            (kind, key), fn = job
            return kind, key, cached_read(f"{kind}-{key}", fn, now, TTL_HOURS.get((kind, key), 6), COMPLETE.get((kind, key)),
                                          sessions=kind in SESSION_KINDS)
        progress.emit(activity="Checking macro sentiment and sector benchmarks")
        with ThreadPoolExecutor(max_workers=4) as pool:
            for i, (kind, key, value) in enumerate(pool.map(run, jobs.items()), 1):
                result[kind][key] = value
                progress.emit(activity=f"Sentiment sources checked: {i}/{len(jobs)}")
    result["macro"]["aaii"] = with_aaii_import(result["macro"]["aaii"], today)
    result["history"]["aaii"] = aaii.spread_series(config.DATA_DIR, result["macro"]["aaii"])
    result["history"]["put_call"] = put_call_series((result["history"].get("put_call_archive") or {}).get("daily") or [])
    return result


def with_aaii_import(live, today, folders=None):
    """The latest survey week among AAII's page, the week entered by hand, and a spreadsheet in data/imports."""
    store = config.DATA_DIR / "sentiment" / "aaii-import.json"
    try:
        saved = json.loads(store.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = None
    found, notes = aaii.imported(today, saved, folders)
    if found and found is not saved:
        write_json(store, found)
    item = aaii.newest(live, aaii.entered(config.DATA_DIR), found, aaii.bundled_reading()) or live
    return {**item, **({"import_note": " ".join(notes)} if notes else {})}


def usable(item, today, max_age):
    return (item.get("status") in ("ok", "cached") and isinstance(item.get("value"), (int, float))
            and not isinstance(item.get("value"), bool) and fresh(item.get("as_of"), today, max_age))


def with_replica(cnn, replica, today):
    """CNN stays primary; the replica is a labelled cross-check, or stands in when CNN has no fresh reading."""
    if not usable(replica, today, REPLICA_MAX_AGE):
        return cnn
    if usable(cnn, today, cnn["max_age"]):
        keys = ("value", "reading", "signal", "as_of", "coverage", "components", "fetched_at", "status", "method")
        return {**cnn, "replica": {k: replica.get(k) for k in keys}}
    return {**replica, "key": "cnn", "name": "Fear & Greed replica", "url": CNN_PAGE, "max_age": REPLICA_MAX_AGE,
            "replica_of": "CNN Fear & Greed", "cnn_status": cnn.get("status"), "cnn_as_of": cnn.get("as_of")}


def chart_history(macro, history):
    """S&P 500 closes and each sentiment series, as [date, value] pairs, for the dashboard's macro chart."""
    cnn = (macro.get("cnn") or {}).get("history") or []
    replica = (macro.get("fear_greed") or {}).get("history") or []
    if len(cnn) >= 20:
        # CNN's feed serves about five years; the replica covers the years before it, labelled as the other source.
        earlier = [p for p in replica if p[0] < cnn[0][0]]
        fear = dict(name="Fear & Greed", points=earlier + cnn,
                    source=f"CNN from {cnn[0][0]}" + (f"; replica from public data {earlier[0][0]} to then" if earlier else ""))
    else:
        fear = dict(name="Fear & Greed replica", source="Replica from public data", points=replica)
    return {"spx": (history.get("spx") or {}).get("points") or [], "series": {
        "aaii": dict(name="AAII bull–bear spread", unit=" pp", source="AAII weekly survey", frequency="weekly", points=history.get("aaii") or []),
        "vix": dict(name="VIX", unit="", source="Cboe", frequency="daily", points=(macro.get("vix") or {}).get("history") or []),
        "put_call": dict(name="Equity put/call, 5-day average", unit="", source="Cboe · archive 2003–2019 (ETF options included before June 2012), then daily statistics",
                         frequency="daily", points=history.get("put_call") or []),
        "fear_greed": {**fear, "unit": "", "frequency": "daily"},
        "rsi": dict(name=f"RSI {RSI_PERIOD}", unit="", source="Computed from S&P 500 daily closes", frequency="daily",
                    points=(history.get("spx") or {}).get("rsi") or []),
        "macd": dict(name=f"MACD {MACD_FAST}/{MACD_SLOW}/{MACD_SIGNAL}, % of index", unit="%", source="Computed from S&P 500 daily closes",
                     frequency="daily", points=(history.get("spx") or {}).get("macd") or [],
                     signal=(history.get("spx") or {}).get("macd_signal") or []),
        "cot": dict(name="COT: asset managers' net, % of open interest", unit="%", source="CFTC Traders in Financial Futures · E-mini S&P 500",
                    frequency="weekly", points=(macro.get("cot") or {}).get("history") or []),
    }}


def unavailable(key, reason):
    return dict(key=key, score=None, status="unavailable", detail=reason, weight=WEIGHTS[key])


def momentum(row, now):
    values = context._closes(row, now.astimezone(context.MARKET_TZ).date(), now)
    days = list(values)
    if len(days) < 21 or not fresh(days[-1], now.astimezone(context.MARKET_TZ).date()):
        return None
    days = days[-21:]
    if any((b - a).days > 5 for a, b in zip(days, days[1:])):
        return None
    return {"as_of": days[-1].isoformat(), "start5": days[-6].isoformat(), "start20": days[0].isoformat(),
            "return5": (values[days[-1]] / values[days[-6]] - 1) * 100,
            "return20": (values[days[-1]] / values[days[0]] - 1) * 100}


def news_component(row, feed, now):
    component = unavailable("news", "News sentiment is not connected. An Alpha Vantage API key is required.")
    if feed.get("status") == "not_configured":
        return component
    if row.get("market") != "US":
        component["detail"] = "News scoring currently covers US listings only; Canadian symbols are not matched to US names."
        return component
    symbol = row.get("yahoo_symbol") or row["symbol"].replace(".", "-")
    evidence = []
    seen = set()
    for article in feed.get("feed", []):
        try:
            when = datetime.strptime(article["time_published"], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
            if not 0 <= (now - when).total_seconds() <= 7 * 86400:
                continue
            url, source = article["url"], article["source"]
            if url in seen or not re.match(r"^https?://", url) or not source or not article["title"]:
                continue
            item = next(v for v in article["ticker_sentiment"] if v["ticker"] == symbol)
            relevance = number(item["relevance_score"], 0, 1)
            if relevance < 0.2:
                continue
            score = number(item["ticker_sentiment_score"], -1, 1)
            seen.add(url)
            evidence.append(dict(title=article["title"], source=source, url=url, as_of=when.isoformat(),
                                 score=score, relevance=relevance))
        except (KeyError, TypeError, ValueError, StopIteration):
            continue
    if len(evidence) < 3 or len({e["source"] for e in evidence}) < 2:
        component.update(detail=f"Insufficient recent coverage: {len(evidence)} relevant articles. Requires 3 articles from 2 sources in 7 days. Feed may be unavailable, quota-limited, or omit this ticker.", evidence=evidence)
        return component
    weighted = sum(e["score"] * e["relevance"] for e in evidence) / sum(e["relevance"] for e in evidence)
    return dict(key="news", weight=20, score=bounded(50 + 50 * weighted), status=feed.get("status", "ok"),
                as_of=max(e["as_of"] for e in evidence)[:10], max_age=7, fetched_at=feed.get("fetched_at"),
                source="Alpha Vantage", url="https://www.alphavantage.co/documentation/#news-sentiment", evidence=evidence,
                detail=f"{len(evidence)} relevant articles across {len({e['source'] for e in evidence})} sources in 7 days. Relevance-weighted provider score (−1 to +1) mapped to 0–100. Automated tone, not verified catalyst attribution; bounded latest-news sample.")


def evaluate(payload, inputs, *, now=None):
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(context.MARKET_TZ).date()
    macro = inputs.get("macro", {})
    cards = []
    for key, (name, url, max_age) in MACRO.items():
        item = {**macro.get(key, {"status": "unavailable"}), "key": key, "name": name, "url": url, "max_age": max_age}
        if item.get("as_of") and not fresh(item["as_of"], today, max_age):
            item["status"] = "stale"
        if key == "cnn":
            item = with_replica(item, macro.get("fear_greed") or {}, today)
        item.pop("history", None)  # histories live once, in the chart data below
        cards.append(item)
    payload["macro_sentiment"] = {"cards": cards, "checked_at": inputs.get("collected_at"),
                                  "history": chart_history(macro, inputs.get("history", {}))}
    benchmarks = {key: momentum(item, now) for key, item in inputs.get("benchmarks", {}).items()}
    market = benchmarks.get("SPY")
    for row in payload["rows"]:
        stock = momentum(row, now)
        symbol = SECTORS.get(str(row.get("sector") or "").lower())
        sector = benchmarks.get(symbol)
        components = [unavailable("stock", "Need 21 recent completed daily closes; missing, stale or gapped prices are excluded."),
                      unavailable("sector", "No matching fresh sector ETF / SPY history on identical dates.")]
        if stock:
            components[0] = dict(key="stock", weight=40, score=bounded(50 + 2 * stock["return5"] + stock["return20"]),
                status="ok", **stock, source="Yahoo daily closes", url=f"https://finance.yahoo.com/quote/{quote(row.get('yahoo_symbol') or row['symbol'], safe='')}/history/",
                detail=f"5 sessions {stock['return5']:+.2f}%; 20 sessions {stock['return20']:+.2f}%. Score = clamp(50 + 2 × 5-session return% + 20-session return%, 0, 100). Completed closes, listing currency, excluding dividends.")
        if sector and market and all(sector[k] == market[k] for k in ("as_of", "start5", "start20")):
            excess5, excess20 = sector["return5"] - market["return5"], sector["return20"] - market["return20"]
            components[1] = dict(key="sector", weight=30, score=bounded(50 + 2 * excess5 + excess20), status="ok",
                **sector, excess5=excess5, excess20=excess20, benchmark=symbol, source=f"Yahoo · {symbol} vs SPY",
                url=f"https://finance.yahoo.com/quote/{symbol}/history/",
                detail=f"US sector proxy {symbol}: 5 sessions {sector['return5']:+.2f}%, 20 sessions {sector['return20']:+.2f}%; relative to SPY {excess5:+.2f} / {excess20:+.2f} percentage points. Score = clamp(50 + 2 × 5-session excess + 20-session excess, 0, 100). US large-cap sector ETF, not the whole industry or a Canadian sector index.")
        components.append(news_component(row, inputs.get("news", {}).get("feed", {"status": "not_configured"}), now))
        social = inputs.get("social", {}).get(row.get("yahoo_symbol") or row["symbol"].replace(".", "-"), {}) if row.get("market") == "US" else {}
        social_part = unavailable("social", "Social sentiment is not connected. Authorized Stocktwits data access is required.")
        if inputs.get("social_configured"):
            social_part["detail"] = "Stocktwits unavailable, stale, or listing unsupported. No social score inferred from price."
        if social.get("score") is not None and fresh(social.get("fetched_at"), today, 1):
            social_part = dict(key="social", weight=10, score=social["score"], status=social.get("status", "ok"),
                as_of=social["fetched_at"][:10], max_age=1, fetched_at=social["fetched_at"], source="Stocktwits",
                url=f"https://stocktwits.com/symbol/{quote(row['symbol'], safe='')}",
                detail=f"24-hour community score: {social['reading']}; message activity: {social['buzz']}. Observation dated by retrieval; provider observation time not supplied. Community sample, not all investors; message activity is not a bullish signal.")
        components.append(social_part)
        # Require contemporaneous stock and sector endpoints to rank, not arbitrary old/new mixes.
        eligible = stock and components[1]["score"] is not None and stock["as_of"] == sector["as_of"]
        available = [c for c in components if c["score"] is not None]
        coverage = sum(c["weight"] for c in available)
        score = round(sum(c["score"] * c["weight"] for c in available) / coverage) if eligible else None
        row["sentiment"] = dict(score=score, label=label(score) if score is not None else "Insufficient evidence",
            as_of=stock["as_of"] if eligible else None, coverage=coverage, components=components,
            mode="Price + opinion" if any(c["key"] in ("news", "social") for c in available) else "Price only",
            method=METHOD, benchmark=symbol)


def enrich(payload):
    inputs = collect(payload["rows"])
    evaluate(payload, inputs)
