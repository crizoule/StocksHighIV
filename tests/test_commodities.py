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


def rows(code, weeks=200, start=date(2022, 11, 29), lead=lambda i: i, second=lambda i: -i, interest=1000, section="physical"):
    """Weekly Tuesday rows ending 2026-09-22: the market's leading and second groups' nets set by index."""
    first, other = cm.roles(code) if code in cm.MARKETS[section] else (("managed", "producers") if section == "physical" else ("lev_money", "asset_mgr"))
    out = []
    for i in range(weeks):
        row = {"report_date_as_yyyy_mm_dd": f"{start + timedelta(weeks=i)}T00:00:00.000", "cftc_contract_market_code": code,
               "open_interest_all": str(interest)}
        for group, value in ((first, lead(i)), (other, second(i))):
            long, short, _ = cm.GROUPS[group]
            row[long], row[short] = str(max(value, 0)), str(max(-value, 0))
        out.append(row)
    return out


class RoleTests(unittest.TestCase):
    def test_each_asset_class_is_led_by_the_group_that_carries_its_direction(self):
        self.assertEqual(cm.roles("067651"), ("managed", "producers"))    # crude: speculators, hedgers opposite
        self.assertEqual(cm.roles("13874A"), ("asset_mgr", "lev_money"))  # S&P 500: leveraged funds' short is the basis trade
        self.assertEqual(cm.roles("043602"), ("asset_mgr", "lev_money"))  # 10-year note: likewise
        self.assertEqual(cm.roles("133741"), ("asset_mgr", "lev_money"))  # bitcoin: short against spot ETFs
        self.assertEqual(cm.roles("099741"), ("lev_money", "asset_mgr"))  # euro: the speculative bet
        self.assertEqual(cm.roles("045601"), ("lev_money", "asset_mgr"))  # fed funds
        self.assertEqual(cm.roles("1170E1"), ("lev_money", "asset_mgr"))  # VIX
        for section, markets in cm.MARKETS.items():
            for code, (_, _, _, category) in markets.items():
                self.assertIn(category, cm.CATEGORIES)
                self.assertTrue(cm.READING.get(category) or cm.READING.get(section), code)


class PositionTests(unittest.TestCase):
    def test_net_positions_as_shares_of_open_interest_with_a_three_year_index(self):
        data = rows("067651") + rows("088691", lead=lambda i: 100 - i)  # crude: most long ever; gold: least
        data.append({**data[-1], "report_date_as_yyyy_mm_dd": "2030-01-01T00:00:00.000"})  # a future week is never read
        data.append({"cftc_contract_market_code": "067651"})  # malformed: skipped
        data += rows("13874A", section="financial")  # a financial market is never read as a commodity
        markets = cm.parse_positions(data, TODAY, "physical")
        crude, gold = markets["067651"], markets["088691"]
        self.assertEqual((crude["as_of"], crude["open_interest"], crude["lead_net"]), ("2026-09-22", 1000, 199))
        self.assertEqual((crude["lead_name"], crude["second_name"]), ("Managed money", "Producers"))
        self.assertEqual((crude["lead_pct"], crude["second_pct"], crude["index"]), (19.9, -19.9, 100))
        self.assertEqual(gold["index"], 0)
        self.assertEqual(crude["lead_history"][-1], ["2026-09-22", 19.9])
        self.assertEqual(len(crude["second_history"]), 200)
        self.assertNotIn("13874A", markets)

    def test_financial_futures_read_asset_managers_or_leveraged_funds_by_asset_class(self):
        data = rows("13874A", section="financial", lead=lambda i: 300, second=lambda i: -250) + \
            rows("099741", section="financial", lead=lambda i: -80, second=lambda i: 40)
        markets = cm.parse_positions(data, TODAY, "financial")
        spx, euro = markets["13874A"], markets["099741"]
        self.assertEqual((spx["lead_name"], spx["lead_pct"], spx["second_name"], spx["second_pct"]), ("Asset managers", 30.0, "Leveraged funds", -25.0))
        self.assertEqual((euro["lead_name"], euro["lead_pct"], euro["second_name"], euro["second_pct"]), ("Leveraged funds", -8.0, "Asset managers", 4.0))

    def test_short_histories_are_listed_but_not_charted(self):
        markets = cm.parse_positions(rows("067651") + rows("058644", weeks=10), TODAY, "physical")
        self.assertNotIn("058644", markets)
        self.assertIsNone(cm.parse_positions(rows("067651", weeks=100), TODAY, "physical")["067651"]["index"])  # under three years
        with self.assertRaisesRegex(ValueError, "No positions"):
            cm.parse_positions([], TODAY, "physical")

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
        contracts = cm.parse_contracts(latest, set(cm.MARKETS["physical"]), "physical")
        self.assertEqual([c["code"] for c in contracts], ["0000X1", "023391", "0000X2"])
        gas = contracts[1]
        self.assertEqual((gas["name"], gas["exchange"], gas["category"], gas["spec_pct"]), ("NAT GAS ICE LD1", "ICE Energy", "energy", 2.5))
        self.assertEqual((contracts[0]["exchange"], contracts[0]["category"], contracts[0]["spec_pct"]), ("Nodal", "electricity", 0.0))
        self.assertEqual((contracts[2]["exchange"], contracts[2]["category"]), ("A New Exchange", "other"))
        financial = cm.parse_contracts([{"cftc_contract_market_code": "133LM1", "market_and_exchange_names": "Nano Bitcoin  - COINBASE DERIVATIVES, LLC",
                                         "commodity_subgroup_name": "DIGITAL ASSET (NON-MAJOR)", "open_interest_all": "1000",
                                         "lev_money_positions_long_all": "0", "lev_money_positions_long": "10", "lev_money_positions_short": "50"},
                                        {"cftc_contract_market_code": "13874+", "market_and_exchange_names": "S&P 500 Consolidated - CHICAGO MERCANTILE EXCHANGE",
                                         "open_interest_all": "10"}], set(cm.MARKETS["financial"]), "financial")
        self.assertEqual([(c["name"], c["exchange"], c["category"], c["spec_pct"]) for c in financial],
                         [("Nano Bitcoin", "Coinbase Derivatives", "crypto", -4.0), ("S&P 500 Consolidated", "CME", "other_financial", 0.0)])


class PriceTests(unittest.TestCase):
    def test_weekly_closes_end_on_friday_and_the_latest_close_keeps_its_own_date(self):
        days = pd.bdate_range("2026-09-01", "2026-09-23")  # ends on a Wednesday
        closes = pd.Series(range(len(days)), index=days, dtype=float)
        points = cm.weekly_prices(closes)
        self.assertEqual(points[0], ["2026-09-04", 3.0])
        self.assertEqual(points[-1], ["2026-09-23", float(len(days) - 1)])  # this week's close so far, not next Friday
        self.assertEqual(cm.weekly_prices(pd.Series([0.0063351234], index=days[:1])), [["2026-09-01", 0.0063351]])  # yen keeps its digits; a lone close keeps its own date
        self.assertEqual(cm.weekly_prices(pd.Series(dtype=float)), [])


class EvaluateTests(unittest.TestCase):
    def test_commodities_then_financial_futures_ordered_by_open_interest(self):
        position = lambda oi, **extra: {"as_of": "2026-09-15", "open_interest": oi, "lead_name": "Managed money", "second_name": "Producers", **extra}
        physical = {"status": "ok", "as_of": "2026-09-15", "contracts": [{"code": "X", "category": "electricity", "open_interest": 5, "spec_pct": 1.0}],
                    "markets": {"067651": position(100), "023651": position(300), "002602": position(1000)}}
        financial = {"status": "cached", "as_of": "2026-09-15", "contracts": [],
                     "markets": {"043602": position(5000, lead_name="Asset managers", second_name="Leveraged funds")}}
        prices = {"status": "ok", "as_of": "2026-09-24", "prices": {"CL=F": {"last": 95.1, "as_of": "2026-09-24", "points": [["2026-09-24", 95.1]]},
                                                                  "GC=F": {"last": 4300.0, "as_of": "2026-09-24", "points": []}}}
        payload = {}
        cm.evaluate(payload, {"physical": physical, "financial": financial, "prices": prices}, now=NOW)
        panel = payload["commodities"]
        self.assertEqual([s["key"] for s in panel["sections"]], ["physical", "financial"])
        commodities, futures = panel["sections"]
        self.assertEqual([g["key"] for g in commodities["groups"]], ["grains", "energy", "metals"])  # 1,000 > 400 > 0
        energy = commodities["groups"][1]
        self.assertEqual([m["code"] for m in energy["markets"]], ["023651", "067651"])  # natural gas holds more
        self.assertIn("Managed money", energy["reading"])
        crude = energy["markets"][1]
        self.assertEqual((crude["name"], crude["symbol"], crude["category"], crude["price"]), ("WTI crude oil", "CL=F", "energy", 95.1))
        gold = commodities["groups"][2]["markets"][0]
        self.assertEqual((gold["name"], gold["price"], gold["lead_name"], gold.get("lead_pct")), ("Gold", 4300.0, "Managed money", None))
        treasuries = futures["groups"][0]
        self.assertEqual((treasuries["key"], treasuries["markets"][0]["lead_name"], futures["status"]), ("treasuries", "Asset managers", "cached"))
        self.assertIn("basis trade", treasuries["reading"])
        self.assertEqual((panel["as_of"], panel["released"], panel["next"], panel["next_time"]), ("2026-09-15", "2026-09-18", "2026-09-25", "3:30 PM ET"))
        self.assertEqual(commodities["contracts"], physical["contracts"])
        self.assertEqual((panel["categories"]["electricity"], panel["categories"]["stir"]), ("Electricity", "Short-term rates"))
        json.dumps(payload, allow_nan=False)

    def test_nothing_collected_leaves_empty_sections_not_a_guess(self):
        payload = {}
        cm.evaluate(payload, {"physical": {"status": "unavailable"}, "financial": {"status": "unavailable"}, "prices": {"status": "unavailable"}}, now=NOW)
        self.assertEqual([s["groups"] for s in payload["commodities"]["sections"]], [[], []])
        self.assertEqual((payload["commodities"]["as_of"], payload["commodities"]["next"]), (None, None))

    def test_collect_caches_every_source_and_refetches_copies_saved_before_financial_futures(self):
        fetched = []
        old = {"as_of": "2026-09-15", "markets": {"067651": {"managed_history": []}}, "status": "ok", "fetched_at": (NOW - timedelta(hours=1)).isoformat()}
        new = {"as_of": "2026-09-15", "markets": {"067651": {"lead_history": []}}}
        with TemporaryDirectory() as temp, patch.object(cm.sentiment.config, "DATA_DIR", Path(temp)), \
                patch.object(cm, "fetch_positions", lambda client, today, section: fetched.append(section) or new), \
                patch.object(cm, "fetch_prices", lambda now: fetched.append("prices") or {"as_of": "2026-09-24", "prices": {"ES=F": {}}}):
            cm.sentiment.write_json(Path(temp) / "sentiment" / "commodities-cot.json", old)  # saved by 3.3.0, well within 12 hours
            first = cm.collect(NOW)
            self.assertEqual({k: v["status"] for k, v in first.items()}, {"physical": "ok", "financial": "ok", "prices": "ok"})
            self.assertEqual(sorted(fetched), ["financial", "physical", "prices"])  # the 3.3.0 copy was fetched again
            cm.collect(NOW + timedelta(hours=1))
            self.assertEqual(len(fetched), 3)  # complete copies are then reused
            self.assertTrue((Path(temp) / "sentiment" / "commodities-financial.json").exists())


if __name__ == "__main__":
    unittest.main()
