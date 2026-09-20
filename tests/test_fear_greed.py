"""Synthetic series only; the replica's arithmetic, never assertions about actual markets."""
import unittest
from datetime import date

import numpy as np
import pandas as pd

from highiv import fear_greed as fg


def sessions(n, end="2026-09-17"):
    return pd.bdate_range(end=end, periods=n)


def walk(n, seed, end="2026-09-17"):
    rng = np.random.default_rng(seed)
    return pd.Series(100 + np.cumsum(rng.normal(0, 1, n)), sessions(n, end))


def inputs(n=700, end="2026-09-17"):
    return {key: walk(n, i, end) + (20 if key == "volatility" else 0) for i, key in enumerate(fg.COMPONENTS)}


class ScoringTests(unittest.TestCase):
    def test_rank_is_strict_share_of_trailing_500(self):
        rising = pd.Series(np.arange(600, dtype=float), sessions(600))
        self.assertAlmostEqual(fg.rank_scores(rising).iloc[-1], 99.8)
        self.assertEqual(fg.rank_scores(pd.Series(1.0, sessions(600))).iloc[-1], 0)
        self.assertTrue(fg.rank_scores(rising.iloc[:499]).empty)

    def test_zscore_is_trailing_and_uses_sample_deviation(self):
        x = walk(200, 1)
        window = x.iloc[-125:]
        self.assertAlmostEqual(fg.zscore(x).iloc[-1], (x.iloc[-1] - window.mean()) / window.std(ddof=1))

    def test_fear_inputs_are_inverted_and_volatility_is_neutral_unless_extreme(self):
        x = walk(700, 3)
        scores = fg.component_scores({"momentum": x, "put_call": x, "junk": x})
        # For continuous values the inverted rank is the complement, less the value itself.
        self.assertTrue(np.allclose(scores["put_call"], 100 - scores["momentum"] - 0.2))
        self.assertTrue(np.allclose(scores["junk"], scores["put_call"]))
        vix = pd.Series(15 + np.sin(np.arange(700)), sessions(700))
        scores = fg.component_scores({"volatility": vix})["volatility"]
        self.assertTrue(((scores == 50) | (scores < fg.VIX_FEAR_BELOW)).all())
        for last, expected in ((40, 0), (5, 50)):  # a spike is extreme fear; a collapse stays neutral
            vix.iloc[-1] = last
            self.assertEqual(fg.component_scores({"volatility": vix})["volatility"].iloc[-1], expected)

    def test_ratings_follow_cnn_bands(self):
        self.assertEqual([fg.rating(v) for v in (0, 24.9, 25, 44.9, 45, 54.9, 55, 74.9, 75, 100)],
                         ["Extreme fear", "Extreme fear", "Fear", "Fear", "Neutral", "Neutral", "Greed", "Greed",
                          "Extreme greed", "Extreme greed"])


class InputTests(unittest.TestCase):
    def test_partial_and_future_sessions_are_dropped(self):
        frame = pd.DataFrame({"x": [1, 2, 3]}, index=pd.to_datetime(["2026-09-17", "2026-09-18", "2026-09-19"]))
        self.assertEqual(len(fg.completed(frame, date(2026, 9, 18), 15)), 1)
        self.assertEqual(len(fg.completed(frame, date(2026, 9, 18), 16)), 2)

    def test_nyse_strength_counts_new_highs_and_lows_only_with_enough_stocks(self):
        days = sessions(300)
        up = pd.DataFrame({f"U{i}": np.arange(300.0) + 10 for i in range(300)}, index=days)
        down = pd.DataFrame({f"D{i}": 400 - np.arange(300.0) for i in range(300)}, index=days)
        close = pd.concat([up, down], axis=1)
        volume = pd.DataFrame(1000.0, index=days, columns=close.columns)
        parts, count = fg.nyse_inputs(fg.nyse_counts(close, close, close, volume))
        self.assertEqual(count, 600)
        self.assertAlmostEqual(parts["strength"].iloc[-1], 0)  # 300 new highs, 300 new lows
        more_up, _ = fg.nyse_inputs(fg.nyse_counts(close.iloc[:, :450], close.iloc[:, :450], close.iloc[:, :450], volume.iloc[:, :450]))
        self.assertTrue(more_up["strength"].empty)  # 450 stocks: below the sample floor
        self.assertTrue(np.allclose(parts["breadth"], parts["breadth"].iloc[0]))  # balanced volume, flat summation
        # Stocks folded in group by group must give exactly the market-wide result, since the counts add up.
        halves = [fg.nyse_counts(f.iloc[:, half::2], f.iloc[:, half::2], f.iloc[:, half::2], volume.iloc[:, half::2])
                  for half in (0, 1) for f in [close]]
        folded, _ = fg.nyse_inputs(halves[0].add(halves[1], fill_value=0))
        self.assertTrue(folded["strength"].equals(parts["strength"]) and folded["breadth"].equals(parts["breadth"]))

    def test_junk_gap_uses_twelve_distributions_and_lags_one_session(self):
        days = sessions(400)
        close = pd.DataFrame({"^GSPC": 100.0, "^VIX": 15.0, "IEF": 100.0, "HYG": 100.0, "LQD": 100.0}, index=days)
        dividends = pd.DataFrame(0.0, index=days, columns=close.columns)
        paid = days[::21][:15]
        dividends.loc[paid, "HYG"], dividends.loc[paid, "LQD"] = 0.5, 0.4
        junk = fg.index_inputs(close, dividends)["junk"]
        self.assertAlmostEqual(junk.iloc[-1], 1.2)
        self.assertEqual(junk.index[0], days[days.get_loc(paid[11]) + 1])

    def test_put_call_volumes_require_one_session_and_both_groups(self):
        html = ('{\\"selectedDate\\":\\"2026-09-17\\",\\"EQUITY OPTIONS\\":[{\\"name\\":\\"VOLUME\\",\\"call\\":200,\\"put\\":100}],'
                '\\"EXCHANGE TRADED PRODUCTS\\":[{\\"name\\":\\"VOLUME\\",\\"call\\":100,\\"put\\":80}]}')
        day, volumes = fg.parse_put_call_volumes(html)
        self.assertEqual(day, "2026-09-17")
        self.assertAlmostEqual(fg.put_call_ratio(volumes), 0.6)
        for bad in (html.replace("selectedDate", "date"), html + '\\"selectedDate\\":\\"2026-09-16\\"',
                    html.replace("EQUITY OPTIONS", "EQUITIES")):
            with self.assertRaises(ValueError):
                fg.parse_put_call_volumes(bad)

    def test_put_call_average_needs_five_consecutive_sessions(self):
        days = [d.date() for d in sessions(8)]
        history = {d.isoformat(): {"equity": [100, 50], "etp": [100, 50]} for d in days if d != days[5]}
        average = fg.put_call_input(history, days)
        # Cboe's discontinued archive covers sessions the app has no stored volumes for.
        archived = fg.put_call_input(history, days, [[days[5].isoformat(), 0.5], [days[0].isoformat(), 9.9]])
        self.assertEqual(list(archived.dropna().index.date), days[4:])  # the gap is filled; stored volumes still win
        self.assertEqual(list(average.index.date), [days[4]])
        self.assertAlmostEqual(average.iloc[0], 0.5)


class ReplicaTests(unittest.TestCase):
    def test_reading_averages_fresh_components_and_names_the_missing(self):
        data = inputs()
        result = fg.replica(data)
        scores = [p["score"] for p in result["components"]]
        self.assertEqual(result["coverage"], 7)
        self.assertAlmostEqual(result["value"], sum(scores) / 7)
        self.assertEqual(result["as_of"], "2026-09-17")
        data["put_call"] = walk(700, 9, end="2026-09-08")
        del data["junk"]
        result = fg.replica(data, {"junk": "No credit data."})
        parts = {p["key"]: p for p in result["components"]}
        self.assertEqual(result["coverage"], 5)
        self.assertIsNone(parts["put_call"]["score"])
        self.assertIn("stale", parts["put_call"]["detail"])
        self.assertEqual(parts["junk"]["detail"], "No credit data.")
        self.assertIn("missing: Put and call options, Junk bond demand", result["detail"])

    def test_too_few_components_is_an_error_not_a_neutral_reading(self):
        data = inputs()
        for key in ("put_call", "junk", "strength"):
            del data[key]
        with self.assertRaises(ValueError):
            fg.replica(data)

    def test_history_and_comparison_on_shared_sessions(self):
        frame = fg.history(inputs())
        self.assertEqual(frame["score"].notna().sum(), 700 - 623)  # 125 sessions for z, then 500 z-scores
        cnn = frame["score"].dropna() + 2
        result = fg.compare(cnn, frame["score"])
        self.assertEqual(result["sessions"], 77)
        self.assertAlmostEqual(result["mean_abs_gap"], 2)
        self.assertAlmostEqual(result["bias"], -2)

    def test_cnn_points_keep_their_utc_market_date(self):
        # 2026-09-17 00:00 UTC (a closed session) and 2026-09-18 18:13 UTC (intraday).
        data = {"fear_and_greed_historical": {"data": [{"x": 1789603200000.0, "y": 28.3}, {"x": 1789755216000.0, "y": 28.6}]}}
        series = fg.cnn_series(data)
        self.assertEqual([d.date().isoformat() for d in series.index], ["2026-09-17", "2026-09-18"])


if __name__ == "__main__":
    unittest.main()
