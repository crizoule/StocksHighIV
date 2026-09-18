"""Synthetic evidence cases: these headlines are fixtures, not claims about real events."""
from datetime import date
import json
import unittest
from unittest.mock import patch

from highiv import config, explain, news, report
from tests.test_regressions import TempProjectTest

AS_OF = date(2026, 9, 18)
ROW = {'symbol': 'ACME', 'name': 'Acme Robotics, Inc.'}


def headline(title='Acme Robotics announces public offering', **overrides):
    return {
        'title': title, 'summary': '', 'date': '2026-09-17',
        'source': 'Example News', 'url': 'https://example.com/event', 'type': 'STORY', **overrides,
    }


class EvidenceTests(unittest.TestCase):
    def choose(self, items, **row):
        return explain.choose({**ROW, **row}, items, AS_OF)

    def test_concrete_event_is_sourced_and_explicitly_uncertain(self):
        article = headline()
        result = self.choose([article])
        self.assertEqual(result['iv_why_kind'], 'news')
        self.assertTrue(result['iv_why'].startswith('Possible catalyst:'))
        self.assertEqual(result['iv_why_url'], article['url'])
        self.assertIn('not been independently verified', result['iv_why_detail'])
        self.assertEqual(result['iv_why_as_of'], AS_OF.isoformat())

    def test_price_moves_and_buzzwords_do_not_become_catalysts(self):
        for title in ('Acme Robotics stock surges', 'Acme Robotics robotaxi outlook',
                      'Acme Robotics is the future of tokenization', 'Acme Robotics stock plunges 40%'):
            with self.subTest(title=title):
                self.assertEqual(self.choose([headline(title, source='Reuters')])['iv_why_kind'], 'none')

    def test_summary_mention_does_not_attach_another_companys_story(self):
        result = self.choose([headline('Other Company announces public offering', summary='Acme Robotics is a peer.')])
        self.assertEqual(result['iv_why_kind'], 'none')

    def test_summary_event_does_not_rescue_a_generic_title(self):
        result = self.choose([headline('Acme Robotics shares rise', summary='Acme Robotics announces public offering')])
        self.assertEqual(result['iv_why_kind'], 'none')

    def test_roundup_event_must_be_in_the_companys_own_clause(self):
        result = self.choose([headline('Other Company announces public offering; Acme Robotics rises')])
        self.assertEqual(result['iv_why_kind'], 'none')

    def test_dotted_legal_suffix_does_not_block_company_match(self):
        result = explain.choose({'symbol': 'ACME', 'name': 'Acme Robotics Group N.V.'}, [headline()], AS_OF)
        self.assertEqual(result['iv_why_kind'], 'news')

    def test_shared_first_word_does_not_match_another_issuer(self):
        result = explain.choose({'symbol': 'IE', 'name': 'Ivanhoe Electric Inc.'},
                                [headline('Ivanhoe Mines cuts guidance')], AS_OF)
        self.assertEqual(result['iv_why_kind'], 'none')

    def test_ordinary_word_is_not_a_ticker_match(self):
        result = explain.choose({'symbol': 'ALL', 'name': 'Allstate Corporation'},
                                [headline('ALL investors watch Other Company announce public offering')], AS_OF)
        self.assertEqual(result['iv_why_kind'], 'none')

    def test_explicit_ticker_and_class_share_notation(self):
        for symbol, title in [('ACME', '(ACME) announces public offering'),
                              ('ACME', '$ACME cuts guidance'),
                              ('BRK.B', '(BRK-B) announces public offering'),
                              ('BBD-B.TO', 'TSX: BBD.B cuts guidance')]:
            with self.subTest(symbol=symbol):
                result = explain.choose({'symbol': symbol}, [headline(title)], AS_OF)
                self.assertEqual(result['iv_why_kind'], 'news')

    def test_stale_future_and_invalid_dates_are_rejected(self):
        for when in ('2026-09-10', '2026-09-19', None, 'bad-date'):
            with self.subTest(when=when):
                self.assertEqual(self.choose([headline(date=when)])['iv_why_kind'], 'none')

    def test_missing_sources_and_unsafe_links_are_rejected(self):
        for change in ({'source': None}, {'url': None}, {'url': 'javascript:alert(1)'}, {'url': 'https://'}, {'type': 'VIDEO'}):
            with self.subTest(change=change):
                self.assertEqual(self.choose([headline(**change)])['iv_why_kind'], 'none')

    def test_opinion_speculation_and_law_firm_ads_are_rejected(self):
        for title in ('Should Acme Robotics acquire a competitor?',
                      'Acme Robotics could announce public offering',
                      'Acme Robotics in talks to acquire Rival',
                      'Acme Robotics announces public offering: shareholder alert and deadline',
                      'Why Acme Robotics stock jumped after it cuts guidance'):
            with self.subTest(title=title):
                self.assertEqual(self.choose([headline(title)])['iv_why_kind'], 'none')

    def test_newest_qualifying_event_wins(self):
        newer = headline('Acme Robotics cuts guidance', date='2026-09-18')
        self.assertEqual(self.choose([newer, headline()])['iv_why_date'], '2026-09-18')

    def test_screening_metrics_alone_are_not_explanations(self):
        result = self.choose([], squeeze='high', short_pct_float=60, days_to_cover=10,
                             borrow_fee=90, range_pos=0, pct_from_high=-90, industry='Gold', iv_hv=4)
        self.assertEqual(result['iv_why'], 'No clear catalyst found')
        self.assertEqual(result['iv_why_kind'], 'none')

    def test_earnings_uses_event_date_not_stale_countdown(self):
        self.assertEqual(self.choose([], next_earnings='2026-09-10', earnings_in_days=3)['iv_why_kind'], 'none')
        self.assertEqual(self.choose([], next_earnings='2026-10-18', earnings_in_days=3)['iv_why_kind'], 'none')
        result = self.choose([], next_earnings='2026-09-20', earnings_in_days=12, earnings_estimated=None)
        self.assertEqual(result['iv_why_kind'], 'earnings')
        self.assertIn('date unconfirmed', result['iv_why'])
        self.assertIn('2 days', result['iv_why_detail'])
        self.assertIn('does not prove', result['iv_why_detail'])

    def test_estimated_earnings_explicitly_labeled(self):
        result = self.choose([], next_earnings='2026-09-20', earnings_estimated=True)
        self.assertIn('(estimated)', result['iv_why'])

    def test_outage_is_distinct_from_empty_feed_and_rejected_evidence(self):
        for items, status in ((None, 'unavailable'), ([], 'empty'), ([headline('Acme Robotics shares surge')], 'checked')):
            with self.subTest(status=status):
                result = self.choose(items)
                self.assertEqual(result['iv_news_status'], status)
                self.assertEqual(result['iv_why_kind'], 'none')
        self.assertIn('could not be retrieved', self.choose(None)['iv_why_detail'])


class NewsTests(unittest.TestCase):
    def test_news_failure_is_not_an_empty_success(self):
        with patch.object(news.yf, 'Ticker') as ticker, patch.object(news.time, 'sleep'):
            ticker.return_value.get_news.side_effect = RuntimeError('offline')
            self.assertIsNone(news.fetch('TEST'))
            self.assertEqual(ticker.return_value.get_news.call_count, 3)

    def test_empty_feed_is_not_replaced_by_an_unrelated_fallback(self):
        with patch.object(news.yf, 'Ticker') as ticker, patch.object(news.time, 'sleep'):
            ticker.return_value.get_news.return_value = []
            self.assertEqual(news.fetch('TEST'), [])
            ticker.return_value.get_news.assert_called_once_with(count=30)

    def test_publication_day_uses_market_timezone(self):
        self.assertEqual(news._iso_day('2026-09-19T01:00:00Z'), '2026-09-18')
        self.assertEqual(news._iso_day('2026-09-18'), '2026-09-18')
        self.assertIsNone(news._iso_day('2026-99-99'))

    def test_malformed_items_do_not_hide_valid_news(self):
        raw = [None, {'title': ['bad']}, {'title': 'Acme Robotics cuts guidance',
                'providerPublishTime': 1789747200, 'link': 'https://example.com/event', 'publisher': 'Example'}]
        with patch.object(news.yf, 'Ticker') as ticker, patch.object(news.time, 'sleep'):
            ticker.return_value.get_news.return_value = raw
            result = news.fetch('TEST')
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['source'], 'Example')


class RefreshTests(TempProjectTest):
    def test_refresh_uses_listing_symbol_and_original_quote_day_and_keeps_evidence(self):
        config.SNAPSHOT_DIR.mkdir()
        payload = {'run_date': '2026-09-18', 'quote_date': '2026-09-18', 'rows': [{
            'symbol': 'BRK.B', 'name': 'Example', 'market': 'US', 'quote_date': '2026-09-17',
            'iv_why_kind': 'squeeze', 'iv_why': 'Old inference',
        }]}
        (config.SNAPSHOT_DIR / '2026-09-18.json').write_text(json.dumps(payload))
        future_news = [headline('(BRK-B) announces public offering', date='2026-09-18')]
        with patch.object(news, 'fetch', return_value=future_news) as fetch, \
                patch.object(report, 'write_outputs', side_effect=lambda data: data), \
                patch.object(report, 'log'):
            result = report.explain_latest()
        fetch.assert_called_once_with('BRK-B')
        row = result['rows'][0]
        self.assertEqual(row['iv_why_kind'], 'none')
        self.assertEqual(row['iv_why_as_of'], '2026-09-17')
        self.assertEqual(row['news_headlines'], future_news)
        self.assertTrue(row['news_checked_at'])


if __name__ == '__main__':
    unittest.main()
