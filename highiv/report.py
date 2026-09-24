"""Rank the latest scan, enrich the leaders (float, short interest, 52-week data, IV rank) and write the dashboard."""
from __future__ import annotations

import base64
import gzip
import json
import os
import re
import shutil
import statistics
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from functools import partial
from pathlib import Path
from zoneinfo import ZoneInfo

from . import borrow, config, context, details, explain, iv, market, net, news, prices, store, progress, watchlist, logos, sentiment, leverage, macro_search, commodities
from .universe import norm_name

log = partial(print, flush=True)
MARKET_TZ = ZoneInfo("America/Toronto")
NORTH_AMERICA = {"United States", "Canada"}
IV_SOURCES = {"cboe": "Cboe", "mx": "Montréal Exchange"}
# Each market-cap tab and filter combination needs an independent top-N list.
VIEWS = [(market, hq, band) for band in config.CAP_BANDS
         for market in ("all", "us", "tsx") for hq in ("any", "north_america")]


def cap_band(cap: float) -> str:
    return "large" if cap >= config.LARGE_MARKET_CAP_USD else "mid"


def _in_view(item: dict, market: str, hq: str, band: str, *, hq_known: bool) -> bool:
    """Whether an enriched row (hq_known=True) belongs to a view, or a universe stock could."""
    if item.get("watch_only"):
        return False
    if item["market_cap_usd"] < config.MIN_MARKET_CAP_USD or cap_band(item["market_cap_usd"]) != band:
        return False
    on_tsx = item["market"] == "CA" or bool(item["also_listed"])
    if (market == "us" and item["market"] != "US") or (market == "tsx" and not on_tsx):
        return False
    if hq == "any":
        return True
    if hq_known:
        return item["hq_north_america"]
    return not item["hq_country"] or item["hq_country"] in NORTH_AMERICA


def _still_needed(stock: dict, filled: Counter) -> bool:
    return any(filled[view] < config.TOP_N and _in_view(stock, *view, hq_known=False) for view in VIEWS)


def _load_universe() -> dict[str, dict]:
    path = config.DATA_DIR / "universe.json"
    if not path.exists():
        raise SystemExit("No universe found. Run `python -m highiv scan` first.")
    return {s["symbol"]: s for s in json.loads(path.read_text(encoding="utf-8"))["stocks"]}


def _needs_bootstrap(conn, symbol: str, scan: dict) -> bool:
    if not scan.get("quote_date") or store.has_source(conn, symbol, "alphaquery"):
        return False
    return len(store.iv_series(conn, symbol)) < config.MIN_IV_HISTORY_POINTS


def _bootstrap_points(client, limiter, symbol: str, scan: dict) -> list[tuple[str, float]]:
    """~3 months of AlphaQuery IV history to seed IV rank, scaled to CBOE's level on their last shared session."""
    points = [(d, v) for d, v in iv.alphaquery_history(client, limiter, symbol) if d < scan["quote_date"]]
    if not points:
        return []
    scale = 1.0
    if scan.get("iv30_change") is not None and points[-1][1] > 0:
        previous_close_iv = scan["iv30"] - scan["iv30_change"]
        gap = (date.fromisoformat(scan["quote_date"]) - date.fromisoformat(points[-1][0])).days
        if 0 < gap <= 5:
            scale = min(max(previous_close_iv / points[-1][1], 0.75), 1.33)
    return [(d, round(v * scale, 3)) for d, v in points]


def _iv_stats(series: list[tuple[str, float]], current: float) -> dict:
    values = [v for _, v in series]
    stats = {
        "iv_days": len(values),
        "iv_since": series[0][0] if series else None,
        "iv_rank": None,
        "iv_percentile": None,
        "iv_low": None,
        "iv_high": None,
        "iv_avg": None,
    }
    if len(values) < config.MIN_IV_HISTORY_POINTS:
        return stats
    low, high = min(values), max(values)
    earlier = values[:-1]
    rank = (current - low) / (high - low) * 100 if high > low else 50.0
    stats.update(
        iv_low=round(low, 1),
        iv_high=round(high, 1),
        iv_avg=round(statistics.fmean(values), 1),
        iv_rank=round(max(0.0, min(100.0, rank))),
        iv_percentile=round(sum(v < current for v in earlier) / len(earlier) * 100),
    )
    return stats


def _squeeze(short_pct: float | None, days_to_cover: float | None) -> str:
    if short_pct is None:
        return "unknown"
    dtc = days_to_cover or 0
    for level, rule in (("high", config.SQUEEZE_HIGH), ("elevated", config.SQUEEZE_ELEVATED)):
        if short_pct >= rule["short_pct_alone"] or (
            short_pct >= rule["short_pct_float"] and dtc >= rule["days_to_cover"]
        ):
            return level
    return "low"


def _pct(value: float | None, digits: int = 1) -> float | None:
    return round(value * 100, digits) if value is not None else None


def _trim(text: str | None, limit: int) -> str | None:
    """Cut at a word boundary rather than mid-word, so nothing is rendered clipped."""
    if not text or len(text) <= limit:
        return text
    return f"{text[:limit].rsplit(' ', 1)[0].rstrip(',;:')}…"


def _business_line(summary: str | None) -> str | None:
    """The first sentence of Yahoo's business summary: what the company actually does."""
    if not summary:
        return None
    first = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", summary.strip())[0]
    return _trim(first.rstrip("."), 140)


NO_EARNINGS = {
    "next_earnings": None,
    "earnings_in_days": None,
    "earnings_time": None,
    "earnings_session": None,
    "earnings_estimated": None,
    "earnings_window_end": None,
}


def _earnings(info: dict, today: date) -> dict:
    """Next report: date, hour in market time, and whether it lands before or after the bell."""
    stamp = info["earnings_ts"]
    if not stamp:
        return dict(NO_EARNINGS)
    when = datetime.fromtimestamp(int(stamp), MARKET_TZ)
    in_days = (when.date() - today).days
    if in_days < 0:
        return dict(NO_EARNINGS)
    minutes = when.hour * 60 + when.minute
    session = "before open" if minutes < 9 * 60 + 30 else "after close" if minutes >= 16 * 60 else "during session"
    end = info["earnings_ts_end"]
    window_end = datetime.fromtimestamp(int(end), MARKET_TZ).date() if end and int(end) != int(stamp) else None
    # Yahoo pads an estimated date with a placeholder hour, so only a confirmed date gets a time.
    estimate_flag = info.get("earnings_estimate")
    estimated = estimate_flag if isinstance(estimate_flag, bool) else None
    confirmed = estimated is False
    return {
        "next_earnings": when.date().isoformat(),
        "earnings_in_days": in_days,
        "earnings_time": when.strftime("%I:%M %p").lstrip("0").lower() if confirmed else None,
        "earnings_session": session if confirmed else None,
        "earnings_estimated": estimated,
        "earnings_window_end": window_end.isoformat() if window_end else None,
    }


def _market_cap_usd(stock: dict, info: dict) -> float:
    """Yahoo's live cap for US lines, the screener's converted cap for Canadian ones."""
    if stock["market"] == "US" and info["market_cap"]:
        return float(info["market_cap"])
    return float(stock["market_cap_usd"])


def _row(stock: dict, scan: dict, info: dict, series: list[tuple[str, float]], today: date,
         price_frames: dict | None = None, price_stats: dict | None = None,
         borrow: dict | None = None) -> dict:
    price, low, high = info["price"], info["low_52w"], info["high_52w"]
    range_pos = (price - low) / (high - low) * 100 if price and low and high and high > low else None
    change = scan["iv30_change"]
    if change is None and len(series) >= 2:
        change = scan["iv30"] - series[-2][1]
    float_shares, shares_out = info["float_shares"], info["shares_outstanding"]
    short_now, short_prior = info["shares_short"], info["shares_short_prior"]
    country = info["country"] or stock["hq_country"] or ("Canada" if stock["market"] == "CA" else None)
    stats = price_stats or {}
    hv30 = stats.get("hv30")
    loan = borrow or {}
    return {
        "symbol": stock["symbol"],
        "yahoo_symbol": stock["yahoo"],
        "quote_date": scan.get("quote_date"),
        "name": info["name"] or stock["name"],
        "exchange": stock["exchange"],
        "market": stock["market"],
        "also_listed": stock["also_listed"],
        "country": country,
        "hq_north_america": country in NORTH_AMERICA,
        "sector": info["sector"] or stock["sector"],
        "industry": info["industry"],
        "business": _business_line(info["summary"]),
        "summary": _trim(info["summary"], 700),
        "market_cap_usd": _market_cap_usd(stock, info) or None,
        "currency": info["currency"],
        "price": price,
        "iv30": round(scan["iv30"], 1),
        "iv30_change": round(change, 1) if change is not None else None,
        "iv_source": IV_SOURCES[scan["source"]],
        **_iv_stats(series, scan["iv30"]),
        "iv_history": [[d, round(v, 1)] for d, v in series],
        "hv30": hv30,
        "iv_hv": round(scan["iv30"] / hv30, 2) if hv30 else None,
        "rsi14": stats.get("rsi14"),
        "volume_surge": stats.get("volume_surge"),
        "borrow_fee": loan.get("fee"),
        "borrow_available": loan.get("available"),
        "borrow_capped": loan.get("capped"),
        "borrow_fetched_at": loan.get("fetched_at"),
        "details_fetched_at": info.get("details_fetched_at"),
        "prices": price_frames,
        "low_52w": low,
        "high_52w": high,
        "range_pos": round(max(0.0, min(100.0, range_pos)), 1) if range_pos is not None else None,
        "pct_from_high": round((price / high - 1) * 100, 1) if price and high else None,
        "float_shares": float_shares,
        "float_pct_outstanding": round(float_shares / shares_out * 100, 1) if float_shares and shares_out else None,
        "shares_short": short_now,
        "short_pct_float": _pct(info["short_pct_float"]),
        "days_to_cover": info["days_to_cover"],
        "short_change_pct": _pct(short_now / short_prior - 1) if short_now and short_prior else None,
        "short_date": info["short_date"],
        "squeeze": _squeeze(info["short_pct_float"], info["days_to_cover"]),
        "small_float": bool(float_shares and float_shares < config.SMALL_FLOAT_SHARES),
        **_earnings(info, today),
    }


def _distribution(values: list[float], width: int = 10, cap: int = 200) -> list[dict]:
    bins = Counter(min(int(v // width) * width, cap) for v in values)
    return [
        {"from": start, "to": start + width if start < cap else None, "count": bins.get(start, 0)}
        for start in range(0, cap + width, width)
    ]


def render(payload: dict) -> str:
    template = (config.TEMPLATE_DIR / "dashboard.html").read_text(encoding="utf-8")
    data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")
    return (
        template.replace("/*__CSS__*/", (config.TEMPLATE_DIR / "dashboard.css").read_text(encoding="utf-8"))
        .replace("/*__JS__*/", (config.TEMPLATE_DIR / "dashboard.js").read_text(encoding="utf-8"))
        .replace("__DATA_JSON__", data)
        .replace("__FAVICON_BASE64__", base64.b64encode((config.TEMPLATE_DIR / "favicon.svg").read_bytes()).decode("ascii"))
    )


def _atomic_text(path: Path, text: str) -> None:
    """Readers keep seeing the last complete report while a refresh is written."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(text)
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def snapshots() -> list[Path]:
    """Saved daily dashboards, oldest first. The newest stays plain JSON; older ones are gzipped."""
    return sorted(list(config.SNAPSHOT_DIR.glob("*.json")) + list(config.SNAPSHOT_DIR.glob("*.json.gz")),
                  key=lambda path: path.name.removesuffix(".gz"))


def read_snapshot(path: Path) -> dict:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return json.load(handle)
    return json.loads(path.read_text(encoding="utf-8"))


def keep_snapshots(keep: int = config.SNAPSHOT_KEEP) -> None:
    """Gzip every snapshot but the newest, then drop the oldest beyond the cap.

    A day's dashboard is about 13 MB of JSON and only the newest is ever read again; compressed they cost
    roughly a tenth of that, so a year of runs stays in the tens of megabytes.
    """
    saved = snapshots()
    for path in saved[:-1]:
        if path.suffix == ".gz":
            continue
        packed = path.with_suffix(".json.gz")
        try:
            with path.open("rb") as source, gzip.open(packed.with_suffix(".gz.part"), "wb", compresslevel=6) as target:
                shutil.copyfileobj(source, target)
            os.replace(packed.with_suffix(".gz.part"), packed)
            path.unlink()
        except OSError:
            packed.with_suffix(".gz.part").unlink(missing_ok=True)
    for path in snapshots()[:-keep]:
        path.unlink(missing_ok=True)


def write_outputs(payload: dict) -> Path:
    context.apply(payload)
    config.SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    page = render(payload)
    _atomic_text(config.SNAPSHOT_DIR / f"{payload['run_date']}.json", json.dumps(payload))
    (config.SNAPSHOT_DIR / f"{payload['run_date']}.json.gz").unlink(missing_ok=True)  # a rebuilt day replaces its packed copy
    keep_snapshots()
    _atomic_text(config.OUTPUT_DIR / "artifact.html", page)  # body-only page for publishing
    standalone = config.OUTPUT_DIR / "dashboard.html"
    _atomic_text(standalone,
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
        f"</head>\n<body>\n{page}\n</body>\n</html>\n"
    )
    log(f"Dashboard written to {standalone}")
    return standalone


def _universe_stats(stocks: list[dict], scans: list[dict]) -> dict:
    excluded = {s["symbol"] for s in stocks if s.get("watch_only")}
    stocks = [s for s in stocks if s["symbol"] not in excluded]
    scans = [r for r in scans if r["symbol"] not in excluded]
    values = [r["iv30"] for r in scans]
    return {
        "total": len(stocks),
        "us": sum(s["market"] == "US" for s in stocks),
        "tsx_only": sum(s["market"] == "CA" for s in stocks),
        "interlisted": sum(bool(s["also_listed"]) for s in stocks),
        "with_iv": len(scans),
        "median_iv": round(statistics.median(values), 1) if values else None,
        "iv_distribution": _distribution(values),
    }


def _settled_rows(run_date: str) -> dict[str, dict]:
    """Rows of the newest earlier-or-same snapshot, each usable only for the session it was built after the close of."""
    for path in reversed(snapshots()):
        if path.name.removesuffix(".gz").removesuffix(".json").rstrip(".") > run_date:
            continue
        try:
            payload = read_snapshot(path)
            built = datetime.fromisoformat(payload["generated_at"])
        except (OSError, ValueError, KeyError, TypeError):
            return {}
        return {r["symbol"]: r for r in payload.get("rows", [])
                if r.get("quote_date") and built >= market.settled_at(date.fromisoformat(r["quote_date"]))}
    return {}


def _reused(saved: dict, stock: dict, borrow_table: dict, today: date) -> dict:
    """A row enriched after its session closed: only borrow terms and the earnings countdown can have moved."""
    loan = borrow.lookup(borrow_table, stock) or {}
    row = {**saved, "borrow_fee": loan.get("fee"), "borrow_available": loan.get("available"),
           "borrow_capped": loan.get("capped"), "borrow_fetched_at": loan.get("fetched_at")}
    if row.get("next_earnings"):
        in_days = (date.fromisoformat(row["next_earnings"]) - today).days
        row.update(dict(NO_EARNINGS) if in_days < 0 else {"earnings_in_days": in_days})
    return row


def _enrich(client, limiter, stock: dict, scan: dict, *, is_watched: bool, bootstrap: bool) -> dict:
    """Network work for one candidate, run on a worker thread; decisions and database writes stay with the caller."""
    info = details.fetch(stock["yahoo"])
    if info is None or (not is_watched and (config.excluded_industry(info["industry"])
                                            or _market_cap_usd(stock, info) < config.MIN_MARKET_CAP_USD)):
        return {"info": info, "rejected": True}
    return {"info": info, "rejected": False,
            "history": _bootstrap_points(client, limiter, stock["symbol"], scan) if bootstrap else [],
            "prices": prices.fetch(stock["yahoo"]), "headlines": news.fetch(stock["yahoo"]),
            "news_checked_at": datetime.now(MARKET_TZ).isoformat(timespec="seconds")}


def _headlines(stock: dict) -> dict:
    return {"headlines": news.fetch(stock["yahoo"]), "news_checked_at": datetime.now(MARKET_TZ).isoformat(timespec="seconds")}


def run(refresh_universe: bool = False, refresh_quotes: bool = False) -> Path:
    """Scan, then build. Likely leaders are downloaded first and shown as a preliminary dashboard while the rest download."""
    from .scan import scan
    run_date, deferred = scan(refresh_universe=refresh_universe, refresh_quotes=refresh_quotes, first=True)
    if not deferred:
        return build(run_date)
    saved = config.SNAPSHOT_DIR / f"{run_date}.json"
    try:  # resuming after an interruption: the preliminary dashboard of this run is still good
        earlier = json.loads(saved.read_text(encoding="utf-8"))
        earlier = earlier if earlier.get("preliminary") and earlier.get("run_date") == run_date else None
    except (OSError, ValueError):
        earlier = None
    build(run_date, cached_snapshot=earlier, preliminary=deferred)
    log(f"Preliminary dashboard ready; downloading the remaining {deferred} stocks")
    scan(run_date)
    return build(run_date, cached_snapshot=json.loads(saved.read_text(encoding="utf-8")))


def build(run_date: str | None = None, *, cached_snapshot: dict | None = None, preliminary: int = 0) -> Path:
    """Rank a scan and write the dashboard. `preliminary` counts stocks still to download, shown as a notice."""
    progress.emit(phase="enrich", completed=0, total=None, activity="Downloading borrow fees and availability from IBKR")
    conn = store.connect()
    run_date = run_date or store.latest_run_date(conn)
    if not run_date:
        conn.close()
        raise SystemExit("No scan found. Run `python -m highiv scan` first.")
    if cached_snapshot is not None and cached_snapshot.get("run_date") != run_date:
        conn.close()
        raise ValueError("Cached enrichment must belong to the scan being built")
    cached = {r["symbol"]: r for r in (cached_snapshot or {}).get("rows", [])}
    universe = _load_universe()
    watched = set(watchlist.load())
    scans = sorted(
        (r for r in store.scan_results(conn, run_date) if r["symbol"] in universe),
        key=lambda r: r["iv30"],
        reverse=True,
    )
    if not scans:
        conn.close()
        raise RuntimeError("No usable IV quotes in this scan. Resume to retry failed requests; the previous dashboard has been kept.")
    today = datetime.now(MARKET_TZ).date()
    borrow_table = borrow.load()
    settled = {} if cached_snapshot is not None else _settled_rows(run_date)
    log(f"Ranking {len(scans)} stocks with IV from the {run_date} scan "
        f"({len(borrow_table)} symbols with borrow data)")

    rows: list[dict] = []
    filled: Counter = Counter()
    seen_companies: set[str] = set()
    lookups: Counter = Counter()
    caps = {symbol: s["market_cap_usd"] for symbol, s in universe.items()}
    for saved in (cached or settled).values():
        if saved["symbol"] in universe and saved.get("market_cap_usd") is not None:
            caps[saved["symbol"]] = saved["market_cap_usd"]
    aq_limiter = net.RateLimiter(min_interval=config.ALPHAQUERY_MIN_INTERVAL_S)
    stock_of = lambda scan: {**universe[scan["symbol"]], "market_cap_usd": caps[scan["symbol"]]}

    def reusable(scan):
        """The saved row this scan can keep: same session and IV (a settled row), or any row of this run's cache."""
        saved = cached.get(scan["symbol"])
        if saved is None:
            saved = settled.get(scan["symbol"])
            if saved is not None and saved.get("quote_date") != scan.get("quote_date"):
                saved = None
        return saved if saved and saved["iv30"] == round(scan["iv30"], 1) else None

    def wanted(scan, counts=filled):
        stock = stock_of(scan)
        return stock["symbol"] in watched or (not stock.get("watch_only") and _still_needed(stock, counts))

    with net.make_client() as client, ThreadPoolExecutor(max_workers=config.ENRICH_WORKERS) as pool:
        jobs = {}  # symbol -> (future, cap band of a detail lookup or None for a headline check)
        pending: Counter = Counter()  # detail lookups submitted but not yet consumed, so prefetching keeps to the budget

        def drop(symbol):
            if symbol in jobs:
                future, band = jobs.pop(symbol)
                future.cancel()
                pending[band] -= band is not None

        def take(symbol):
            future, band = jobs.pop(symbol)
            pending[band] -= band is not None
            return future.result()

        def prefetch(start):
            """Keep the workers busy on the next likely leaders; anything later found unneeded is simply dropped.

            Candidates already queued count as if they will fill their lists, so look-ahead stops as the lists fill up.
            """
            projected = filled.copy()
            for scan in scans[start:start + config.ENRICH_LOOKAHEAD]:
                symbol = scan["symbol"]
                if not wanted(scan, projected if scan is not scans[start] else filled):
                    continue
                if symbol not in watched:
                    projected.update(v for v in VIEWS if _in_view(stock_of(scan), *v, hq_known=False))
                if symbol in jobs:
                    continue
                saved = reusable(scan)
                if saved and symbol in cached:
                    continue  # this run's own rows keep their headlines
                stock = stock_of(scan)
                if saved:
                    jobs[symbol] = (pool.submit(_headlines, stock), None)
                    continue
                band = cap_band(stock["market_cap_usd"])
                # The candidate at `start` has already passed the budget check; only look-ahead is held to it here.
                if scan is not scans[start] and symbol not in watched and lookups[band] + pending[band] >= config.MAX_DETAIL_LOOKUPS:
                    continue
                pending[band] += 1
                jobs[symbol] = (pool.submit(_enrich, client, aq_limiter, stock, scan, is_watched=symbol in watched,
                                            bootstrap=stock["market"] == "US" and _needs_bootstrap(conn, symbol, scan)), band)

        try:
            for position, scan in enumerate(scans):
                stock = stock_of(scan)
                is_watched = stock["symbol"] in watched
                if not wanted(scan):
                    drop(scan["symbol"])
                    continue
                saved = reusable(scan)
                if saved:
                    company = norm_name(saved["name"])
                    if is_watched or company not in seen_companies:
                        row = saved
                        if stock["symbol"] not in cached:
                            prefetch(position)
                            fresh = take(stock["symbol"])
                            row = _reused(saved, stock, borrow_table, today)
                            row.update(news_headlines=fresh["headlines"], news_checked_at=fresh["news_checked_at"])
                            row.update(explain.choose(row, fresh["headlines"], as_of=date.fromisoformat(row["quote_date"])))
                        rows.append(row)
                        seen_companies.add(company)
                        filled.update(v for v in VIEWS if _in_view(row, *v, hq_known=True))
                        progress.emit(completed=len(rows), activity=f"Kept {stock['symbol']} from the last close; checked headlines")
                    drop(stock["symbol"])
                    continue
                band = cap_band(stock["market_cap_usd"])
                if not is_watched and lookups[band] >= config.MAX_DETAIL_LOOKUPS:
                    drop(stock["symbol"])
                    continue
                progress.emit(activity=f"Downloading company details, charts and headlines for {stock['symbol']}")
                prefetch(position)
                fetched = take(stock["symbol"])
                info = fetched["info"]
                lookups[band] += 1
                if fetched["rejected"]:
                    continue
                caps[stock["symbol"]] = _market_cap_usd(stock, info)
                company = norm_name(info["name"] or stock["name"])
                if not is_watched and company in seen_companies:
                    continue  # same company on a second line
                seen_companies.add(company)
                if fetched["history"]:
                    store.insert_history(conn, stock["symbol"], fetched["history"], "alphaquery")
                price_frames, price_stats = fetched["prices"]
                row = _row(stock, scan, info, store.iv_series(conn, stock["symbol"]), today,
                           price_frames, price_stats, borrow.lookup(borrow_table, stock))
                headlines = fetched["headlines"]
                row["news_headlines"] = headlines
                row["news_checked_at"] = fetched["news_checked_at"]
                row.update(explain.choose(row, headlines, as_of=date.fromisoformat(scan.get("quote_date") or run_date)))
                row["watch_only"] = bool(stock.get("watch_only") or config.excluded_industry(info["industry"]))
                rows.append(row)
                progress.emit(completed=len(rows), activity=f"Enriched {stock['symbol']}: charts, short interest and news")
                filled.update(v for v in VIEWS if _in_view(row, *v, hq_known=True))
                if len(rows) % 10 == 0:
                    log(f"  {len(rows)} leaders enriched ({sum(lookups.values())} new lookups)")
        finally:
            for future, _ in jobs.values():
                future.cancel()

    logos.apply(rows)
    quote_dates = Counter(r["quote_date"] for r in scans if r["quote_date"])
    stocks = [{**s, "market_cap_usd": caps[s["symbol"]]} for s in universe.values()]
    payload = {
        "run_date": run_date,
        "quote_date": quote_dates.most_common(1)[0][0] if quote_dates else run_date,
        "generated_at": datetime.now(MARKET_TZ).isoformat(timespec="minutes"),
        "settings": {
            "min_market_cap_usd": config.MIN_MARKET_CAP_USD,
            "large_market_cap_usd": config.LARGE_MARKET_CAP_USD,
            "top_n": config.TOP_N,
            "squeeze_high": config.SQUEEZE_HIGH,
            "squeeze_elevated": config.SQUEEZE_ELEVATED,
            "small_float_shares": config.SMALL_FLOAT_SHARES,
            "min_iv_history_points": config.MIN_IV_HISTORY_POINTS,
            "excluded_label": config.EXCLUDED_LABEL,
            "excluded_short": config.EXCLUDED_SHORT,
            "price_frames": list(prices.FRAMES),
        },
        "universe": _universe_stats(stocks, scans),
        "universe_by_cap": {
            band: _universe_stats(
                [s for s in stocks if cap_band(s["market_cap_usd"]) == band],
                [r for r in scans if cap_band(caps[r["symbol"]]) == band],
            ) for band in config.CAP_BANDS
        },
        "watchlist": sorted(watched),
        "rows": rows,
    }
    if preliminary:
        payload["preliminary"] = {"remaining": preliminary, "checked": len(scans)}
    conn.close()
    leverage.enrich(payload, sentiment.enrich(payload))
    commodities.enrich(payload)
    macro_search.enrich(payload, refresh=not preliminary)
    progress.emit(phase="render", activity="Building both dashboard tabs")
    return write_outputs(payload)


def explain_latest() -> Path:
    """Re-read the latest snapshot, fetch news, and rewrite the Why this IV column."""
    snaps = snapshots()
    if not snaps:
        raise SystemExit("No snapshot found. Run `python -m highiv build` first.")
    payload = read_snapshot(snaps[-1])
    rows = payload["rows"]
    log(f"Explaining IV for {len(rows)} leaders from the {payload['run_date']} snapshot")
    for i, row in enumerate(rows, 1):
        as_of = date.fromisoformat(row.get("quote_date") or payload.get("quote_date") or payload["run_date"])
        # Old snapshots did not retain the Yahoo spelling of US class shares (BRK.B -> BRK-B).
        yahoo_symbol = row.get("yahoo_symbol") or (row["symbol"] if row["market"] == "CA" else row["symbol"].replace(".", "-"))
        headlines = news.fetch(yahoo_symbol)
        row["news_headlines"] = headlines
        row["news_checked_at"] = datetime.now(MARKET_TZ).isoformat(timespec="seconds")
        row.update(explain.choose(row, headlines, as_of=as_of))
        if i % 10 == 0 or i == len(rows):
            log(f"  {i}/{len(rows)} news lookups")
    payload["generated_at"] = datetime.now(MARKET_TZ).isoformat(timespec="minutes")
    return write_outputs(payload)


def sentiment_latest() -> Path:
    """Refresh sentiment on the saved screen without running the full IV scan."""
    snaps = snapshots()
    if not snaps:
        raise SystemExit("No snapshot found. Run `python -m highiv build` first.")
    payload = read_snapshot(snaps[-1])
    leverage.enrich(payload, sentiment.enrich(payload))
    commodities.enrich(payload)
    macro_search.enrich(payload)
    # Keep market-data generation timestamps intact; sentiment carries its own timestamps.
    return write_outputs(payload)


def macro_search_latest() -> Path:
    """Refresh only search concerns, preserving all market data and sentiment scores."""
    snaps = snapshots()
    if not snaps:
        raise SystemExit("No snapshot found. Run `python -m highiv build` first.")
    payload = read_snapshot(snaps[-1])
    macro_search.enrich(payload)
    return write_outputs(payload)
