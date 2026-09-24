"""AAII sentiment readings: AAII's page, bundled history, weeks entered by hand, or a spreadsheet in data/imports.

AAII blocks automated requests, so the app never downloads the survey. aaii_history.csv bundles AAII's
weekly results (personal use); each Thursday the user copies the new week's three percentages from
AAII's results page into the dashboard. Standard library only at import time: the local launcher runs
on system Python, and only spreadsheet reading needs xlrd or openpyxl.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import config

RESULTS_URL = "https://www.aaii.com/sentimentsurvey"
HISTORY_FILE = Path(__file__).with_name("aaii_history.csv")
LONG_RUN = (37.5, 31.0, 31.5)   # AAII's published long-run averages (bullish, neutral, bearish), from its results page
MAX_ENTRY_AGE_DAYS = 70
FILE_NAME = re.compile(r"sentiment.*\.(xls|xlsx|csv)", re.I)
MAX_BYTES = 20_000_000
MAX_FILES = 5
DATE_FORMATS = ("%m-%d-%y", "%m/%d/%y", "%m-%d-%Y", "%m/%d/%Y", "%Y-%m-%d", "%b %d, %Y", "%B %d, %Y")


def summary(when, bull, neutral, bear, label="Week ending"):
    spread = round(bull - bear, 1)
    return dict(as_of=when.isoformat(), value=spread, bullish=bull, neutral=neutral, bearish=bear,
                reading=f"{spread:+.1f} pp", signal="Bullish tilt" if spread > 10 else "Bearish tilt" if spread < -10 else "Mixed",
                direction=1 if spread > 10 else -1 if spread < -10 else 0,
                detail=f"{label} {when}: {bull:g}% bullish / {neutral:g}% neutral / {bear:g}% bearish. Bull–bear spread; weekly six-month outlook survey. ±10 pp defines the app’s tilt; extremes can be contrarian.")


def week_of(item):
    """The survey week's closing Wednesday; spreadsheet rows are dated the Thursday they were reported."""
    try:
        when = date.fromisoformat(str(item["as_of"])[:10])
    except (KeyError, TypeError, ValueError):
        return None
    return when - timedelta(days=1) if item.get("date_label") == "reported" else when


def newest(*items):
    """The reading for the latest survey week; earlier arguments win a tie."""
    candidates = [(week_of(item), -rank, item) for rank, item in enumerate(items)
                  if item and item.get("status") in ("ok", "cached") and week_of(item)]
    return max(candidates, key=lambda c: c[:2])[2] if candidates else None


def bundled():
    """AAII's weekly results shipped with the app, keyed by reported (Thursday) date, in percent."""
    weeks = {}
    for line in HISTORY_FILE.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith(("#", "reported")):
            day, *values = line.split(",")
            weeks[date.fromisoformat(day)] = [float(v) for v in values]
    return weeks


def bundled_reading():
    weeks = bundled()
    when = max(weeks)
    bull, neutral, bear = (round(v, 1) for v in weeks[when])
    item = summary(when, bull, neutral, bear, "Reported")
    item["detail"] += (f" From AAII's history bundled with the app ({len(weeks)} weeks since {min(weeks)}). "
                       f"AAII's long-run averages: {LONG_RUN[0]:g}% bullish / {LONG_RUN[1]:g}% neutral / {LONG_RUN[2]:g}% bearish.")
    return {**item, "status": "ok", "source": "AAII history bundled with the app", "date_label": "reported"}


def spread_series(data_dir, *readings):
    """Weekly bull–bear spread by survey week (Wednesday): bundled history, then any newer readings, then entries."""
    weeks = {when - timedelta(days=1): round(v[0] - v[2], 2) for when, v in bundled().items()}
    for item in readings:
        week = week_of(item or {})
        if week and item.get("status") in ("ok", "cached") and isinstance(item.get("value"), (int, float)):
            weeks[week] = item["value"]
    weeks.update({when: round(v[0] - v[2], 2) for when, v in entered_weeks(data_dir).items()})
    return [[when.isoformat(), weeks[when]] for when in sorted(weeks)]


SHARES = ("bullish", "neutral", "bearish")


def share_series(data_dir, *readings):
    """Bullish, neutral and bearish percentages by survey week, from the same sources and in the same order as the spread."""
    weeks = {when - timedelta(days=1): v for when, v in bundled().items()}
    for item in readings:
        week = week_of(item or {})
        values = [(item or {}).get(k) for k in SHARES]
        if week and item.get("status") in ("ok", "cached") and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values):
            weeks[week] = values
    weeks.update(entered_weeks(data_dir))
    return {name: [[when.isoformat(), round(weeks[when][i], 2)] for when in sorted(weeks)] for i, name in enumerate(SHARES)}


def entries_path(data_dir):
    return Path(data_dir) / "sentiment" / "aaii-manual.json"


def entered_weeks(data_dir):
    try:
        weeks = json.loads(entries_path(data_dir).read_text(encoding="utf-8"))["weeks"]
        return {date.fromisoformat(k): [float(v) for v in values] for k, values in weeks.items() if len(values) == 3}
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return {}


def entered(data_dir):
    """The latest week the user entered, as a card reading, or None."""
    weeks = entered_weeks(data_dir)
    if not weeks:
        return None
    when = max(weeks)
    item = summary(when, *weeks[when])
    item["detail"] += (f" Entered from AAII's weekly results. AAII's long-run averages: {LONG_RUN[0]:g}% bullish / "
                       f"{LONG_RUN[1]:g}% neutral / {LONG_RUN[2]:g}% bearish.")
    return {**item, "status": "ok", "source": "Entered from AAII's results page", "date_label": "week ending", "entered": True}


def save_week(data_dir, week_ending, bullish, neutral, bearish, today):
    """Validate and store one week's percentages; returns the updated card reading."""
    try:
        when = date.fromisoformat(str(week_ending))
    except ValueError:
        raise ValueError("Enter the week-ending date shown on AAII's page.") from None
    if when > today or (today - when).days > MAX_ENTRY_AGE_DAYS:
        raise ValueError("The week must end today or earlier, within the last 10 weeks.")
    values = []
    for value in (bullish, neutral, bearish):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 100:
            raise ValueError("Enter each percentage between 0 and 100.")
        values.append(round(float(value), 1))
    if abs(sum(values) - 100) > 0.5:
        raise ValueError(f"Bullish, neutral and bearish add up to {sum(values):g}%, not 100%.")
    weeks = entered_weeks(data_dir)
    weeks[when] = values
    path = entries_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"weeks": {d.isoformat(): weeks[d] for d in sorted(weeks)}}), encoding="utf-8")
    os.replace(temporary, path)
    return entered(data_dir)


def apply_entered(payload, data_dir):
    """Show hand-entered weeks on a saved report (card and chart) without waiting for the next refresh.

    A report saved before 2.9.0 holds only the bull–bear spread; its three shares are rebuilt here from the bundled
    history, the entered weeks and the report's own AAII card, so an update shows the three lines straight away.
    """
    macro = payload.get("macro_sentiment") or {}
    reading = entered(data_dir)
    weeks = entered_weeks(data_dir)
    chart = ((macro.get("history") or {}).get("series") or {}).get("aaii")
    if chart and isinstance(chart.get("points"), list):
        points = {day: value for day, value in chart["points"]}
        points.update({when.isoformat(): round(v[0] - v[2], 2) for when, v in weeks.items()})
        chart["points"] = [[day, points[day]] for day in sorted(points)]
        lines = chart.get("lines")
        if isinstance(lines, dict) and all(isinstance(lines.get(name), list) and lines[name] for name in SHARES):
            for i, name in enumerate(SHARES):
                shares = {day: value for day, value in lines[name]}
                shares.update({when.isoformat(): round(v[i], 2) for when, v in weeks.items()})
                lines[name] = [[day, shares[day]] for day in sorted(shares)]
        else:
            card = next((c for c in macro.get("cards") or [] if c.get("key") == "aaii"), None)
            chart.update(name="AAII bullish / neutral / bearish", unit="%", lines=share_series(data_dir, card))
    if reading:
        for index, card in enumerate(macro.get("cards") or []):
            if card.get("key") == "aaii" and newest(card, reading) is reading:
                keep = {k: card[k] for k in ("key", "name", "url", "max_age") if k in card}
                macro["cards"][index] = {**reading, **keep}
    return payload


def import_folders():
    return (config.DATA_DIR / "imports",)


def find_files(folders):
    """Files named like AAII's download, newest first; unreadable folders are reported, not fatal."""
    found, notes = [], []
    for folder in folders:
        try:
            paths = [p for p in Path(folder).iterdir() if FILE_NAME.fullmatch(p.name)]
            found += [(p.stat().st_mtime, p) for p in paths if p.is_file() and 0 < p.stat().st_size <= MAX_BYTES]
        except FileNotFoundError:
            continue
        except OSError:
            notes.append(f"The app cannot read {folder} (check the operating system's privacy settings).")
    return [p for _, p in sorted(found, key=lambda item: item[0], reverse=True)], notes


def _xls_value(book, cell):
    import xlrd
    if cell.ctype == xlrd.XL_CELL_DATE:
        try:
            return xlrd.xldate.xldate_as_datetime(cell.value, book.datemode).date()
        except (ValueError, OverflowError, xlrd.xldate.XLDateError):
            return None
    return None if cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK, xlrd.XL_CELL_ERROR) else cell.value


def read_sheets(data):
    """Rows of every sheet, as plain values, from .xls, .xlsx or CSV bytes."""
    if data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        import xlrd
        book = xlrd.open_workbook(file_contents=data)
        return [[[_xls_value(book, sheet.cell(r, c)) for c in range(sheet.ncols)] for r in range(sheet.nrows)]
                for sheet in book.sheets()]
    if data.startswith(b"PK\x03\x04"):
        import openpyxl
        book = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        return [[list(row) for row in sheet.iter_rows(values_only=True)] for sheet in book.worksheets]
    text = data.decode("utf-8-sig", errors="replace")
    if re.search(r"<!doctype|<html|pardon our interruption", text[:5000], re.I):
        raise ValueError("it is a saved web page (possibly AAII's bot check), not the spreadsheet")
    return [list(csv.reader(io.StringIO(text)))]


def _text(value):
    return str(value).strip().lower() if value is not None else ""


def _date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        for pattern in DATE_FORMATS:
            try:
                return datetime.strptime(value.strip(), pattern).date()
            except ValueError:
                continue
    return None


def _share(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(str(value).strip().rstrip("%")) if isinstance(value, str) else float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def weekly_results(sheets, today):
    """Dated bullish/neutral/bearish percentages from the first sheet that has them, plus the date column's label."""
    for rows in sheets:
        header = next((i for i, row in enumerate(rows[:50])
                       if all(k in [_text(v) for v in row] for k in ("bullish", "neutral", "bearish"))), None)
        if header is None:
            continue
        names = [_text(v) for v in rows[header]]
        columns = [names.index(k) for k in ("bullish", "neutral", "bearish")]
        date_column = next((i for i, name in enumerate(names) if "date" in name), 0)
        # AAII splits headings over rows ("Reported" above "Date"); read the whole stack.
        heading = " ".join(_text(row[date_column]) for row in rows[:header + 1] if len(row) > date_column)
        label = "Reported" if "reported" in heading else "Week ending" if "ending" in heading else "Dated"
        weeks = {}
        for row in rows[header + 1:]:
            cells = list(row) + [None] * (max(columns + [date_column]) + 1 - len(row))
            when = _date(cells[date_column])
            values = [_share(cells[c]) for c in columns]
            if when is None or not date(1987, 1, 1) <= when <= today or None in values:
                continue  # blank rows, summary rows (averages, counts) and future dates
            if max(values) <= 1:
                values = [100 * v for v in values]
            if min(values) >= 0 and abs(sum(values) - 100) <= 0.6:
                weeks[when] = values
        if weeks:
            return weeks, label
    raise ValueError("no dated Bullish / Neutral / Bearish columns found")


def reading(path, today):
    stat = path.stat()
    if stat.st_size > MAX_BYTES:
        raise ValueError("file is too large")
    weeks, label = weekly_results(read_sheets(path.read_bytes()), today)
    when = max(weeks)
    bull, neutral, bear = (round(v, 1) for v in weeks[when])
    averages = [sum(week[i] for week in weeks.values()) / len(weeks) for i in range(3)]
    item = summary(when, bull, neutral, bear, label)
    item["detail"] += (f" Long-run averages since {min(weeks)}: {averages[0]:.1f}% bullish / {averages[1]:.1f}% neutral / "
                       f"{averages[2]:.1f}% bearish.")
    return {**item, "status": "ok", "source": "AAII spreadsheet", "source_file": path.name, "date_label": label.lower(),
            "file_saved": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(timespec="seconds"),
            "signature": [path.name, stat.st_size, int(stat.st_mtime)], "weeks": len(weeks)}


def imported(today, saved=None, folders=None):
    """Newest week from saved AAII spreadsheets (or the previous import), with notes on unreadable files or folders."""
    files, notes = find_files(folders or import_folders())
    found = []
    for path in files[:MAX_FILES]:
        try:
            signature = [path.name, path.stat().st_size, int(path.stat().st_mtime)]
            if saved and saved.get("signature") == signature:
                found.append(saved)  # unchanged since the last import; older files cannot be newer
                break
            found.append(reading(path, today))
        except Exception as exc:  # a damaged or unexpected file must not stop the refresh
            notes.append(f"{path.name} could not be read: {exc}.")
    return max(found, key=lambda item: (item["as_of"], item["file_saved"]), default=saved), notes
