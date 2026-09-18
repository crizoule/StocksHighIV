"""Stock-borrow costs from Interactive Brokers' public shortability files.

One pipe-delimited file per region, refreshed through the trading day:

    #SYM|CUR|NAME|CON|ISIN|REBATERATE|FEERATE|AVAILABLE|FIGI

FEERATE is the annual cost of borrowing the stock, in percent — what a short actually pays to
stay short. AVAILABLE is the lendable share count, capped in the file as ">10000000".
"""
from __future__ import annotations

import urllib.request
from datetime import datetime, timezone

FTP_ROOT = "ftp://shortstock:@ftp2.interactivebrokers.com"
FILES = (("US", "usa.txt"), ("CA", "canada.txt"))


def _parse(text: str) -> dict[str, dict]:
    table: dict[str, dict] = {}
    for line in text.splitlines():
        if line.startswith("#") or "|" not in line:
            continue
        parts = line.split("|")
        if len(parts) < 8:
            continue
        try:
            fee = float(parts[6])
        except ValueError:
            continue
        available = parts[7].strip()
        try:
            shares = int(float(available.lstrip(">")))
        except ValueError:
            shares = None
        table[parts[0].strip().upper()] = {
            "fee": round(fee, 2),
            "available": shares,
            "capped": available.startswith(">"),
        }
    return table


def load() -> dict[tuple[str, str], dict]:
    """Borrow costs keyed by market and IBKR symbol; unavailable regions are skipped."""
    table: dict[tuple[str, str], dict] = {}
    for market, name in FILES:
        try:
            with urllib.request.urlopen(f"{FTP_ROOT}/{name}", timeout=90) as response:
                loans = _parse(response.read().decode("latin-1"))
                fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                table.update({(market, symbol): {**loan, "fetched_at": fetched_at}
                              for symbol, loan in loans.items()})
        except Exception:
            continue
    return table


def lookup(table: dict[tuple[str, str], dict], stock: dict) -> dict | None:
    """Class shares differ by region: US files write BRK B, Canadian files write BBD.B (from BBD-B.TO)."""
    symbol = stock["symbol"].upper()
    base = symbol.rsplit(".", 1)[0] if stock["market"] == "CA" else symbol
    variants = (
        base.replace(".", " ").replace("-", " "),   # BRK.B -> BRK B
        base.replace("-", "."),                     # BBD-B -> BBD.B
        base,
        base.replace(".", "").replace("-", ""),
    )
    for key in variants:
        found = table.get((stock["market"], key))
        if found:
            return found
    return None
