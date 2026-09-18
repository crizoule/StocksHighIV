import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from highiv import watchlist, scan

class WatchlistTests(unittest.TestCase):
    def test_persistent_unique_add_and_remove(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(watchlist.change(root, ' aapl ', 'add'), ['AAPL'])
            self.assertEqual(watchlist.change(root, 'AAPL', 'add'), ['AAPL'])
            self.assertEqual(watchlist.load(root), ['AAPL'])
            self.assertEqual(watchlist.change(root, 'AAPL', 'remove'), [])
            with self.assertRaises(ValueError):
                watchlist.change(root, '../secret', 'add')

    def test_adds_outside_universe_and_removes_old_watch_only_entries(self):
        stocks = [{'symbol':'AAPL','market_cap_usd':1e12}, {'symbol':'OLD','watch_only':True}]
        result = watchlist.include(stocks, ['AAPL','SMALL','SHOP.TO'])
        self.assertEqual([s['symbol'] for s in result], ['AAPL','SMALL','SHOP.TO'])
        self.assertTrue(result[1]['watch_only'])
        self.assertEqual(result[1]['cboe'], 'SMALL')
        self.assertEqual(result[2]['mx'], 'SHOP')

    def test_same_day_resume_includes_new_favorites(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch('highiv.config.DATA_DIR', root/'data'), patch('highiv.config.ROOT', root), patch('highiv.universe.build_universe', return_value=[]):
                scan.load_universe(None, '2026-09-18')
                watchlist.change(root, 'FVRR', 'add')
                self.assertEqual(scan.load_universe(None, '2026-09-18')[0]['symbol'], 'FVRR')
