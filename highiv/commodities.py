"""Futures markets: each price with the CFTC's weekly positioning underneath, grouped by category, most liquid first.

Two Commitments of Traders reports, futures only, read differently:

* Physical commodities (Disaggregated report). Producers and merchants hedge what they grow, mine or use and are
  structurally net short; managed money (hedge funds and commodity trading advisers) is the speculative crowd, so it
  leads, and its extremes are usually read contrarian.
* Financial futures (Traders in Financial Futures report). There are no producers. In stock indices, Treasuries and
  crypto, leveraged funds' large net shorts are mostly basis trades (short futures against long stocks, bonds or ETFs,
  financed with borrowed money), so asset managers' real-money position carries the directional view. In currencies,
  short-term rates and VIX, leveraged funds are the speculative bet and lead.

Every contract either report covers is listed; the main futures with a free continuous price on Yahoo get a chart.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import httpx
import pandas as pd
import yfinance as yf

from . import config, context, fear_greed, progress, sentiment

COT_URLS = {"physical": "https://publicreporting.cftc.gov/resource/72hh-3qpy.json",    # Disaggregated, futures only
            "financial": "https://publicreporting.cftc.gov/resource/gpe5-46if.json"}   # Traders in Financial Futures
COT_PAGE = "https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm"
PRICE_START = "2006-01-01"   # both reports begin in June 2006
COT_INDEX_WEEKS = 156        # the usual three-year "COT index" window
CHUNK = 8                    # markets per request: a full history for eight stays under the response-size cap
TTL_HOURS = {"physical": 12, "financial": 12, "prices": 6}
GROUPS = {  # trader group → report fields (long, short) and label
    "managed": ("m_money_positions_long_all", "m_money_positions_short_all", "Managed money"),
    "producers": ("prod_merc_positions_long", "prod_merc_positions_short", "Producers"),
    "asset_mgr": ("asset_mgr_positions_long", "asset_mgr_positions_short", "Asset managers"),
    "lev_money": ("lev_money_positions_long", "lev_money_positions_short", "Leveraged funds"),
}
SECTIONS = {"physical": "Physical commodities", "financial": "Financial futures"}
# Category → name, the group that leads, the group drawn beside it, and how to read them.
CATEGORIES = {
    "energy": ("Energy", "managed", "producers"), "metals": ("Metals", "managed", "producers"),
    "grains": ("Grains", "managed", "producers"), "oilseeds": ("Oilseeds", "managed", "producers"),
    "softs": ("Softs", "managed", "producers"), "livestock": ("Livestock", "managed", "producers"),
    "dairy": ("Dairy", "managed", "producers"), "wood": ("Lumber", "managed", "producers"),
    "equities": ("Stock indices", "asset_mgr", "lev_money"), "treasuries": ("Treasuries", "asset_mgr", "lev_money"),
    "stir": ("Short-term rates", "lev_money", "asset_mgr"), "currencies": ("Currencies", "lev_money", "asset_mgr"),
    "volatility": ("Volatility", "lev_money", "asset_mgr"), "crypto": ("Crypto", "asset_mgr", "lev_money"),
    # listed only, no chart
    "electricity": ("Electricity", None, None), "emissions": ("Emissions", None, None), "chemicals": ("Chemicals", None, None),
    "swaps": ("Rate swaps", None, None), "other_financial": ("Other financial", None, None), "other": ("Other", None, None),
}
READING = {
    "physical": "Managed money (hedge funds and commodity trading advisers) is the speculative crowd and leads; producers and "
                "merchants hedge what they produce or use and are usually net short, the other side. Extremes in managed "
                "money are often read contrarian.",
    "equities": "Asset managers' real-money position carries the direction. Leveraged funds' large net short is mostly the "
                "basis trade (short futures against long stocks, financed with borrowed money), not a bet on a fall.",
    "treasuries": "Asset managers lead; long Treasury futures is a bet on falling yields. Leveraged funds' large net short is "
                  "mostly the cash-futures basis trade financed in repo, not a bet on rising yields.",
    "stir": "Leveraged funds are the speculative bet: long fed funds futures is a bet on lower policy rates.",
    "currencies": "Leveraged funds are the speculative bet: long a currency's futures is a bet it rises against the US dollar "
                  "(for the dollar index, that the dollar rises).",
    "volatility": "Leveraged funds are the speculative bet. Long VIX futures is a bet volatility rises, which usually comes "
                  "with falling stocks; a deep net short is a bet on calm.",
    "crypto": "Asset managers lead. Leveraged funds are largely short against spot ETFs and cash holdings (the basis trade), "
              "not betting on a fall.",
}
# The main futures per market with a continuous Yahoo price: CFTC code → name, exchange, symbol, category.
MARKETS = {
    "physical": {
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
    },
    "financial": {
        "13874A": ("E-mini S&P 500", "CME", "ES=F", "equities"),
        "209742": ("E-mini Nasdaq-100", "CME", "NQ=F", "equities"),
        "239742": ("E-mini Russell 2000", "CME", "RTY=F", "equities"),
        "124603": ("E-mini Dow ($5)", "CBOT", "YM=F", "equities"),
        "240743": ("Nikkei 225 (yen)", "CME", "NIY=F", "equities"),
        "044601": ("5-year Treasury note", "CBOT", "ZF=F", "treasuries"),
        "043602": ("10-year Treasury note", "CBOT", "ZN=F", "treasuries"),
        "042601": ("2-year Treasury note", "CBOT", "ZT=F", "treasuries"),
        "043607": ("Ultra 10-year Treasury", "CBOT", "TN=F", "treasuries"),
        "020604": ("Ultra Treasury bond", "CBOT", "UB=F", "treasuries"),
        "020601": ("Treasury bond", "CBOT", "ZB=F", "treasuries"),
        "045601": ("30-day fed funds", "CBOT", "ZQ=F", "stir"),
        "099741": ("Euro", "CME", "6E=F", "currencies"),
        "097741": ("Japanese yen", "CME", "6J=F", "currencies"),
        "232741": ("Australian dollar", "CME", "6A=F", "currencies"),
        "090741": ("Canadian dollar", "CME", "6C=F", "currencies"),
        "095741": ("Mexican peso", "CME", "6M=F", "currencies"),
        "096742": ("British pound", "CME", "6B=F", "currencies"),
        "092741": ("Swiss franc", "CME", "6S=F", "currencies"),
        "112741": ("New Zealand dollar", "CME", "6N=F", "currencies"),
        "102741": ("Brazilian real", "CME", "6L=F", "currencies"),
        "122741": ("South African rand", "CME", "6Z=F", "currencies"),
        "098662": ("US dollar index", "ICE US", "DX-Y.NYB", "currencies"),  # the spot index: Yahoo has no continuous future
        "1170E1": ("VIX futures", "CFE", "^VIX", "volatility"),            # the spot index: VIX futures differ in level
        "133741": ("Bitcoin", "CME", "BTC=F", "crypto"),
        "146021": ("Ether", "CME", "ETH=F", "crypto"),
    },
}
SUBGROUPS = {"PETROLEUM AND PRODUCTS": "energy", "NATURAL GAS AND PRODUCTS": "energy", "PRECIOUS METALS": "metals",
             "BASE METALS": "metals", "GRAINS": "grains", "OILSEED and PRODUCTS": "oilseeds", "FOODSTUFFS/SOFTS": "softs",
             "FIBER": "softs", "LIVESTOCK/MEAT PRODUCTS": "livestock", "DAIRY PRODUCTS": "dairy", "WOOD PRODUCTS": "wood",
             "ELECTRICITY AND SOURCES": "electricity", "EMISSIONS": "emissions", "CHEMICALS": "chemicals",
             "CURRENCY": "currencies", "CURRENCY(NON-MAJOR)": "currencies", "DIGITAL ASSET": "crypto",
             "DIGITAL ASSET (NON-MAJOR)": "crypto", "Interest Rates - U.S. Treasury": "treasuries",
             "Interest Rates - non U.S. Treasury": "stir", "INTEREST RATE SWAPS": "swaps", "STOCK INDICES": "equities",
             "OTHER FINANCIAL INSTRUMENTS": "other_financial"}
EXCHANGES = {"ICE FUTURES ENERGY DIV": "ICE Energy", "ICE FUTURES U.S.": "ICE US", "ICE FUTURES EUROPE": "ICE Europe",
             "NEW YORK MERCANTILE EXCHANGE": "NYMEX", "CHICAGO MERCANTILE EXCHANGE": "CME", "CHICAGO BOARD OF TRADE": "CBOT",
             "COMMODITY EXCHANGE INC.": "COMEX", "MIAX FUTURES EXCHANGE": "MIAX", "NODAL EXCHANGE": "Nodal",
             "CBOE FUTURES EXCHANGE": "CFE", "COINBASE DERIVATIVES, LLC": "Coinbase Derivatives"}


def cot_index(values, current, weeks=COT_INDEX_WEEKS):
    """Percentile of the latest net position within the trailing window, 0–100; None without a full window."""
    window = values[-weeks:]
    if len(window) < weeks:
        return None
    return round(sum(v < current for v in window[:-1]) / (len(window) - 1) * 100)


def section_of(code):
    return next(section for section, markets in MARKETS.items() if code in markets)


def roles(code):
    """The leading and second trader groups for a charted market."""
    _, _, _, category = MARKETS[section_of(code)][code]
    return CATEGORIES[category][1], CATEGORIES[category][2]


def net(row, group):
    long, short, _ = GROUPS[group]
    return sentiment.number(row.get(long) or 0, 0) - sentiment.number(row.get(short) or 0, 0)


def parse_positions(rows, today, section):
    """Weekly net positions of each market's leading and second trader groups, as shares of open interest."""
    weeks = {code: {} for code in MARKETS[section]}
    for row in rows:
        try:
            code, when = row["cftc_contract_market_code"].strip(), sentiment.iso_date(row["report_date_as_yyyy_mm_dd"])
            if code not in weeks or when > today:
                continue
            lead, second = roles(code)
            weeks[code][when] = (sentiment.number(row["open_interest_all"], 1), net(row, lead), net(row, second))
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    markets = {}
    for code, history in weeks.items():
        days = sorted(history)
        if len(days) < 20:
            continue  # too little to chart; the market still appears in the full contract list
        lead, second = roles(code)
        last = days[-1]
        interest, leading, other = history[last]
        pct = lambda day, i: round(history[day][i] / history[day][0] * 100, 1)
        markets[code] = dict(as_of=last.isoformat(), open_interest=round(interest), lead=lead, second=second,
                             lead_name=GROUPS[lead][2], second_name=GROUPS[second][2], lead_net=round(leading),
                             lead_pct=pct(last, 1), second_pct=pct(last, 2), index=cot_index([history[d][1] for d in days], leading),
                             lead_history=[[d.isoformat(), pct(d, 1)] for d in days],
                             second_history=[[d.isoformat(), pct(d, 2)] for d in days])
    if not markets:
        raise ValueError("No positions")
    return markets


def parse_contracts(rows, charted, section):
    """Every other contract in the latest week: name, category, open interest and the speculative group's net."""
    spec = "managed" if section == "physical" else "lev_money"
    out = []
    for row in rows:
        try:
            code = row["cftc_contract_market_code"].strip()
            interest = sentiment.number(row["open_interest_all"], 1)
            speculators = net(row, spec)
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
        if code in charted:
            continue
        name, _, exchange = str(row.get("market_and_exchange_names") or row.get("contract_market_name") or code).partition(" - ")
        out.append(dict(code=code, name=name.strip(), exchange=EXCHANGES.get(exchange.strip(), exchange.strip().title()),
                        category=SUBGROUPS.get(str(row.get("commodity_subgroup_name") or "").strip(),
                                               "other" if section == "physical" else "other_financial"),
                        open_interest=round(interest), spec_pct=round(speculators / interest * 100, 1)))
    return sorted(out, key=lambda c: -c["open_interest"])


def fetch_positions(client, today, section):
    groups = sorted({g for code in MARKETS[section] for g in roles(code)})
    fields = ", ".join(["report_date_as_yyyy_mm_dd", "cftc_contract_market_code", "open_interest_all"]
                       + [f for g in groups for f in GROUPS[g][:2]])
    codes, url = list(MARKETS[section]), COT_URLS[section]

    def chunk(part):
        where = "cftc_contract_market_code in (" + ", ".join(f"'{c}'" for c in part) + ")"
        return json.loads(sentiment.request_text(client, url, params={"$select": fields, "$where": where,
                                                                      "$order": "report_date_as_yyyy_mm_dd", "$limit": 20000}))
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = [row for part in pool.map(chunk, [codes[i:i + CHUNK] for i in range(0, len(codes), CHUNK)]) for row in part]
    markets = parse_positions(rows, today, section)
    latest = max(m["as_of"] for m in markets.values())
    spec = GROUPS["managed" if section == "physical" else "lev_money"][:2]
    everything = json.loads(sentiment.request_text(client, url, params={
        "$select": "cftc_contract_market_code, market_and_exchange_names, commodity_subgroup_name, open_interest_all, " + ", ".join(spec),
        "$where": f"report_date_as_yyyy_mm_dd = '{latest}T00:00:00.000'", "$limit": 5000}))
    return {"as_of": latest, "markets": markets, "contracts": parse_contracts(everything, set(MARKETS[section]), section)}


def weekly_prices(closes):
    """Each week's last close (weeks end on Friday), plus the latest close, oldest first."""
    closes = closes.dropna()
    if closes.empty:
        return []
    weekly = closes.resample("W-FRI").last().dropna()
    points = [(d.date() if d.date() <= closes.index[-1].date() else closes.index[-1].date(), float(v)) for d, v in weekly.items()]
    return [[d.isoformat(), float(f"{v:.5g}")] for d, v in points]


def fetch_prices(now):
    local = now.astimezone(context.MARKET_TZ)
    symbols = sorted({m[2] for markets in MARKETS.values() for m in markets.values()})
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
        prices[symbol] = dict(as_of=when.isoformat(), last=float(f"{last:.5g}"), change_1m=change(30), change_1y=change(365),
                              points=weekly_prices(series))
    if not prices:
        raise ValueError("No futures prices")
    return {"as_of": max(p["as_of"] for p in prices.values()), "prices": prices}


# Copies cached by an older version lack what this one draws, so they are fetched again rather than reused.
COMPLETE = {"physical": lambda item: all("lead_history" in m for m in (item.get("markets") or {}).values()),  # before 3.4.0
            "financial": lambda item: bool(item.get("markets")),
            "prices": lambda item: "ES=F" in (item.get("prices") or {})}                                        # before 3.4.0


def collect(now=None):
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(context.MARKET_TZ).date()
    progress.emit(activity="Checking futures prices and CFTC positions")
    with httpx.Client(headers={"User-Agent": config.USER_AGENT}, timeout=30, follow_redirects=True) as client:
        jobs = {"physical": lambda: fetch_positions(client, today, "physical"),
                "financial": lambda: fetch_positions(client, today, "financial"), "prices": lambda: fetch_prices(now)}
        with ThreadPoolExecutor(max_workers=3) as pool:
            # The commodity report keeps the cache name it had before financial futures were added.
            names = {"physical": "commodities-cot", "financial": "commodities-financial", "prices": "commodities-prices"}
            done = {key: pool.submit(sentiment.cached_read, names[key], fetch, now, TTL_HOURS[key], COMPLETE[key],
                                     sessions=key == "prices") for key, fetch in jobs.items()}
            return {key: future.result() for key, future in done.items()}


def section_payload(section, cot, quotes):
    positions = cot.get("markets") or {}
    groups = {}
    for code, (name, exchange, symbol, category) in MARKETS[section].items():
        held, quote = positions.get(code), quotes.get(symbol)
        if not held and not quote:
            continue
        lead, second = CATEGORIES[category][1:]
        groups.setdefault(category, []).append({
            "lead_name": GROUPS[lead][2], "second_name": GROUPS[second][2], **(held or {}),  # names stand even without positions
            "code": code, "name": name, "exchange": exchange, "symbol": symbol, "category": category, "price": (quote or {}).get("last"),
            "price_as_of": (quote or {}).get("as_of"), "change_1m": (quote or {}).get("change_1m"),
            "change_1y": (quote or {}).get("change_1y"), "price_history": (quote or {}).get("points") or []})
    ordered = sorted(groups.items(), key=lambda kv: -sum(m.get("open_interest") or 0 for m in kv[1]))
    return dict(key=section, name=SECTIONS[section], status=cot.get("status", "unavailable"), as_of=cot.get("as_of"),
                groups=[dict(key=key, name=CATEGORIES[key][0], reading=READING.get(key) or READING.get(section, ""),
                             open_interest=sum(m.get("open_interest") or 0 for m in markets),
                             markets=sorted(markets, key=lambda m: -(m.get("open_interest") or 0))) for key, markets in ordered],
                contracts=cot.get("contracts") or [])


def evaluate(payload, inputs, *, now=None):
    """Physical commodities, then financial futures; within each, categories and markets most liquid first."""
    now = now or datetime.now(timezone.utc)
    prices = inputs.get("prices") or {}
    quotes = prices.get("prices") or {}
    sections = [section_payload(key, inputs.get(key) or {}, quotes) for key in SECTIONS]
    dates = [s["as_of"] for s in sections if s["as_of"]]
    as_of = max(dates) if dates else None
    released = next_release = None
    if as_of:
        report = sentiment.iso_date(as_of)
        released, next_release = (report + timedelta(days=3)).isoformat(), (report + timedelta(days=10)).isoformat()
    payload["commodities"] = {
        "checked_at": now.isoformat(), "as_of": as_of, "released": released, "next": next_release, "next_time": "3:30 PM ET",
        "price_status": prices.get("status", "unavailable"), "prices_as_of": prices.get("as_of"), "url": COT_PAGE,
        "sections": sections, "categories": {key: value[0] for key, value in CATEGORIES.items()},
    }


def enrich(payload):
    evaluate(payload, collect())
