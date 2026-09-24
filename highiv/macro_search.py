"""Separate Google search-attention context; never an input to sentiment scores."""
from __future__ import annotations

import calendar
import json
import math
import os
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from urllib.parse import urlencode

from . import config, progress

TERMS = (
    ("recession", "Economic anxiety"), ("layoffs", "Economic anxiety"),
    ("inflation", "Cost of living"), ("bank failure", "Financial stress"),
    ("stock market crash", "Financial stress"), ("war", "Geopolitical concerns"),
)
MAX_AGE_DAYS = 4
HISTORY_MAX_AGE_DAYS = 14
VIEWS = {"recent": ("today 3-m", "terms"), "historical": ("all", "historical_terms")}
REQUEST_GAP = 20
RUN_BUDGET = 240
FETCH_TIMEOUT = 75


def stamp(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Missing timestamp timezone")
    return parsed.astimezone(timezone.utc)


def strict_multiline(data):
    """Pinned trendspyg adapter: reject malformed upstream data before it can become zero."""
    entries = data.get("default", {}).get("timelineData")
    if not isinstance(entries, list) or not entries:
        raise ValueError("Google returned no search history")
    points = []
    for entry in entries:
        values = entry.get("value")
        if not isinstance(values, list) or len(values) != 1:
            raise ValueError("Unexpected search-value shape")
        value = values[0]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 100:
            raise ValueError("Invalid search value")
        partial = entry.get("isPartial", False)
        if not isinstance(partial, bool):
            raise ValueError("Invalid partial-period flag")
        raw_time = entry.get("time")
        if isinstance(raw_time, bool) or not str(raw_time).isdigit():
            raise ValueError("Invalid search timestamp")
        when = datetime.fromtimestamp(int(raw_time), timezone.utc)
        if when.hour or when.minute or when.second:
            raise ValueError("Expected UTC observations at midnight")
        if "hasData" in entry and entry["hasData"] != [True]:
            raise ValueError("Google marked a search observation as missing")
        points.append({"date": when.isoformat(), "value": value, "is_partial": partial})
    return points


def analyze(envelope, now):
    """Use 7 complete days against the preceding 56; never compare raw scales across terms."""
    if envelope.get("geo") != "US" or envelope.get("timeframe") != "today 3-m":
        raise ValueError("Expected USA-only recent search data")
    fetched = stamp(envelope["fetched_at"])
    if fetched > now + timedelta(minutes=5):
        raise ValueError("Future retrieval date")
    points, previous = [], None
    for point in envelope["interest_over_time"]:
        when = stamp(point["date"])
        value, partial = point["value"], point["is_partial"]
        if when > now or when.hour or when.minute or when.second or (previous and when - previous != timedelta(days=1)):
            raise ValueError("Expected consecutive, chronological daily observations")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 100 or not isinstance(partial, bool):
            raise ValueError("Invalid search observation")
        previous = when
        if not partial and when.date() < now.date():
            points.append([when.date().isoformat(), value])
    if len(points) < 63:
        raise ValueError("Fewer than 63 complete daily observations")
    # A partial observation in the middle must not let statistics bridge a gap.
    window = points[-63:]
    if (datetime.fromisoformat(window[-1][0]) - datetime.fromisoformat(window[0][0])).days != 62:
        raise ValueError("Incomplete comparison window")
    baseline = [p[1] for p in window[:56]]
    recent = [p[1] for p in window[-7:]]
    prior = [p[1] for p in window[-14:-7]]
    result = {"as_of": points[-1][0], "fetched_at": envelope["fetched_at"], "points": points,
              "status": "ok", "ratio": None, "change_pct": None, "signal": "Limited data"}
    # Zero can mean insufficient volume; do not classify a sparse series as calm.
    if sum(v > 0 for v in baseline) < 45 or sum(v > 0 for v in recent) < 6 or mean(baseline) < 1:
        return result
    ratio = mean(recent) / mean(baseline)
    change = (mean(recent) / mean(prior) - 1) * 100 if mean(prior) >= 1 else None
    return {**result, "ratio": round(ratio, 3), "change_pct": round(change, 1) if change is not None else None,
            "signal": "Elevated" if ratio >= 1.5 else "Below baseline" if ratio <= .75 else "Near baseline"}


def analyze_history(envelope, now):
    """Compare the latest complete month with earlier complete months on one scale."""
    if envelope.get("geo") != "US" or envelope.get("timeframe") != "all":
        raise ValueError("Expected USA-only full-history search data")
    if stamp(envelope["fetched_at"]) > now + timedelta(minutes=5):
        raise ValueError("Future retrieval date")
    points, previous = [], None
    current_month = now.year * 12 + now.month
    for point in envelope["interest_over_time"]:
        when = stamp(point["date"])
        month = when.year * 12 + when.month
        value, partial = point["value"], point["is_partial"]
        if when.year < 2004 or month > current_month or when.day != 1 or when.hour or when.minute or when.second or (previous is not None and month != previous + 1):
            raise ValueError("Expected consecutive monthly observations since 2004")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 100 or not isinstance(partial, bool):
            raise ValueError("Invalid monthly search observation")
        previous = month
        if not partial and month < current_month:
            points.append([when.date().isoformat(), value])
    if len(points) < 25:
        raise ValueError("Fewer than 25 complete months")
    first = datetime.fromisoformat(points[0][0])
    latest = datetime.fromisoformat(points[-1][0])
    if (latest.year - first.year) * 12 + latest.month - first.month != len(points) - 1:
        raise ValueError("Incomplete historical month inside the comparison window")
    as_of = latest.replace(day=calendar.monthrange(latest.year, latest.month)[1]).date().isoformat()
    baseline, value = [p[1] for p in points[:-1]], points[-1][1]
    result = {"status": "ok", "signal": "Limited data", "percentile": None, "points": points,
              "as_of": as_of, "period": points[-1][0][:7], "history_start": points[0][0],
              "fetched_at": envelope["fetched_at"], "geo": "US", "frequency": "monthly"}
    if value == 0 or sum(v > 0 for v in baseline) < .8 * len(baseline):
        return result
    percentile = 100 * (sum(v < value for v in baseline) + .5 * sum(v == value for v in baseline)) / len(baseline)
    return {**result, "percentile": round(percentile, 1),
            "signal": "Elevated" if percentile >= 80 else "Lower historical interest" if percentile <= 20 else "Typical range"}


def read_json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(value, handle, allow_nan=False)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def fetch(term, folder, timeout=FETCH_TIMEOUT, view="recent"):
    """Chrome and driver startup are bounded too; kill only this worker's process group."""
    result = folder / "worker-result.json"
    result.unlink(missing_ok=True)
    process = subprocess.Popen([sys.executable, "-m", "highiv.macro_search", term, str(folder), view],
                               cwd=config.ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               start_new_session=os.name != "nt")
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=10)
        else:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=10)
        return {"error": "Search request timed out; the previous reading is retained.", "stop": True}
    return read_json(result) or {"error": "Search collector could not start. Check the installed dependencies and Chrome.", "stop": True}


def collect(data_dir=None, now=None):
    live_clock = now is None
    now = now or datetime.now(timezone.utc)
    folder = Path(data_dir or config.DATA_DIR) / "macro_search"
    folder.mkdir(parents=True, exist_ok=True)
    path, lock = folder / "state.json", folder / "collecting"
    state = read_json(path)
    # Protect browser requests and the cache from concurrent command-line/app refreshes.
    try:
        if lock.exists() and time.time() - lock.stat().st_mtime > 900:
            lock.rmdir()
        lock.mkdir()
    except OSError:
        return state
    try:
        if state.get("paused_until") and stamp(state["paused_until"]) > now:
            return state
        started, last_request = time.monotonic(), None
        jobs = [(view, term) for view in VIEWS for term, _ in TERMS]
        for view, term in jobs:
            entries = state.setdefault(VIEWS[view][1], {})
            entry = entries.setdefault(term, {})
            if entry.get("attempted_at") and now - stamp(entry["attempted_at"]) < timedelta(hours=24):
                continue
            if (view == "historical" and entry.get("envelope")
                    and now - stamp(entry["envelope"]["fetched_at"]) < timedelta(days=7)
                    and stamp(entry["envelope"]["fetched_at"]).strftime("%Y-%m") == now.strftime("%Y-%m")):
                continue
            remaining = RUN_BUDGET - (time.monotonic() - started)
            if remaining < REQUEST_GAP + 15:
                break
            if last_request is not None:
                time.sleep(max(0, REQUEST_GAP - (time.monotonic() - last_request)))
            entry["attempted_at"] = (datetime.now(timezone.utc) if live_clock else now).isoformat()
            save_json(path, state)  # interrupted requests cannot cause a retry burst
            progress.emit(phase="sentiment", activity=f"Macro search concerns · {view}: {term} (USA)")
            result = fetch(term, folder, timeout=min(FETCH_TIMEOUT, RUN_BUDGET - (time.monotonic() - started)), view=view)
            last_request = time.monotonic()
            if "envelope" in result:
                try:
                    envelope = result["envelope"]
                    if envelope.get("keyword") != term:
                        raise ValueError("Search term does not match the request")
                    analyzer = analyze_history if view == "historical" else analyze
                    analyzer(envelope, datetime.now(timezone.utc) if live_clock else now)
                    entry["envelope"] = envelope
                    entry.pop("error", None)
                    save_json(folder / "history" / f"{now.date()}-{view}-{term.replace(' ', '-')}.json", envelope)
                except (ValueError, KeyError, TypeError, OverflowError) as exc:
                    entry["error"] = f"Search data could not be validated: {exc}"
            else:
                entry["error"] = result.get("error", "No search data returned")
            if result.get("stop"):
                state["paused_until"] = (now + timedelta(hours=24)).isoformat()
            state["checked_at"] = (datetime.now(timezone.utc) if live_clock else now).isoformat()
            save_json(path, state)
            if result.get("stop"):
                break
        cutoff = (now - timedelta(days=90)).date().isoformat()
        for old in (folder / "history").glob("*.json"):
            if old.name[:10] < cutoff:
                old.unlink(missing_ok=True)
        return state
    finally:
        lock.rmdir()


def evaluate(state, now=None):
    now = now or datetime.now(timezone.utc)
    views = {}
    for view, (timeframe, state_key) in VIEWS.items():
        cards = []
        for term, theme in TERMS:
            card = {"term": term, "theme": theme, "geo": "US", "status": "unavailable", "signal": "Awaiting data", "points": [],
                    "url": "https://trends.google.com/trends/explore?" + urlencode({"q": term, "geo": "US", "date": timeframe})}
            entry = state.get(state_key, {}).get(term, {})
            if entry.get("envelope"):
                try:
                    if entry["envelope"].get("keyword") != term:
                        raise ValueError("Search term mismatch")
                    card.update((analyze_history if view == "historical" else analyze)(entry["envelope"], now))
                    if view == "historical":
                        expected_month_end = now.date().replace(day=1) - timedelta(days=1)
                        stale = card["as_of"] < expected_month_end.isoformat() or now - stamp(card["fetched_at"]) > timedelta(days=HISTORY_MAX_AGE_DAYS)
                    else:
                        stale = (now.date() - datetime.fromisoformat(card["as_of"]).date()).days > MAX_AGE_DAYS or now - stamp(card["fetched_at"]) > timedelta(days=MAX_AGE_DAYS)
                    if stale:
                        card.update(status="stale", signal="Stale")
                except (ValueError, KeyError, TypeError, OverflowError):
                    card["signal"] = "Invalid saved data"
            card["attempted_at"] = entry.get("attempted_at")
            if entry.get("error"):
                card["error"] = entry["error"]
                if card["status"] == "unavailable":
                    card["signal"] = "Unavailable"
                    card["error"] = card["error"].replace("the previous reading is retained", "no saved reading is available")
            cards.append(card)
        views[view] = cards
    return {"geo": "US", "checked_at": state.get("checked_at"), "paused_until": state.get("paused_until"),
            "cards": views["recent"], "historical_cards": views["historical"]}


def enrich(payload, refresh=True):
    try:
        state = collect() if refresh else read_json(config.DATA_DIR / "macro_search" / "state.json")
        payload["macro_search"] = evaluate(state)
    except (OSError, ValueError, TypeError, subprocess.SubprocessError) as exc:
        payload["macro_search"] = evaluate(read_json(config.DATA_DIR / "macro_search" / "state.json"))
        payload["macro_search"]["error"] = f"Search collection unavailable: {exc}"


def worker(term, folder, view="recent"):
    folder = Path(folder)
    os.environ["SE_CACHE_PATH"] = str(folder / "selenium")
    os.environ["TRENDSPYG_COOKIES"] = str(folder / "cookies.json")
    try:
        import trendspyg
        from trendspyg.explore import _engine
        # This runs only in the isolated worker; the dependency is pinned to this adapter.
        _engine._parse_multiline = strict_multiline
        envelope = trendspyg.download_google_trends_explore(
            term, geo="US", timeframe=VIEWS[view][0], include_related=False, include_geo=False,
            max_retries=1, retry_wait=6, cookies="disk")
        result = {"envelope": envelope}
    except Exception as exc:
        stop = type(exc).__name__ in ("RateLimitError", "BrowserError", "ModuleNotFoundError", "ImportError")
        message = "Google paused search access; collection will wait 24 hours." if type(exc).__name__ == "RateLimitError" else (
            "Search collection needs Chrome and the project's trendspyg dependency." if type(exc).__name__ in ("ModuleNotFoundError", "ImportError") else
            "Search history could not be retrieved or validated. A saved reading is shown when available.")
        result = {"error": message, "stop": stop}
    save_json(folder / "worker-result.json", result)


if __name__ == "__main__":
    worker(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "recent")
