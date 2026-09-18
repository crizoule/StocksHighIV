"""Business exposures and measured price patterns, separate from event attribution.

Rules intentionally leave gaps: an industry or a passing mention of AI is not enough
to identify a company as an AI beneficiary or disruption victim.
"""
from __future__ import annotations

import math
import re
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

MARKET_TZ = ZoneInfo("America/New_York")
MIN_PEERS = 3  # Other companies, excluding the subject.
PEER_MOVE_PCT = 20
ADOBE_REPORT = "https://www.adobe.com/cc-shared/assets/investor-relations/pdfs/adbe-2025-annual-report.pdf"
FIVERR_REPORT = "https://investors.fiverr.com/static-files/0e4a7726-4035-458d-8ce7-adc602d2c4a3"
MICRON_REPORT = "https://s25.q4cdn.com/621799436/files/doc_financials/2025/ar/2025-Form-10-K.pdf"


def exposures(row: dict, as_of: date) -> list[dict]:
    """Classify only explicit business activities or reviewed issuer disclosures."""
    result = []
    industry = row.get("industry") or ""
    profile = row.get("summary") or row.get("business") or ""
    symbol = row.get("yahoo_symbol") or row["symbol"]
    profile_url = f"https://finance.yahoo.com/quote/{quote(symbol, safe='')}/profile/"

    def add(key, label, detail, evidence, url=profile_url, source="Yahoo Finance company profile"):
        result.append(dict(key=key, label=label, detail=detail, evidence=evidence,
                           source=source, url=url))

    commodities = {"Gold": "gold", "Silver": "silver", "Copper": "copper", "Uranium": "uranium",
                   "Other Precious Metals & Mining": "precious metals",
                   "Other Industrial Metals & Mining": "industrial metals", "Steel": "steel"}
    if industry in commodities:
        metal = commodities[industry]
        add(metal.replace(" ", "_"), f"{metal.capitalize()} cycle exposure",
            f"Exposure to {metal} prices and supply/demand cycles. Costs, project stage, hedging and "
            "royalty versus operating models can change the sensitivity; no current cycle direction is assumed.",
            f"Profile industry: {industry}.")
    if industry in ("Oil & Gas E&P", "Oil & Gas Integrated"):
        add("oil_gas", "Oil / gas cycle exposure",
            "Oil and gas production links the business to commodity prices and supply/demand cycles. "
            "Hedging and downstream operations can offset that exposure.", f"Profile industry: {industry}.")
    elif industry in ("Oil & Gas Equipment & Services", "Oil & Gas Drilling"):
        add("oil_services", "Energy spending cycle",
            "Indirect oil/gas exposure through producers’ drilling and capital spending; not the same "
            "price sensitivity as owning production.", f"Profile industry: {industry}.")
    elif industry == "Oil & Gas Refining & Marketing":
        add("refining", "Refining margin cycle",
            "Sensitive to fuel demand and the spread between product prices and crude input costs. "
            "Higher oil prices alone do not imply higher refining profits.", f"Profile industry: {industry}.")

    # Do not confuse HDD storage with DRAM/NAND manufacturing, or memory users with suppliers.
    if industry in ("Semiconductors", "Computer Hardware"):
        if re.search(r"\bmanufactur(?:es?|ed|ing)\b", profile, re.I) and re.search(r"\b(?:DRAM|NAND|dynamic random access memory)\b", profile, re.I):
            add("memory", "Memory supply / demand cycle",
                "DRAM/NAND supply, inventories and selling prices can swing across the memory cycle. "
                "This is structural exposure, not evidence that a new shortage or glut caused this IV.",
                "Profile describes manufacturing DRAM/NAND memory.")
            if as_of >= date(2025, 10, 3):
                result[-1]["reference_url"] = MICRON_REPORT
                result[-1]["reference_source"] = "Micron FY2025 10-K: memory industry risk factors"
        elif industry == "Computer Hardware" and re.search(r"\b(?:hard (?:disk )?(?:drives?|disks?)|HDD)\b", profile, re.I) and re.search(r"\b(?:offers|manufactures|sells)\b.{0,60}\b(?:hard (?:disk )?drives?|HDD)\b", profile, re.I):
            add("storage", "Data-storage spending cycle",
                "Hard-drive demand is exposed to cloud and device spending cycles; HDDs are distinct "
                "from the DRAM/NAND memory market.", "Profile describes supplying hard disk drives.")

    ai_match = re.search(
        r"\b(?:AI cloud|AI infrastructure|infrastructure (?:for|to service the global) AI|"
        r"data center.scale AI|high.performance (?:compute|computing)|"
        r"data center.{0,45}(?:GPUs|artificial intelligence)|high.bandwidth memory)\b", profile, re.I)
    if ai_match:
        add("ai_infrastructure", "AI / compute spending exposure",
            "Supplies AI or high-performance computing capacity, infrastructure or components. "
            "Demand growth is an opportunity; spending slowdowns, competition and capacity investment "
            "are risks. This does not establish an AI boom or a cause of this stock’s move.",
            f"Profile business match: {ai_match.group(0)}.")

    # Both name and US ticker must match. Never assign a US issuer's story to a TSX ticker collision.
    name = row.get("name") or ""
    if row.get("market") == "US" and symbol == "ADBE" and re.match(r"Adobe\b", name, re.I) and as_of >= date(2026, 1, 15):
        add("ai_disruption", "AI competition / adaptation risk",
            "Adobe identifies AI-native competitors and rapid changes in creative software. "
            "Its own Firefly and AI products also offer growth opportunities. The net impact on "
            "revenue, margins and valuation is uncertain; no specific price decline is attributed to AI.",
            "FY2025 annual report, Business—Competition and AI product strategy.", ADOBE_REPORT, "Adobe FY2025 annual report")
    if row.get("market") == "US" and symbol == "FVRR" and re.match(r"Fiverr\b", name, re.I) and as_of >= date(2026, 3, 12):
        add("ai_disruption", "AI substitution / adaptation risk",
            "Fiverr reports AI reducing demand for simple, low-skilled services while expanding "
            "demand for complex, high-skilled work. This is a disclosed business headwind and "
            "opportunity, not proof of what caused this session’s IV.",
            "2025 Form 20-F, risk factors and operating review.", FIVERR_REPORT, "Fiverr 2025 Form 20-F")

    bitcoin = re.search(r"\b(?:bitcoin|BTC) (?:mining|treasury)|\bmin(?:es|ing) (?:of )?bitcoin\b|\bvalidate transactions on the bitcoin blockchain\b", profile, re.I)
    ether = re.search(r"\b(?:Ethereum|Ether|ETH) (?:treasury|staking)|\bstaking.{0,25}\b(?:ETH|Ethereum)\b", profile, re.I)
    for key, token, match in (("bitcoin", "Bitcoin", bitcoin), ("ether", "Ether", ether)):
        if not match:
            continue
        add(key, f"{token} business exposure",
            f"Profile identifies {token} mining, holdings or staking activities. Token prices, "
            "financing and operating economics can amplify gains and losses. Stock price moves "
            "below are not measurements of Bitcoin or Ether returns.",
            f"Profile business match: {match.group(0)}.")
    if not bitcoin and not ether and re.search(r"\b(?:cryptocurrency mining|mining cryptocurrencies)\b", profile, re.I):
        add("crypto_mining", "Crypto mining exposure",
            "Mining economics depend on token prices, network difficulty, energy costs and capital spending. "
            "The cached description does not establish a specific token mix.", "Profile describes cryptocurrency mining.")
    elif not bitcoin and not ether and re.search(r"\b(?:crypto(?:currency|currencies| asset)?|digital asset)\b", profile, re.I) and re.search(r"\b(?:exchange|trading platform)\b", profile, re.I):
        add("crypto_activity", "Crypto trading activity exposure",
            "Trading-platform activity is exposed to crypto participation and trading volumes, "
            "as well as prices and regulation; it is not a direct token holding.", "Profile describes a crypto trading platform.")
    return result


def _closes(row: dict, as_of: date, now: datetime) -> dict[date, float]:
    frame = (row.get("prices") or {}).get("1D") or {}
    values = {}
    local_now = now.astimezone(MARKET_TZ)
    for timestamp, close in zip(frame.get("t", []), frame.get("c", [])):
        if not isinstance(close, (int, float)) or isinstance(close, bool) or not math.isfinite(close) or close <= 0:
            continue
        try:
            day = datetime.fromtimestamp(timestamp, MARKET_TZ).date()
        except (ValueError, TypeError, OverflowError, OSError):
            continue
        if day > as_of or day > local_now.date() or (day == local_now.date() and local_now.hour < 16):
            continue
        values[day] = float(close)
    return dict(sorted(values.items()))


def _window(values: dict[date, float], start: date, end: date) -> dict[date, float] | None:
    window = {d: c for d, c in values.items() if start <= d <= end}
    days = list(window)
    if start not in window or end not in window or len(days) < 50:
        return None
    if any((b - a).days > 7 for a, b in zip(days, days[1:])):
        return None
    return window


def price_pattern(values: dict[date, float], as_of: date) -> dict | None:
    if not values:
        return None
    end = max(values)
    if (as_of - end).days > 4:
        return None
    target = end - timedelta(days=91)
    starts = [d for d in values if target <= d <= target + timedelta(days=7)]
    if not starts:
        return None
    start = min(starts)
    window = _window(values, start, end)
    if window is None:
        return None
    first, last = window[start], window[end]
    move = (last / first - 1) * 100
    peak = max(window, key=window.get)
    trough = min(window, key=window.get)
    rise = (window[peak] / first - 1) * 100
    fall = (last / window[peak] - 1) * 100
    dip = (window[trough] / first - 1) * 100
    rebound = (last / window[trough] - 1) * 100
    label = f"Stock {move:+.1f}% over ~3 months"
    if start < peak < end and rise >= 25 and fall <= -20:
        label = f"Stock rose {rise:.1f}%, then fell {abs(fall):.1f}%"
    elif start < trough < end and dip <= -20 and rebound >= 25:
        label = f"Stock fell {abs(dip):.1f}%, then rebounded {rebound:.1f}%"
    return dict(label=label, start=start.isoformat(), end=end.isoformat(), return_pct=round(move, 1),
                peak_date=peak.isoformat(), from_peak_pct=round(fall, 1), trough_date=trough.isoformat(),
                detail=f"{start} to {end}: price return {move:+.1f}%; {fall:.1f}% from the window’s "
                       f"highest close ({peak}). Yahoo daily closes, in the listing currency, excluding "
                       "dividends. Price history is observed context, not an IV attribution.")


def apply(payload: dict, *, now: datetime | None = None) -> None:
    """Refresh context from cached profiles/prices on every report write; no network calls."""
    now = now or datetime.now(MARKET_TZ)
    rows = payload["rows"]
    groups = defaultdict(list)
    prices = {}
    for i, row in enumerate(rows):
        as_of = date.fromisoformat(row.get("quote_date") or payload.get("quote_date") or payload["run_date"])
        row["iv_context"] = exposures(row, as_of)
        row["iv_peer_trends"] = []
        prices[i] = _closes(row, as_of, now)
        row["iv_price_context"] = price_pattern(prices[i], as_of)
        for item in row["iv_context"]:
            groups[item["key"]].append(i)

    for i, row in enumerate(rows):
        pattern = row["iv_price_context"]
        if not pattern:
            continue
        start, end = date.fromisoformat(pattern["start"]), date.fromisoformat(pattern["end"])
        for exposure in row["iv_context"]:
            members = []
            for j in groups[exposure["key"]]:
                if i == j:
                    continue
                window = _window(prices[j], start, end)
                if window:
                    members.append(dict(symbol=rows[j]["symbol"], return_pct=(window[end] / window[start] - 1) * 100))
            if len(members) < MIN_PEERS:
                continue
            median = statistics.median(p["return_pct"] for p in members)
            up = sum(p["return_pct"] > 0 for p in members)
            down = sum(p["return_pct"] < 0 for p in members)
            direction = "up" if median >= PEER_MOVE_PCT and up / len(members) >= 2/3 else "down" if median <= -PEER_MOVE_PCT and down / len(members) >= 2/3 else "mixed"
            for member in members:
                member["return_pct"] = round(member["return_pct"], 1)
            row["iv_peer_trends"].append(dict(key=exposure["key"], label=exposure["label"],
                direction=direction, median_pct=round(median, 1), up=up, down=down, count=len(members),
                start=start.isoformat(), end=end.isoformat(), members=members,
                detail="Other companies sharing this exposure in the enriched screen, across both cap tabs. "
                       "The subject is excluded; dates are identical. This high-IV sample is not the whole "
                       "industry or a market benchmark. Co-movement does not prove a shared cause or hype."))
