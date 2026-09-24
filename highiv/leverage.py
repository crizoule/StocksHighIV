"""Market leverage: how much investors borrow, from free public sources, each dated by its own release.

Every card keeps the provider's observation date, when that release came out, and when the next one is due:
scheduled when the publisher announces the date, otherwise estimated from its usual cadence. Monthly and
quarterly figures describe how stretched borrowing is; they are context, not timing signals.
"""
from __future__ import annotations

import csv
import io
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import httpx
import openpyxl
import pandas as pd
import yfinance as yf

from . import context, fear_greed, progress, sentiment

# FINRA refuses browser-like agents that lack a browser's other headers; every source here accepts a plain, honest one.
USER_AGENT = "StocksHighIV (+https://github.com/crizoule/StocksHighIV)"
FINRA_PAGE = "https://www.finra.org/rules-guidance/key-topics/margin-accounts/margin-statistics"
FINRA_FILE = "https://www.finra.org/sites/default/files/2021-03/margin-statistics.xlsx"  # the page's link since 2021
OFR_URL = "https://data.financialresearch.gov/hf/v1/series/multifull/"
OFR_PAGE = "https://www.financialresearch.gov/hedge-fund-monitor/"
OFR_SERIES = {"gav": "FPF-ALLQHF_GAV_SUM", "nav": "FPF-ALLQHF_NAV_SUM", "gne": "FPF-ALLQHF_GNE_SUM",
              "equity": "FPF-STRATEGY_EQUITY_LEVERAGERATIO_GAVWMEAN", "dealers": "SCOOS-NET_HF_LEVERAGEUSE"}
FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv"
Z1_MARGIN = "BOGZ1FL663067003Q"    # security brokers and dealers: receivables due from customers (margin loans and other)
Z1_EQUITIES = "BOGZ1LM883164105Q"  # all domestic sectors: corporate equities at market value
Z1_PAGE = "https://www.federalreserve.gov/releases/z1/"
FSR_PAGE = "https://www.federalreserve.gov/publications/financial-stability-report.htm"
FSR_FILES = "https://www.federalreserve.gov/publications/files/"
ETF_PAIRS = {"Nasdaq-100": ("TQQQ", "SQQQ"), "S&P 500": ("UPRO", "SPXU"), "S&P 500 · Direxion": ("SPXL", "SPXS"),
             "Semiconductors": ("SOXL", "SOXS"), "Russell 2000": ("TNA", "TZA"), "Technology": ("TECL", "TECS")}
ETF_BENCHMARKS = ("SPY", "QQQ")
# ProShares publishes each fund's daily NAV, shares outstanding and assets since launch; Direxion publishes only
# today's shares outstanding (and its pages sit behind a bot check), so its funds have no history to rank.
PROSHARES_URL = "https://accounts.profunds.com/etfdata/ByFund/{}-historical_nav.csv"
PROSHARES_PAIRS = {"Nasdaq-100": ("TQQQ", "SQQQ"), "S&P 500": ("UPRO", "SPXU"), "Dow": ("UDOW", "SDOW"),
                   "Russell 2000": ("URTY", "SRTY")}
SHARE_WINDOW = 756   # sessions: bull funds grew from about 43% to over 90% of these assets since 2010, so rank within 3 years
ETF_START = "2010-01-01"
ETF_WINDOW = 20      # sessions: one month of trading, so a single hectic day does not swing the reading
ETF_MIN_PAIRS = 4    # a basket missing more than two index pairs is no longer comparable with its history
ELEVATED, LOW = 80, 20  # percentiles within each measure's own history
TTL_HOURS = {"etf": 6}  # the others publish monthly or less often
DEFAULT_TTL = 12
ORDER = ("finra", "z1", "ofr", "cot", "etf", "fsr")


def add_months(day, months):
    total = day.year * 12 + day.month - 1 + months
    year, month = divmod(total, 12)
    month += 1
    last = (date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1)).day
    return date(year, month, min(day.day, last))


def month_end(day):
    return add_months(date(day.year, day.month, 1), 1) - timedelta(days=1)


def quarter_label(day):
    return f"Q{(day.month - 1) // 3 + 1} {day.year}"


def percentile(values, current):
    """Share of earlier readings below the latest one, 0–100; None with under 12 earlier readings."""
    earlier = values[:-1]
    if len(earlier) < 12:
        return None
    return round(sum(v < current for v in earlier) / len(earlier) * 100)


def tone(rank):
    return None if rank is None else "high" if rank >= ELEVATED else "low" if rank <= LOW else "normal"


def ordinal(n):
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def ranked(rank, since):
    return f"{ordinal(rank)} percentile since {since}" if rank is not None else f"too little history since {since} to rank"


def dollars(millions):
    return f"${millions / 1e6:.2f}T" if millions >= 1e6 else f"${millions / 1e3:,.0f}B"


def pairs(points, digits=2):
    return [[d.isoformat(), round(v, digits)] for d, v in points]


def released_on(headers):
    """The provider's Last-Modified date in market time, or None."""
    try:
        return parsedate_to_datetime(headers["last-modified"]).astimezone(context.MARKET_TZ).date()
    except (KeyError, TypeError, ValueError):
        return None


# ---------- FINRA: customer margin debt, monthly ----------

def parse_finra(data, released):
    sheet = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True).active
    months = {}
    for row in sheet.iter_rows(values_only=True):
        try:
            month = (date(row[0].year, row[0].month, 1) if isinstance(row[0], (date, datetime))
                     else datetime.strptime(str(row[0]).strip()[:7], "%Y-%m").date())
            debit = sentiment.number(row[1], 1)
        except (TypeError, ValueError, IndexError):
            continue
        try:  # cash and margin-account free credits: money investors hold at their brokers, not borrowed
            free = sentiment.number(row[2], 0) + sentiment.number(row[3], 0)
        except (TypeError, ValueError, IndexError):
            free = None
        months[month] = (debit, free)
    days = sorted(months)
    if len(days) < 25:
        raise ValueError("Unrecognized FINRA margin statistics")
    yoy = [(month_end(d), (months[d][0] / months[prior][0] - 1) * 100)
           for d in days if (prior := add_months(d, -12)) in months]
    last = days[-1]
    debit, free = months[last]
    change = yoy[-1][1]
    rank = percentile([v for _, v in yoy], change)
    peak = max(days, key=lambda d: months[d][0])
    previous = months.get(add_months(last, -1))
    lines = [f"12-month change {change:+.1f}% · {ranked(rank, yoy[0][0].year)}",
             f"1-month change {(debit / previous[0] - 1) * 100:+.1f}%" if previous else None,
             f"Record high ({peak:%b %Y})" if peak == last else f"{(1 - debit / months[peak][0]) * 100:.1f}% below the {peak:%b %Y} record",
             f"Net of investors' free credit balances: {dollars(debit - free)}" if free is not None else None]
    level = tone(rank)
    return dict(
        as_of=month_end(last).isoformat(), period=f"{last:%B %Y}", value=change, reading=dollars(debit), tone=level,
        signal={"high": "Rapid build-up", "low": "Deleveraging"}.get(level) or ("Borrowing growing" if change >= 0 else "Borrowing easing"),
        lines=[line for line in lines if line], frequency="monthly",
        released=released.isoformat() if released else None,
        # FINRA posts each month around the third week of the next; its file's date is the last posting.
        next=(add_months(released, 1) if released else add_months(last, 2) + timedelta(days=19)).isoformat(), next_basis="estimated",
        detail=("FINRA member firms' debit balances in customers' securities margin accounts: money investors have borrowed "
                "from their brokers against their holdings, reported monthly in US dollars. The level rises with stock prices, "
                "so the chart shows its 12-month change, which is ranked against every month since FINRA's series began. "
                "Free credit balances are cash investors keep at their brokers. Rapid growth in margin debt has come near "
                "past market peaks, but also in long advances; it measures how stretched borrowing is, not when it unwinds."),
        series={"finra": dict(name="Margin debt, 12-month change", unit="%", source="FINRA margin statistics",
                              frequency="monthly", points=pairs(yoy, 1))})


def fetch_finra(client):
    try:
        found = re.search(r'href="([^"]*margin-statistics[^"]*\.xlsx)"', sentiment.request_text(client, FINRA_PAGE, errors="replace"))
        url = httpx.URL(FINRA_PAGE).join(found[1]) if found else FINRA_FILE
    except ValueError:
        url = FINRA_FILE
    data, headers = sentiment.request_bytes(client, str(url))
    return {**parse_finra(data, released_on(headers)), "url": FINRA_PAGE}


# ---------- OFR Hedge Fund Monitor: SEC Form PF aggregates, quarterly ----------

def parse_ofr(data):
    series, updated = {}, {}
    for key, mnemonic in OFR_SERIES.items():
        item = data.get(mnemonic) or {}
        points = {}
        for day, value in (item.get("timeseries") or {}).get("aggregation") or []:
            try:
                points[sentiment.iso_date(day)] = sentiment.number(value)
            except (TypeError, ValueError):
                continue  # blank values protect individual filers
        series[key] = points
        try:
            updated[key] = sentiment.iso_date(item["metadata"]["schedule"]["last_update"])
        except (KeyError, TypeError, ValueError):
            pass
    quarters = sorted(d for d in series["gav"] if d in series["nav"] and series["nav"][d] > 0)
    if len(quarters) < 8:
        raise ValueError("Too little OFR history")
    ratio = [(d, series["gav"][d] / series["nav"][d]) for d in quarters]
    gross = [(d, series["gne"][d] / series["nav"][d]) for d in quarters if d in series["gne"]]
    last, value = ratio[-1]
    rank = percentile([v for _, v in ratio], value)
    level = tone(rank)
    released = updated.get("gav")
    lines = [f"Gross assets ÷ net assets · {ranked(rank, quarters[0].year)}"]
    if gross and gross[-1][0] == last:
        lines.append(f"{gross[-1][1]:.2f}× including derivatives (gross notional exposure) · "
                     f"{ranked(percentile([v for _, v in gross], gross[-1][1]), gross[0][0].year)}")
    equity = series["equity"].get(last)
    if equity is not None:
        lines.append(f"Equity-strategy funds {equity:.2f}×")
    lines.append(f"Net assets {dollars(series['nav'][last] / 1e6)}")
    if series["dealers"]:
        survey = max(series["dealers"])
        net = series["dealers"][survey]
        lines.append(f"Dealer survey, {quarter_label(survey)}: " + (f"net {net:+.0f}% report hedge funds using more leverage" if round(net)
                     else "as many dealers report hedge funds using more leverage as less"))
    return dict(
        as_of=last.isoformat(), period=quarter_label(last), value=round(value, 3), reading=f"{value:.2f}×", tone=level,
        signal={"high": "Near the top of its range", "low": "Low for its history"}.get(level, "Within its usual range"),
        lines=lines, frequency="quarterly", released=released.isoformat() if released else None,
        # OFR does not announce dates: the next quarter is due after the same delay as this one.
        next=(add_months(last, 3) + (released - last)).isoformat() if released else None, next_basis="estimated",
        detail=("Aggregated SEC Form PF filings by qualifying hedge funds (over US$500M in net assets), published by the "
                "Treasury's Office of Financial Research. Balance-sheet leverage is total gross assets over total net assets; "
                "the derivatives measure divides gross notional exposure, long plus short, by the same net assets. Funds under "
                "the filing threshold are not included, and blank values protect individual filers. The dealer line is the "
                "Federal Reserve's Senior Credit Officer Opinion Survey: dealers reporting more use of leverage by hedge-fund "
                "clients, minus those reporting less."),
        series={"ofr": dict(name="Hedge funds: gross assets ÷ net assets", unit="×", source="OFR Hedge Fund Monitor · SEC Form PF",
                            frequency="quarterly", points=pairs(ratio, 3)),
                "ofr_gne": dict(name="Hedge funds: gross notional exposure ÷ net assets", unit="×",
                                source="OFR Hedge Fund Monitor · SEC Form PF", frequency="quarterly", points=pairs(gross, 3))})


def fetch_ofr(client):
    text = sentiment.request_text(client, OFR_URL, params={"mnemonics": ",".join(OFR_SERIES.values())})
    return {**parse_ofr(json.loads(text)), "url": OFR_PAGE}


# ---------- Federal Reserve Z.1 Financial Accounts: margin loans against the stock market, quarterly ----------

def parse_fred(text):
    """Quarter-end dates and values from a FRED CSV (quarters are dated by their first day; '.' marks no value)."""
    rows = {}
    for row in csv.DictReader(io.StringIO(text)):
        try:
            start = sentiment.iso_date(row["observation_date"])
            rows[add_months(start, 3) - timedelta(days=1)] = sentiment.number(next(v for k, v in row.items() if k != "observation_date"))
        except (KeyError, TypeError, ValueError, StopIteration):
            continue
    if len(rows) < 20:
        raise ValueError("Unrecognized FRED series")
    return rows


def parse_z1_page(html):
    """This release's date and the next scheduled one, from the Fed's Z.1 page."""
    text = sentiment.page_text(html)
    when = lambda pattern: (datetime.strptime(found[1], "%B %d, %Y").date() if (found := re.search(pattern, text)) else None)
    return (when(r"Release Date:\s*([A-Z][a-z]+ \d{1,2}, \d{4})"),
            when(r"next Z\.1 release will be\s+(?:[A-Z][a-z]+day,\s+)?([A-Z][a-z]+ \d{1,2}, \d{4})"))


def parse_z1(margin, equities, released=None, upcoming=None):
    quarters = sorted(d for d in margin if equities.get(d, 0) > 0)
    if len(quarters) < 20:
        raise ValueError("Too little Z.1 history")
    ratio = [(d, margin[d] / equities[d] * 100) for d in quarters]
    last, value = ratio[-1]
    rank = percentile([v for _, v in ratio], value)
    level = tone(rank)
    year_ago = margin.get(add_months(last + timedelta(days=1), -12) - timedelta(days=1))
    lines = [f"Margin loans ÷ US stock market value · {ranked(rank, quarters[0].year)}",
             f"{dollars(margin[last])} margin loans and other customer receivables, {quarter_label(last)}",
             f"12-month change {(margin[last] / year_ago - 1) * 100:+.1f}%" if year_ago else None,
             f"US stock market value {dollars(equities[last])}"]
    return dict(
        as_of=last.isoformat(), period=quarter_label(last), value=round(value, 3), reading=f"{value:.2f}%", tone=level,
        signal={"high": "High share of stock value borrowed", "low": "Low share of stock value borrowed"}.get(level, "Within its usual range"),
        lines=[line for line in lines if line], frequency="quarterly",
        released=released.isoformat() if released else None,
        next=(upcoming or (released + timedelta(days=91) if released else add_months(last, 3) + timedelta(days=72))).isoformat(),
        next_basis="scheduled" if upcoming else "estimated",
        detail=("Federal Reserve Financial Accounts of the United States (Z.1), via FRED: broker-dealers' receivables due from "
                "customers, mostly margin loans, divided by the market value of all US corporate equities, so the ratio does not "
                "rise just because prices do. It covers broker-dealer lending only: securities-based loans from banks and "
                "hedge funds' prime-brokerage financing are counted elsewhere. Quarterly since 1945; each release can revise "
                "earlier quarters."),
        series={"z1": dict(name="Margin loans, % of US stock market value", unit="%", source="Federal Reserve Z.1 via FRED",
                           frequency="quarterly", points=pairs(ratio, 3))})


def fetch_z1(client):
    margin, equities = (parse_fred(sentiment.request_text(client, FRED_CSV, params={"id": series})) for series in (Z1_MARGIN, Z1_EQUITIES))
    try:
        released, upcoming = parse_z1_page(sentiment.request_text(client, Z1_PAGE, errors="replace"))
    except ValueError:
        released = upcoming = None  # the figures stand without the schedule
    return {**parse_z1(margin, equities, released, upcoming), "url": Z1_PAGE}


# ---------- Federal Reserve Financial Stability Report: twice a year, no data series ----------

def parse_fsr(html, today):
    dates = sorted({datetime.strptime(d, "%Y%m%d").date() for d in re.findall(r"financial-stability-report-(\d{8})\.pdf", html)})
    dates = [d for d in dates if d <= today]
    if not dates:
        raise ValueError("No dated Financial Stability Report")
    last = dates[-1]
    return dict(
        as_of=last.isoformat(), period=f"{last:%B %Y}", value=None, reading=f"{last:%B %Y}", tone=None,
        signal="Semiannual review", frequency="semiannual", released=last.isoformat(),
        next=add_months(last, 6).isoformat(), next_basis="estimated", next_precision="month",
        pdf=f"{FSR_FILES}financial-stability-report-{last:%Y%m%d}.pdf",
        lines=["Read section 3, Leverage in the Financial Sector, for hedge funds, broker-dealers and banks",
               *([f"Previous reports: {', '.join(f'{d:%b %Y}' for d in dates[-3:-1][::-1])}"] if len(dates) > 1 else [])],
        detail=("The Federal Reserve Board's assessment of US financial-system vulnerabilities, published each spring and fall. "
                "Its leverage section covers hedge-fund borrowing (from the same Form PF filings as the OFR card), the Treasury "
                "cash-futures basis trade, and bank and broker-dealer leverage. It is a written review with charts, not a data "
                "series, so there is nothing to chart here; the next date is estimated six months on."))


def fetch_fsr(client, today):
    return {**parse_fsr(sentiment.request_text(client, FSR_PAGE, errors="replace"), today), "url": FSR_PAGE}


# ---------- Leveraged ETFs: daily trading in 3× index funds, a retail proxy ----------

def parse_etfs(dollar_volume, assets=None):
    """Bull share of 3× ETF dollar volume and that volume against SPY + QQQ, both over 20 sessions.

    Sessions missing any fund are left out, so the basket is the same throughout its history. Yahoo occasionally
    skips a day for a few funds; a pair still counts when it has most of the last week.
    """
    recent = dollar_volume.tail(5)
    used = {name: pair for name, pair in ETF_PAIRS.items()
            if all(s in dollar_volume and recent[s].notna().sum() >= 3 for s in pair)}
    if len(used) < ETF_MIN_PAIRS or not all(s in dollar_volume for s in ETF_BENCHMARKS):
        raise ValueError("Too few leveraged ETFs downloaded")
    bulls, bears = [p[0] for p in used.values()], [p[1] for p in used.values()]
    frame = dollar_volume[bulls + bears + list(ETF_BENCHMARKS)].dropna()
    rolled = frame.rolling(ETF_WINDOW).sum().dropna()
    bull, bear = rolled[bulls].sum(axis=1), rolled[bears].sum(axis=1)
    rows = [(d.date(), b / (b + s) * 100, (b + s) / m * 100)
            for d, b, s, m in zip(rolled.index, bull, bear, rolled[list(ETF_BENCHMARKS)].sum(axis=1)) if b + s > 0 and m > 0]
    if len(rows) < 250:
        raise ValueError("Too little leveraged ETF history")
    last, share, activity = rows[-1]
    share_rank = percentile([r[1] for r in rows], share)
    activity_rank = percentile([r[2] for r in rows], activity)
    # Either reading near the top of its history marks the card: heavy leveraged trading counts whatever its direction.
    level = "high" if "high" in (tone(share_rank), tone(activity_rank)) else tone(share_rank)
    busy = tone(activity_rank) == "high"
    mix = {"high": "Leaning bullish", "low": "Leaning bearish"}.get(tone(share_rank), "Typical bull–bear mix")
    since = rows[0][0].year
    recent_from = last - timedelta(days=sentiment.HISTORY_DAYS)
    lines = [f"Bull funds' share of 3× ETF trading, {ETF_WINDOW}-session average · {ranked(share_rank, since)}",
             f"3× ETF trading = {activity:.1f}% of SPY + QQQ dollar volume · {ranked(activity_rank, since)}"]
    held = {s: v for s, v in (assets or {}).items() if v}
    if held and all(s in held for s in bulls + bears):
        lines.append(f"Assets {dollars(sum(held[s] for s in bulls) / 1e6)} in bull funds, "
                     f"{dollars(sum(held[s] for s in bears) / 1e6)} in bear funds (latest, no history)")
    return dict(
        as_of=last.isoformat(), value=round(share, 1), reading=f"{share:.0f}% bull", tone=level,
        signal=f"Heavy 3× trading · {mix.lower()}" if busy else mix,
        lines=lines, frequency="daily", funds=[f"{name}: {bull_symbol} / {bear_symbol}" for name, (bull_symbol, bear_symbol) in used.items()],
        detail=("Yahoo daily closes × volume for 3× leveraged index ETFs, bull funds against their bear twins: "
                + "; ".join(f"{name} {b}/{s}" for name, (b, s) in used.items()) + ". These funds reset their leverage daily "
                "and are traded heavily by individual investors, so their dollar volume is a same-day proxy for leveraged "
                "retail appetite. It is not a measure of borrowing: nobody reports retail leverage daily. Institutions trade "
                "these funds too, and bear-fund volume includes hedging. Asset totals are Yahoo's latest figures, with no history."),
        series={"etf_bull": dict(name=f"Bull share of 3× ETF dollar volume, {ETF_WINDOW}-session", unit="%", source="Yahoo daily bars",
                                 frequency="daily", points=pairs(sentiment.thinned([(d, v) for d, v, _ in rows], recent_from), 1)),
                "etf_activity": dict(name=f"3× ETF dollar volume, % of SPY + QQQ, {ETF_WINDOW}-session", unit="%",
                                     source="Yahoo daily bars", frequency="daily",
                                     points=pairs(sentiment.thinned([(d, v) for d, _, v in rows], recent_from), 2))})


def parse_proshares(texts):
    """Daily NAV and assets per fund from ProShares' split-adjusted history files, keyed by symbol."""
    frames = {}
    for symbol, text in texts.items():
        rows = []
        for row in csv.DictReader(io.StringIO(text)):
            try:
                rows.append((datetime.strptime(row["Date"].strip(), "%m/%d/%Y").date(), sentiment.number(row["NAV"], 0.0),
                             sentiment.number(row["Assets Under Management"], 0)))
            except (KeyError, TypeError, ValueError):
                continue
        if len(rows) < 300:
            raise ValueError(f"Unrecognized ProShares history for {symbol}")
        frames[symbol] = pd.DataFrame(rows, columns=["day", "nav", "aum"]).drop_duplicates("day").set_index("day").sort_index()
    return frames


def signed_money(value):
    return f"{'+' if value >= 0 else '−'}${abs(value) / 1e9:.2f}B"


def etf_flows(frames):
    """Net money into 3× bull minus bear funds over 20 sessions, and bull funds' share of the assets.

    A fund's flow is its change in assets beyond what its own NAV return explains, so a leveraged fund's daily gains
    and losses never count as money arriving or leaving. The files are split-adjusted, so reverse splits do not either.
    """
    bulls, bears = [p[0] for p in PROSHARES_PAIRS.values()], [p[1] for p in PROSHARES_PAIRS.values()]
    nav = pd.DataFrame({s: frames[s]["nav"] for s in bulls + bears}).dropna()
    aum = pd.DataFrame({s: frames[s]["aum"] for s in bulls + bears}).reindex(nav.index)
    flow = (aum - aum.shift(1) * nav / nav.shift(1)).iloc[1:]
    bull_in, bear_in = flow[bulls].sum(axis=1).rolling(ETF_WINDOW).sum(), flow[bears].sum(axis=1).rolling(ETF_WINDOW).sum()
    assets = aum.iloc[1:].sum(axis=1)
    frame = pd.DataFrame({"flows": (bull_in - bear_in) / assets.rolling(ETF_WINDOW).mean() * 100, "bull_in": bull_in, "bear_in": bear_in,
                          "share": aum[bulls].iloc[1:].sum(axis=1) / assets * 100, "bull": aum[bulls].iloc[1:].sum(axis=1), "assets": assets}).dropna()
    if len(frame) < SHARE_WINDOW:
        raise ValueError("Too little ProShares history")
    return frame


def parse_etfs_combined(frames=None, volume=None):
    """The daily card: ProShares flows and asset share lead; Yahoo trading volume, when downloaded, stays as context."""
    if frames is None:
        if volume is None:
            raise ValueError("No leveraged ETF data")
        return volume  # flows unavailable this refresh: the volume card as before, without the flow inputs
    frame = etf_flows(frames)
    last = frame.index[-1]
    row = frame.iloc[-1]
    flow_rank = percentile(list(frame["flows"]), row["flows"])
    share_rank = percentile(list(frame["share"].iloc[-SHARE_WINDOW:]), row["share"])
    since = frame.index[0].year
    inflow = row["flows"] >= 0
    level = "high" if "high" in (tone(flow_rank), tone(share_rank)) else "low" if "low" in (tone(flow_rank), tone(share_rank)) else "normal"
    lines = [f"Bull funds hold {row['share']:.1f}% of 3× fund assets ({dollars(row['bull'] / 1e6)} of {dollars(row['assets'] / 1e6)}) · "
             f"{ordinal(share_rank)} percentile of the last 3 years",
             f"Net flows, {ETF_WINDOW} sessions: bull funds {signed_money(row['bull_in'])}, bear funds {signed_money(row['bear_in'])} = "
             f"{row['flows']:+.1f}% of assets · {ranked(flow_rank, since)}".replace("-", "−")]
    if volume:
        lines += [line + " (Yahoo volume, context)" for line in volume.get("lines", [])[:2]]
    recent_from = last - timedelta(days=sentiment.HISTORY_DAYS)
    series = lambda column, digits: pairs(sentiment.thinned([(d, float(v)) for d, v in frame[column].items()], recent_from), digits)
    return dict(
        as_of=last.isoformat(), value=round(float(row["share"]), 1), reading=f"{row['share']:.0f}% bull", tone=level,
        signal=("Money entering bull funds" if inflow else "Money leaving bull funds") + f" · {ordinal(share_rank)} pct of 3-yr assets",
        lines=lines, frequency="daily", funds=[f"{name}: {b} / {s}" for name, (b, s) in PROSHARES_PAIRS.items()],
        detail=("ProShares' daily fund files for its 3× index ETFs, bull funds against their bear twins: "
                + "; ".join(f"{name} {b}/{s}" for name, (b, s) in PROSHARES_PAIRS.items()) + ". Net flow is each fund's change in "
                "assets beyond its own NAV return, so gains and losses are not counted as money moving. These funds are held "
                "mostly by individual investors, so flows are a same-day read of leveraged retail demand: new money tends to "
                "arrive on dips while a rally is intact. Bull funds' share of the assets is the standing leveraged position; it "
                "is ranked within three years because the funds grew steadily, and it falls when leveraged holders give up. "
                "Direxion's funds (SPXL, SOXL, TNA, TECL and their bear twins) publish no share history and are not included. "
                "This is not a measure of borrowing: nobody reports retail leverage daily."),
        series={**(volume or {}).get("series", {}),
                "etf_flows": dict(name=f"Net flows into 3× bull minus bear funds, {ETF_WINDOW} sessions, % of assets", unit="%",
                                  source="ProShares daily fund data", frequency="daily", points=series("flows", 2)),
                "etf_share": dict(name="Bull funds' share of 3× fund assets", unit="%", source="ProShares daily fund data",
                                  frequency="daily", points=series("share", 1))})


def etf_assets(symbols):
    def one(symbol):
        try:
            return symbol, sentiment.number(yf.Ticker(symbol).info.get("totalAssets"), 1)
        except Exception:
            return symbol, None
    with ThreadPoolExecutor(max_workers=4) as pool:
        return dict(pool.map(one, symbols))


def fetch_volume(now):
    local = now.astimezone(context.MARKET_TZ)
    symbols = [s for pair in ETF_PAIRS.values() for s in pair]
    frame = fear_greed.completed(yf.download(symbols + list(ETF_BENCHMARKS), start=ETF_START, interval="1d", auto_adjust=False,
                                             group_by="column", progress=False, threads=True, timeout=20), local.date(), local.hour)
    return parse_etfs(frame["Close"] * frame["Volume"], etf_assets(symbols))


def fetch_etfs(client, now):
    """ProShares flows and Yahoo volume, each optional: either source alone still gives the card a reading."""
    try:
        frames = parse_proshares({s: sentiment.request_text(client, PROSHARES_URL.format(s))
                                  for pair in PROSHARES_PAIRS.values() for s in pair})
    except (ValueError, OSError, httpx.HTTPError):
        frames = None
    try:
        volume = fetch_volume(now)
    except Exception:
        volume = None
    return {**parse_etfs_combined(frames, volume), "url": "https://www.proshares.com/our-etfs/leveraged-and-inverse/tqqq"}


# ---------- CFTC: leveraged funds in S&P 500 futures, weekly (already fetched for the sentiment panel) ----------

def cot_card(cot):
    """The leveraged-funds side of the COT reading the sentiment panel collects; no extra request."""
    spx = (cot.get("groups") or [{}])[0]
    history = cot.get("leveraged_history") or []
    if cot.get("status") not in ("ok", "cached") or not history or spx.get("leveraged") is None:
        return {"status": "unavailable", "error": cot.get("error") or "No leveraged-funds history in this reading; the next refresh collects it."}
    as_of = sentiment.iso_date(cot["as_of"])
    value, index = history[-1][1], spx.get("leveraged_index")
    # Either extreme is crowded leveraged positioning: a deep net short is often a borrowed-money basis trade.
    extreme = None if index is None else "long" if index >= ELEVATED else "short" if index <= LOW else ""
    sign = lambda v: f"{v:+,.0f}".replace("-", "−")
    return dict(
        status=cot["status"], fetched_at=cot.get("fetched_at"), as_of=as_of.isoformat(), value=value,
        reading=f"{value:+.1f}% of OI".replace("-", "−"), tone=None if extreme is None else "high" if extreme else "normal",
        signal=f"Unusually {extreme} for 3 years" if extreme else "Typical for 3 years",
        lines=[f"Leveraged funds net {sign(spx['leveraged'])} E-mini S&P 500 contracts"
               + (f" · COT index {index}" if index is not None else ""),
               *(f"{g['name']}: leveraged funds net {sign(g['leveraged'])}" for g in (cot.get("groups") or [])[1:] if g.get("leveraged") is not None)],
        frequency="weekly", released=(as_of + timedelta(days=3)).isoformat(), next=(as_of + timedelta(days=10)).isoformat(),
        next_basis="scheduled", next_time="3:30 PM ET",
        detail=("CFTC Traders in Financial Futures: hedge funds and commodity trading advisers (\"leveraged funds\") in E-mini "
                "S&P 500 futures, net contracts as a share of non-spreading open interest. Futures need only a margin deposit, so "
                "these positions are leveraged by construction. A deep net short is often one leg of a basis trade (long "
                "stocks or swaps, short futures) financed with borrowed money, not a bet on a fall. The COT index ranks the net "
                "position within the last three years. Positions are as of Tuesday and published Friday at 3:30 PM ET; "
                "federal holidays delay the release."),
        series={"cot_lev": dict(name="Leveraged funds' net, % of non-spreading open interest", unit="%",
                                source="CFTC Traders in Financial Futures · E-mini S&P 500", frequency="weekly", points=history)})


# ---------- collection and the dashboard payload ----------

def collect(now=None):
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(context.MARKET_TZ).date()
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=True) as client:
        jobs = {"finra": lambda: fetch_finra(client), "z1": lambda: fetch_z1(client), "ofr": lambda: fetch_ofr(client),
                "fsr": lambda: fetch_fsr(client, today), "etf": lambda: fetch_etfs(client, now)}

        def run(job):
            key, fetch = job

            def dated():
                item = fetch()
                if sentiment.iso_date(item["as_of"]) > today:
                    raise ValueError("Future observation")
                return item
            return key, sentiment.cached_read(f"leverage-{key}", dated, now, TTL_HOURS.get(key, DEFAULT_TTL), sessions=key == "etf")

        progress.emit(activity="Checking market leverage sources")
        with ThreadPoolExecutor(max_workers=4) as pool:
            return dict(pool.map(run, jobs.items()))


NAMES = {"finra": ("Margin debt · FINRA", FINRA_PAGE), "z1": ("Margin loans · Fed Z.1", Z1_PAGE),
         "ofr": ("Hedge fund leverage · OFR", OFR_PAGE), "etf": ("Leveraged ETFs · daily", "https://finance.yahoo.com/quote/TQQQ/history/"),
         "cot": ("Leveraged funds · CFTC", "https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm"),
         "fsr": ("Fed Financial Stability Report", FSR_PAGE)}


def evaluate(payload, items, cot, *, now=None):
    now = now or datetime.now(timezone.utc)
    items = {**items, "cot": cot_card(cot or {})}
    cards, series = [], {}
    for key in ORDER:
        item = dict(items.get(key) or {"status": "unavailable"})
        name, url = NAMES[key]
        series.update(item.pop("series", None) or {})
        cards.append({**item, "key": key, "name": name, "url": item.get("url") or url})
    payload["market_leverage"] = {"checked_at": now.isoformat(), "cards": cards, "series": series}


def enrich(payload, sentiment_inputs=None):
    evaluate(payload, collect(), ((sentiment_inputs or {}).get("macro") or {}).get("cot"))
