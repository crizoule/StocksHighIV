"""Synthetic AAII-style spreadsheets only; never AAII's own file or real survey results."""
import os
import unittest
from datetime import date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import openpyxl

from highiv import aaii

TODAY = date(2026, 9, 18)


def aaii_rows():
    """AAII's layout: a title row, a heading split over two rows, a blank row, weeks, then summary rows."""
    return [
        [None, None, None, "Association address line"],
        [None, None, None, None, None, "Bullish"],
        ["Reported", None, None, None, None, "8-week"],
        ["Date", "Bullish", "Neutral", "Bearish", "Total", "Mov Avg"],
        [None] * 6,
        [date(2026, 9, 3), 0.40, 0.25, 0.35, 1.0, 0.38],
        [date(2026, 9, 10), 0.30, 0.20, 0.50, 1.0, 0.37],
        [date(2026, 9, 17), 0.25, 0.15, 0.60, 1.0, 0.36],
        [date(2026, 9, 24), 0.90, 0.05, 0.05, 1.0, 0.40],  # future: not yet reported
        [date(2026, 8, 27), 0.50, 0.50, 0.50, 1.5, 0.39],  # does not total 100
        ["Avg", 0.37, 0.31, 0.32],
        ["Count '25", 52, 52, 52],
    ]


def save_xlsx(path, rows, mtime=None):
    book = openpyxl.Workbook()
    for row in rows:
        book.active.append([datetime(v.year, v.month, v.day) if isinstance(v, date) else v for v in row])
    book.save(path)
    if mtime:
        os.utime(path, (mtime, mtime))


class ParseTests(unittest.TestCase):
    def test_reads_latest_reported_week_and_skips_summary_future_and_invalid_rows(self):
        weeks, label = aaii.weekly_results([aaii_rows()], TODAY)
        self.assertEqual(label, "Reported")
        self.assertEqual(sorted(weeks), [date(2026, 9, 3), date(2026, 9, 10), date(2026, 9, 17)])
        self.assertEqual([round(v, 6) for v in weeks[date(2026, 9, 17)]], [25, 15, 60])

    def test_percent_text_csv_and_week_ending_heading(self):
        csv = b"Week ending,Bullish,Neutral,Bearish\n09/16/2026,28.8%,17.9%,53.3%\n09/09/2026,38%,23%,39%\n"
        weeks, label = aaii.weekly_results(aaii.read_sheets(csv), TODAY)
        self.assertEqual(label, "Week ending")
        self.assertEqual(weeks[date(2026, 9, 16)], [28.8, 17.9, 53.3])

    def test_saved_web_page_or_missing_columns_is_a_clear_error(self):
        with self.assertRaisesRegex(ValueError, "web page"):
            aaii.read_sheets(b"<!DOCTYPE html><title>Pardon Our Interruption</title>")
        with self.assertRaisesRegex(ValueError, "Bullish / Neutral / Bearish"):
            aaii.weekly_results([[["Date", "Bulls", "Bears"], ["09/17/2026", 1, 2]]], TODAY)


class ImportTests(unittest.TestCase):
    def test_newest_matching_file_in_any_folder_becomes_the_reading(self):
        with TemporaryDirectory() as downloads, TemporaryDirectory() as imports:
            old, new = Path(imports) / "sentiment.xlsx", Path(downloads) / "sentiment (1).xlsx"
            save_xlsx(old, aaii_rows()[:7], mtime=1_789_000_000)  # through Sep 10
            save_xlsx(new, aaii_rows(), mtime=1_789_700_000)
            save_xlsx(Path(downloads) / "portfolio.xlsx", aaii_rows())  # not AAII's file name
            found, notes = aaii.imported(TODAY, folders=[imports, downloads])
        self.assertEqual(notes, [])
        self.assertEqual((found["as_of"], found["value"], found["signal"]), ("2026-09-17", -35.0, "Bearish tilt"))
        self.assertEqual((found["source_file"], found["date_label"], found["weeks"]), ("sentiment (1).xlsx", "reported", 3))
        self.assertIn("Reported 2026-09-17: 25% bullish / 15% neutral / 60% bearish", found["detail"])
        self.assertIn("Long-run averages since 2026-09-03", found["detail"])

    def test_unchanged_file_reuses_the_saved_import_and_a_removed_file_keeps_it(self):
        with TemporaryDirectory() as folder:
            save_xlsx(Path(folder) / "sentiment.xlsx", aaii_rows())
            saved, _ = aaii.imported(TODAY, folders=[folder])
            with patch.object(aaii, "reading", side_effect=AssertionError("parsed again")):
                self.assertIs(aaii.imported(TODAY, saved, [folder])[0], saved)
            (Path(folder) / "sentiment.xlsx").unlink()
            self.assertIs(aaii.imported(TODAY, saved, [folder])[0], saved)

    def test_unreadable_files_and_folders_are_reported_without_stopping(self):
        with TemporaryDirectory() as folder:
            (Path(folder) / "sentiment.xls").write_text("<html>Pardon Our Interruption</html>")
            save_xlsx(Path(folder) / "sentiment (2).xlsx", aaii_rows(), mtime=1_700_000_000)
            found, notes = aaii.imported(TODAY, folders=[folder, Path(folder) / "missing"])
            self.assertEqual(found["source_file"], "sentiment (2).xlsx")
            self.assertEqual(len(notes), 1)
            self.assertIn("sentiment.xls could not be read", notes[0])
            with patch.object(Path, "iterdir", side_effect=PermissionError("denied")):
                found, notes = aaii.imported(TODAY, folders=[folder])
            self.assertIsNone(found)
            self.assertIn("cannot read", notes[0])


class EnteredWeekTests(unittest.TestCase):
    def test_saved_week_becomes_the_reading_with_published_long_run_averages(self):
        with TemporaryDirectory() as data:
            reading = aaii.save_week(data, "2026-09-16", 28.8, 17.9, 53.3, date(2026, 9, 19))
            self.assertEqual((reading["as_of"], reading["value"], reading["signal"]), ("2026-09-16", -24.5, "Bearish tilt"))
            self.assertEqual((reading["date_label"], reading["entered"], reading["status"]), ("week ending", True, "ok"))
            self.assertIn("long-run averages: 37.5% bullish / 31% neutral / 31.5% bearish", reading["detail"])
            aaii.save_week(data, "2026-09-09", 40, 30, 30, date(2026, 9, 19))  # an older correction never hides the latest week
            self.assertEqual(aaii.entered(data)["as_of"], "2026-09-16")
            self.assertEqual(sorted(aaii.entered_weeks(data)), [date(2026, 9, 9), date(2026, 9, 16)])

    def test_invalid_weeks_are_rejected_with_a_reason(self):
        with TemporaryDirectory() as data:
            for args, reason in [(("2026-09-23", 30, 30, 40), "today or earlier"), (("2026-06-01", 30, 30, 40), "last 10 weeks"),
                                 (("09/16/2026", 30, 30, 40), "week-ending date"), (("2026-09-16", 30, 30, 30), "add up to 90%"),
                                 (("2026-09-16", 130, -10, -20), "between 0 and 100"), (("2026-09-16", True, 50, 49), "between 0 and 100"),
                                 (("2026-09-16", "28.8", 17.9, 53.3), "between 0 and 100")]:
                with self.assertRaisesRegex(ValueError, reason):
                    aaii.save_week(data, *args, date(2026, 9, 19))
            self.assertIsNone(aaii.entered(data))

    def test_latest_survey_week_wins_across_page_entry_and_spreadsheet(self):
        spreadsheet = {"status": "ok", "as_of": "2026-09-17", "date_label": "reported", "value": 1}  # reported Thursday
        entered = {"status": "ok", "as_of": "2026-09-16", "date_label": "week ending", "value": 2}  # the same survey week
        later = {**entered, "as_of": "2026-09-23", "value": 3}
        self.assertEqual(aaii.week_of(spreadsheet), date(2026, 9, 16))
        self.assertIs(aaii.newest(entered, spreadsheet), entered)  # same week: the earlier argument wins
        self.assertIs(aaii.newest(spreadsheet, later), later)
        self.assertIs(aaii.newest({"status": "unavailable", "as_of": "2026-09-30"}, spreadsheet), spreadsheet)
        self.assertIsNone(aaii.newest({"status": "unavailable"}, None))

    def test_saved_report_shows_a_newer_entered_week_but_keeps_its_card_identity(self):
        with TemporaryDirectory() as data:
            aaii.save_week(data, "2026-09-16", 28.8, 17.9, 53.3, date(2026, 9, 19))
            card = {"key": "aaii", "name": "AAII sentiment", "url": "https://example.com", "max_age": 10,
                    "status": "unavailable", "error": "Provider blocked"}
            payload = aaii.apply_entered({"macro_sentiment": {"cards": [card]}}, data)
            shown = payload["macro_sentiment"]["cards"][0]
            self.assertEqual((shown["name"], shown["url"], shown["reading"]), ("AAII sentiment", "https://example.com", "-24.5 pp"))
            self.assertNotIn("error", shown)
            newer = {**card, "status": "ok", "as_of": "2026-09-23", "value": 5.0}
            self.assertIs(aaii.apply_entered({"macro_sentiment": {"cards": [newer]}}, data)["macro_sentiment"]["cards"][0], newer)
            self.assertEqual(aaii.apply_entered({"rows": []}, data), {"rows": []})

    def test_bundled_history_is_weekly_since_1987_and_its_latest_week_is_a_reading(self):
        weeks = aaii.bundled()
        self.assertEqual(min(weeks), date(1987, 7, 24))
        self.assertGreater(len(weeks), 2000)
        self.assertTrue(all(abs(sum(v) - 100) <= 0.6 for v in weeks.values()))
        reading = aaii.bundled_reading()
        self.assertEqual((reading["as_of"], reading["date_label"], reading["status"]), (max(weeks).isoformat(), "reported", "ok"))

    def test_chart_series_is_by_survey_week_with_entries_and_newer_readings_on_top(self):
        with TemporaryDirectory() as data, patch.object(aaii, "bundled", return_value={date(2026, 9, 10): [40.0, 30.0, 30.0]}):
            aaii.save_week(data, "2026-09-16", 28.8, 17.9, 53.3, date(2026, 9, 19))
            live = {"status": "ok", "as_of": "2026-09-23", "value": 5.0}
            self.assertEqual(aaii.spread_series(data, live, {"status": "unavailable"}, None),
                             [["2026-09-09", 10.0], ["2026-09-16", -24.5], ["2026-09-23", 5.0]])
            payload = {"macro_sentiment": {"cards": [], "history": {"series": {"aaii": {"points": [["2026-09-09", 10.0]]}}}}}
            aaii.apply_entered(payload, data)
            self.assertEqual(payload["macro_sentiment"]["history"]["series"]["aaii"]["points"],
                             [["2026-09-09", 10.0], ["2026-09-16", -24.5]])

    def test_spreadsheets_are_read_only_from_the_imports_folder(self):
        self.assertEqual([p.name for p in aaii.import_folders()], ["imports"])


if __name__ == "__main__":
    unittest.main()
