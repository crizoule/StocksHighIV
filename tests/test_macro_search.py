"""Search-attention statistics and collection failures, using synthetic histories."""
import copy
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from highiv import macro_search as m

NOW = datetime(2026, 9, 24, 15, tzinfo=timezone.utc)


def envelope(term="recession"):
    return {"keyword": term, "geo": "US", "timeframe": "today 3-m", "fetched_at": NOW.isoformat(), "interest_over_time": [
        {"date": (NOW.replace(hour=0) - timedelta(days=90-i)).isoformat(),
         "value": 40 if i >= 83 else 20, "is_partial": False} for i in range(90)]}


def historical(term="recession", latest=30):
    points = []
    for year in range(2004, 2027):
        for month in range(1, 13):
            if (year, month) > (2026, 9):
                continue
            points.append({"date": datetime(year, month, 1, tzinfo=timezone.utc).isoformat(),
                           "value": latest if (year, month) == (2026, 8) else 100 if (year, month) == (2026, 9) else 10,
                           "is_partial": (year, month) == (2026, 9)})
    return {"keyword": term, "geo": "US", "timeframe": "all", "fetched_at": NOW.isoformat(), "interest_over_time": points}


class HistoricalTests(unittest.TestCase):
    def test_2004_history_excludes_partial_month_and_ranks_complete_month(self):
        result = m.analyze_history(historical(), NOW)
        self.assertEqual((result['history_start'], result['as_of'], result['period']), ('2004-01-01', '2026-08-31', '2026-08'))
        self.assertEqual((result['percentile'], result['signal']), (100, 'Elevated'))
        self.assertEqual(result['points'][-1], ['2026-08-01', 30])
        self.assertEqual(m.analyze_history(historical(latest=10), NOW)['percentile'], 50)

    def test_independent_scale_and_current_month_exclusion_even_without_flag(self):
        data = historical()
        data['interest_over_time'][-1]['is_partial'] = False
        for point in data['interest_over_time']:
            point['value'] /= 2
        self.assertEqual(m.analyze_history(data, NOW)['percentile'], 100)
        self.assertEqual(m.analyze_history(data, NOW)['period'], '2026-08')

    def test_wrong_country_timeframe_missing_month_and_annual_data_rejected(self):
        for field, value in (('geo', ''), ('geo', 'CA'), ('timeframe', 'today 3-m')):
            data = historical(); data[field] = value
            with self.assertRaises(ValueError):
                m.analyze_history(data, NOW)
        data = historical(); data['interest_over_time'].pop(10)
        with self.assertRaises(ValueError): m.analyze_history(data, NOW)
        data = historical(); data['interest_over_time'] = data['interest_over_time'][::12]
        with self.assertRaises(ValueError): m.analyze_history(data, NOW)
        data = envelope(); data['geo'] = 'CA'
        with self.assertRaises(ValueError): m.analyze(data, NOW)

    def test_sparse_history_is_not_low_concern_and_old_fetch_is_stale(self):
        data = historical(latest=0)
        self.assertIsNone(m.analyze_history(data, NOW)['percentile'])
        data = historical(); data['fetched_at'] = (NOW - timedelta(days=15)).isoformat()
        card = m.evaluate({'historical_terms': {'recession': {'envelope': data}}}, NOW)['historical_cards'][0]
        self.assertEqual(card['status'], 'stale')
        data['fetched_at'] = NOW.isoformat()
        card = m.evaluate({'historical_terms': {'recession': {'envelope': data}}}, NOW.replace(month=10, day=1))['historical_cards'][0]
        self.assertEqual(card['status'], 'stale')  # August is no longer the latest complete month

    def test_daily_and_monthly_caches_and_archives_are_separate(self):
        def response(term, *args, view='recent', **kwargs):
            return {'envelope': historical(term) if view == 'historical' else envelope(term)}
        with TemporaryDirectory() as root, patch.object(m, 'TERMS', (m.TERMS[0],)), patch.object(m, 'fetch', side_effect=response) as fetch, patch.object(m.time, 'sleep'), patch.object(m.progress, 'emit'):
            state = m.collect(root, NOW)
            self.assertEqual(fetch.call_count, 2)
            self.assertEqual(state['terms']['recession']['envelope']['timeframe'], 'today 3-m')
            self.assertEqual(state['historical_terms']['recession']['envelope']['timeframe'], 'all')
            m.collect(root, NOW + timedelta(days=2))
            self.assertEqual(fetch.call_count, 3)  # recent only; monthly download cached for a week
            self.assertEqual(len(list((Path(root)/'macro_search'/'history').glob('*historical*.json'))), 1)

    def test_new_month_refreshes_history_before_weekly_cache_expires(self):
        with TemporaryDirectory() as root, patch.object(m, 'TERMS', (m.TERMS[0],)), patch.object(m, 'fetch', return_value={'error': 'test'}) as fetch, patch.object(m.time, 'sleep'), patch.object(m.progress, 'emit'):
            data = historical(); data['fetched_at'] = '2026-09-29T15:00:00+00:00'
            m.save_json(Path(root)/'macro_search'/'state.json', {'historical_terms': {'recession': {'envelope': data, 'attempted_at': data['fetched_at']}}})
            m.collect(root, datetime(2026,10,1,15,tzinfo=timezone.utc))
            self.assertEqual([c.kwargs['view'] for c in fetch.call_args_list], ['recent', 'historical'])

    def test_worker_forces_US_for_both_views_and_war_is_in_both(self):
        import trendspyg
        from trendspyg.explore import _engine
        with TemporaryDirectory() as root, patch.object(m.os, 'environ', {}), patch.object(_engine, '_parse_multiline'), patch.object(trendspyg, 'download_google_trends_explore', return_value=historical('war')) as download:
            for view in m.VIEWS:
                m.worker('war', root, view)
                self.assertEqual(download.call_args.args, ('war',))
                self.assertEqual(download.call_args.kwargs['geo'], 'US')
                self.assertEqual(download.call_args.kwargs['timeframe'], m.VIEWS[view][0])
        result = m.evaluate({}, NOW)
        for key in ('cards', 'historical_cards'):
            self.assertIn('war', [c['term'] for c in result[key]])
            self.assertTrue(all('geo=US' in c['url'] for c in result[key]))


class StatisticsTests(unittest.TestCase):
    def test_relative_change_and_uniform_rescaling(self):
        data = envelope()
        result = m.analyze(data, NOW)
        self.assertEqual((result["ratio"], result["change_pct"], result["signal"]), (2, 100, "Elevated"))
        scaled = copy.deepcopy(data)
        for p in scaled["interest_over_time"]:
            p["value"] /= 2
        self.assertEqual(m.analyze(scaled, NOW)["ratio"], result["ratio"])

    def test_partial_and_current_day_are_excluded(self):
        data = envelope()
        data["interest_over_time"][-1]["is_partial"] = True
        data["interest_over_time"].append({"date": NOW.replace(hour=0).isoformat(), "value": 100, "is_partial": False})
        self.assertEqual(m.analyze(data, NOW)["as_of"], "2026-09-22")

    def test_sparse_series_is_not_interpreted_as_low_concern(self):
        data = envelope()
        for p in data["interest_over_time"]:
            p["value"] = 0
        result = m.analyze(data, NOW)
        self.assertIsNone(result["ratio"])
        self.assertEqual(result["signal"], "Limited data")

    def test_bad_data_and_gaps_are_rejected(self):
        for key, value in (("value", float('nan')), ("value", True), ("value", 101), ("date", ""), ("is_partial", "false")):
            data = envelope()
            data["interest_over_time"][-1][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                m.analyze(data, NOW)
        for change in (lambda p: p.pop(40), lambda p: p.reverse(), lambda p: p[40].update(is_partial=True)):
            data = envelope()
            change(data["interest_over_time"])
            with self.assertRaises(ValueError):
                m.analyze(data, NOW)

    def test_strict_adapter_rejects_values_that_library_would_convert_to_zero(self):
        row = {"time": "1789516800", "value": [25], "isPartial": False, "hasData": [True]}
        self.assertEqual(m.strict_multiline({"default": {"timelineData": [row]}})[0]["value"], 25)
        for field, value in (("value", ["invalid"]), ("time", "invalid"), ("hasData", [False]), ("value", [])):
            with self.subTest(field=field), self.assertRaises(ValueError):
                m.strict_multiline({"default": {"timelineData": [{**row, field: value}]}})

    def test_old_reading_retains_history_but_becomes_stale(self):
        state = {"terms": {"recession": {"envelope": envelope(), "error": "Refresh failed"}}}
        card = m.evaluate(state, NOW + timedelta(days=5))["cards"][0]
        self.assertEqual((card["status"], card["signal"], card["as_of"]), ("stale", "Stale", "2026-09-23"))
        self.assertEqual(card["error"], "Refresh failed")
        self.assertEqual(len(card["points"]), 90)


class CollectionTests(unittest.TestCase):
    def test_cached_day_does_not_fetch_and_archives_original_observation(self):
        with TemporaryDirectory() as root, patch.object(m, 'VIEWS', {"recent": m.VIEWS["recent"]}), patch.object(m, 'fetch', side_effect=lambda term, *a, **k: {"envelope": envelope(term)}) as fetch, patch.object(m.time, 'sleep'), patch.object(m.progress, 'emit'):
            state = m.collect(root, NOW)
            self.assertEqual(fetch.call_count, 6)
            m.collect(root, NOW + timedelta(hours=2))
            self.assertEqual(fetch.call_count, 6)
            self.assertEqual(state['terms']['recession']['envelope']['fetched_at'], NOW.isoformat())
            self.assertEqual(len(list((Path(root)/'macro_search'/'history').glob('*.json'))), 6)

    def test_rate_limit_stops_remaining_requests_and_preserves_last_success(self):
        with TemporaryDirectory() as root:
            path = Path(root)/'macro_search'/'state.json'
            m.save_json(path, {'terms': {'recession': {'envelope': envelope()}}})
            with patch.object(m, 'fetch', return_value={'error': 'Google paused access', 'stop': True}) as fetch, patch.object(m.progress, 'emit'):
                state = m.collect(root, NOW)
                self.assertEqual(fetch.call_count, 1)
                self.assertEqual(state['terms']['recession']['envelope'], envelope())
                m.collect(root, NOW + timedelta(hours=2))
                self.assertEqual(fetch.call_count, 1)
            self.assertEqual(state['paused_until'], (NOW + timedelta(hours=24)).isoformat())

    def test_invalid_fresh_response_cannot_replace_good_history(self):
        with TemporaryDirectory() as root, patch.object(m, 'TERMS', (m.TERMS[0],)), patch.object(m.time, 'sleep'), patch.object(m.progress, 'emit'):
            path = Path(root)/'macro_search'/'state.json'
            m.save_json(path, {'terms': {'recession': {'envelope': envelope()}}})
            bad = envelope(); bad['interest_over_time'][40]['value'] = 'bad'
            with patch.object(m, 'fetch', return_value={'envelope': bad}):
                state = m.collect(root, NOW)
            self.assertEqual(state['terms']['recession']['envelope'], envelope())
            self.assertIn('validated', state['terms']['recession']['error'])

    def test_enrichment_never_changes_existing_scores(self):
        payload = {'macro_sentiment': {'score': 25}, 'rows': [{'sentiment': {'score': 42}}]}
        original = copy.deepcopy(payload)
        with patch.object(m, 'collect', return_value={}):
            m.enrich(payload)
        self.assertEqual({k: payload[k] for k in original}, original)
        self.assertEqual(len(payload['macro_search']['cards']), 6)

    def test_timeout_kills_only_the_worker_process_group(self):
        if m.os.name == 'nt':
            self.skipTest('POSIX process-group test')
        with TemporaryDirectory() as root, patch.object(m.subprocess, 'Popen') as popen, patch.object(m.os, 'killpg') as kill:
            process = popen.return_value
            process.pid = 12345
            process.wait.side_effect = [m.subprocess.TimeoutExpired('worker', 1), 0]
            result = m.fetch('recession', Path(root), timeout=1)
            self.assertTrue(result['stop'])
            kill.assert_called_once_with(12345, m.signal.SIGKILL)

    def test_search_only_refresh_preserves_scores_and_market_timestamp(self):
        from highiv import report
        payload = {'generated_at': 'old market timestamp', 'macro_sentiment': {'score': 25}, 'rows': [{'sentiment': {'score': 42}}]}
        original = copy.deepcopy(payload)
        with patch.object(report, 'snapshots', return_value=[Path('saved.json')]), patch.object(report, 'read_snapshot', return_value=payload), \
                patch.object(m, 'collect', return_value={}), patch.object(report, 'write_outputs') as write, \
                patch.object(report.sentiment, 'enrich') as sentiment, patch.object(report.leverage, 'enrich') as leverage:
            report.macro_search_latest()
            written = write.call_args.args[0]
            self.assertEqual({key: written[key] for key in original}, original)
            sentiment.assert_not_called()
            leverage.assert_not_called()

    def test_concurrent_collector_reads_saved_state_without_requests(self):
        with TemporaryDirectory() as root, patch.object(m, 'fetch') as fetch:
            folder = Path(root)/'macro_search'; (folder/'collecting').mkdir(parents=True)
            self.assertEqual(m.collect(root, NOW), {})
            fetch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
