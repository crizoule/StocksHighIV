"""Command line: python -m highiv {scan,build,run,explain}"""
from __future__ import annotations

import argparse


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="highiv", description="Daily high-IV stock screener (US + Canada).")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("scan", "Build the universe and fetch 30-day IV for every stock (resumable)."),
        ("build", "Rank the latest scan, add float/short data and write the dashboard."),
        ("run", "scan, then build."),
        ("explain", "Re-read the latest snapshot, fetch news, and rewrite the Why this IV column."),
        ("sentiment", "Refresh macro and stock sentiment on the saved dashboard without an IV scan."),
    ):
        cmd = sub.add_parser(name, help=help_text)
        if name not in ("build", "explain", "sentiment"):
            cmd.add_argument("--refresh-universe", action="store_true", help="Rebuild today's stock list.")
            cmd.add_argument("--refresh-quotes", action="store_true", help="Fetch all quotes again, including today's completed symbols.")
    args = parser.parse_args(argv)

    if args.command in ("scan", "run"):
        from .scan import scan
        scan(refresh_universe=args.refresh_universe, refresh_quotes=args.refresh_quotes)
    if args.command in ("build", "run"):
        from .report import build
        build()
    if args.command == "explain":
        from .report import explain_latest
        explain_latest()
    if args.command == "sentiment":
        from .report import sentiment_latest
        sentiment_latest()


if __name__ == "__main__":
    main()
