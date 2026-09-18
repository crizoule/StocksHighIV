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
    return conn


def scanned_symbols(conn: sqlite3.Connection, run_date: str) -> set[str]:
    return {row[0] for row in conn.execute(
        "SELECT symbol FROM scans WHERE run_date = ? AND status IN ('ok', 'no_iv')", (run_date,)
    )}


def record_scan(conn: sqlite3.Connection, run_date: str, symbol: str, source: str,
                result: dict | None, *, failed: bool = False) -> None:
    r = result or {}
    conn.execute(
        "INSERT OR REPLACE INTO scans "
        "(run_date, symbol, source, iv30, iv30_change, price, quote_date, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (run_date, symbol, source, r.get("iv30"), r.get("iv30_change"), r.get("price"), r.get("quote_date"),
         "error" if failed else "ok" if r.get("iv30") else "no_iv"),
    )
    if r.get("iv30") and r.get("quote_date"):
        conn.execute(
            "INSERT OR REPLACE INTO iv_history VALUES (?, ?, ?, ?)",
            (symbol, r["quote_date"], r["iv30"], source),
        )
    conn.commit()


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
