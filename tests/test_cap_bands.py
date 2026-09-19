from collections import Counter
from contextlib import ExitStack, closing
import json
import unittest
from unittest.mock import patch

from highiv import config, details, report, store
from tests.test_regressions import TempProjectTest, STOCK, QUOTE, RUN_DATE


class CapBandTests(TempProjectTest):
    def info(self, stock):
        with patch.object(details, '_info', return_value={
            'quoteType': 'EQUITY', 'longName': stock['name'], 'marketCap': stock['market_cap_usd'],
            'country': 'United States', 'industry': 'Software',
        }), patch.object(details.time, 'sleep'):
            return details.fetch(stock['symbol'])

    def fixture(self):
        stocks = [{**STOCK, 'symbol': f'MID{i}', 'yahoo': f'MID{i}', 'name': f'Medium{i}',
                   'market_cap_usd': 2e9} for i in range(4)]
        stocks += [{**STOCK, 'symbol': f'BIG{i}', 'yahoo': f'BIG{i}', 'name': f'Large{i}',
                    'market_cap_usd': 100e9 + i * 1e9} for i in range(2)]
        (config.DATA_DIR / 'universe.json').write_text(json.dumps({'run_date': RUN_DATE, 'stocks': stocks}))
        with closing(store.connect()) as conn:
            for i, stock in enumerate(stocks):
                store.record_scan(conn, RUN_DATE, stock['symbol'], 'cboe', {**QUOTE, 'iv30': 100 - i})
        return stocks

    def test_boundary_and_all_filter_combinations(self):
        self.assertEqual(len(report.VIEWS), 12)
        for cap, expected in [(1e9, 'mid'), (100e9 - 1, 'mid'), (100e9, 'large'), (500e9, 'large')]:
            row = {**STOCK, 'market_cap_usd': cap}
            for band in config.CAP_BANDS:
                self.assertEqual(report._in_view(row, 'all', 'any', band, hq_known=False), band == expected)
        self.assertFalse(report._in_view({**STOCK, 'market_cap_usd': 1e9 - 1}, 'all', 'any', 'mid', hq_known=False))

    def test_full_smaller_views_do_not_suppress_larger_candidates(self):
        filled = Counter({view: 100 for view in report.VIEWS if view[2] == 'mid'})
        self.assertTrue(report._still_needed({**STOCK, 'market_cap_usd': 100e9}, filled))
        self.assertFalse(report._still_needed(STOCK, filled))

    def test_each_band_has_independent_lookup_budget_and_statistics(self):
        stocks = self.fixture()
        infos = {s['symbol']: self.info(s) for s in stocks}
        with patch.object(config, 'TOP_N', 2), patch.object(config, 'MAX_DETAIL_LOOKUPS', 2), \
                patch.object(details, 'fetch', side_effect=lambda symbol: infos[symbol]) as lookup, \
                patch.object(report.borrow, 'load', return_value={}), patch.object(report, '_bootstrap_points', return_value=[]), \
                patch.object(report.prices, 'fetch', return_value=(None, {})), \
                patch.object(report.news, 'fetch', return_value=[]), patch.object(report, 'log'):
            report.build(RUN_DATE)
        payload = json.loads((config.SNAPSHOT_DIR / f'{RUN_DATE}.json').read_text())
        self.assertEqual(lookup.call_count, 4)
        self.assertEqual([r['symbol'] for r in payload['rows']], ['MID0', 'MID1', 'BIG0', 'BIG1'])
        self.assertEqual(payload['universe_by_cap']['mid']['with_iv'], 4)
        self.assertEqual(payload['universe_by_cap']['large']['with_iv'], 2)
        self.assertEqual(payload['universe_by_cap']['large']['median_iv'], 95.5)
        self.assertEqual(payload['settings']['large_market_cap_usd'], 100e9)
        with patch.object(details, 'fetch') as lookup, patch.object(report.borrow, 'load', return_value={}), \
                patch.object(config, 'TOP_N', 2), patch.object(config, 'MAX_DETAIL_LOOKUPS', 0), patch.object(report, 'log'):
            report.build(RUN_DATE, cached_snapshot=payload)
        lookup.assert_not_called()
        reused = json.loads((config.SNAPSHOT_DIR / f'{RUN_DATE}.json').read_text())
        self.assertEqual(reused['rows'], payload['rows'])

    def test_rows_enriched_after_the_close_are_kept_for_the_same_session(self):
        stocks = self.fixture()
        infos = {s['symbol']: self.info(s) for s in stocks}
        patches = lambda: (patch.object(config, 'TOP_N', 2), patch.object(report.borrow, 'load', return_value={}),
                           patch.object(report, '_bootstrap_points', return_value=[]), patch.object(report, 'log'),
                           patch.object(report.prices, 'fetch', return_value=(None, {})))
        with ExitStack() as stack:
            for item in patches():
                stack.enter_context(item)
            stack.enter_context(patch.object(details, 'fetch', side_effect=lambda symbol: infos[symbol]))
            stack.enter_context(patch.object(report.news, 'fetch', return_value=[]))
            report.build(RUN_DATE)  # generated now, after the 2026-09-18 close
        payload = json.loads((config.SNAPSHOT_DIR / f'{RUN_DATE}.json').read_text())
        with closing(store.connect()) as conn:
            for i, stock in enumerate(stocks):  # the next day's scan copied Friday's final quotes
                store.record_scan(conn, '2026-09-19', stock['symbol'], 'cboe', {**QUOTE, 'iv30': 100 - i})
            store.record_scan(conn, '2026-09-19', 'BIG1', 'cboe', {**QUOTE, 'iv30': 95, 'quote_date': '2026-09-19'})
        headline = [{'title': 'Weekend news'}]
        with ExitStack() as stack:
            for item in patches():
                stack.enter_context(item)
            lookup = stack.enter_context(patch.object(details, 'fetch', side_effect=lambda symbol: infos[symbol]))
            checked = stack.enter_context(patch.object(report.news, 'fetch', return_value=headline))
            report.build('2026-09-19')
        rows = {r['symbol']: r for r in json.loads((config.SNAPSHOT_DIR / '2026-09-19.json').read_text())['rows']}
        self.assertEqual([call.args[0] for call in lookup.call_args_list], ['BIG1'])  # only the newer quote is enriched again
        self.assertEqual(checked.call_count, 4)  # headlines stay current for every row
        self.assertEqual(rows['MID0']['news_headlines'], headline)
        self.assertEqual(rows['MID0']['details_fetched_at'], payload['rows'][0]['details_fetched_at'])

    def test_watched_low_iv_stock_bypasses_enrichment_budget_and_cap_floor(self):
        stocks = self.fixture()
        infos = {s['symbol']: self.info(s) for s in stocks}
        infos['MID3']['market_cap'] = 100e6
        with patch.object(report.watchlist, 'load', return_value=['MID3']), \
                patch.object(config, 'MAX_DETAIL_LOOKUPS', 0), \
                patch.object(details, 'fetch', side_effect=lambda symbol: infos[symbol]), \
                patch.object(report.borrow, 'load', return_value={}), patch.object(report, '_bootstrap_points', return_value=[]), \
                patch.object(report.prices, 'fetch', return_value=(None, {})), \
                patch.object(report.news, 'fetch', return_value=[]), patch.object(report, 'log'):
            report.build(RUN_DATE)
        payload = json.loads((config.SNAPSHOT_DIR / f'{RUN_DATE}.json').read_text())
        self.assertEqual([row['symbol'] for row in payload['rows']], ['MID3'])
        self.assertEqual(payload['watchlist'], ['MID3'])

    def test_cache_from_another_scan_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Cached enrichment'):
            report.build(RUN_DATE, cached_snapshot={'run_date': '2026-09-17'})


if __name__ == '__main__':
    unittest.main()
