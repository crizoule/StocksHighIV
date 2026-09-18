"""Offline regression coverage. Run with python -m unittest discover -v."""
from contextlib import ExitStack, closing
from datetime import date, datetime, timezone
from io import BytesIO
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import httpx

from highiv import borrow, config, details, iv, net, prices, report, scan, store
from highiv.__main__ import main


RUN_DATE = "2026-09-18"
QUOTE = {"iv30": 40.0, "iv30_change": 2.0, "price": 10.0, "quote_date": RUN_DATE}
STOCK = {
    "symbol": "TEST", "market": "US", "cboe": "TEST", "mx": None,
    "yahoo": "TEST", "name": "Test Company", "exchange": "NASDAQ",
    "hq_country": "United States", "market_cap_usd": 2e9,
    "sector": "Technology", "also_listed": None,
}


class TempProjectTest(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.stack.enter_context(patch("highiv.logos.fetch", return_value=None))
        self.stack.enter_context(patch("highiv.sentiment.collect", return_value={}))
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(TemporaryDirectory()))
        for name, value in {
            "DATA_DIR": self.root, "DB_PATH": self.root / "test.sqlite",
            "SNAPSHOT_DIR": self.root / "snapshots", "OUTPUT_DIR": self.root / "output",
        }.items():
            self.stack.enter_context(patch.object(config, name, value))


class PriceTests(TempProjectTest):
    def test_provider_failure_returns_unpackable_pair(self):
        with patch.object(prices.yf, "Ticker") as ticker:
            ticker.return_value.history.side_effect = RuntimeError("provider unavailable")
            frames, stats = prices.fetch("TEST")
        self.assertIsNone(frames)
        self.assertEqual(stats, {})

    def test_build_still_writes_dashboard_when_prices_fail(self):
        (self.root / "universe.json").write_text(json.dumps({"run_date": RUN_DATE, "stocks": [STOCK]}))
        with closing(store.connect()) as conn:
            store.record_scan(conn, RUN_DATE, "TEST", "cboe", QUOTE)
        with patch.object(details, "_info", return_value={
            "quoteType": "EQUITY", "marketCap": 2e9, "country": "United States",
        }), patch.object(details.time, "sleep"):
            info = details.fetch("TEST")
        with patch.object(prices.yf, "Ticker") as ticker, \
                patch.object(details, "fetch", return_value=info), \
                patch.object(borrow, "load", return_value={}), \
                patch.object(report, "_bootstrap_history"), \
                patch.object(report.news, "fetch", return_value=[]), \
                patch.object(report, "log"):
            ticker.return_value.history.side_effect = RuntimeError("provider unavailable")
            path = report.build(RUN_DATE)
        self.assertTrue(path.exists())
        payload = json.loads((config.SNAPSHOT_DIR / f"{RUN_DATE}.json").read_text())
        self.assertEqual(len(payload["rows"]), 1)
        self.assertIsNone(payload["rows"][0]["prices"])
        self.assertIsNone(payload["rows"][0]["hv30"])


class BorrowTests(unittest.TestCase):
    def test_same_ticker_in_different_markets_stays_separate(self):
        usa = b"H|USD|Hyatt|1|US1|0|1.5|1000|FIGI1\nBRK B|USD|Berkshire|3|US3|0|2.5|3000|FIGI3\n"
        canada = b"H|CAD|Hydro One|2|CA2|0|9.5|2000|FIGI2\nBBD.B|CAD|Bombardier|4|CA4|0|3.5|>10000000|FIGI4\n"
        with patch.object(borrow.urllib.request, "urlopen", side_effect=[BytesIO(usa), BytesIO(canada)]):
            table = borrow.load()
        for symbol, market, expected in (("H", "US", 1.5), ("H.TO", "CA", 9.5),
                                         ("BRK.B", "US", 2.5), ("BBD-B.TO", "CA", 3.5)):
            with self.subTest(symbol=symbol):
                self.assertEqual(borrow.lookup(table, {"symbol": symbol, "market": market})["fee"], expected)
        self.assertTrue(borrow.lookup(table, {"symbol": "BBD-B.TO", "market": "CA"})["capped"])

    def test_failed_region_does_not_use_other_markets_ticker(self):
        canada = b"H|CAD|Hydro One|2|CA2|0|9.5|2000|FIGI2\n"
        with patch.object(borrow.urllib.request, "urlopen", side_effect=[OSError("offline"), BytesIO(canada)]):
            table = borrow.load()
        self.assertIsNone(borrow.lookup(table, {"symbol": "H", "market": "US"}))
        self.assertEqual(borrow.lookup(table, {"symbol": "H.TO", "market": "CA"})["fee"], 9.5)


class ScanTests(TempProjectTest):
    def setUp(self):
        super().setUp()
        self.stack.enter_context(patch.object(scan, "load_universe", return_value=[STOCK]))
        self.stack.enter_context(patch.object(scan, "log"))
        self.fetch = self.stack.enter_context(patch.object(iv, "cboe_iv30"))

    def test_failed_quote_is_retried_and_success_is_resumed(self):
        self.fetch.side_effect = [net.FetchError("offline"), QUOTE]
        scan.scan(RUN_DATE)
        with closing(store.connect()) as conn:
            self.assertEqual(store.scanned_symbols(conn, RUN_DATE), set())
            self.assertEqual(store.scan_results(conn, RUN_DATE), [])
        scan.scan(RUN_DATE)
        scan.scan(RUN_DATE)
        self.assertEqual(self.fetch.call_count, 2)
        with closing(store.connect()) as conn:
            self.assertEqual(store.scan_results(conn, RUN_DATE)[0]["iv30"], 40)

    def test_valid_no_iv_response_is_not_retried(self):
        self.fetch.return_value = None
        scan.scan(RUN_DATE)
        scan.scan(RUN_DATE)
        self.assertEqual(self.fetch.call_count, 1)

    def test_refresh_replaces_quote_and_same_session_history(self):
        self.fetch.side_effect = [QUOTE, {**QUOTE, "iv30": 80}]
        scan.scan(RUN_DATE)
        scan.scan(RUN_DATE, refresh_universe=True)
        self.assertEqual(self.fetch.call_count, 1)
        scan.scan(RUN_DATE, refresh_quotes=True)
        with closing(store.connect()) as conn:
            self.assertEqual(store.scan_results(conn, RUN_DATE)[0]["iv30"], 80)
            self.assertEqual(store.iv_series(conn, "TEST"), [(RUN_DATE, 80)])

    def test_malformed_quote_does_not_abort_remaining_symbols(self):
        self.fetch.side_effect = [ValueError("invalid payload"), QUOTE]
        with patch.object(scan, "load_universe", return_value=[STOCK, {**STOCK, "symbol": "OTHER"}]):
            scan.scan(RUN_DATE)
        with closing(store.connect()) as conn:
            self.assertEqual(store.scanned_symbols(conn, RUN_DATE), {"OTHER"})

    def test_interrupted_same_day_refresh_resumes_pending_old_quotes(self):
        with closing(store.connect()) as conn:
            for symbol in ('TEST', 'OTHER'):
                store.record_scan(conn, RUN_DATE, symbol, 'cboe', QUOTE)
        self.fetch.side_effect = [{**QUOTE, 'iv30': 80}, KeyboardInterrupt(), {**QUOTE, 'iv30': 90}]
        with patch.object(scan, 'load_universe', return_value=[STOCK, {**STOCK, 'symbol': 'OTHER'}]):
            with self.assertRaises(KeyboardInterrupt):
                scan.scan(RUN_DATE, refresh_quotes=True)
            with closing(store.connect()) as conn:
                self.assertEqual(store.scanned_symbols(conn, RUN_DATE), {'TEST'})
                self.assertEqual(len(store.scan_results(conn, RUN_DATE)), 1)
            scan.scan(RUN_DATE)
        self.assertEqual(self.fetch.call_count, 3)
        with closing(store.connect()) as conn:
            self.assertEqual({r['symbol']: r['iv30'] for r in store.scan_results(conn, RUN_DATE)}, {'TEST': 80, 'OTHER': 90})

    def test_existing_database_migration_preserves_history_and_retries_ambiguous_nulls(self):
        with closing(sqlite3.connect(config.DB_PATH)) as conn:
            conn.execute("CREATE TABLE scans (run_date TEXT, symbol TEXT, source TEXT, iv30 REAL, "
                         "iv30_change REAL, price REAL, quote_date TEXT, PRIMARY KEY(run_date, symbol))")
            conn.executemany("INSERT INTO scans VALUES (?, ?, ?, ?, ?, ?, ?)", [
                (RUN_DATE, "GOOD", "cboe", 40, 2, 10, RUN_DATE),
                (RUN_DATE, "MISSING", "cboe", None, None, None, None),
            ])
            conn.execute("CREATE TABLE iv_history (symbol TEXT, date TEXT, iv30 REAL, source TEXT, "
                         "PRIMARY KEY(symbol, date, source))")
            conn.execute("INSERT INTO iv_history VALUES ('GOOD', ?, 40, 'cboe')", (RUN_DATE,))
            conn.commit()
        for _ in range(2):
            with closing(store.connect()) as conn:
                self.assertEqual(store.scanned_symbols(conn, RUN_DATE), {"GOOD"})
                self.assertEqual(store.iv_series(conn, "GOOD"), [(RUN_DATE, 40)])
                self.assertEqual(conn.execute("SELECT count(*) FROM scans").fetchone()[0], 2)

    def test_cli_passes_refresh_options_for_scan_and_run(self):
        with patch.object(scan, "scan") as scanner, patch.object(report, "build") as build:
            main(["scan", "--refresh-quotes"])
            scanner.assert_called_once_with(refresh_universe=False, refresh_quotes=True)
            build.assert_not_called()
            scanner.reset_mock()
            main(["run", "--refresh-universe", "--refresh-quotes"])
            scanner.assert_called_once_with(refresh_universe=True, refresh_quotes=True)
            build.assert_called_once_with()


class ProviderTests(unittest.TestCase):
    def test_transport_and_http_failures_are_retryable(self):
        for fetch in (iv.cboe_iv30, iv.mx_iv30):
            for response in (None, httpx.Response(429), httpx.Response(503), httpx.Response(403)):
                with self.subTest(provider=fetch.__name__, response=response), \
                        patch.object(net, "get", return_value=response):
                    with self.assertRaises(net.FetchError):
                        fetch(None, None, "TEST")

    def test_absent_quotes_are_valid_no_iv_results(self):
        for fetch in (iv.cboe_iv30, iv.mx_iv30):
            with patch.object(net, "get", return_value=httpx.Response(404)):
                self.assertIsNone(fetch(None, None, "TEST"))
        with patch.object(net, "get", return_value=httpx.Response(200, json={"data": {"iv30": 0}})):
            self.assertIsNone(iv.cboe_iv30(None, None, "TEST"))

    def test_invalid_cboe_json_is_retryable(self):
        with patch.object(net, "get", return_value=httpx.Response(200, text="invalid")):
            with self.assertRaises(net.FetchError):
                iv.cboe_iv30(None, None, "TEST")


class RetryTests(unittest.TestCase):
    def test_retry_after_formats_and_invalid_values(self):
        now = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
        cases = [("5", 6), ("Fri, 18 Sep 2026 12:00:20 GMT", 21),
                 ("Fri, 18 Sep 2026 11:59:00 GMT", 1), ("-3", 1),
                 (None, 61), ("invalid", 61), ("NaN", 61), ("Infinity", 61)]
        for header, expected in cases:
            with self.subTest(header=header), patch.object(net, "datetime") as clock, \
                    patch.object(net.time, "sleep") as sleep:
                clock.now.return_value = now
                responses = iter([httpx.Response(429, headers={} if header is None else {"Retry-After": header}),
                                  httpx.Response(200, text="ok")])
                with httpx.Client(transport=httpx.MockTransport(lambda request: next(responses))) as client:
                    self.assertEqual(net.get(client, "https://test.invalid").status_code, 200)
                sleep.assert_called_once_with(expected)

    def test_retry_after_with_limiter(self):
        with patch.object(net.time, "sleep") as sleep:
            responses = iter([httpx.Response(429, headers={"Retry-After": "2"}), httpx.Response(200)])
            with httpx.Client(transport=httpx.MockTransport(lambda request: next(responses))) as client:
                self.assertEqual(net.get(client, "https://test.invalid", net.RateLimiter()).status_code, 200)
            sleep.assert_called_once_with(3)


class EarningsTests(unittest.TestCase):
    def test_unknown_estimated_and_confirmed_earnings(self):
        stamp = int(datetime(2026, 9, 22, 17, tzinfo=timezone.utc).timestamp())
        for flag in (None, True, False, "false"):
            with self.subTest(flag=flag), patch.object(details, "_info", return_value={
                "quoteType": "EQUITY", "earningsTimestamp": stamp,
                **({"isEarningsDateEstimate": flag} if flag is not None else {}),
            }), patch.object(details.time, "sleep"):
                row = report._earnings(details.fetch("TEST"), date(2026, 9, 18))
                self.assertEqual(row["next_earnings"], "2026-09-22")
                self.assertIs(row["earnings_estimated"], flag if isinstance(flag, bool) else None)
                if flag is False:
                    self.assertEqual(row["earnings_time"], "1:00 pm")
                    self.assertEqual(row["earnings_session"], "during session")
                else:
                    self.assertIsNone(row["earnings_time"])
                    self.assertIsNone(row["earnings_session"])


if __name__ == "__main__":
    unittest.main()
