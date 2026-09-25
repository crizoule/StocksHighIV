"""NAAIM history bundled with the app, plus synthetic delayed-table pages."""
import unittest
from datetime import date

from highiv import naaim, sentiment


def table(*rows):
    """NAAIM's embeddable table layout: a header row, then one row per week, newest first."""
    body = "".join(f"<tr><td>{d}</td>" + "".join(f'<td class="text-end">{v}</td>' for v in values) + "</tr>" for d, *values in rows)
    return ('<table class="table-naaim"><thead><tr><th>Date</th><th>NAAIM Number</th><th>Bearish</th><th>Quarter 1</th>'
            f'<th>Quarter 2</th><th>Quarter 3</th><th>Bullish</th><th>Deviation</th></tr></thead><tbody>{body}</tbody></table>')


class BundleTests(unittest.TestCase):
    def test_the_bundled_history_runs_from_2006_to_the_last_free_file(self):
        weeks = naaim.bundled()
        self.assertEqual((len(weeks), min(weeks), max(weeks)), (1047, date(2006, 7, 5), date(2026, 7, 29)))
        self.assertEqual(weeks[date(2026, 7, 29)], {"naaim": 79.7, "bearish": 0.0, "quartile1": 60.0, "median": 90.0,
                                                    "quartile3": 100.0, "bullish": 200.0, "deviation": 48.99})
        self.assertEqual(weeks[date(2026, 7, 22)]["naaim"], 84.02)  # confirmed by CEIC and ISABELNET
        self.assertEqual(weeks[date(2022, 6, 22)]["naaim"], 19.86)  # the survey mean, not the file's mistyped copy
        self.assertTrue(all(-200 <= w["naaim"] <= 200 for w in weeks.values()))


class TableTests(unittest.TestCase):
    def test_weeks_are_read_from_the_public_table_and_future_rows_ignored(self):
        page = table(("12/30/2026", 150, -200, 50, 100, 150, 200, 60), ("08/05/2026", 101.25, 0, 90, 100, 110, 200, 40.5),
                     ("not a date", 1, 1, 1, 1, 1, 1, 1), ("07/29/2026", 79.7, 0, 60, 90, 100, 200, 48.99))
        parsed = naaim.parse_table(page, date(2026, 11, 6))
        self.assertEqual(parsed["as_of"], "2026-08-05")
        self.assertEqual([d for d, _ in parsed["weeks"]], ["2026-07-29", "2026-08-05"])
        self.assertEqual(parsed["weeks"][1][1]["naaim"], 101.25)
        with self.assertRaisesRegex(ValueError, "No NAAIM weeks"):
            naaim.parse_table("<html>Subscribe to see the index</html>", date(2026, 11, 6))

    def test_newer_public_weeks_extend_the_bundle(self):
        fetched = {**naaim.parse_table(table(("08/05/2026", 101.25, 0, 90, 100, 110, 200, 40.5)), date(2026, 11, 6)), "status": "ok"}
        item = naaim.reading(fetched)
        self.assertEqual((item["as_of"], item["value"], item["reading"], item["signal"]), ("2026-08-05", 101.25, "101.25", "Leveraged long"))
        self.assertEqual((item["next_public"], item["source"], item["delayed"]), ("2026-11-11", "NAAIM's delayed public table", True))  # Aug 12, three months on
        self.assertEqual(item["history"][-2:], [["2026-07-29", 79.7], ["2026-08-05", 101.25]])
        self.assertEqual(item["quartiles"], [90.0, 100.0, 110.0])
        self.assertGreater(item["percentile"], 90)
        bundled_only = naaim.reading({"status": "unavailable"})
        self.assertEqual((bundled_only["as_of"], bundled_only["source"]), ("2026-07-29", "History bundled with the app; NAAIM's table unavailable"))
        self.assertEqual([naaim.signal(v) for v in (120, 100, 85, 60, 20)],
                         ["Leveraged long", "Heavily invested", "Heavily invested", "Moderately invested", "Defensive"])

    def test_a_report_saved_before_naaim_gains_its_card_and_chart_once(self):
        payload = {"macro_sentiment": {"cards": [{"key": "vix"}], "history": {"series": {"vix": {}}}}}
        naaim.apply_bundled(payload)
        cards = payload["macro_sentiment"]["cards"]
        self.assertEqual([c["key"] for c in cards], ["vix", "naaim"])
        self.assertEqual((cards[1]["as_of"], cards[1]["max_age"], cards[1]["name"]), ("2026-07-29", 120, "NAAIM exposure"))
        self.assertNotIn("history", cards[1])
        self.assertEqual(len(payload["macro_sentiment"]["history"]["series"]["naaim"]["points"]), 1047)
        naaim.apply_bundled(payload)
        self.assertEqual(len(payload["macro_sentiment"]["cards"]), 2)  # never added twice
        self.assertEqual(naaim.apply_bundled({"rows": []}), {"rows": []})  # no sentiment panel: left alone

    def test_the_sentiment_chart_carries_the_series_and_the_card_is_listed(self):
        series = sentiment.chart_history({}, {"naaim": [["2026-07-29", 79.7]]})["series"]["naaim"]
        self.assertEqual((series["points"], series["frequency"], series["unit"]), ([["2026-07-29", 79.7]], "weekly", "%"))
        self.assertEqual(sentiment.MACRO["naaim"][2], 120)


if __name__ == "__main__":
    unittest.main()
