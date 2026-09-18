"""Rank the latest scan, enrich the leaders (float, short interest, 52-week data, IV rank) and write the dashboard."""
from __future__ import annotations

import json
import re
import statistics
from collections import Counter
from datetime import date, datetime
from functools import partial
from pathlib import Path
from zoneinfo import ZoneInfo

from . import borrow, config, context, details, explain, iv, net, news, prices, store
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
    return {s["symbol"]: s for s in json.loads(path.read_text())["stocks"]}


def _bootstrap_history(conn, client, limiter, symbol: str, scan: dict) -> None:
    """Seed IV rank with ~3 months of AlphaQuery history, scaled to CBOE's level on their last shared session."""
    if not scan.get("quote_date") or store.has_source(conn, symbol, "alphaquery"):
        return
    if len(store.iv_series(conn, symbol)) >= config.MIN_IV_HISTORY_POINTS:
        return
    points = [(d, v) for d, v in iv.alphaquery_history(client, limiter, symbol) if d < scan["quote_date"]]
    if not points:
        return
    scale = 1.0
    if scan.get("iv30_change") is not None and points[-1][1] > 0:
        previous_close_iv = scan["iv30"] - scan["iv30_change"]
        gap = (date.fromisoformat(scan["quote_date"]) - date.fromisoformat(points[-1][0])).days
        if 0 < gap <= 5:
            scale = min(max(previous_close_iv / points[-1][1], 0.75), 1.33)
    store.insert_history(conn, symbol, [(d, round(v * scale, 3)) for d, v in points], "alphaquery")


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
        "earnings_time": when.strftime("%-I:%M %p").lower() if confirmed else None,
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
        "market_cap_usd": _market_cap_usd(stock, info),
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
    template = (config.TEMPLATE_DIR / "dashboard.html").read_text()
    data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")
    return (
        template.replace("/*__CSS__*/", (config.TEMPLATE_DIR / "dashboard.css").read_text())
        .replace("/*__JS__*/", (config.TEMPLATE_DIR / "dashboard.js").read_text())
        .replace("__DATA_JSON__", data)
    )


def write_outputs(payload: dict) -> Path:
    context.apply(payload)
    config.SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (config.SNAPSHOT_DIR / f"{payload['run_date']}.json").write_text(json.dumps(payload))
    page = render(payload)
    (config.OUTPUT_DIR / "artifact.html").write_text(page)  # body-only page for publishing
    standalone = config.OUTPUT_DIR / "dashboard.html"
    standalone.write_text(
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
        f"</head>\n<body>\n{page}\n</body>\n</html>\n"
    )
    log(f"Dashboard written to {standalone}")
    return standalone


def _universe_stats(stocks: list[dict], scans: list[dict]) -> dict:
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


def build(run_date: str | None = None, *, cached_snapshot: dict | None = None) -> Path:
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
    scans = sorted(
        (r for r in store.scan_results(conn, run_date) if r["symbol"] in universe),
        key=lambda r: r["iv30"],
        reverse=True,
    )
    today = datetime.now(MARKET_TZ).date()
    borrow_table = borrow.load()
    log(f"Ranking {len(scans)} stocks with IV from the {run_date} scan "
        f"({len(borrow_table)} symbols with borrow data)")

    rows: list[dict] = []
    filled: Counter = Counter()
    seen_companies: set[str] = set()
    lookups: Counter = Counter()
    caps = {symbol: s["market_cap_usd"] for symbol, s in universe.items()}
    caps.update({symbol: r["market_cap_usd"] for symbol, r in cached.items() if symbol in universe})
    aq_limiter = net.RateLimiter(min_interval=config.ALPHAQUERY_MIN_INTERVAL_S)
    with net.make_client() as client:
        for scan in scans:
            if all(filled[v] >= config.TOP_N for v in VIEWS):
                break
            stock = {**universe[scan["symbol"]], "market_cap_usd": caps[scan["symbol"]]}
            if not _still_needed(stock, filled):
                continue
            saved = cached.get(stock["symbol"])
            if saved and saved["iv30"] == round(scan["iv30"], 1):
                company = norm_name(saved["name"])
                if company not in seen_companies:
                    rows.append(saved)
                    seen_companies.add(company)
                    filled.update(v for v in VIEWS if _in_view(saved, *v, hq_known=True))
                continue
            band = cap_band(stock["market_cap_usd"])
            if lookups[band] >= config.MAX_DETAIL_LOOKUPS:
                continue
            info = details.fetch(stock["yahoo"])
            lookups[band] += 1
            if info is None or config.excluded_industry(info["industry"]):
                continue
            if _market_cap_usd(stock, info) < config.MIN_MARKET_CAP_USD:
                continue  # the two sources disagree on size; the stock must clear the floor on both
            caps[stock["symbol"]] = _market_cap_usd(stock, info)
            company = norm_name(info["name"] or stock["name"])
            if company in seen_companies:
                continue  # same company on a second line
            seen_companies.add(company)
            if stock["market"] == "US":
                _bootstrap_history(conn, client, aq_limiter, stock["symbol"], scan)
            price_frames, price_stats = prices.fetch(stock["yahoo"])
            row = _row(stock, scan, info, store.iv_series(conn, stock["symbol"]), today,
                       price_frames, price_stats, borrow.lookup(borrow_table, stock))
            headlines = news.fetch(stock["yahoo"])
            row["news_headlines"] = headlines
            row["news_checked_at"] = datetime.now(MARKET_TZ).isoformat(timespec="seconds")
            row.update(explain.choose(row, headlines, as_of=date.fromisoformat(scan.get("quote_date") or run_date)))
            rows.append(row)
            filled.update(v for v in VIEWS if _in_view(row, *v, hq_known=True))
            if len(rows) % 10 == 0:
                log(f"  {len(rows)} leaders enriched ({sum(lookups.values())} new lookups)")

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
        "rows": rows,
    }
    conn.close()
    return write_outputs(payload)


def explain_latest() -> Path:
    """Re-read the latest snapshot, fetch news, and rewrite the Why this IV column."""
    snaps = sorted(config.SNAPSHOT_DIR.glob("*.json"))
    if not snaps:
        raise SystemExit("No snapshot found. Run `python -m highiv build` first.")
    payload = json.loads(snaps[-1].read_text())
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
