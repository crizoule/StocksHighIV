"""Synthetic business profiles and prices; no claims about actual market moves."""
from datetime import date, datetime, time, timedelta
import copy
import unittest

from highiv import context

AS_OF = date(2026, 9, 17)
NOW = datetime(2026, 9, 18, 12, tzinfo=context.MARKET_TZ)


def row(symbol="TEST", **kwargs):
    return dict(symbol=symbol, name="Test Company", market="US", quote_date=AS_OF.isoformat(), **kwargs)


def prices(move=30, start=date(2026, 6, 18), end=AS_OF):
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)
            if (start + timedelta(days=i)).weekday() < 5]
    closes = [100 * (1 + move / 100 * i / (len(days) - 1)) for i in range(len(days))]
    return {"1D": {"t": [int(datetime.combine(d, time(), context.MARKET_TZ).timestamp()) for d in days], "c": closes}}


def apply(rows):
    payload = dict(run_date=AS_OF.isoformat(), rows=rows)
    context.apply(payload, now=NOW)
    return rows


class ExposureTests(unittest.TestCase):
    def keys(self, **kwargs):
        return {e["key"] for e in context.exposures(row(**kwargs), AS_OF)}

    def test_oil_producers_services_and_refiners_are_distinct(self):
        self.assertEqual(self.keys(industry="Oil & Gas E&P"), {"oil_gas"})
        self.assertEqual(self.keys(industry="Oil & Gas Equipment & Services"), {"oil_services"})
        self.assertEqual(self.keys(industry="Oil & Gas Refining & Marketing"), {"refining"})
        self.assertEqual(self.keys(industry="Oil & Gas Midstream"), set())

    def test_memory_requires_supplier_evidence_not_just_semiconductor_sector(self):
        self.assertEqual(self.keys(industry="Semiconductors", summary="Manufactures NAND flash memory."), {"memory"})
        self.assertEqual(self.keys(industry="Semiconductors", summary="Sells design tools to NAND manufacturers."), set())
        self.assertEqual(self.keys(industry="Semiconductors", summary="Designs general purpose processors."), set())
        self.assertEqual(self.keys(industry="Computer Hardware", summary="Offers hard disk drives to equipment manufacturers."), {"storage"})

    def test_ai_mention_does_not_imply_beneficiary_or_disruption(self):
        self.assertEqual(self.keys(industry="Software - Application", summary="Uses AI for customer support."), set())
        self.assertEqual(self.keys(summary="Offers AI cloud infrastructure and GPU servers."), {"ai_infrastructure"})
        self.assertEqual(self.keys(industry="Software - Application"), set())

    def test_issuer_disclosures_require_identity_and_date_and_preserve_two_sides(self):
        for symbol, name, published in [("ADBE", "Adobe Inc.", date(2026, 1, 15)), ("FVRR", "Fiverr International Ltd.", date(2026, 3, 12))]:
            r = {**row(symbol), "name": name}
            items = context.exposures(r, AS_OF)
            self.assertEqual(items[0]["key"], "ai_disruption")
            self.assertIn("opportunit", items[0]["detail"])
            self.assertTrue(items[0]["url"].startswith("https://"))
            self.assertEqual(context.exposures(r, published - timedelta(days=1)), [])
            self.assertEqual(context.exposures({**r, "market": "CA"}, AS_OF), [])
            self.assertEqual(context.exposures({**r, "name": "Unrelated Company"}, AS_OF), [])

    def test_crypto_tokens_and_platforms_are_not_conflated(self):
        self.assertEqual(self.keys(summary="Operates Bitcoin mining and ETH treasury operations."), {"bitcoin", "ether"})
        self.assertEqual(self.keys(summary="Operates an Ethereum treasury."), {"ether"})
        self.assertEqual(self.keys(summary="Operates a cryptocurrency trading platform."), {"crypto_activity"})
        self.assertEqual(self.keys(summary="Provides cryptocurrency mining."), {"crypto_mining"})
        self.assertEqual(self.keys(industry="Capital Markets", summary="Trades traditional securities."), set())


class ObservedTests(unittest.TestCase):
    def test_peer_evidence_excludes_self_uses_same_dates_and_keeps_event_unknown(self):
        rows = apply([row(str(i), industry="Gold", prices=prices(move), iv_why="No clear catalyst found", iv_why_kind="none")
                      for i, move in enumerate([-40, 30, 40, 50])])
        peer = rows[0]["iv_peer_trends"][0]
        self.assertEqual((peer["count"], peer["median_pct"], peer["up"], peer["direction"]), (3, 40, 3, "up"))
        self.assertEqual({m["symbol"] for m in peer["members"]}, {"1", "2", "3"})
        self.assertEqual((peer["start"], peer["end"]), ("2026-06-18", "2026-09-17"))
        self.assertEqual(rows[0]["iv_why_kind"], "none")
        self.assertIn("not the whole", peer["detail"])

    def test_small_or_mixed_peer_group_does_not_become_broad_trend(self):
        rows = apply([row(str(i), industry="Gold", prices=prices()) for i in range(3)])
        self.assertFalse(rows[0]["iv_peer_trends"])
        rows = apply([row(str(i), industry="Gold", prices=prices(move)) for i, move in enumerate([0, 40, -30, 0])])
        self.assertEqual(rows[0]["iv_peer_trends"][0]["direction"], "mixed")

    def test_downward_peer_moves_are_reported_as_downward(self):
        rows = apply([row(str(i), summary="Offers AI cloud services.", prices=prices(move))
                      for i, move in enumerate([20, -30, -40, -50])])
        self.assertEqual(rows[0]["iv_peer_trends"][0]["direction"], "down")

    def test_bitcoin_and_ether_are_not_used_as_each_others_peers(self):
        rows = apply([row("BTC", summary="Bitcoin mining", prices=prices())] +
                     [row(str(i), summary="Ethereum treasury", prices=prices()) for i in range(4)])
        self.assertFalse(rows[0]["iv_peer_trends"])

    def test_missing_endpoints_short_history_and_stale_prices_are_not_compared(self):
        rows = [row(str(i), industry="Gold", prices=prices()) for i in range(5)]
        rows[3]["prices"] = prices(start=date(2026, 8, 1))
        rows[4]["prices"] = prices(end=date(2026, 9, 1))
        apply(rows)
        self.assertIsNone(rows[3]["iv_price_context"])
        self.assertIsNone(rows[4]["iv_price_context"])
        self.assertFalse(rows[0]["iv_peer_trends"])

    def test_future_and_unfinished_bars_cannot_change_observed_returns(self):
        r = row(prices=prices(end=date(2026, 9, 18)))
        old = context._closes(r, AS_OF, NOW)
        r["prices"]["1D"]["c"][-1] = 10000
        self.assertEqual(context._closes(r, AS_OF, NOW), old)
        self.assertEqual(context._closes(r, date(2026, 9, 18), NOW), old)

    def test_invalid_closes_and_large_gaps_do_not_generate_patterns(self):
        r = row(prices=prices())
        r["prices"]["1D"]["c"][0] = float("nan")
        r["prices"]["1D"]["c"][1] = 0
        self.assertTrue(all(v > 0 for v in context._closes(r, AS_OF, NOW).values()))
        r["prices"]["1D"]["c"][20:30] = [None] * 10
        apply([r])
        self.assertIsNone(r["iv_price_context"])

    def test_rally_then_selloff_and_reverse_require_both_legs(self):
        for peak, end, expected in [(200, 140, "rose 100.0%, then fell 30.0%"), (50, 80, "fell 50.0%, then rebounded 60.0%")]:
            r = row(prices=prices(0))
            c = r["prices"]["1D"]["c"]
            middle = len(c) // 2
            c[:] = [100 + (peak - 100) * i / middle if i <= middle else peak + (end - peak) * (i - middle) / (len(c) - 1 - middle) for i in range(len(c))]
            apply([r])
            self.assertIn(expected, r["iv_price_context"]["label"])
        r = apply([row(prices=prices(40))])[0]
        self.assertEqual(r["iv_price_context"]["label"], "Stock +40.0% over ~3 months")

    def test_recomputing_clears_old_context_and_does_not_mutate_event_evidence(self):
        r = row(industry="Gold", prices=prices(), iv_why="Possible catalyst: example", iv_why_kind="news")
        apply([r])
        r["industry"] = "Unknown"
        r["prices"] = None
        expected = copy.deepcopy(r["iv_why"])
        apply([r])
        self.assertEqual(r["iv_context"], [])
        self.assertEqual(r["iv_peer_trends"], [])
        self.assertIsNone(r["iv_price_context"])
        self.assertEqual(r["iv_why"], expected)


if __name__ == "__main__":
    unittest.main()
