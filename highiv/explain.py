"""Conservative, traceable candidate events; never infer catalysts from screening metrics."""
from __future__ import annotations

import re
from datetime import date
from urllib.parse import urlsplit

from . import config

NONE = {
    "iv_why": "No clear catalyst found",
    "iv_why_kind": "none",
    "iv_why_detail": (
        "No qualifying company-specific event found in the available headlines or earnings dates. "
        "This does not establish that no catalyst exists."
    ),
    "iv_why_date": None,
    "iv_why_source": None,
    "iv_why_url": None,
    "iv_why_event": None,
}

# Keep business qualifiers: Ivanhoe Electric and Ivanhoe Mines are different issuers.
_LEGAL_SUFFIX = re.compile(
    r"(?:\s+(?:incorporated|inc|corporation|corp|limited|ltd|plc|llc|holdings?|group|sa|nv))+$", re.I
)
_SHARE_CLASS = re.compile(r"\s+(?:class [a-z]|ordinary shares?|common stock|adr|ads)\b.*$", re.I)

# Reject opinion, speculation, recaps and law-firm solicitation even when they contain event words.
_REJECT = re.compile(
    r"\?|\b(?:could|might|may|potential|rumou?rs?|reportedly|report of|in talks|considering|"
    r"speculat\w*|should|would|if|predict\w*|outlook|price target|analyst|"
    r"stocks? to (?:buy|watch|avoid)|better buy|why .{0,60}(?:stock|shares)|"
    r"earnings call highlights|roundup|market today|last month|in (?:january|february|march|"
    r"april|may|june|july|august|september|october|november|december)|"
    r"shareholder alert|investor alert|class action|law firm|lead plaintiff|"
    r"reminds? investors|deadline|contact|investigates? (?:claims|on behalf))\b",
    re.I,
)

# An action is required in the title itself. Price moves, buzzwords and publisher reputation
# cannot qualify a story on their own. Patterns deliberately favor precision over recall.
_EVENTS = (
    ("Acquisition / merger", re.compile(
        r"\b(?:agrees? to (?:acquire|buy|merge)|to acquire|"
        r"(?:announces?|completes?|terminates?|cancels?) (?:\w+\s+){0,4}(?:acquisition|merger)|"
        r"signs? (?:\w+\s+){0,4}(?:merger|acquisition) agreement|"
        r"accepts? (?:\w+\s+){0,3}takeover (?:bid|offer))\b", re.I)),
    ("Financing", re.compile(
        r"\b(?:announces?|prices?|launches?|completes?) (?:[^.!?;]{0,65})"
        r"\b(?:public offering|stock offering|share offering|registered direct offering|"
        r"convertible (?:notes?|debt)|at-the-market offering)\b", re.I)),
    ("Guidance change", re.compile(
        r"\b(?:(?:cuts?|raises?|lowers?|withdraws?|suspends?) (?:\w+\s+){0,4}(?:guidance|forecast)|"
        r"(?:guidance|forecast) (?:cut|raised|lowered|withdrawn))\b", re.I)),
    ("Earnings results", re.compile(
        r"\b(?:(?:reports?|posts?|announces?) (?:\w+\s+){0,4}(?:quarterly results|earnings)|"
        r"(?:beats?|misses?) (?:\w+\s+){0,3}(?:earnings|revenue|profit) estimates)\b", re.I)),
    ("Regulatory / court decision", re.compile(
        r"\b(?:(?:SEC|FDA|FTC|DOJ|court|regulator) (?:\w+\s+){0,3}"
        r"(?:approves?|clears?|rejects?|blocks?|orders?|sues?)|"
        r"(?:wins?|receives?|secures?) (?:\w+\s+){0,3}(?:regulatory|FDA|SEC) approval|"
        r"(?:FDA|SEC) (?:approval|rejection))\b", re.I)),
    ("Financial distress", re.compile(
        r"\b(?:files? for (?:chapter (?:7|11)|bankruptcy)|files? (?:chapter (?:7|11)|bankruptcy)|"
        r"defaults? on|receives? (?:\w+\s+){0,3}delisting notice)\b", re.I)),
    ("Operating event", re.compile(
        r"\b(?:(?:halts?|suspends?|resumes?) (?:\w+\s+){0,3}(?:production|operations|deliveries)|"
        r"(?:wins?|signs?|awarded) (?:[^.!?;]{0,50})\b(?:contract|supply agreement)|"
        r"announces? (?:\w+\s+){0,3}reverse (?:stock )?split)\b", re.I)),
)


def _trim(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0].rstrip(",;:…") + "…"


def _words(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower().replace("n.v.", "nv").replace("s.a.", "sa").replace("&", " and ")))


def _about(headline: dict, name: str | None, symbol: str) -> bool:
    title = headline.get("title") or ""
    # Only explicitly marked tickers count; NOW, ALL, GOLD, etc. are also ordinary words.
    base = re.sub(r"\.(?:TO|V)$", "", symbol, flags=re.I)
    ticker = re.escape(base).replace(r"\-", r"[.\-]").replace(r"\.", r"[.\-]")
    if re.search(rf"(?:\({ticker}\)|\${ticker}(?![\w.-])|(?:NYSE|NASDAQ|TSX)\s*:\s*{ticker}(?![\w.-]))", title, re.I):
        return True
    name = _SHARE_CLASS.sub("", _words(name or ""))
    name = _LEGAL_SUFFIX.sub("", name).strip()
    if not name or len(name) < 3:
        return False
    # Exact multiword issuer name, not just the first shared word or a summary mention.
    return bool(re.search(rf"(?<!\w){re.escape(name)}(?!\w)", _words(title)))


def _event(headline: dict, row: dict, as_of: date) -> str | None:
    title = headline.get("title") or ""
    try:
        age = (as_of - date.fromisoformat(headline.get("date") or "")).days
        link = urlsplit(headline.get("url") or "")
    except (ValueError, TypeError):
        return None
    if not 0 <= age <= config.NEWS_LOOKBACK_DAYS:
        return None
    if link.scheme not in ("https", "http") or not link.hostname or not headline.get("source"):
        return None
    if (headline.get("type") or "STORY").upper() != "STORY" or _REJECT.search(title):
        return None
    if not _about(headline, row.get("name"), row["symbol"]):
        return None
    for clause in re.split(r";|\s+\|\s+", title):
        if not _about({"title": clause}, row.get("name"), row["symbol"]):
            continue
        for label, pattern in _EVENTS:
            if pattern.search(clause):
                return label
    return None


def _earnings(row: dict, as_of: date) -> dict | None:
    when = row.get("next_earnings")
    try:
        event_date = date.fromisoformat(when)
    except (ValueError, TypeError):
        return None
    # Recompute rather than trusting a countdown saved on an earlier build date.
    days = (event_date - as_of).days
    if not 0 <= days <= config.EARNINGS_WHY_DAYS:
        return None
    flag = row.get("earnings_estimated")
    status = "estimated" if flag is True else "confirmed date" if flag is False else "date unconfirmed"
    label = event_date.strftime("%b %-d")
    return {
        "iv_why": f"Upcoming earnings {label} ({status})",
        "iv_why_kind": "earnings",
        "iv_why_detail": (
            f"Yahoo earnings calendar lists {when} ({status}), {days} days after the IV session. "
            "A scheduled report is potential event risk; it does not prove why IV is elevated."
        ),
        "iv_why_date": when,
        "iv_why_source": "Yahoo Finance earnings calendar",
        "iv_why_url": None,
        "iv_why_event": "Scheduled earnings",
    }


def choose(row: dict, headlines: list[dict] | None, as_of: date | None = None) -> dict:
    """Show a sourced possible event or admit uncertainty. No inferred squeeze/sector causes."""
    as_of = as_of or date.today()
    candidates = []
    for item in headlines or []:
        event = _event(item, row, as_of)
        if event:
            candidates.append((item, event))
    if candidates:
        # Most recent qualifying evidence wins; outlet reputation cannot make a non-event qualify.
        item, event = max(candidates, key=lambda pair: pair[0]["date"])
        result = {
            "iv_why": f"Possible catalyst: {_trim(item['title'], 110)}",
            "iv_why_kind": "news",
            "iv_why_detail": (
                f"Reported event: {item['title']} ({item['source']}, {item['date']}). "
                "Based on the headline only; the article body and its connection to elevated IV "
                "have not been independently verified."
            ),
            "iv_why_date": item["date"],
            "iv_why_source": item["source"],
            "iv_why_url": item["url"],
            "iv_why_event": event,
        }
    else:
        result = _earnings(row, as_of) or dict(NONE)
    if headlines is None:
        result["iv_why_detail"] += " News could not be retrieved; headline coverage is unavailable."
    elif not headlines:
        result["iv_why_detail"] += " The news feed returned no usable headlines."
    result["iv_why_as_of"] = as_of.isoformat()
    result["iv_news_status"] = "unavailable" if headlines is None else "empty" if not headlines else "checked"
    return result
