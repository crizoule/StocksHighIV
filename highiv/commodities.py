"""Commodity futures: each market's price with the CFTC's weekly positioning underneath, most liquid first.

Positions come from the Disaggregated Commitments of Traders report (futures only), which splits traders into
producers/merchants, swap dealers, managed money and other reportables. Managed money (hedge funds and commodity
trading advisers) is the speculative crowd most COT readers follow; producers and merchants are the hedgers on the
other side. Every commodity contract the report covers is listed; the ~30 main futures with a free continuous price
on Yahoo get a chart, and the rest (regional basis swaps, indices, electricity, emissions) a row in a table.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import httpx
import pandas as pd
import yfinance as yf

from . import config, context, fear_greed, progress, sentiment

COT_URL = "https://publicreporting.cftc.gov/resource/72hh-3qpy.json"  # Disaggregated, futures only; no key
COT_PAGE = "https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm"
PRICE_START = "2006-01-01"   # the disaggregated report begins in June 2006
COT_INDEX_WEEKS = 156        # the usual three-year "COT index" window
CHUNK = 8                    # markets per request: a full history for eight stays under the response-size cap
TTL_HOURS = {"cot": 12, "prices": 6}
CATEGORIES = {"energy": "Energy", "metals": "Metals", "grains": "Grains", "oilseeds": "Oilseeds", "softs": "Softs",
              "livestock": "Livestock", "dairy": "Dairy", "wood": "Lumber", "electricity": "Electricity",
              "emissions": "Emissions", "chemicals": "Chemicals"}
# The main futures contract per commodity with a continuous Yahoo price: CFTC code → name, exchange, symbol, category.
MARKETS = {
    "067651": ("WTI crude oil", "NYMEX", "CL=F", "energy"),
    "06765T": ("Brent crude oil", "NYMEX", "BZ=F", "energy"),
    "023651": ("Natural gas (Henry Hub)", "NYMEX", "NG=F", "energy"),
    "111659": ("RBOB gasoline", "NYMEX", "RB=F", "energy"),
    "022651": ("ULSD heating oil", "NYMEX", "HO=F", "energy"),
    "088691": ("Gold", "COMEX", "GC=F", "metals"),
    "084691": ("Silver", "COMEX", "SI=F", "metals"),
    "085692": ("Copper", "COMEX", "HG=F", "metals"),
    "076651": ("Platinum", "NYMEX", "PL=F", "metals"),
    "075651": ("Palladium", "NYMEX", "PA=F", "metals"),
    "192651": ("Steel, hot-rolled coil", "COMEX", "HRC=F", "metals"),
    "002602": ("Corn", "CBOT", "ZC=F", "grains"),
    "001602": ("Wheat, soft red winter", "CBOT", "ZW=F", "grains"),
    "001612": ("Wheat, hard red winter", "CBOT", "KE=F", "grains"),
    "039601": ("Rough rice", "CBOT", "ZR=F", "grains"),
    "005602": ("Soybeans", "CBOT", "ZS=F", "oilseeds"),
    "026603": ("Soybean meal", "CBOT", "ZM=F", "oilseeds"),
    "007601": ("Soybean oil", "CBOT", "ZL=F", "oilseeds"),
    "080732": ("Sugar No. 11", "ICE US", "SB=F", "softs"),
    "033661": ("Cotton No. 2", "ICE US", "CT=F", "softs"),
    "073732": ("Cocoa", "ICE US", "CC=F", "softs"),
    "083731": ("Coffee C", "ICE US", "KC=F", "softs"),
    "040701": ("Orange juice", "ICE US", "OJ=F", "softs"),
    "054642": ("Lean hogs", "CME", "HE=F", "livestock"),
    "057642": ("Live cattle", "CME", "LE=F", "livestock"),
    "061641": ("Feeder cattle", "CME", "GF=F", "livestock"),
    "052641": ("Milk, Class III", "CME", "DC=F", "dairy"),
    "063642": ("Cheese", "CME", "CSC=F", "dairy"),
    "050642": ("Butter", "CME", "CB=F", "dairy"),
    "052642": ("Nonfat dry milk", "CME", "GNF=F", "dairy"),
    "058644": ("Lumber", "CME", "LBR=F", "wood"),
}
SUBGROUPS = {"PETROLEUM AND PRODUCTS": "energy", "NATURAL GAS AND PRODUCTS": "energy", "PRECIOUS METALS": "metals",
             "BASE METALS": "metals", "GRAINS": "grains", "OILSEED and PRODUCTS": "oilseeds", "FOODSTUFFS/SOFTS": "softs",
             "FIBER": "softs", "LIVESTOCK/MEAT PRODUCTS": "livestock", "DAIRY PRODUCTS": "dairy", "WOOD PRODUCTS": "wood",
             "ELECTRICITY AND SOURCES": "electricity", "EMISSIONS": "emissions", "CHEMICALS": "chemicals"}
EXCHANGES = {"ICE FUTURES ENERGY DIV": "ICE Energy", "ICE FUTURES U.S.": "ICE US", "ICE FUTURES EUROPE": "ICE Europe",
             "NEW YORK MERCANTILE EXCHANGE": "NYMEX", "CHICAGO MERCANTILE EXCHANGE": "CME", "CHICAGO BOARD OF TRADE": "CBOT",
             "COMMODITY EXCHANGE INC.": "COMEX", "MIAX FUTURES EXCHANGE": "MIAX", "NODAL EXCHANGE": "Nodal"}
FIELDS = ("report_date_as_yyyy_mm_dd", "cftc_contract_market_code", "open_interest_all", "m_money_positions_long_all",
          "m_money_positions_short_all", "prod_merc_positions_long", "prod_merc_positions_short")


def cot_index(values, current, weeks=COT_INDEX_WEEKS):
    """Percentile of the latest net position within the trailing window, 0–100; None without a full window."""
    window = values[-weeks:]
    if len(window) < weeks:
        return None
    return round(sum(v < current for v in window[:-1]) / (len(window) - 1) * 100)


def parse_cot(rows, today):
    """Weekly managed-money and producer/merchant net positions per market, as shares of open interest."""
    weeks = {code: {} for code in MARKETS}
    for row in rows:
        try:
            code, when = row["cftc_contract_market_code"].strip(), sentiment.iso_date(row["report_date_as_yyyy_mm_dd"])
            interest = sentiment.number(row["open_interest_all"], 1)
            managed = sentiment.number(row["m_money_positions_long_all"], 0) - sentiment.number(row["m_money_positions_short_all"], 0)
            producers = sentiment.number(row["prod_merc_positions_long"], 0) - sentiment.number(row["prod_merc_positions_short"], 0)
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
        if code in weeks and when <= today:
            weeks[code][when] = (interest, managed, producers)
    markets = {}
    for code, history in weeks.items():
        days = sorted(history)
        if len(days) < 20:
            continue  # too little to chart; the market still appears in the full contract list
        last = days[-1]
        interest, managed, producers = history[last]
        pct = lambda day, i: round(history[day][i] / history[day][0] * 100, 1)
        markets[code] = dict(as_of=last.isoformat(), open_interest=round(interest), managed=round(managed),
                             managed_pct=pct(last, 1), producers_pct=pct(last, 2),
                             index=cot_index([history[d][1] for d in days], managed),
                             managed_history=[[d.isoformat(), pct(d, 1)] for d in days],
                             producer_history=[[d.isoformat(), pct(d, 2)] for d in days])
    if not markets:
        raise ValueError("No commodity positions")
    return markets


def parse_contracts(rows, charted):
    """Every other commodity contract in the latest week: name, category, open interest and managed-money net."""
    out = []
    for row in rows:
        try:
            code = row["cftc_contract_market_code"].strip()
            interest = sentiment.number(row["open_interest_all"], 1)
            managed = sentiment.number(row.get("m_money_positions_long_all") or 0, 0) - sentiment.number(row.get("m_money_positions_short_all") or 0, 0)
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
        if code in charted:
            continue
        name, _, exchange = str(row.get("market_and_exchange_names") or row.get("contract_market_name") or code).partition(" - ")
        out.append(dict(code=code, name=name.strip(), exchange=EXCHANGES.get(exchange.strip(), exchange.strip().title()),
                        category=SUBGROUPS.get(str(row.get("commodity_subgroup_name") or "").strip(), "other"),
                        open_interest=round(interest), managed_pct=round(managed / interest * 100, 1)))
    return sorted(out, key=lambda c: -c["open_interest"])


def fetch_cot(client, today):
    fields = ", ".join(FIELDS)
    codes = list(MARKETS)

    def chunk(part):
        where = "cftc_contract_market_code in (" + ", ".join(f"'{c}'" for c in part) + ")"
        return json.loads(sentiment.request_text(client, COT_URL, params={"$select": fields, "$where": where,
                                                                          "$order": "report_date_as_yyyy_mm_dd", "$limit": 20000}))
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = [row for part in pool.map(chunk, [codes[i:i + CHUNK] for i in range(0, len(codes), CHUNK)]) for row in part]
    markets = parse_cot(rows, today)
    latest = max(m["as_of"] for m in markets.values())
    everything = json.loads(sentiment.request_text(client, COT_URL, params={
        "$select": "cftc_contract_market_code, market_and_exchange_names, commodity_subgroup_name, open_interest_all, "
                   "m_money_positions_long_all, m_money_positions_short_all",
        "$where": f"report_date_as_yyyy_mm_dd = '{latest}T00:00:00.000'", "$limit": 5000}))
    return {"as_of": latest, "markets": markets, "contracts": parse_contracts(everything, set(MARKETS))}


def weekly_prices(closes):
    """Each week's last close (weeks end on Friday), plus the latest close, oldest first."""
    closes = closes.dropna()
    if closes.empty:
        return []
    weekly = closes.resample("W-FRI").last().dropna()
    points = [(d.date() if d.date() <= closes.index[-1].date() else closes.index[-1].date(), float(v)) for d, v in weekly.items()]
    return [[d.isoformat(), round(v, 4 if v < 10 else 2)] for d, v in points]


def fetch_prices(now):
    local = now.astimezone(context.MARKET_TZ)
    symbols = sorted({m[2] for m in MARKETS.values()})
    frame = fear_greed.completed(yf.download(symbols, start=PRICE_START, interval="1d", auto_adjust=False, group_by="column",
                                             progress=False, threads=True, timeout=30), local.date(), local.hour)
    closes = frame["Close"]
    prices = {}
    for symbol in symbols:
        if symbol not in closes:
            continue
        series = closes[symbol].dropna()
        if len(series) < 60:
            continue
        last, when = float(series.iloc[-1]), series.index[-1].date()
        before = lambda days: series[series.index <= series.index[-1] - pd.Timedelta(days=days)]
        change = lambda days: round((last / float(before(days).iloc[-1]) - 1) * 100, 1) if len(before(days)) else None
        prices[symbol] = dict(as_of=when.isoformat(), last=round(last, 4 if last < 10 else 2), change_1m=change(30),
                              change_1y=change(365), points=weekly_prices(series))
    if not prices:
        raise ValueError("No commodity prices")
    return {"as_of": max(p["as_of"] for p in prices.values()), "prices": prices}


def collect(now=None):
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(context.MARKET_TZ).date()
    progress.emit(activity="Checking commodity prices and CFTC positions")
    with httpx.Client(headers={"User-Agent": config.USER_AGENT}, timeout=30, follow_redirects=True) as client:
        jobs = {"cot": lambda: fetch_cot(client, today), "prices": lambda: fetch_prices(now)}
        with ThreadPoolExecutor(max_workers=2) as pool:
            done = {key: pool.submit(sentiment.cached_read, f"commodities-{key}", fetch, now, TTL_HOURS[key], sessions=key == "prices")
                    for key, fetch in jobs.items()}
            return {key: future.result() for key, future in done.items()}


def evaluate(payload, inputs, *, now=None):
    """Markets grouped by category, each category and each market most liquid (by open interest) first."""
    now = now or datetime.now(timezone.utc)
    cot, prices = inputs.get("cot") or {}, inputs.get("prices") or {}
    positions, quotes = cot.get("markets") or {}, prices.get("prices") or {}
    groups = {}
    for code, (name, exchange, symbol, category) in MARKETS.items():
        held, quote = positions.get(code), quotes.get(symbol)
        if not held and not quote:
            continue
        groups.setdefault(category, []).append(dict(
            code=code, name=name, exchange=exchange, symbol=symbol, **{k: v for k, v in (held or {}).items()},
            price=(quote or {}).get("last"), price_as_of=(quote or {}).get("as_of"), change_1m=(quote or {}).get("change_1m"),
            change_1y=(quote or {}).get("change_1y"), price_history=(quote or {}).get("points") or []))
    ordered = sorted(groups.items(), key=lambda kv: -sum(m.get("open_interest") or 0 for m in kv[1]))
    as_of = cot.get("as_of")
    released = next_release = None
    if as_of:
        report = sentiment.iso_date(as_of)
        released, next_release = (report + timedelta(days=3)).isoformat(), (report + timedelta(days=10)).isoformat()
    payload["commodities"] = {
        "checked_at": now.isoformat(), "as_of": as_of, "released": released, "next": next_release, "next_time": "3:30 PM ET",
        "cot_status": cot.get("status", "unavailable"), "price_status": prices.get("status", "unavailable"),
        "prices_as_of": prices.get("as_of"), "url": COT_PAGE,
        "groups": [dict(key=key, name=CATEGORIES[key], open_interest=sum(m.get("open_interest") or 0 for m in markets),
                        markets=sorted(markets, key=lambda m: -(m.get("open_interest") or 0))) for key, markets in ordered],
        "contracts": cot.get("contracts") or [],
        "categories": CATEGORIES,
    }


def enrich(payload):
    evaluate(payload, collect())
