"""Daily scan: build the universe, then fetch 30-day IV for every stock. Safe to interrupt and re-run."""
from __future__ import annotations

import json
import time
from contextlib import closing
from datetime import datetime
from functools import partial
from zoneinfo import ZoneInfo

from . import config, iv, net, store, universe

MARKET_TZ = ZoneInfo("America/Toronto")
log = partial(print, flush=True)


def market_today() -> str:
    return datetime.now(MARKET_TZ).date().isoformat()


def load_universe(client, run_date: str, refresh: bool = False) -> list[dict]:
    """Build the universe once per day and cache it, so a resumed scan works on the same list."""
    path = config.DATA_DIR / "universe.json"
    if path.exists() and not refresh:
        cached = json.loads(path.read_text())
        if cached.get("run_date") == run_date:
            return cached["stocks"]
    stocks = universe.build_universe(client)
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"run_date": run_date, "stocks": stocks}))
    return stocks


def scan(run_date: str | None = None, refresh_universe: bool = False, refresh_quotes: bool = False) -> str:
    run_date = run_date or market_today()
    with closing(store.connect()) as conn, net.make_client() as client:
        stocks = load_universe(client, run_date, refresh_universe)
        us_count = sum(s["market"] == "US" for s in stocks)
        log(f"Universe {run_date}: {len(stocks)} optionable stocks over "
            f"${config.MIN_MARKET_CAP_USD / 1e9:g}B ({us_count} US-listed, {len(stocks) - us_count} TSX-only)")

        done = set() if refresh_quotes else store.scanned_symbols(conn, run_date)
        # Montreal Exchange names first: they use a separate, faster limit.
        todo = sorted((s for s in stocks if s["symbol"] not in done), key=lambda s: s["market"] != "CA")
        if done:
            log(f"Resuming: {len(done)} already scanned today, {len(todo)} to go")

        cboe_limiter = net.RateLimiter(config.CBOE_MAX_PER_MINUTE, 60.0, 60.0 / config.CBOE_MAX_PER_MINUTE)
        mx_limiter = net.RateLimiter(min_interval=config.MX_MIN_INTERVAL_S)
        started = time.monotonic()
        with_iv = 0
        for i, stock in enumerate(todo, 1):
            source = "cboe" if stock["cboe"] else "mx"
            failed = False
            try:
                result = (iv.cboe_iv30(client, cboe_limiter, stock["cboe"]) if stock["cboe"]
                          else iv.mx_iv30(client, mx_limiter, stock["mx"]))
            except (net.FetchError, ValueError, TypeError, KeyError, OverflowError) as exc:
                result, failed = None, True
                log(f"  {stock['symbol']}: {exc}; will retry on the next scan")
            store.record_scan(conn, run_date, stock["symbol"], source, result, failed=failed)
            with_iv += result is not None
            if i % 100 == 0 or i == len(todo):
                per_item = (time.monotonic() - started) / i
                log(f"  {i}/{len(todo)} scanned ({with_iv} with IV), ~{(len(todo) - i) * per_item / 60:.0f} min left")
    return run_date
