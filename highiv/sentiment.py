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

from . import aaii, config, context, fear_greed, net, progress

VIX_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
PC_URL = "https://www.cboe.com/us/options/market_statistics/daily/"
AAII_URL = "https://www.aaii.com/sentimentsurvey"
CNN_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
CNN_PAGE = "https://www.cnn.com/markets/fear-and-greed"
MACRO = {
    "vix": ("VIX", VIX_URL, 4),
    "put_call": ("Put/call ratios", PC_URL, 4),
    "aaii": ("AAII sentiment", AAII_URL, 10),
    "cnn": ("CNN Fear & Greed", CNN_PAGE, 2),
}
REPLICA_MAX_AGE = 4
REPLICA_HISTORY = "4y"            # 52-week highs, 20-session smoothing, 125-session z-scores, 500-session ranks
PUT_CALL_BACKFILL_SECONDS = 180   # Cboe history fills over a few refreshes, never in one long burst
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
    item = json.loads(text)["fear_and_greed"]
    value = number(item["score"], 0, 100)
    when = datetime.fromisoformat(item["timestamp"].replace("Z", "+00:00"))
    if when.tzinfo is None:
        raise ValueError("CNN timestamp has no timezone")
    return dict(as_of=when.date().isoformat(), observed_at=when.isoformat(), value=value, reading=f"{value:.1f}/100",
                signal=str(item["rating"]).title(), direction=1 if value >= 55 else -1 if value <= 45 else 0,
                detail="CNN’s seven-component Fear & Greed index. Includes volatility and options inputs already shown here, so it is not an independent confirmation. Public website feed may be unavailable; no official API guarantee.")


def request_text(client, url, **kwargs):
    # Bounded requests, no anti-bot workarounds and no leaking API keys in failure messages.
    with client.stream("GET", url, **kwargs) as response:
        if response.status_code != 200:
            raise ValueError(f"Provider unavailable (HTTP {response.status_code})")
        data = bytearray()
        for chunk in response.iter_bytes():
            data.extend(chunk)
            if len(data) > 3_000_000:
                raise ValueError("Provider response too large")
    return data.decode("utf-8")


def cached_read(key, fetch, now, ttl_hours=6):
    """Cache successful observations only. Keep provenance when refresh fails."""
    path = config.DATA_DIR / "sentiment" / f"{key}.json"
    saved = None
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
        age = (now - datetime.fromisoformat(saved["fetched_at"])).total_seconds()
        if 0 <= age < ttl_hours * 3600:
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


def download_bars(symbols, *, actions=False, period=REPLICA_HISTORY, chunk=150):
    frames = []
    for start in range(0, len(symbols), chunk):
        frame = yf.download(list(symbols[start:start + chunk]), period=period, interval="1d", auto_adjust=False,
                            actions=actions, group_by="column", progress=False, threads=True, timeout=20)
        if frame is not None and not frame.empty:
            frames.append(frame)
    if not frames:
        raise ValueError("No price history")
    return pd.concat(frames, axis=1, sort=True)


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


def fear_greed_inputs(client, now, *, period=REPLICA_HISTORY, put_call_sessions=fear_greed.PUT_CALL_SESSIONS,
                      backfill_seconds=PUT_CALL_BACKFILL_SECONDS):
    local = now.astimezone(context.MARKET_TZ)
    bars = fear_greed.completed(download_bars(fear_greed.INDEX_SYMBOLS, actions=True, period=period), local.date(), local.hour)
    sessions = [d.date() for d in bars["Close"]["^GSPC"].dropna().index][-put_call_sessions:]
    inputs = fear_greed.index_inputs(bars["Close"], bars["Dividends"])
    notes = {}
    with ThreadPoolExecutor(max_workers=1) as pool:
        # Cboe and Yahoo are separate hosts, so the history backfill overlaps the stock download.
        stored = pool.submit(put_call_history, client, sessions, time.monotonic() + backfill_seconds)
        try:
            stocks = fear_greed.completed(download_bars(nyse_symbols(), period=period), local.date(), local.hour)
            nyse, count = fear_greed.nyse_inputs(stocks["Close"], stocks["High"], stocks["Low"], stocks["Volume"])
            inputs.update(nyse)
            notes["strength"] = notes["breadth"] = f"{count} NYSE stocks with a year of daily bars (screen universe, ≥ $1B)."
        except (OSError, ValueError, KeyError, TypeError):
            notes["strength"] = notes["breadth"] = "NYSE stock history unavailable; run a scan to build the stock list."
        history = stored.result()
    inputs["put_call"] = fear_greed.put_call_input(history, sessions)
    held = sum(d.isoformat() in history for d in sessions)
    notes["put_call"] = (f"Building Cboe history: {held}/{len(sessions)} sessions stored; fills over the next refreshes."
                         if held < len(sessions) - 20 else f"{held} Cboe sessions stored.")
    return inputs, notes


def fetch_fear_greed(client, now):
    progress.emit(activity="Building the Fear & Greed replica from public data")
    return fear_greed.replica(*fear_greed_inputs(client, now))


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
        raise ValueError("News unavailable or quota exhausted")
    return {"feed": data["feed"]}


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
    result = {"collected_at": now.isoformat(), "macro": {}, "benchmarks": {}, "social": {}}
    with httpx.Client(headers={"User-Agent": config.USER_AGENT}, timeout=12, follow_redirects=True) as client:
        jobs = {}
        parsers = {"vix": (VIX_URL, parse_vix), "put_call": (PC_URL, parse_put_call),
                   "aaii": (AAII_URL, parse_aaii), "cnn": (CNN_URL, parse_cnn)}
        for key, (url, parser) in parsers.items():
            def macro_fetch(url=url, parser=parser):
                item = parser(request_text(client, url), today)
                if iso_date(item["as_of"]) > today:
                    raise ValueError("Future observation")
                return item
            jobs[("macro", key)] = macro_fetch
        jobs[("macro", "fear_greed")] = lambda: fetch_fear_greed(client, now)
        symbols = {SECTORS.get(str(row.get("sector") or "").lower()) for row in rows} - {None}
        for symbol in sorted(symbols | {"SPY"}):
            jobs[("benchmarks", symbol)] = lambda symbol=symbol: fetch_benchmark(symbol)
        api_key = os.environ.get("ALPHAVANTAGE_API_KEY")
        if api_key:
            # One bounded feed request per refresh, not one request for every screened ticker.
            jobs[("news", "feed")] = lambda: parse_news(request_text(client, "https://www.alphavantage.co/query",
                params={"function": "NEWS_SENTIMENT", "sort": "LATEST", "limit": 1000, "apikey": api_key}))
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
            return kind, key, cached_read(f"{kind}-{key}", fn, now)
        progress.emit(activity="Checking macro sentiment and sector benchmarks")
        with ThreadPoolExecutor(max_workers=4) as pool:
            for i, (kind, key, value) in enumerate(pool.map(run, jobs.items()), 1):
                result[kind][key] = value
                progress.emit(activity=f"Sentiment sources checked: {i}/{len(jobs)}")
    result["macro"]["aaii"] = with_aaii_import(result["macro"]["aaii"], today)
    return result


def with_aaii_import(live, today, folders=None):
    """AAII's page when it answers; otherwise, or when newer, the spreadsheet the user saved. Read every refresh."""
    store = config.DATA_DIR / "sentiment" / "aaii-import.json"
    try:
        saved = json.loads(store.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = None
    found, notes = aaii.imported(today, saved, folders)
    if found and found is not saved:
        write_json(store, found)
    item = live
    if found and not (live.get("status") in ("ok", "cached") and str(live.get("as_of", "")) > found["as_of"]):
        item = found
    return {**item, "import_url": aaii.DOWNLOAD_URL, **({"import_note": " ".join(notes)} if notes else {})}


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
    cards = []
    for key, (name, url, max_age) in MACRO.items():
        item = {**inputs.get("macro", {}).get(key, {"status": "unavailable"}), "key": key,
                "name": name, "url": url, "max_age": max_age}
        if item.get("as_of") and not fresh(item["as_of"], today, max_age):
            item["status"] = "stale"
        if key == "cnn":
            item = with_replica(item, inputs.get("macro", {}).get("fear_greed") or {}, today)
        cards.append(item)
    payload["macro_sentiment"] = {"cards": cards, "checked_at": inputs.get("collected_at")}
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
