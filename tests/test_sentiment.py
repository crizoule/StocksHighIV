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
        self.assertEqual([c["status"] for c in cards], ["stale", "unavailable", "ok", "unavailable"])
        self.assertEqual(cards[0]["as_of"], "2026-09-01")

    def test_replica_is_a_cross_check_and_stands_in_only_without_a_fresh_cnn_reading(self):
        replica = {"status": "ok", "value": 31.2, "reading": "31.2/100", "signal": "Fear", "direction": -1,
                   "as_of": "2026-09-17", "coverage": 7, "components": [], "detail": "Replica", "method": "Method"}
        cnn = {"status": "ok", "value": 28.6, "reading": "28.6/100", "signal": "Fear", "direction": -1, "as_of": "2026-09-18"}
        card = lambda sources: evaluate(sources={"macro": sources})["macro_sentiment"]["cards"][3]
        both = card({"cnn": cnn, "fear_greed": replica})
        self.assertEqual((both["name"], both["value"], both["replica"]["value"]), ("CNN Fear & Greed", 28.6, 31.2))
        self.assertNotIn("replica_of", both)
        for missing in ({}, {"cnn": {**cnn, "as_of": "2026-09-01", "status": "cached"}}):
            stand_in = card({**missing, "fear_greed": replica})
            self.assertEqual((stand_in["name"], stand_in["value"], stand_in["replica_of"]),
                             ("Fear & Greed replica", 31.2, "CNN Fear & Greed"))
        self.assertEqual(card({"cnn": {**cnn, "as_of": "2026-09-01"}, "fear_greed": replica})["cnn_status"], "stale")
        stale_replica = card({"fear_greed": {**replica, "as_of": "2026-09-01"}})
        self.assertEqual((stale_replica["name"], stale_replica["status"]), ("CNN Fear & Greed", "unavailable"))

    def test_saved_aaii_spreadsheet_stands_in_for_a_blocked_page_unless_the_page_is_newer(self):
        imported = {"status": "ok", "value": -35.0, "reading": "-35.0 pp", "signal": "Bearish tilt", "direction": -1,
                    "as_of": "2026-09-17", "date_label": "reported", "source_file": "sentiment.xls",
                    "file_saved": "2026-09-18T12:00:00+00:00", "signature": ["sentiment.xls", 1, 1]}
        blocked = {"status": "unavailable", "error": "Provider blocked"}
        with TemporaryDirectory() as temp, patch.object(s.config, "DATA_DIR", Path(temp)):
            with patch.object(s.aaii, "imported", return_value=(imported, [])):
                item = s.with_aaii_import(blocked, TODAY)
                self.assertEqual((item["value"], item["source_file"], item["import_url"]), (-35.0, "sentiment.xls", s.aaii.DOWNLOAD_URL))
                newer = {"status": "ok", "value": 5.0, "as_of": "2026-09-23"}
                self.assertEqual(s.with_aaii_import(newer, date(2026, 9, 24))["value"], 5.0)
            stored = json.loads((Path(temp) / "sentiment" / "aaii-import.json").read_text(encoding="utf-8"))
            self.assertEqual(stored["as_of"], "2026-09-17")  # kept for later refreshes, even if the file goes away
            with patch.object(s.aaii, "imported", return_value=(None, ["The app cannot read Downloads."])):
                item = s.with_aaii_import(blocked, TODAY)
            self.assertEqual((item["status"], item["import_note"]), ("unavailable", "The app cannot read Downloads."))
        card = evaluate(sources={"macro": {"aaii": {**imported, "as_of": "2026-09-03"}}})["macro_sentiment"]["cards"][2]
        self.assertEqual(card["status"], "stale")  # a spreadsheet more than 10 days old is excluded like any AAII reading

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
