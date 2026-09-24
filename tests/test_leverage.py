"""Synthetic leverage releases; never assertions about actual market levels."""
import io
import json
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import openpyxl
import pandas as pd

from highiv import leverage as lv

NOW = datetime(2026, 9, 24, 15, tzinfo=timezone.utc)


def finra_file(months):
    """FINRA's layout: a heading row, then newest month first, values in US$ millions."""
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(["Year-Month", "Debit Balances in Customers' Securities Margin Accounts",
                  "Free Credit Balances in Customers' Cash Accounts", "Free Credit Balances in Customers' Securities Margin Accounts"])
    for month, debit, cash, margin in reversed(months):
        sheet.append([month, debit, cash, margin])
    data = io.BytesIO()
    book.save(data)
    return data.getvalue()


class DateTests(unittest.TestCase):
    def test_months_clamp_to_the_last_day(self):
        self.assertEqual(lv.add_months(date(2026, 1, 31), 1), date(2026, 2, 28))
        self.assertEqual(lv.add_months(date(2026, 11, 30), 3), date(2027, 2, 28))
        self.assertEqual(lv.add_months(date(2026, 3, 31), -12), date(2025, 3, 31))
        self.assertEqual(lv.month_end(date(2024, 2, 1)), date(2024, 2, 29))

    def test_percentile_ranks_the_latest_against_earlier_readings_only(self):
        self.assertEqual(lv.percentile(list(range(20)) + [100], 100), 100)
        self.assertEqual(lv.percentile(list(range(20)) + [-1], -1), 0)
        self.assertIsNone(lv.percentile([1, 2, 3], 3))  # too little history to rank
        self.assertEqual([lv.tone(r) for r in (80, 50, 20, None)], ["high", "normal", "low", None])


class FinraTests(unittest.TestCase):
    def test_margin_debt_charts_its_twelve_month_change_and_estimates_the_next_posting(self):
        months = [(f"{2023 + i // 12}-{i % 12 + 1:02d}", 500_000 + 1_000 * i, 100_000, 50_000) for i in range(30)]
        months[-1] = ("2025-06", 900_000, 100_000, 50_000)  # a jump: fastest growth and a record
        item = lv.parse_finra(finra_file(months), date(2025, 7, 16))
        self.assertEqual((item["as_of"], item["period"], item["reading"]), ("2025-06-30", "June 2025", "$900B"))
        self.assertEqual((item["tone"], item["signal"]), ("high", "Rapid build-up"))
        self.assertEqual((item["released"], item["next"], item["next_basis"]), ("2025-07-16", "2025-08-16", "estimated"))
        self.assertIn("Record high (Jun 2025)", item["lines"])
        self.assertIn("Net of investors' free credit balances: $750B", item["lines"])
        points = item["series"]["finra"]["points"]
        self.assertEqual(len(points), 18)  # a 12-month change needs the same month a year earlier
        self.assertEqual(points[0], ["2024-01-31", round((512_000 / 500_000 - 1) * 100, 1)])

    def test_without_a_file_date_the_next_posting_follows_the_data_month(self):
        months = [(f"{2023 + i // 12}-{i % 12 + 1:02d}", 500_000 + 1_000 * i, None, None) for i in range(30)]
        item = lv.parse_finra(finra_file(months), None)
        self.assertEqual((item["released"], item["next"]), (None, "2025-08-20"))  # June's data: July's posting, then August's
        self.assertFalse(any("free credit" in line for line in item["lines"]))

    def test_unrecognized_file_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unrecognized FINRA"):
            lv.parse_finra(finra_file([("2025-06", 1, 1, 1)]), None)


def ofr_payload(quarters=20, last_update="2026-03-05 12:52:35"):
    days = [lv.add_months(date(2021, 3, 31), 3 * i) for i in range(quarters)]
    days = [lv.month_end(d) for d in days]
    series = lambda values, update=last_update: {"timeseries": {"aggregation": [[d.isoformat(), v] for d, v in zip(days, values)]},
                                                 "metadata": {"schedule": {"last_update": update}}}
    nav = [100.0] * quarters
    return {
        "FPF-ALLQHF_GAV_SUM": series([200.0 + i for i in range(quarters)]),
        "FPF-ALLQHF_NAV_SUM": series(nav),
        "FPF-ALLQHF_GNE_SUM": series([600.0 + 10 * i for i in range(quarters - 1)] + [None]),  # a withheld value
        "FPF-STRATEGY_EQUITY_LEVERAGERATIO_GAVWMEAN": series([2.5] * quarters),
        "SCOOS-NET_HF_LEVERAGEUSE": series([0.0] * (quarters - 1) + [12.5]),
    }, days


class OfrTests(unittest.TestCase):
    def test_hedge_fund_leverage_and_the_next_quarter_after_the_same_delay(self):
        data, days = ofr_payload()
        item = lv.parse_ofr(data)
        self.assertEqual((item["as_of"], item["period"], item["reading"], item["tone"]), ("2025-12-31", "Q4 2025", "2.19×", "high"))
        self.assertEqual(item["released"], "2026-03-05")
        # Q4 2025 came out 64 days after the quarter closed, so Q1 2026 is due 64 days after March 31.
        self.assertEqual((item["next"], item["next_basis"]), ("2026-06-03", "estimated"))
        self.assertEqual(len(item["series"]["ofr"]["points"]), 20)
        self.assertEqual(len(item["series"]["ofr_gne"]["points"]), 19)  # blank values stay blank, never zero
        self.assertIn("Equity-strategy funds 2.50×", item["lines"])
        self.assertIn("Dealer survey, Q4 2025: net +12% report hedge funds using more leverage", item["lines"])
        self.assertFalse(any("including derivatives" in line for line in item["lines"]))  # latest quarter withheld

    def test_an_even_dealer_survey_is_not_signed(self):
        data, _ = ofr_payload()
        data["SCOOS-NET_HF_LEVERAGEUSE"]["timeseries"]["aggregation"][-1][1] = 0.0
        self.assertIn("Dealer survey, Q4 2025: as many dealers report hedge funds using more leverage as less", lv.parse_ofr(data)["lines"])

    def test_short_history_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Too little OFR history"):
            lv.parse_ofr(ofr_payload(quarters=5)[0])


def fred_csv(series, values, start=date(2015, 1, 1)):
    rows = [f"observation_date,{series}"]
    for i, value in enumerate(values):
        rows.append(f"{lv.add_months(start, 3 * i).isoformat()},{value}")
    return "\n".join(rows) + "\n"


class Z1Tests(unittest.TestCase):
    PAGE = ("<html><body><p>Release Date: September 11, 2026</p><script>var d = 'Release Date: January 1, 2020';</script>"
            "<p>The next Z.1 release will be Thursday, December 10, 2026, at 12:00 noon.</p></body></html>")

    def test_the_schedule_comes_from_the_fed_page(self):
        self.assertEqual(lv.parse_z1_page(self.PAGE), (date(2026, 9, 11), date(2026, 12, 10)))
        self.assertEqual(lv.parse_z1_page("<p>No dates</p>"), (None, None))

    def test_margin_loans_against_stock_value_with_quarter_end_dates(self):
        margin = lv.parse_fred(fred_csv("M", [100 + i for i in range(45)] + ["."]))  # FRED marks a missing quarter with '.'
        equities = lv.parse_fred(fred_csv("E", [10_000] * 46))
        self.assertEqual(min(margin), date(2015, 3, 31))  # quarters are dated by their first day on FRED
        self.assertNotIn(date(2026, 6, 30), margin)
        item = lv.parse_z1(margin, equities, date(2026, 9, 11), date(2026, 12, 10))
        self.assertEqual((item["as_of"], item["period"], item["reading"], item["tone"]), ("2026-03-31", "Q1 2026", "1.44%", "high"))
        self.assertEqual((item["released"], item["next"], item["next_basis"]), ("2026-09-11", "2026-12-10", "scheduled"))
        self.assertIn("12-month change +2.9%", item["lines"])
        unscheduled = lv.parse_z1(margin, equities, date(2026, 9, 11), None)
        self.assertEqual((unscheduled["next"], unscheduled["next_basis"]), ("2026-12-11", "estimated"))

    def test_fred_error_pages_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unrecognized FRED"):
            lv.parse_fred("<!DOCTYPE html><html></html>")


class FsrTests(unittest.TestCase):
    def test_latest_report_and_the_next_one_six_months_on(self):
        html = "".join(f'<a href="/publications/files/financial-stability-report-{d}.pdf">PDF</a>'
                       for d in ("20251107", "20260508", "20250425", "20261120"))  # a future link is never read as released
        item = lv.parse_fsr(html, date(2026, 9, 24))
        self.assertEqual((item["as_of"], item["released"], item["reading"]), ("2026-05-08", "2026-05-08", "May 2026"))
        self.assertEqual((item["next"], item["next_basis"], item["next_precision"]), ("2026-11-08", "estimated", "month"))
        self.assertTrue(item["pdf"].endswith("financial-stability-report-20260508.pdf"))
        self.assertIn("Previous reports: Nov 2025, Apr 2025", item["lines"])
        self.assertIsNone(item["value"])
        with self.assertRaisesRegex(ValueError, "No dated"):
            lv.parse_fsr("<html></html>", date(2026, 9, 24))


def etf_volume(sessions=400, bull=100.0, bear=50.0, benchmark=1000.0, last_bull=None):
    index = pd.bdate_range("2024-01-01", periods=sessions)
    frame = pd.DataFrame(index=index)
    for b, s in lv.ETF_PAIRS.values():
        frame[b], frame[s] = bull, bear
    for symbol in lv.ETF_BENCHMARKS:
        frame[symbol] = benchmark
    if last_bull is not None:
        for b, _ in lv.ETF_PAIRS.values():
            frame.iloc[-lv.ETF_WINDOW:, frame.columns.get_loc(b)] = last_bull
    return frame


class EtfTests(unittest.TestCase):
    def test_bull_share_and_activity_over_twenty_sessions(self):
        item = lv.parse_etfs(etf_volume())
        self.assertEqual((item["reading"], item["value"]), ("67% bull", 66.7))
        self.assertEqual(item["series"]["etf_activity"]["points"][-1][1], 45.0)  # 6 × 150 against 2 × 1,000
        self.assertEqual(len(item["funds"]), 6)
        self.assertNotIn("released", item)  # daily: no release calendar

    def test_heavy_bullish_trading_is_marked(self):
        item = lv.parse_etfs(etf_volume(last_bull=400.0))
        self.assertEqual((item["tone"], item["signal"]), ("high", "Heavy 3× trading · leaning bullish"))

    def test_a_skipped_day_is_left_out_rather_than_the_fund(self):
        frame = etf_volume()
        frame.iloc[-2, frame.columns.get_loc("TQQQ")] = float("nan")  # Yahoo sometimes skips a day for a few funds
        item = lv.parse_etfs(frame)
        self.assertEqual(len(item["funds"]), 6)
        self.assertEqual(item["as_of"], frame.index[-1].date().isoformat())
        self.assertNotIn(frame.index[-2].date().isoformat(), [p[0] for p in item["series"]["etf_bull"]["points"]])

    def test_assets_show_only_when_every_fund_reports(self):
        symbols = [s for pair in lv.ETF_PAIRS.values() for s in pair]
        full = lv.parse_etfs(etf_volume(), {s: 2e9 if s in [b for b, _ in lv.ETF_PAIRS.values()] else 1e9 for s in symbols})
        self.assertIn("Assets $12B in bull funds, $6B in bear funds (latest, no history)", full["lines"])
        partial = lv.parse_etfs(etf_volume(), {"TQQQ": 2e9})
        self.assertFalse(any(line.startswith("Assets") for line in partial["lines"]))

    def test_missing_funds_are_rejected(self):
        frame = etf_volume().drop(columns=["TQQQ", "UPRO", "SOXS"])  # three index pairs broken
        with self.assertRaisesRegex(ValueError, "Too few leveraged ETFs"):
            lv.parse_etfs(frame)


def proshares_file(symbol, days, nav, aum):
    """ProShares' layout, newest first; shares outstanding in thousands."""
    rows = ["Date,ProShares Name,Ticker,NAV,Prior NAV,NAV Change (%),NAV Change ($),Shares Outstanding (000),Assets Under Management"]
    for d, n, a in reversed(list(zip(days, nav, aum))):
        rows.append(f"{d:%m/%d/%Y},ProShares Test,{symbol},{n},{n},0,0,{a / n / 1000:.3f},{a}")
    return "\n".join(rows) + "\n"


def proshares_texts(sessions=900, new_shares=0.0, split=False):
    """Each fund's assets are shares × NAV; bull funds gain `new_shares` a session over the last 20 (money arriving)."""
    days = list(pd.bdate_range("2023-01-02", periods=sessions).date)
    texts = {}
    for bull, bear in lv.PROSHARES_PAIRS.values():
        nav_b = [10 * 1.001 ** i for i in range(sessions)]
        nav_s = [10 * 0.999 ** i for i in range(sessions)]
        shares_b = [9e8 + new_shares * max(0, i - (sessions - lv.ETF_WINDOW) + 1) for i in range(sessions)]
        aum_b = [s * n for s, n in zip(shares_b, nav_b)]
        aum_s = [1e8 * n for n in nav_s]
        if split:  # a 1-for-10 reverse split, applied to the whole history as ProShares does: NAV ×10, shares ÷10
            nav_s = [n * 10 for n in nav_s]
        texts[bull], texts[bear] = proshares_file(bull, days, nav_b, aum_b), proshares_file(bear, days, nav_s, aum_s)
    return texts, days


class ProSharesTests(unittest.TestCase):
    def test_price_moves_and_splits_are_not_flows(self):
        texts, days = proshares_texts(split=True)
        frame = lv.etf_flows(lv.parse_proshares(texts))
        self.assertAlmostEqual(frame["flows"].abs().max(), 0, places=6)
        self.assertEqual(frame.index[-1], days[-1])
        self.assertTrue(frame["share"].iloc[-1] > frame["share"].iloc[0])  # bull funds gain share as their NAV rises

    def test_card_leads_with_flows_and_asset_share(self):
        texts, days = proshares_texts(new_shares=5e6)
        item = lv.parse_etfs_combined(lv.parse_proshares(texts))
        self.assertEqual(item["as_of"], days[-1].isoformat())
        self.assertEqual((item["frequency"], item["tone"]), ("daily", "high"))
        self.assertTrue(item["signal"].startswith("Money entering bull funds"))
        navs = [10 * 1.001 ** i for i in range(880, 900)]
        expected = 4 * 5e6 * sum(navs)  # four bull funds, 5M new shares each session at that session's NAV
        self.assertIn(f"bull funds +${expected / 1e9:.2f}B, bear funds +$0.00B", item["lines"][1])
        self.assertIn("= +10.3% of assets", item["lines"][1])  # against the 20-session average of all eight funds' assets
        self.assertIn("100th percentile since 2023", item["lines"][1])
        self.assertEqual(set(item["series"]), {"etf_flows", "etf_share"})

    def test_either_source_alone_still_gives_a_reading(self):
        texts, _ = proshares_texts()
        volume = {**lv.parse_etfs(etf_volume()), "status": "ok"}
        both = lv.parse_etfs_combined(lv.parse_proshares(texts), volume)
        self.assertEqual(set(both["series"]), {"etf_flows", "etf_share", "etf_bull", "etf_activity"})
        self.assertTrue(both["lines"][2].endswith("(Yahoo volume, context)"))
        self.assertIs(lv.parse_etfs_combined(None, volume), volume)  # flows unavailable: the volume card as before
        with self.assertRaisesRegex(ValueError, "No leveraged ETF data"):
            lv.parse_etfs_combined(None, None)
        with self.assertRaisesRegex(ValueError, "Unrecognized ProShares"):
            lv.parse_proshares({"TQQQ": "<html>blocked</html>"})

    def test_fetch_survives_a_failed_proshares_download(self):
        volume = lv.parse_etfs(etf_volume())
        with patch.object(lv.sentiment, "request_text", side_effect=ValueError("Provider unavailable (HTTP 403)")), \
                patch.object(lv, "fetch_volume", return_value=volume):
            item = lv.fetch_etfs(None, NOW)
        self.assertEqual(set(item["series"]), {"etf_bull", "etf_activity"})


def cot(**extra):
    history = [[(date(2023, 9, 19) + timedelta(weeks=i)).isoformat(), -10.0 - i / 100] for i in range(160)]
    return {"status": "ok", "as_of": history[-1][0], "fetched_at": NOW.isoformat(), "leveraged_history": history,
            "groups": [{"name": "E-mini S&P 500", "leveraged": -293143, "leveraged_index": 3},
                       {"name": "VIX futures", "leveraged": -16504, "leveraged_index": 70}], **extra}


class CotTests(unittest.TestCase):
    def test_leveraged_funds_come_from_the_sentiment_panel_reading(self):
        item = lv.cot_card(cot())
        self.assertEqual((item["as_of"], item["reading"]), ("2026-10-06", "−11.6% of OI"))
        self.assertEqual((item["released"], item["next"], item["next_basis"], item["next_time"]),
                         ("2026-10-09", "2026-10-16", "scheduled", "3:30 PM ET"))  # Tuesday data, out Friday
        self.assertEqual((item["tone"], item["signal"]), ("high", "Unusually short for 3 years"))
        self.assertEqual(item["lines"], ["Leveraged funds net −293,143 E-mini S&P 500 contracts · COT index 3",
                                         "VIX futures: leveraged funds net −16,504"])
        self.assertEqual(len(item["series"]["cot_lev"]["points"]), 160)

    def test_typical_positioning_and_missing_readings(self):
        typical = cot()
        typical["groups"][0]["leveraged_index"] = 50
        self.assertEqual((lv.cot_card(typical)["tone"], lv.cot_card(typical)["signal"]), ("normal", "Typical for 3 years"))
        self.assertEqual(lv.cot_card({})["status"], "unavailable")
        self.assertEqual(lv.cot_card(cot(leveraged_history=[]))["status"], "unavailable")  # an older cached reading without the history
        self.assertIn("next refresh", lv.cot_card(cot(leveraged_history=[]))["error"])
        self.assertEqual(lv.cot_card({"status": "unavailable", "error": "Provider blocked"})["error"], "Provider blocked")


class EvaluateTests(unittest.TestCase):
    def test_cards_in_order_with_charts_kept_apart(self):
        data, _ = ofr_payload()
        items = {"ofr": {**lv.parse_ofr(data), "status": "ok"}, "finra": {"status": "unavailable", "error": "Provider blocked"}}
        payload = {}
        lv.evaluate(payload, items, cot(), now=NOW)
        leverage = payload["market_leverage"]
        self.assertEqual([c["key"] for c in leverage["cards"]], list(lv.ORDER))
        self.assertEqual(set(leverage["series"]), {"ofr", "ofr_gne", "cot_lev"})
        self.assertTrue(all("series" not in c for c in leverage["cards"]))
        finra, z1 = leverage["cards"][0], leverage["cards"][1]
        self.assertEqual((finra["status"], finra["name"], finra["url"]), ("unavailable", "Margin debt · FINRA", lv.FINRA_PAGE))
        self.assertEqual(z1["status"], "unavailable")  # never collected: no substitute value
        self.assertEqual(leverage["checked_at"], NOW.isoformat())
        json.dumps(payload, allow_nan=False)

    def test_a_cached_etf_card_from_before_the_fund_flows_is_fetched_again(self):
        old = {"as_of": "2026-09-23", "value": 70, "series": {"etf_bull": {}, "etf_activity": {}}}
        new = {"as_of": "2026-09-23", "value": 94, "series": {"etf_flows": {}, "etf_share": {}}}
        calls = []
        other = lambda *args: {"as_of": "2026-06-30", "value": 1}
        with TemporaryDirectory() as temp, patch.object(lv.sentiment.config, "DATA_DIR", Path(temp)), \
                patch.multiple(lv, fetch_finra=other, fetch_z1=other, fetch_ofr=other, fetch_fsr=other,
                               fetch_etfs=lambda client, now: calls.append(now) or new):
            lv.sentiment.write_json(Path(temp) / "sentiment" / "leverage-etf.json",
                                    {**old, "status": "ok", "fetched_at": (NOW - timedelta(hours=1)).isoformat()})  # well inside 6 hours
            self.assertEqual(lv.collect(NOW)["etf"]["value"], 94)
            self.assertEqual(len(calls), 1)
            self.assertEqual(lv.collect(NOW)["etf"]["value"], 94)  # the complete copy is then reused
            self.assertEqual(len(calls), 1)

    def test_collect_caches_each_source_and_refuses_future_observations(self):
        fetched = {"finra": {"as_of": "2026-08-31", "value": 1}, "z1": {"as_of": "2026-06-30", "value": 1},
                   "ofr": {"as_of": "2026-03-31", "value": 1}, "fsr": {"as_of": "2026-12-01", "value": None},
                   "etf": {"as_of": "2026-09-23", "value": 1}}
        with TemporaryDirectory() as temp, patch.object(lv.sentiment.config, "DATA_DIR", Path(temp)), \
                patch.multiple(lv, fetch_finra=lambda client: fetched["finra"], fetch_z1=lambda client: fetched["z1"],
                               fetch_ofr=lambda client: fetched["ofr"], fetch_fsr=lambda client, today: fetched["fsr"],
                               fetch_etfs=lambda client, now: fetched["etf"]):
            items = lv.collect(NOW)
            self.assertEqual({k: v["status"] for k, v in items.items()},
                             {"finra": "ok", "z1": "ok", "ofr": "ok", "fsr": "unavailable", "etf": "ok"})
            self.assertTrue((Path(temp) / "sentiment" / "leverage-finra.json").exists())


if __name__ == "__main__":
    unittest.main()
