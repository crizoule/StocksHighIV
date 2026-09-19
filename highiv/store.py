"""SQLite store: resumable daily scan results and the IV history behind IV rank."""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
    run_date    TEXT NOT NULL,  -- calendar date of the run (America/Toronto)
    symbol      TEXT NOT NULL,  -- display symbol: NVDA, BRK.B, IVN.TO
    source      TEXT NOT NULL,  -- cboe | mx
    iv30        REAL,           -- NULL = checked, no usable option IV
    iv30_change REAL,
    price       REAL,
    quote_date  TEXT,           -- trading session the quote belongs to
    status      TEXT NOT NULL DEFAULT 'error', -- ok | no_iv | error | pending
    fetched_at  TEXT,           -- UTC time of the download; reused copies keep the original
    PRIMARY KEY (run_date, symbol)
);
CREATE TABLE IF NOT EXISTS iv_history (
    symbol TEXT NOT NULL,
    date   TEXT NOT NULL,
    iv30   REAL NOT NULL,
    source TEXT NOT NULL,       -- cboe | mx | alphaquery (bootstrap, scaled to CBOE)
    PRIMARY KEY (symbol, date, source)
);
"""

COLUMNS = ("symbol", "source", "iv30", "iv30_change", "price", "quote_date")


def connect() -> sqlite3.Connection:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.executescript(SCHEMA)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(scans)")}
    if "status" not in columns:
        # Old NULL results could be outages, so retry them once instead of marking them complete.
        conn.execute("ALTER TABLE scans ADD COLUMN status TEXT NOT NULL DEFAULT 'error'")
        conn.execute("UPDATE scans SET status = 'ok' WHERE iv30 IS NOT NULL")
        conn.commit()
    if "fetched_at" not in columns:
        conn.execute("ALTER TABLE scans ADD COLUMN fetched_at TEXT")  # older rows are never treated as final
        conn.commit()
    return conn


def scanned_symbols(conn: sqlite3.Connection, run_date: str) -> set[str]:
    return {row[0] for row in conn.execute(
        "SELECT symbol FROM scans WHERE run_date = ? AND status IN ('ok', 'no_iv')", (run_date,)
    )}


def record_scan(conn: sqlite3.Connection, run_date: str, symbol: str, source: str,
                result: dict | None, *, failed: bool = False, fetched_at: str | None = None) -> None:
    r = result or {}
    conn.execute(
        "INSERT OR REPLACE INTO scans "
        "(run_date, symbol, source, iv30, iv30_change, price, quote_date, status, fetched_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (run_date, symbol, source, r.get("iv30"), r.get("iv30_change"), r.get("price"), r.get("quote_date"),
         "error" if failed else "ok" if r.get("iv30") else "no_iv", fetched_at),
    )
    if r.get("iv30") and r.get("quote_date"):
        conn.execute(
            "INSERT OR REPLACE INTO iv_history VALUES (?, ?, ?, ?)",
            (symbol, r["quote_date"], r["iv30"], source),
        )
    conn.commit()


def reopen_unsettled(conn: sqlite3.Connection, run_date: str, settled: dict[str, str | None]) -> None:
    """Mark a run's quotes for download again, except those fetched after their market last closed."""
    conn.execute("UPDATE scans SET status = 'pending' WHERE run_date = ?", (run_date,))
    for source, since in settled.items():
        if since:
            conn.execute("UPDATE scans SET status = CASE WHEN iv30 IS NULL THEN 'no_iv' ELSE 'ok' END "
                         "WHERE run_date = ? AND source = ? AND fetched_at >= ? AND status = 'pending'", (run_date, source, since))
    conn.commit()


def reuse_settled(conn: sqlite3.Connection, run_date: str, symbols: dict[str, str], settled: dict[str, str | None]) -> set[str]:
    """Copy each symbol's newest quote fetched after its market last closed into this run; returns the symbols copied.

    `symbols` maps symbol to source; `settled` maps source to the earliest fetch time (UTC ISO) that is still final.
    """
    reused = set()
    for source, since in settled.items():
        wanted = [symbol for symbol, s in symbols.items() if s == source]
        if not since or not wanted:
            continue
        rows = conn.execute(
            "SELECT symbol, iv30, iv30_change, price, quote_date, status, fetched_at FROM scans "
            "WHERE source = ? AND status IN ('ok', 'no_iv') AND fetched_at >= ? AND run_date < ? "
            "ORDER BY fetched_at", (source, since, run_date)).fetchall()
        newest = {row[0]: row for row in rows}  # later fetches win
        copies = [(run_date, symbol, source, *newest[symbol][1:]) for symbol in wanted if symbol in newest]
        conn.executemany(
            "INSERT OR REPLACE INTO scans (run_date, symbol, source, iv30, iv30_change, price, quote_date, status, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", copies)
        reused.update(row[1] for row in copies)
    conn.commit()
    return reused


def previous_iv(conn: sqlite3.Connection, run_date: str) -> dict[str, float | None]:
    """Each symbol's IV30 from its latest completed check before this run; None when it had no usable IV."""
    rows = conn.execute(
        "SELECT symbol, iv30 FROM scans s WHERE run_date < ? AND status IN ('ok', 'no_iv') AND run_date = "
        "(SELECT MAX(run_date) FROM scans t WHERE t.symbol = s.symbol AND t.run_date < ? AND t.status IN ('ok', 'no_iv'))",
        (run_date, run_date))
    return dict(rows.fetchall())


def scan_results(conn: sqlite3.Connection, run_date: str) -> list[dict]:
    cur = conn.execute(
        f"SELECT {', '.join(COLUMNS)} FROM scans WHERE run_date = ? AND iv30 IS NOT NULL AND status = 'ok'", (run_date,)
    )
    return [dict(zip(COLUMNS, row)) for row in cur]


def latest_run_date(conn: sqlite3.Connection) -> str | None:
    return conn.execute("SELECT MAX(run_date) FROM scans").fetchone()[0]


def iv_series(conn: sqlite3.Connection, symbol: str) -> list[tuple[str, float]]:
    """Daily IV30 over the look-back, oldest first. Own snapshots win; bootstrap only fills earlier dates."""
    rows = conn.execute(
        "SELECT date, iv30, source FROM iv_history WHERE symbol = ? ORDER BY date", (symbol,)
    ).fetchall()
    if not rows:
        return []
    own = {d: v for d, v, s in rows if s != "alphaquery"}
    first_own = min(own) if own else None
    merged = {d: v for d, v, s in rows if s == "alphaquery" and (first_own is None or d < first_own)}
    merged.update(own)
    latest = max(merged)
    since = (date.fromisoformat(latest) - timedelta(days=config.IV_LOOKBACK_DAYS)).isoformat()
    return sorted((d, v) for d, v in merged.items() if d >= since)


def has_source(conn: sqlite3.Connection, symbol: str, source: str) -> bool:
    row = conn.execute("SELECT 1 FROM iv_history WHERE symbol = ? AND source = ? LIMIT 1", (symbol, source))
    return row.fetchone() is not None


def insert_history(conn: sqlite3.Connection, symbol: str, points: list[tuple[str, float]], source: str) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO iv_history VALUES (?, ?, ?, ?)",
        [(symbol, d, v, source) for d, v in points],
    )
    conn.commit()
