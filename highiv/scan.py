"""Daily scan: build the universe, then fetch 30-day IV for every stock. Safe to interrupt and re-run."""
from __future__ import annotations

import json
import time
from contextlib import closing
from datetime import date, datetime, timezone
from functools import partial
from zoneinfo import ZoneInfo

from . import config, iv, market, net, store, universe, progress, watchlist

MARKET_TZ = ZoneInfo("America/Toronto")
log = partial(print, flush=True)


def market_today() -> str:
    return datetime.now(MARKET_TZ).date().isoformat()


def load_universe(client, run_date: str, refresh: bool = False) -> list[dict]:
    """Build the universe once per day and cache it, so a resumed scan works on the same list."""
    path = config.DATA_DIR / "universe.json"
    if path.exists() and not refresh:
        cached = json.loads(path.read_text(encoding="utf-8"))
        if cached.get("run_date") == run_date:
            stocks = watchlist.include(cached["stocks"], watchlist.load())
            path.write_text(json.dumps({"run_date": run_date, "stocks": stocks}))
            return stocks
    stocks = watchlist.include(universe.build_universe(client), watchlist.load())
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"run_date": run_date, "stocks": stocks}))
    return stocks


def settled_since(client, limiter: net.RateLimiter, now: datetime) -> dict[str, str | None]:
    """Per source, the earliest download time (UTC) whose quotes are still final; None while quotes can change.

    A quote fetched after its session settled stays final until the next session trades. For Cboe, SPY's quote
    date tells which session is the latest, so market holidays need no calendar; the Montreal Exchange page
    has no reliable session date, so it follows the weekday calendar.
    """
    if market.is_open(now):
        return {"cboe": None, "mx": None}
    try:
        probe = iv.cboe_iv30(client, limiter, "SPY")
    except (net.FetchError, ValueError, TypeError, KeyError, OverflowError):
        probe = None
    session = (probe or {}).get("quote_date")
    day, next_open = market.last_session(now)
    stamp = lambda day: market.settled_at(day).astimezone(timezone.utc).isoformat(timespec="seconds")
    return {"cboe": stamp(date.fromisoformat(session)) if session else None,
            "mx": stamp(day) if now < next_open else None}


def priority(stocks: list[dict], previous: dict[str, float | None], watched: set[str]) -> set[str]:
    """Stocks likely to reach a top-100 list today, downloaded first so a preliminary dashboard can be built.

    Large caps, TSX listings (Montréal Exchange names and interlisted stocks, which fill the TSX view), the
    watchlist, stocks never checked before, and the mid caps whose last IV ranked within PRIORITY_TOP of their
    band — overall or among North American headquarters. Empty when there is no earlier scan to rank by.
    """
    if not any(v is not None for v in previous.values()):
        return set()
    chosen = {s["symbol"] for s in stocks if s["market_cap_usd"] >= config.LARGE_MARKET_CAP_USD or s["market"] == "CA"
              or s["also_listed"] or s["symbol"] in watched or s["symbol"] not in previous}
    ranked = sorted((s for s in stocks if previous.get(s["symbol"]) is not None), key=lambda s: -previous[s["symbol"]])
    north_america = [s for s in ranked if not s["hq_country"] or s["hq_country"] in ("United States", "Canada")]
    for group in (ranked, north_america):
        chosen.update(s["symbol"] for s in group[:config.PRIORITY_TOP])
    return chosen


def scan(run_date: str | None = None, refresh_universe: bool = False, refresh_quotes: bool = False,
         first: bool = False) -> tuple[str, int]:
    """Download what today's run still needs; returns the run date and how many stocks were deferred.

    With `first`, only the priority stocks are downloaded (see `priority`) and the rest are deferred to a second call.
    """
    run_date = run_date or market_today()
    progress.emit(phase="universe", activity="Downloading the list of optionable companies")
    with closing(store.connect()) as conn, net.make_client() as client:
        stocks = load_universe(client, run_date, refresh_universe)
        us_count = sum(s["market"] == "US" for s in stocks)
        log(f"Universe {run_date}: {len(stocks)} optionable stocks over "
            f"${config.MIN_MARKET_CAP_USD / 1e9:g}B ({us_count} US-listed, {len(stocks) - us_count} TSX-only)")

        cboe_limiter = net.RateLimiter(config.CBOE_MAX_PER_MINUTE, 60.0, 60.0 / config.CBOE_MAX_PER_MINUTE)
        settled = settled_since(client, cboe_limiter, datetime.now(timezone.utc))
        if refresh_quotes:
            # Persist pending work so Resume also works after an interrupted same-day refresh.
            store.reopen_unsettled(conn, run_date, settled)
        done = store.scanned_symbols(conn, run_date)
        waiting = {s["symbol"]: "cboe" if s["cboe"] else "mx" for s in stocks if s["symbol"] not in done}
        # Quotes downloaded after the last close cannot have changed since: copy them instead of asking again.
        reused = store.reuse_settled(conn, run_date, waiting, settled)
        # Montreal Exchange names first: they use a separate, faster limit.
        todo = sorted((s for s in stocks if s["symbol"] not in done | reused), key=lambda s: s["market"] != "CA")
        later = []
        if first:
            wanted = priority(stocks, store.previous_iv(conn, run_date), set(watchlist.load()))
            if wanted:
                later = [s for s in todo if s["symbol"] not in wanted]
                todo = [s for s in todo if s["symbol"] in wanted]
                if later:
                    log(f"Priority pass: {len(todo)} likely leaders first; {len(later)} lower-IV stocks follow after a preliminary dashboard")
        if reused:
            log(f"Reusing {len(reused)} quotes downloaded after the last close, {len(todo)} to download")
        if done:
            log(f"Resuming: {len(done)} already scanned today, {len(todo)} to go")

        mx_limiter = net.RateLimiter(min_interval=config.MX_MIN_INTERVAL_S)
        started = time.monotonic()
        with_iv = 0
        errors = 0
        progress.emit(phase="scan", completed=0, total=len(todo), reused=len(stocks)-len(todo)-len(later), with_iv=0, errors=0,
                      activity="Downloading IV quotes for likely leaders" if later else "Downloading IV quotes")
        for i, stock in enumerate(todo, 1):
            source = "cboe" if stock["cboe"] else "mx"
            failed = False
            progress.emit(activity=f"Downloading {stock['symbol']} from {source.upper()}")
            try:
                result = (iv.cboe_iv30(client, cboe_limiter, stock["cboe"]) if stock["cboe"]
                          else iv.mx_iv30(client, mx_limiter, stock["mx"]))
            except (net.FetchError, ValueError, TypeError, KeyError, OverflowError) as exc:
                result, failed = None, True
                log(f"  {stock['symbol']}: {exc}; will retry on the next scan")
            store.record_scan(conn, run_date, stock["symbol"], source, result, failed=failed,
                              fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
            with_iv += result is not None
            errors += failed
            progress.emit(completed=i, with_iv=with_iv, errors=errors, activity=f"Checked {stock['symbol']}")
            if i % 100 == 0 or i == len(todo):
                per_item = (time.monotonic() - started) / i
                log(f"  {i}/{len(todo)} scanned ({with_iv} with IV), ~{(len(todo) - i) * per_item / 60:.0f} min left")
    return run_date, len(later)
