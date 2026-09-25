"""NAAIM Exposure Index: active managers' weekly US equity exposure, from a bundled history plus NAAIM's delayed table.

Members of the National Association of Active Investment Managers report their equity exposure each Wednesday, from
-200% (leveraged short) through 0 (cash) and 100% (fully invested) to +200% (leveraged long); the index is the mean.
Since 2026-08-01 NAAIM sells current readings by subscription and shows the public a table three months late.
naaim_history.csv bundles the last free full-history file (2006 to 2026-07-29); each refresh adds any newer week from
the delayed table, so the series stays about three months behind and is never counted as a current reading.
"""
from __future__ import annotations

import html
import re
from datetime import date, datetime, timedelta
from pathlib import Path

HISTORY_FILE = Path(__file__).with_name("naaim_history.csv")
TABLE_URL = "https://index.naaim.org/embeddable/table"
PAGE_URL = "https://naaim.org/programs/naaim-exposure-index/"
FIELDS = ("naaim", "bearish", "quartile1", "median", "quartile3", "bullish", "deviation")
DELAY_DAYS = 91   # NAAIM's public table runs three months behind


def bundled():
    """Weekly readings shipped with the app, keyed by survey date."""
    weeks = {}
    for line in HISTORY_FILE.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith(("#", "week")):
            day, *values = line.split(",")
            weeks[date.fromisoformat(day)] = dict(zip(FIELDS, (float(v) if v else None for v in values)))
    return weeks


def parse_table(text, today):
    """Weeks from NAAIM's public table: date, NAAIM Number, bearish, three quartiles, bullish, deviation."""
    weeks = {}
    for row in re.findall(r"<tr>(.*?)</tr>", text, re.S):
        cells = [html.unescape(re.sub(r"<[^>]+>", "", c)).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if len(cells) != 8:
            continue
        try:
            when = datetime.strptime(cells[0], "%m/%d/%Y").date()
            values = [float(v) for v in cells[1:]]
        except ValueError:
            continue
        if when <= today and -200 <= values[0] <= 200:
            weeks[when] = dict(zip(FIELDS, values))
    if not weeks:
        raise ValueError("No NAAIM weeks in the table")
    return {"as_of": max(weeks).isoformat(), "weeks": [[d.isoformat(), weeks[d]] for d in sorted(weeks)]}


def apply_bundled(payload):
    """Give a report saved before NAAIM was added its card and chart series, from the bundled history."""
    macro = payload.get("macro_sentiment")
    if not isinstance(macro, dict) or any(c.get("key") == "naaim" for c in macro.get("cards") or []):
        return payload
    item = reading()
    macro.setdefault("cards", []).append({**{k: v for k, v in item.items() if k != "history"},
                                          "key": "naaim", "name": "NAAIM exposure", "url": PAGE_URL, "max_age": 120})
    series = (macro.get("history") or {}).get("series")
    if isinstance(series, dict):
        series["naaim"] = chart_series(item["history"])
    return payload


def chart_series(points):
    return dict(name="NAAIM exposure index", unit="%", source="NAAIM weekly survey · public data three months late",
                frequency="weekly", points=points)


def signal(value):
    return ("Leveraged long" if value > 100 else "Heavily invested" if value >= 80 else "Defensive" if value < 40
            else "Moderately invested")


def reading(fetched=None):
    """The latest week and the weekly series: the bundled history, with any newer weeks from NAAIM's table on top."""
    weeks = bundled()
    added = 0
    for day, values in (fetched or {}).get("weeks") or []:
        when = date.fromisoformat(day)
        added += when not in weeks
        weeks[when] = values
    last = max(weeks)
    value = weeks[last]["naaim"]
    ranked = sorted(v["naaim"] for v in weeks.values())
    pct = round(sum(v < value for v in ranked) / len(ranked) * 100)
    table_ok = (fetched or {}).get("status") in ("ok", "cached")
    return dict(
        status="ok", as_of=last.isoformat(), value=value, reading=f"{value:.2f}", signal=signal(value), delayed=True,
        percentile=pct, quartiles=[weeks[last].get(k) for k in ("quartile1", "median", "quartile3")],
        next_public=(last + timedelta(days=7 + DELAY_DAYS)).isoformat(),
        source="NAAIM's delayed public table" if added else "History bundled with the app" + ("" if table_ok else "; NAAIM's table unavailable"),
        history=[[d.isoformat(), weeks[d]["naaim"]] for d in sorted(weeks)],
        detail=(f"Week of {last:%b %d, %Y}: active managers averaged {value:.2f}% US equity exposure (median "
                f"{weeks[last].get('median'):g}%), the {pct}th percentile since {min(weeks):%Y}. Scale: -200% leveraged short, 0 cash, "
                "100% fully invested, +200% leveraged long; above 100 means managers on average use leverage. NAAIM publishes "
                "current readings only to subscribers since August 2026 and shows everyone else a table three months late, so "
                "this card is always about three months behind and is not counted in the Sentiment label. Extremes are often "
                "read contrarian, but readings in the 90s have lasted for months."))
