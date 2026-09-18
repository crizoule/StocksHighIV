from collections import Counter
from contextlib import closing
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
                patch.object(report.borrow, 'load', return_value={}), patch.object(report, '_bootstrap_history'), \
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

    def test_cache_from_another_scan_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Cached enrichment'):
            report.build(RUN_DATE, cached_snapshot={'run_date': '2026-09-17'})


if __name__ == '__main__':
    unittest.main()
