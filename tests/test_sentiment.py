"""Synthetic sentiment evidence; never assertions about actual securities."""
import copy
import json
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from highiv import sentiment as s

NOW = datetime(2026, 9, 18, 15, tzinfo=timezone.utc)
TODAY = NOW.date()


def prices(move=10, end=date(2026, 9, 17)):
    days = [end - timedelta(days=i) for i in range(50) if (end - timedelta(days=i)).weekday() < 5][::-1][-21:]
    return {"prices": {"1D": {"t": [datetime(d.year, d.month, d.day, 16, tzinfo=timezone.utc).timestamp() for d in days],
                              "c": [100 + move * i / 20 for i in range(21)]}}}


def row(**kwargs):
    return dict(symbol="TEST", yahoo_symbol="TEST", market="US", sector="Technology", **prices(), **kwargs)


def inputs():
    return {"benchmarks": {"XLK": prices(5), "SPY": prices(2)}}


def evaluate(r=None, sources=None):
    payload = {"rows": [r or row()]}
    s.evaluate(payload, sources if sources is not None else inputs(), now=NOW)
    return payload


class MacroTests(unittest.TestCase):
    def test_vix_uses_latest_nonfuture_observation(self):
        data = "DATE,OPEN,HIGH,LOW,CLOSE\n09/17/2026,1,1,1,15.44\n09/19/2026,1,1,1,80\n"
        r = s.parse_vix(data, TODAY)
        self.assertEqual((r["as_of"], r["value"], r["signal"]), ("2026-09-17", 15.44, "Calm"))
        with self.assertRaises(ValueError):
            s.parse_vix(data.replace("15.44", "NaN"), TODAY)

    def test_put_call_requires_selected_session_and_does_not_use_index_as_equity(self):
        data = json.dumps({"ratios": [{"name": "INDEX PUT/CALL RATIO", "value": "1.2"},
                                     {"name": "EQUITY PUT/CALL RATIO", "value": "0.52"}], "selectedDate": "2026-09-17"})
        parsed = s.parse_put_call(data.replace('"', '\\"'), TODAY)
        self.assertEqual(parsed["value"], .52)
        self.assertEqual(parsed["ratios"]["index"], 1.2)
        with self.assertRaises(ValueError):
            s.parse_put_call(data.replace("selectedDate", "date"), TODAY)
        with self.assertRaises(ValueError):
            s.parse_put_call(data + '{"selectedDate":"2026-09-16"}', TODAY)

    def test_aaii_ignores_averages_and_rejects_undated_or_invalid_percentages(self):
        html = "<p>Week ending September 16, 2026</p><p>Bullish 28.8% Avg 37.5% Neutral 17.9% Avg 31.0% Bearish 53.3%</p>"
        r = s.parse_aaii(html, TODAY)
        self.assertEqual((r["value"], r["as_of"]), (-24.5, "2026-09-16"))
        for bad in [html.replace("Week ending", "Average"), html.replace("53.3", "99.9"), "Access denied"]:
            with self.assertRaises(ValueError):
                s.parse_aaii(bad, TODAY)

    def test_cnn_requires_valid_score_and_provider_timestamp(self):
        raw = {"fear_and_greed": {"score": 30, "rating": "fear", "timestamp": "2026-09-17T20:00:00Z"}}
        self.assertEqual(s.parse_cnn(json.dumps(raw), TODAY)["signal"], "Fear")
        raw["fear_and_greed"]["score"] = 105
        with self.assertRaises(ValueError):
            s.parse_cnn(json.dumps(raw), TODAY)

    def test_freshness_and_failed_refresh_preserve_observation_dates(self):
        sources = {"macro": {"vix": {"status": "cached", "value": 15, "as_of": "2026-09-01"},
                              "aaii": {"status": "ok", "value": -20, "as_of": "2026-09-09"}}}
        cards = evaluate(sources=sources)["macro_sentiment"]["cards"]
        self.assertEqual([c["status"] for c in cards], ["stale", "unavailable", "ok", "unavailable", "unavailable"])
        self.assertEqual(cards[0]["as_of"], "2026-09-01")

    def test_replica_is_a_cross_check_and_stands_in_only_without_a_fresh_cnn_reading(self):
        replica = {"status": "ok", "value": 31.2, "reading": "31.2/100", "signal": "Fear", "direction": -1,
                   "as_of": "2026-09-17", "coverage": 7, "components": [], "detail": "Replica", "method": "Method"}
        cnn = {"status": "ok", "value": 28.6, "reading": "28.6/100", "signal": "Fear", "direction": -1, "as_of": "2026-09-18"}
        card = lambda sources: evaluate(sources={"macro": sources})["macro_sentiment"]["cards"][3]
        both = card({"cnn": cnn, "fear_greed": replica})
        self.assertEqual((both["name"], both["value"], both["replica"]["value"]), ("Fear & Greed", 28.6, 31.2))
        self.assertNotIn("replica_of", both)
        for missing in ({}, {"cnn": {**cnn, "as_of": "2026-09-01", "status": "cached"}}):
            stand_in = card({**missing, "fear_greed": replica})
            self.assertEqual((stand_in["name"], stand_in["value"], stand_in["replica_of"]),
                             ("Fear & Greed replica", 31.2, "CNN Fear & Greed"))
        self.assertEqual(card({"cnn": {**cnn, "as_of": "2026-09-01"}, "fear_greed": replica})["cnn_status"], "stale")
        stale_replica = card({"fear_greed": {**replica, "as_of": "2026-09-01"}})
        self.assertEqual((stale_replica["name"], stale_replica["status"]), ("Fear & Greed", "unavailable"))

    def test_aaii_card_uses_the_latest_week_from_the_page_an_entry_or_an_imported_spreadsheet(self):
        imported = {"status": "ok", "value": -35.0, "reading": "-35.0 pp", "signal": "Bearish tilt", "direction": -1,
                    "as_of": "2026-09-10", "date_label": "reported", "source_file": "sentiment.xls",
                    "file_saved": "2026-09-18T12:00:00+00:00", "signature": ["sentiment.xls", 1, 1]}
        blocked = {"status": "unavailable", "error": "Provider blocked"}
        bundled = {"status": "ok", "value": -20.0, "as_of": "2026-09-10", "date_label": "reported", "source": "bundled"}
        with TemporaryDirectory() as temp, patch.object(s.config, "DATA_DIR", Path(temp)), \
                patch.object(s.aaii, "bundled_reading", return_value=bundled):
            with patch.object(s.aaii, "imported", return_value=(imported, [])):
                self.assertEqual(s.with_aaii_import(blocked, TODAY)["source_file"], "sentiment.xls")  # same week: import first
                s.aaii.save_week(temp, "2026-09-16", 28.8, 17.9, 53.3, TODAY)
                item = s.with_aaii_import(blocked, TODAY)
                self.assertEqual((item["value"], item["entered"]), (-24.5, True))  # the entered week is newer
                newer = {"status": "ok", "value": 5.0, "as_of": "2026-09-23"}
                self.assertEqual(s.with_aaii_import(newer, date(2026, 9, 24))["value"], 5.0)
            stored = json.loads((Path(temp) / "sentiment" / "aaii-import.json").read_text(encoding="utf-8"))
            self.assertEqual(stored["as_of"], "2026-09-10")  # kept for later refreshes, even if the file goes away
            with patch.object(s.aaii, "imported", return_value=(None, ["sentiment.xls could not be read."])):
                item = s.with_aaii_import(blocked, TODAY)
            self.assertEqual(item["import_note"], "sentiment.xls could not be read.")
        with TemporaryDirectory() as temp, patch.object(s.config, "DATA_DIR", Path(temp)), \
                patch.object(s.aaii, "imported", return_value=(None, [])):
            self.assertEqual(s.with_aaii_import(blocked, TODAY)["source"], "AAII history bundled with the app")
        card = evaluate(sources={"macro": {"aaii": {**imported, "as_of": "2026-09-03"}}})["macro_sentiment"]["cards"][2]
        self.assertEqual(card["status"], "stale")  # a spreadsheet more than 10 days old is excluded like any AAII reading

    def test_vix_and_cnn_keep_dated_history_for_the_chart(self):
        rows = "DATE,OPEN,HIGH,LOW,CLOSE\n01/02/2015,1,1,1,19.2\n09/16/2026,1,1,1,17.71\n09/17/2026,1,1,1,15.44\n"
        self.assertEqual(s.parse_vix(rows, TODAY)["history"], [["2015-01-02", 19.2], ["2026-09-16", 17.71], ["2026-09-17", 15.44]])
        old = rows.replace("01/02/2015,1,1,1,19.2", "01/01/1985,1,1,1,30\n01/05/2015,1,1,1,18\n01/06/2015,1,1,1,17\n01/08/2015,1,1,1,16")
        self.assertEqual(s.parse_vix(old, TODAY)["history"][:2], [["2015-01-06", 17.0], ["2015-01-08", 16.0]])  # weekly before 10 years, none before 1987
        raw = {"fear_and_greed": {"score": 28.6, "rating": "fear", "timestamp": "2026-09-18T18:13:36+00:00"},
               "fear_and_greed_historical": {"data": [{"x": 1789603200000, "y": 28.29}, {"x": 1789689600000, "y": "bad"},
                                                      {"x": 1789862400000, "y": 30}]}}
        self.assertEqual(s.parse_cnn(json.dumps(raw), TODAY)["history"], [["2026-09-17", 28.3]])  # no bad or future points
        asked = []
        def fake(client, url):
            asked.append(url)
            if url != s.CNN_URL:
                raise ValueError("Provider unavailable (HTTP 500)")
            return json.dumps(raw)
        with patch.object(s, "request_text", fake):
            self.assertEqual(s.fetch_cnn(None, TODAY)["value"], 28.6)
        self.assertEqual(asked, [f"{s.CNN_URL}/2021-09-18", s.CNN_URL])

    def test_put_call_chart_series_is_a_five_session_equity_average(self):
        sessions = {f"2026-09-{d:02d}": {"equity": [100, p], "etp": [1, 1]} for d, p in zip((8, 9, 10, 11, 14, 15), (50, 60, 70, 80, 90, 100))}
        with TemporaryDirectory() as temp, patch.object(s.config, "DATA_DIR", Path(temp)):
            self.assertEqual(s.put_call_series(), [])
            s.write_json(Path(temp) / "sentiment" / "put_call_history.json", {"sessions": sessions})
            self.assertEqual(s.put_call_series(), [["2026-09-14", 0.7], ["2026-09-15", 0.8]])

    def test_weekly_thinning_keeps_each_week_s_last_point_before_the_cutoff(self):
        rows = [(date(2016, 9, 5) + timedelta(days=i), float(i)) for i in range(10)]  # Mon Sep 5 to Wed Sep 14
        self.assertEqual(s.thinned(rows, date(2016, 9, 12)),
                         [(date(2016, 9, 7), 2.0), (date(2016, 9, 11), 6.0), (date(2016, 9, 12), 7.0),
                          (date(2016, 9, 13), 8.0), (date(2016, 9, 14), 9.0)])

    def test_cboe_archive_rows_are_read_after_the_disclaimer_and_joined_without_bridging_gaps(self):
        text = ('Cboe data is provided for informational purposes only\ufffd,,,,\nEquity P/C Ratios,,,,\n'
                'Date,Equity Call Volume,Equity Put Volume,Equity Total Volume,Equity P/C Ratio\n'
                + "".join(f"{(date(2003, 10, 1) + timedelta(days=i)).strftime('%m/%d/%Y')},1000,{400 + i},0,0\n" for i in range(120))
                + "bad,row\n")
        daily = s.parse_put_call_archive(text)
        self.assertEqual((len(daily), daily[date(2003, 10, 2)]), (120, 0.401))
        with self.assertRaises(ValueError):
            s.parse_put_call_archive("DATE,CALL,PUT\n10/1/2003,1,1\n")
        archive = [[f"2019-10-{d:02d}", 0.5] for d in (1, 2, 3, 4, 7)]
        stored = {f"2026-09-{d:02d}": {"equity": [100, 70], "etp": [1, 1]} for d in (8, 9, 10, 11, 14)}
        with TemporaryDirectory() as temp, patch.object(s.config, "DATA_DIR", Path(temp)):
            s.write_json(Path(temp) / "sentiment" / "put_call_history.json", {"sessions": stored})
            self.assertEqual(s.put_call_series(archive), [["2019-10-07", 0.5], ["2026-09-14", 0.7]])  # no average spans the gap

    def test_put_call_backfill_reaches_the_end_of_cboes_archive(self):
        sessions = [date(2019, 10, 1) + timedelta(days=i) for i in range(1200)]
        archive = [["2003-10-17", 0.7], ["2019-10-04", 0.62]]
        window = s.put_call_window(sessions, archive, 649)
        self.assertEqual((window[0], window[-1], len(window)), (date(2019, 10, 5), sessions[-1], 1196))
        self.assertEqual(s.put_call_window(sessions, [], 649), sessions[-649:])  # no archive: only the rank window
        caught_up = [["2003-10-17", 0.7], [sessions[-1].isoformat(), 0.62]]
        self.assertEqual(s.put_call_window(sessions, caught_up, 649), sessions[-649:])

    def test_rsi_and_macd_come_from_the_saved_daily_closes(self):
        import pandas as pd
        rising = pd.Series([100 + i for i in range(60)], index=pd.date_range("2026-01-01", periods=60, freq="B"), dtype=float)
        self.assertAlmostEqual(s.rsi(rising).iloc[-1], 100)  # only gains
        self.assertEqual(len(s.rsi(rising)), 60 - s.RSI_PERIOD)
        line, signal = s.macd(rising)
        self.assertEqual(len(line), 60 - s.MACD_SLOW - s.MACD_SIGNAL)
        self.assertGreater(line.iloc[-1], 0)  # a climb keeps MACD above zero
        climbing = pd.Series([100 * 1.01 ** i for i in range(80)], index=pd.date_range("2026-01-01", periods=80, freq="B"))
        faster, slower = s.macd(climbing)
        self.assertTrue(faster.iloc[-1] > slower.iloc[-1] > 0)  # accelerating: MACD pulls above its signal
        falling = pd.Series(rising.to_numpy()[::-1], index=rising.index)
        self.assertLess(s.macd(falling)[0].iloc[-1], 0)
        history = {"spx": {"points": [["2026-09-17", 7637.76]], "rsi": [["2026-09-17", 54.1]],
                           "macd": [["2026-09-17", 0.42]], "macd_signal": [["2026-09-17", 0.31]]}}
        series = s.chart_history({}, history)["series"]
        self.assertEqual(series["rsi"]["points"], [["2026-09-17", 54.1]])
        self.assertEqual((series["macd"]["points"], series["macd"]["signal"]), ([["2026-09-17", 0.42]], [["2026-09-17", 0.31]]))
        self.assertEqual(s.chart_history({}, {})["series"]["macd"]["points"], [])

    def test_chart_history_prefers_cnn_and_cards_never_carry_it(self):
        replica = {"status": "ok", "value": 31.0, "as_of": "2026-09-17", "history": [["2026-09-17", 31.0]]}
        short_cnn = {"status": "ok", "value": 28.6, "as_of": "2026-09-18", "history": [["2026-09-17", 28.3]]}
        chart = s.chart_history({"cnn": short_cnn, "fear_greed": replica}, {"spx": {"points": [["2026-09-17", 7637.76]]}})
        self.assertEqual((chart["series"]["fear_greed"]["name"], chart["series"]["fear_greed"]["points"]),
                         ("Fear & Greed replica", [["2026-09-17", 31.0]]))
        long_cnn = {**short_cnn, "history": [[f"2026-08-{d:02d}", 40.0] for d in range(1, 26)]}
        self.assertEqual(s.chart_history({"cnn": long_cnn}, {})["series"]["fear_greed"]["name"], "Fear & Greed")
        # CNN's feed reaches back about five years; older replica scores continue the line and the source says so.
        older = {**replica, "history": [["2009-08-05", 55.0], ["2026-07-31", 44.0], ["2026-08-01", 39.0]]}
        spliced = s.chart_history({"cnn": long_cnn, "fear_greed": older}, {})["series"]["fear_greed"]
        self.assertEqual(spliced["points"][:3], [["2009-08-05", 55.0], ["2026-07-31", 44.0], ["2026-08-01", 40.0]])
        self.assertEqual(len(spliced["points"]), 27)  # two replica points, then CNN's own 25
        self.assertEqual(spliced["source"], "CNN from 2026-08-01; replica from public data 2009-08-05 to then")
        self.assertEqual(s.chart_history({"cnn": long_cnn}, {})["series"]["fear_greed"]["source"], "CNN from 2026-08-01")
        sources = {"macro": {"vix": {"status": "ok", "value": 15.4, "as_of": "2026-09-17", "history": [["2026-09-17", 15.4]]},
                             "cnn": long_cnn, "fear_greed": replica}, "history": {"spx": {"points": [["2026-09-17", 7637.76]]}}}
        macro = evaluate(sources=sources)["macro_sentiment"]
        self.assertTrue(all("history" not in card and "history" not in (card.get("replica") or {}) for card in macro["cards"]))
        self.assertEqual(macro["history"]["series"]["vix"]["points"], [["2026-09-17", 15.4]])
        self.assertEqual(macro["history"]["spx"], [["2026-09-17", 7637.76]])

    def test_put_call_history_fills_newest_first_and_never_relabels_a_session(self):
        days = [date(2026, 9, d) for d in (14, 15, 16, 17)]
        page = lambda day: '{\\"selectedDate\\":\\"%s\\",\\"EQUITY OPTIONS\\":[{\\"name\\":\\"VOLUME\\",\\"call\\":2,\\"put\\":1}],' \
                           '\\"EXCHANGE TRADED PRODUCTS\\":[{\\"name\\":\\"VOLUME\\",\\"call\\":2,\\"put\\":1}]}' % day
        asked = []
        def fake(client, url, params):
            asked.append(params["dt"])
            return page("2026-09-14" if params["dt"] == "2026-09-15" else params["dt"])
        with TemporaryDirectory() as temp, patch.object(s.config, "DATA_DIR", Path(temp)), \
                patch.object(s, "request_text", fake), patch.object(s.config, "CBOE_MAX_PER_MINUTE", 10**6):
            history = s.put_call_history(None, days, s.time.monotonic() + 60, workers=1)
            self.assertEqual(asked, ["2026-09-17", "2026-09-16", "2026-09-15", "2026-09-14"])
            self.assertEqual(sorted(history), ["2026-09-14", "2026-09-16", "2026-09-17"])
            asked.clear()
            s.put_call_history(None, days, s.time.monotonic() + 60, workers=1)
            self.assertEqual(asked, ["2026-09-15"])  # stored sessions are never fetched again
            asked.clear()
            s.put_call_history(None, days, s.time.monotonic() - 1, workers=1)
            self.assertEqual(asked, [])  # an exhausted budget sends nothing

    def test_a_fresh_cache_with_too_little_history_is_fetched_again(self):
        short = {"points": [["2016-09-19", 2139.12], ["2026-09-17", 7637.76]]}
        full = {"points": [["1987-07-01", 302.94], ["2026-09-17", 7637.76]], "rsi": [["2026-09-17", 54.1]]}
        spx, vix = s.COMPLETE[("history", "spx")], s.COMPLETE[("macro", "vix")]
        self.assertEqual((spx(short), spx(full), spx({"points": []})), (False, True, False))
        self.assertFalse(spx({k: v for k, v in full.items() if k != "rsi"}))  # saved before RSI and MACD were computed
        self.assertEqual((vix({"history": [["2016-09-12", 15.2]]}), vix({"history": [["1990-01-02", 17.24]]})), (False, True))
        with TemporaryDirectory() as temp, patch.object(s.config, "DATA_DIR", Path(temp)):
            s.cached_read("history-spx", lambda: short, NOW)  # saved by 1.5.0, an hour earlier
            later = NOW + timedelta(hours=1)
            self.assertEqual(s.cached_read("history-spx", lambda: full, later, complete=spx)["points"][0][0], "1987-07-01")
            self.assertEqual(s.cached_read("history-spx", lambda: short, later, complete=spx)["points"][0][0], "1987-07-01")  # now reused
            def blocked():
                raise ValueError("offline")
            s.write_json(Path(temp) / "sentiment" / "history-spx.json", {**short, "fetched_at": NOW.isoformat(), "status": "ok"})
            kept = s.cached_read("history-spx", blocked, later, complete=spx)
            self.assertEqual((kept["status"], kept["points"][0][0]), ("cached", "2016-09-19"))  # offline: the shorter copy beats nothing

    def test_a_reading_of_the_latest_close_is_reused_until_the_next_open(self):
        ny = s.context.MARKET_TZ
        at = lambda *args: datetime(*args, tzinfo=ny)
        self.assertEqual(s.market.last_session(at(2026, 9, 18, 16, 29))[0].isoformat(), "2026-09-17")
        self.assertEqual(s.market.last_session(at(2026, 9, 18, 16, 30)), (date(2026, 9, 18), at(2026, 9, 21, 9, 30)))  # Friday → Monday
        self.assertEqual(s.market.last_session(at(2026, 9, 20, 12))[0].isoformat(), "2026-09-18")  # Sunday
        friday = {"as_of": "2026-09-18", "value": 15, "fetched_at": at(2026, 9, 18, 17).isoformat()}
        self.assertTrue(s.settled(friday, at(2026, 9, 21, 9, 29)))  # the whole weekend
        self.assertFalse(s.settled(friday, at(2026, 9, 21, 9, 30)))  # Monday's session has opened
        self.assertFalse(s.settled({**friday, "as_of": "2026-09-17"}, at(2026, 9, 19, 12)))  # fetched before Cboe posted Friday
        self.assertFalse(s.settled({**friday, "fetched_at": at(2026, 9, 18, 16).isoformat()}, at(2026, 9, 19, 12)))  # before the close settled
        self.assertTrue(s.settled({"points": [["2026-09-18", 1]], "fetched_at": friday["fetched_at"]}, at(2026, 9, 19, 12)))
        bars = {"prices": {"1D": {"t": [int(at(2026, 9, 18, 0).timestamp())], "c": [1]}}, "fetched_at": friday["fetched_at"]}
        self.assertTrue(s.settled(bars, at(2026, 9, 19, 12)))
        filling = {**friday, "components": [{"detail": "Building Cboe history: 100/649 sessions stored"}]}
        self.assertFalse(s.settled(filling, at(2026, 9, 19, 12)))  # the backfill keeps going
        with TemporaryDirectory() as temp, patch.object(s.config, "DATA_DIR", Path(temp)):
            s.write_json(Path(temp) / "sentiment" / "macro-vix.json", friday)
            fetched = []
            fetch = lambda: fetched.append(1) or {"as_of": "2026-09-18", "value": 16}
            self.assertEqual(s.cached_read("macro-vix", fetch, at(2026, 9, 20, 12), sessions=True)["value"], 15)
            self.assertEqual(s.cached_read("macro-vix", fetch, at(2026, 9, 20, 12))["value"], 16)  # news and social: expiry only
            self.assertEqual(len(fetched), 1)

    def test_cot_reads_asset_manager_positioning_with_a_three_year_index(self):
        start = date(2023, 9, 19)
        def rows(code, asset_net, lev_net=-50):
            return [{"cftc_contract_market_code": code, "report_date_as_yyyy_mm_dd": f"{start + timedelta(weeks=i)}T00:00:00.000",
                     "open_interest_all": "1000", "asset_mgr_positions_long": str(100 + max(net, 0)),
                     "asset_mgr_positions_short": str(100 + max(-net, 0)),
                     "lev_money_positions_long": "100", "lev_money_positions_short": str(100 - lev_net)} for i, net in enumerate(asset_net)]
        weeks = s.COT_LOOKBACK
        data = rows("13874A", [i for i in range(weeks - 1)] + [400]) + rows("1170E1", [-i for i in range(weeks)])
        data.append({"cftc_contract_market_code": "13874A", "report_date_as_yyyy_mm_dd": "2030-01-01T00:00:00.000"})  # malformed: skipped
        item = s.parse_cot(data, date(2026, 9, 19))
        self.assertEqual(item["as_of"], (start + timedelta(weeks=weeks - 1)).isoformat())
        self.assertEqual((item["value"], item["reading"], item["index"]), (40.0, "+40.0% of OI", 100))
        self.assertEqual((item["signal"], item["direction"]), ("Institutions heavily long", 1))
        spx, vix = item["groups"]
        self.assertEqual((spx["leveraged"], spx["leveraged_index"]), (-50, 0))  # never above its own past: ranks 0
        self.assertEqual((vix["name"], vix["asset_managers"], vix["asset_managers_index"]), ("VIX futures", -155, 0))
        self.assertEqual(len(item["history"]), weeks)
        self.assertNotIn("history", spx)
        with self.assertRaisesRegex(ValueError, "Too little COT history"):
            s.parse_cot(data[:40], date(2026, 9, 19))
        with self.assertRaisesRegex(ValueError, "Too little COT history"):  # weeks after today are never read
            s.parse_cot(data, start + timedelta(weeks=weeks - 2))

    def test_cache_failure_never_replaces_fetch_time_with_now(self):
        with TemporaryDirectory() as temp, patch.object(s.config, "DATA_DIR", Path(temp)):
            saved = s.cached_read("vix", lambda: {"as_of": "2026-09-17", "value": 15}, NOW)
            def fail():
                raise ValueError("blocked")
            cached = s.cached_read("vix", fail, NOW + timedelta(days=1))
            self.assertEqual(cached["status"], "cached")
            self.assertEqual(cached["fetched_at"], saved["fetched_at"])
            self.assertEqual(cached["as_of"], saved["as_of"])
            self.assertEqual(s.cached_read("aaii", fail, NOW)["status"], "unavailable")


class StockTests(unittest.TestCase):
    def test_price_only_score_is_explicit_and_missing_opinion_is_not_neutral(self):
        result = evaluate()["rows"][0]["sentiment"]
        stock, sector, news, social = result["components"]
        self.assertEqual(result["mode"], "Price only")
        self.assertEqual(result["coverage"], 70)
        self.assertEqual(result["score"], round((stock["score"] * 40 + sector["score"] * 30) / 70))
        self.assertIsNone(news["score"])
        self.assertIsNone(social["score"])

    def test_no_rank_without_fresh_matched_sector_and_stock(self):
        for benchmarks in [{}, {"XLK": prices(10, date(2026, 9, 16)), "SPY": prices()},
                           {"XLK": prices(10, date(2026, 9, 16)), "SPY": prices(0, date(2026, 9, 16))}]:
            self.assertIsNone(evaluate(sources={"benchmarks": benchmarks})["rows"][0]["sentiment"]["score"])
        r = row()
        r.update(prices(end=date(2026, 9, 1)))
        self.assertIsNone(evaluate(r)["rows"][0]["sentiment"]["score"])

    def test_intraday_and_future_bars_cannot_change_score(self):
        r = row()
        original = evaluate(copy.deepcopy(r))["rows"][0]["sentiment"]["score"]
        r["prices"]["1D"]["t"].extend([NOW.timestamp(), (NOW + timedelta(days=1)).timestamp()])
        r["prices"]["1D"]["c"].extend([10000, 10000])
        self.assertEqual(evaluate(r)["rows"][0]["sentiment"]["score"], original)

    def test_news_requires_exact_ticker_freshness_diverse_sources_and_keeps_evidence(self):
        articles = [{"time_published": "20260917T130000", "url": f"https://example.com/{i}", "title": "Example news",
                     "source": f"Source{i % 2}", "ticker_sentiment": [{"ticker": "TEST", "relevance_score": "0.8", "ticker_sentiment_score": "0.6"}]} for i in range(3)]
        feed = {"feed": articles, "status": "ok"}
        part = s.news_component(row(), feed, NOW)
        self.assertEqual(part["score"], 80)
        self.assertEqual(len(part["evidence"]), 3)
        for modified in [articles[:2], [articles[0]] * 3, [{**a, "source": "same"} for a in articles],
                         [{**a, "time_published": "20260919T130000"} for a in articles]]:
            self.assertIsNone(s.news_component(row(), {"feed": modified}, NOW)["score"])
        self.assertIsNone(s.news_component({**row(), "symbol": "ELSE", "yahoo_symbol": "ELSE"}, feed, NOW)["score"])
        self.assertIsNone(s.news_component({**row(), "market": "CA"}, feed, NOW)["score"])

    def test_social_missing_score_is_not_zero_and_canadian_ticker_collision_is_rejected(self):
        with self.assertRaises((KeyError, ValueError)):
            s.parse_social(json.dumps({"data": {"sentiment": {"24h": {"labelNormalized": "NA", "valueNormalized": 0}}}}))
        source = inputs()
        source["social"] = {"TEST": {"score": 90, "reading": "Bullish", "buzz": "HIGH", "fetched_at": NOW.isoformat()}}
        source["social_configured"] = True
        result = evaluate(sources=source)["rows"][0]["sentiment"]
        self.assertEqual(result["mode"], "Price + opinion")
        self.assertIsNone(evaluate({**row(), "market": "CA"}, source)["rows"][0]["sentiment"]["components"][3]["score"])

    def test_catalysts_are_never_changed_by_sentiment(self):
        r = row(iv_why="No clear catalyst found", iv_why_kind="none")
        evaluate(r)
        self.assertEqual((r["iv_why"], r["iv_why_kind"]), ("No clear catalyst found", "none"))


if __name__ == "__main__":
    unittest.main()
