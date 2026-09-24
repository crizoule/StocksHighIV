"""Synthetic CFTC rows and prices; never assertions about actual market levels."""
import json
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

from highiv import commodities as cm

NOW = datetime(2026, 9, 24, 21, tzinfo=timezone.utc)
TODAY = date(2026, 9, 24)


def rows(code, weeks=200, start=date(2022, 11, 29), managed=lambda i: i, interest=1000, producers=lambda i: -i):
    """Weekly Tuesday rows: managed-money and producer nets set by index, open interest fixed."""
    out = []
    for i in range(weeks):
        m, p = managed(i), producers(i)
        out.append({"report_date_as_yyyy_mm_dd": f"{start + timedelta(weeks=i)}T00:00:00.000", "cftc_contract_market_code": code,
                    "open_interest_all": str(interest), "m_money_positions_long_all": str(max(m, 0)),
                    "m_money_positions_short_all": str(max(-m, 0)), "prod_merc_positions_long": str(max(p, 0)),
                    "prod_merc_positions_short": str(max(-p, 0))})
    return out


class CotTests(unittest.TestCase):
    def test_net_positions_as_shares_of_open_interest_with_a_three_year_index(self):
        data = rows("067651") + rows("088691", managed=lambda i: 100 - i)  # crude: most long ever; gold: least
        data.append({**data[-1], "report_date_as_yyyy_mm_dd": "2030-01-01T00:00:00.000"})  # a future week is never read
        data.append({"cftc_contract_market_code": "067651"})  # malformed: skipped
        markets = cm.parse_cot(data, TODAY)
        crude, gold = markets["067651"], markets["088691"]
        last = "2026-09-22"  # 199 weeks after the first Tuesday
        self.assertEqual((crude["as_of"], crude["open_interest"], crude["managed"]), (last, 1000, 199))
        self.assertEqual((crude["managed_pct"], crude["producers_pct"], crude["index"]), (19.9, -19.9, 100))
        self.assertEqual(gold["index"], 0)
        self.assertEqual(crude["managed_history"][-1], [last, 19.9])
        self.assertEqual(len(crude["producer_history"]), 200)
        self.assertNotIn("002602", markets)  # no rows at all: left out, not zero

    def test_short_histories_are_listed_but_not_charted(self):
        markets = cm.parse_cot(rows("067651") + rows("058644", weeks=10), TODAY)
        self.assertNotIn("058644", markets)
        self.assertIsNone(cm.parse_cot(rows("067651", weeks=100), TODAY)["067651"]["index"])  # under three years: no index
        with self.assertRaisesRegex(ValueError, "No commodity positions"):
            cm.parse_cot([], TODAY)

    def test_every_other_contract_is_listed_by_category_most_held_first(self):
        latest = [
            {"cftc_contract_market_code": "023391", "market_and_exchange_names": "NAT GAS ICE LD1 - ICE FUTURES ENERGY DIV",
             "commodity_subgroup_name": "NATURAL GAS AND PRODUCTS", "open_interest_all": "8000", "m_money_positions_long_all": "300",
             "m_money_positions_short_all": "100"},
            {"cftc_contract_market_code": "0000X1", "market_and_exchange_names": "PJM WESTERN HUB - NODAL EXCHANGE",
             "commodity_subgroup_name": "ELECTRICITY AND SOURCES", "open_interest_all": "9000"},  # no managed-money fields
            {"cftc_contract_market_code": "067651", "market_and_exchange_names": "WTI-PHYSICAL - NEW YORK MERCANTILE EXCHANGE",
             "commodity_subgroup_name": "PETROLEUM AND PRODUCTS", "open_interest_all": "20000"},  # charted: not repeated
            {"cftc_contract_market_code": "0000X2", "market_and_exchange_names": "SOMETHING NEW - A NEW EXCHANGE",
             "commodity_subgroup_name": "UNHEARD OF", "open_interest_all": "5"},
        ]
        contracts = cm.parse_contracts(latest, set(cm.MARKETS))
        self.assertEqual([c["code"] for c in contracts], ["0000X1", "023391", "0000X2"])
        gas = contracts[1]
        self.assertEqual((gas["name"], gas["exchange"], gas["category"], gas["managed_pct"]), ("NAT GAS ICE LD1", "ICE Energy", "energy", 2.5))
        self.assertEqual((contracts[0]["exchange"], contracts[0]["category"], contracts[0]["managed_pct"]), ("Nodal", "electricity", 0.0))
        self.assertEqual((contracts[2]["exchange"], contracts[2]["category"]), ("A New Exchange", "other"))


class PriceTests(unittest.TestCase):
    def test_weekly_closes_end_on_friday_and_the_latest_close_keeps_its_own_date(self):
        days = pd.bdate_range("2026-09-01", "2026-09-23")  # ends on a Wednesday
        closes = pd.Series(range(len(days)), index=days, dtype=float)
        points = cm.weekly_prices(closes)
        self.assertEqual(points[0], ["2026-09-04", 3.0])
        self.assertEqual(points[-1], ["2026-09-23", float(len(days) - 1)])  # this week's close so far, not next Friday
        self.assertEqual(cm.weekly_prices(pd.Series(dtype=float)), [])


class EvaluateTests(unittest.TestCase):
    def test_categories_and_markets_ordered_by_open_interest_with_release_dates(self):
        cot = {"status": "ok", "as_of": "2026-09-15", "contracts": [{"code": "X", "category": "electricity", "open_interest": 5, "managed_pct": 1.0}],
               "markets": {"067651": {"as_of": "2026-09-15", "open_interest": 100, "managed_pct": 5.0},
                           "023651": {"as_of": "2026-09-15", "open_interest": 300, "managed_pct": -5.0},
                           "002602": {"as_of": "2026-09-15", "open_interest": 1000, "managed_pct": 20.0}}}
        prices = {"status": "ok", "as_of": "2026-09-24", "prices": {"CL=F": {"last": 95.1, "as_of": "2026-09-24", "points": [["2026-09-24", 95.1]]},
                                                                  "GC=F": {"last": 4300.0, "as_of": "2026-09-24", "points": []}}}
        payload = {}
        cm.evaluate(payload, {"cot": cot, "prices": prices}, now=NOW)
        panel = payload["commodities"]
        self.assertEqual([g["key"] for g in panel["groups"]], ["grains", "energy", "metals"])  # 1,000 > 400 > 0
        energy = panel["groups"][1]
        self.assertEqual([m["code"] for m in energy["markets"]], ["023651", "067651"])  # natural gas holds more
        crude = energy["markets"][1]
        self.assertEqual((crude["name"], crude["symbol"], crude["price"], crude["managed_pct"]), ("WTI crude oil", "CL=F", 95.1, 5.0))
        gold = panel["groups"][2]["markets"][0]
        self.assertEqual((gold["name"], gold["price"], gold.get("managed_pct")), ("Gold", 4300.0, None))  # price only this week
        self.assertEqual((panel["as_of"], panel["released"], panel["next"], panel["next_time"]), ("2026-09-15", "2026-09-18", "2026-09-25", "3:30 PM ET"))
        self.assertEqual(panel["contracts"], cot["contracts"])
        self.assertEqual(panel["categories"]["electricity"], "Electricity")
        json.dumps(payload, allow_nan=False)

    def test_nothing_collected_leaves_an_empty_panel_not_a_guess(self):
        payload = {}
        cm.evaluate(payload, {"cot": {"status": "unavailable"}, "prices": {"status": "unavailable"}}, now=NOW)
        self.assertEqual((payload["commodities"]["groups"], payload["commodities"]["as_of"], payload["commodities"]["next"]), ([], None, None))

    def test_collect_caches_both_sources(self):
        fetched = []
        with TemporaryDirectory() as temp, patch.object(cm.sentiment.config, "DATA_DIR", Path(temp)), \
                patch.object(cm, "fetch_cot", lambda client, today: fetched.append("cot") or {"as_of": "2026-09-15", "markets": {}}), \
                patch.object(cm, "fetch_prices", lambda now: fetched.append("prices") or {"as_of": "2026-09-24", "prices": {}}):
            first = cm.collect(NOW)
            second = cm.collect(NOW + timedelta(hours=1))
            self.assertEqual({k: v["status"] for k, v in first.items()}, {"cot": "ok", "prices": "ok"})
            self.assertEqual(sorted(fetched), ["cot", "prices"])  # the second collection reused both caches
            self.assertEqual(second["cot"]["as_of"], "2026-09-15")
            self.assertTrue((Path(temp) / "sentiment" / "commodities-cot.json").exists())


if __name__ == "__main__":
    unittest.main()
