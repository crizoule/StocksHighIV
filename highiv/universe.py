"""Build the screen universe: US- and Canada-listed stocks above the market-cap floor that have listed options."""
from __future__ import annotations

import re
from difflib import SequenceMatcher

import httpx
import yfinance as yf
from yfinance import EquityQuery

from . import config, net

NASDAQ_SCREENER_URL = "https://api.nasdaq.com/api/screener/stocks"
OCC_DIRECTORY_URL = "https://marketdata.theocc.com/delo-download"
TSX_DIRECTORY_URL = "https://www.tsx.com/json/company-directory/search/tsx/%5E*"
US_EXCHANGES = {"nasdaq": "NASDAQ", "nyse": "NYSE", "amex": "NYSE American"}
CA_EXCHANGES = {"TOR": "TSX", "VAN": "TSXV"}

# Nasdaq lists notes, preferreds and warrants beside common stock; ADRs and LP units stay in.
DEBT_LIKE = re.compile(r"\b(notes?|debentures?|warrants?|rights|perpetual)\b|\d%", re.I)
PREFERRED = re.compile(r"\bpreferred (stock|shares?)\b|\bdepositary shares?\b.*\bpreferred\b", re.I)
ADR = re.compile(r"american depositary|\bADRs?\b|\bADSs?\b", re.I)
SECURITY_SUFFIX = re.compile(
    r"\s+(class [a-z] )?(common|ordinary|subordinate voting|voting|limited partnership|units)"
    r"( stock| shares?| units?)?\b.*$|\s+american depositary.*$",
    re.I,
)
# Yahoo symbols for TSX preferreds, warrants, debentures, rights and USD-denominated lines
CA_NON_COMMON = re.compile(r"-(P[A-Z]{0,3}|WT[A-Z]?|DB[A-Z]?|U|R|RT)\.(TO|V)$")
PHYSICAL_TRUST = re.compile(r"\bphysical\b", re.I)  # Sprott bullion/uranium trusts are not operating companies
NAME_STOPWORDS = {
    "inc", "incorporated", "corp", "corporation", "ltd", "limited", "co", "company", "plc", "the",
    "sa", "nv", "ag", "lp", "llc", "common", "stock", "shares", "share", "class", "subordinate",
    "voting", "ordinary", "units", "unit", "and", "of",
}


def _to_float(value) -> float:
    try:
        return float(str(value).replace("$", "").replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def norm_name(name: str | None) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", (name or "").lower().replace("&", " and "))
    return " ".join(t for t in text.split() if t not in NAME_STOPWORDS and len(t) > 1)


def is_common_equity(name: str) -> bool:
    if DEBT_LIKE.search(name):
        return False
    return not (PREFERRED.search(name) and not ADR.search(name))


def _nasdaq_rows(client: httpx.Client) -> list[dict]:
    rows = []
    for key, label in US_EXCHANGES.items():
        params = {"tableonly": "true", "download": "true", "exchange": key}
        resp = net.get(client, NASDAQ_SCREENER_URL, params=params)
        if resp is None or resp.status_code != 200:
            raise RuntimeError(f"Nasdaq screener unavailable for {label}")
        rows += [{**row, "exchange": label} for row in resp.json()["data"]["rows"]]
    return rows


def _occ_underlyings(client: httpx.Client) -> set[str]:
    params = {"prodType": "ALL", "downloadFields": "OS;US;SN", "format": "txt"}
    resp = net.get(client, OCC_DIRECTORY_URL, params=params)
    if resp is None or resp.status_code != 200:
        raise RuntimeError("OCC options directory unavailable")
    parts = (line.split("\t") for line in resp.text.splitlines())
    return {p[1].strip() for p in parts if len(p) > 1 and p[1].strip()}


def us_universe(client: httpx.Client) -> list[dict]:
    optionable = _occ_underlyings(client)
    stocks = []
    for row in _nasdaq_rows(client):
        symbol, name = row["symbol"].strip(), row.get("name") or ""
        cap = _to_float(row.get("marketCap"))
        if cap < config.MIN_MARKET_CAP_USD or not is_common_equity(name):
            continue
        if symbol.replace("/", "") not in optionable:
            continue
        if config.excluded_industry(row.get("industry")):
            continue
        stocks.append({
            "symbol": symbol.replace("/", "."),
            "cboe": symbol.replace("/", "."),
            "mx": None,
            "yahoo": symbol.replace("/", "-"),
            "name": SECURITY_SUFFIX.sub("", name).strip(" ,"),
            "exchange": row["exchange"],
            "market": "US",
            "hq_country": row.get("country") or None,
            "market_cap_usd": cap,
            "sector": row.get("sector") or None,
            "industry": row.get("industry") or None,
            "also_listed": None,
        })
    return stocks


def usd_cad_rate() -> float:
    info = yf.Ticker("CAD=X").fast_info
    rate = float(getattr(info, "last_price", None) or info["lastPrice"])
    if not 1.0 < rate < 2.0:
        raise RuntimeError(f"Implausible USD/CAD rate: {rate}")
    return rate


def _yahoo_canada_quotes() -> list[dict]:
    # Loose cap pre-filter (the screener's currency is unclear); the exact USD test happens below.
    query = EquityQuery("and", [
        EquityQuery("eq", ["region", "ca"]),
        EquityQuery("gt", ["intradaymarketcap", int(config.MIN_MARKET_CAP_USD * 0.7)]),
    ])
    quotes: dict[str, dict] = {}
    offset = 0
    while True:
        page = yf.screen(query, size=250, offset=offset, sortField="intradaymarketcap", sortAsc=False)
        batch = page.get("quotes") or []
        for quote in batch:
            quotes.setdefault(quote["symbol"], quote)
        offset += len(batch)
        if not batch or offset >= (page.get("total") or 0):
            return list(quotes.values())


def _tsx_cdr_symbols(client: httpx.Client) -> set[str]:
    """Canadian Depositary Receipts (e.g. NVDA.TO) wrap US companies; they are not Canadian stocks."""
    resp = net.get(client, TSX_DIRECTORY_URL)
    if resp is None or resp.status_code != 200:
        raise RuntimeError("TSX company directory unavailable")
    cdrs: set[str] = set()
    for company in resp.json().get("results", []):
        if "CDR" in (company.get("name") or "").upper():
            cdrs.add(company["symbol"].upper())
            cdrs.update(inst["symbol"].upper() for inst in company.get("instruments", []))
    return cdrs


def _us_listing_for(name: str, base: str, by_name: dict, by_symbol: dict) -> dict | None:
    key = norm_name(name)
    if key in by_name:
        return by_name[key]
    candidate = by_symbol.get(base)
    if candidate and SequenceMatcher(None, key, norm_name(candidate["name"])).ratio() >= 0.6:
        return candidate
    return None


def canada_universe(client: httpx.Client, us_stocks: list[dict]) -> list[dict]:
    """TSX/TSXV stocks over the floor. Interlisted names stay on their US line (deeper options) and get tagged."""
    usdcad = usd_cad_rate()
    cdrs = _tsx_cdr_symbols(client)
    by_name = {norm_name(s["name"]): s for s in us_stocks}
    by_symbol = {s["symbol"]: s for s in us_stocks}
    by_root: dict[str, tuple[float, dict]] = {}
    for quote in _yahoo_canada_quotes():
        symbol = quote["symbol"]
        exchange = CA_EXCHANGES.get(quote.get("exchange"))
        if not exchange or quote.get("quoteType") != "EQUITY" or CA_NON_COMMON.search(symbol):
            continue
        local = symbol.rsplit(".", 1)[0]          # BBD-B
        base = local.split("-")[0]                # BBD, the Montreal Exchange option root
        if local.replace("-", ".") in cdrs or base in cdrs:
            continue
        cap = quote.get("marketCap") or 0
        cap_usd = cap / usdcad if quote.get("currency", "CAD") == "CAD" else cap
        if cap_usd < config.MIN_MARKET_CAP_USD:
            continue
        name = quote.get("longName") or quote.get("shortName") or symbol
        if PHYSICAL_TRUST.search(name):
            continue
        us_line = _us_listing_for(name, base, by_name, by_symbol)
        if us_line is not None:
            us_line["also_listed"] = symbol
            continue
        # Dual-class names (BBD-A / BBD-B) share one option root: keep the most traded line.
        volume = quote.get("averageDailyVolume3Month") or 0
        if base in by_root and by_root[base][0] >= volume:
            continue
        by_root[base] = (volume, {
            "symbol": symbol,
            "cboe": None,
            "mx": base,
            "yahoo": symbol,
            "name": name,
            "exchange": exchange,
            "market": "CA",
            "hq_country": None,
            "market_cap_usd": cap_usd,
            "sector": None,
            "also_listed": None,
        })
    return [stock for _, stock in by_root.values()]


def build_universe(client: httpx.Client) -> list[dict]:
    us = us_universe(client)
    return us + canada_universe(client, us)
