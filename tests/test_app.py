"""Launcher workflow, progress and local HTTP boundaries without market requests."""
import json
from datetime import datetime
import shutil
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

from highiv.app import App, handler


class AppTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = App(Path(self.tmp.name))

    def test_measured_pace_and_scan_eta(self):
        with patch('highiv.app.time.monotonic', return_value=100):
            self.app.started = 40
            self.app.phase_started = 40
            self.app.update(status='running', phase='scan', completed=10, total=100)
            self.app.phase_started = 40
            status = self.app.snapshot()
        self.assertEqual(status['rate'], 10)
        self.assertEqual(status['eta'], 540)
        self.assertEqual(status['elapsed'], 60)

    def test_phase_switch_resets_scan_denominator_and_eta(self):
        self.app.update(status='running', phase='scan', completed=50, total=100)
        self.app.update(phase='enrich', activity='Downloading charts')
        state = self.app.snapshot()
        self.assertEqual(state['completed'], 0)
        self.assertIsNone(state['total'])
        self.assertIsNone(state['eta'])

    def test_duplicate_download_is_rejected(self):
        self.app.update(status='idle', ready=True)
        with patch('highiv.app.threading.Thread') as thread:
            self.assertTrue(self.app.start('refresh'))
            self.assertFalse(self.app.start('refresh'))
            thread.assert_called_once()

    def test_failure_preserves_saved_dashboard_and_offers_resume(self):
        output = self.app.root/'output'
        output.mkdir()
        saved = output/'dashboard.html'
        saved.write_text('previous report')
        self.app.started = 1
        with patch.object(self.app, 'run_process', side_effect=RuntimeError('offline')):
            self.app.download('refresh')
        self.assertEqual(self.app.state['status'], 'error')
        self.assertEqual(saved.read_text(), 'previous report')
        self.assertIn('offline', self.app.state['logs'])

    def test_refresh_forces_quotes_but_resume_does_not(self):
        output = self.app.root/'output'
        output.mkdir()
        (output/'dashboard.html').write_text('report')
        self.app.started = 1
        with patch.object(self.app, 'run_process') as run:
            self.app.download('refresh')
            self.assertIn('--refresh-quotes', run.call_args.args[0])
            self.app.download('resume')
            self.assertNotIn('--refresh-quotes', run.call_args.args[0])

    def test_provider_credentials_come_from_the_data_folder(self):
        self.assertEqual(self.app.credentials(), {})  # nothing configured
        (self.app.data_root/'data').mkdir(parents=True, exist_ok=True)
        (self.app.data_root/'data/credentials.env').write_text(
            "# provider credentials\nALPHAVANTAGE_API_KEY = KEY123 \nSTOCKTWITS_USERNAME=\"someone\"\n"
            "STOCKTWITS_PASSWORD=\nPATH=/tmp/evil\nnonsense\n", encoding="utf-8")
        self.assertEqual(self.app.credentials(), {"ALPHAVANTAGE_API_KEY": "KEY123", "STOCKTWITS_USERNAME": "someone"})
        with patch('highiv.app.subprocess.Popen', side_effect=RuntimeError("stop")) as popen:
            with self.assertRaises(RuntimeError):
                self.app.run_process(['true'])
        env = popen.call_args.kwargs['env']
        self.assertEqual(env['ALPHAVANTAGE_API_KEY'], 'KEY123')
        self.assertNotEqual(env['PATH'], '/tmp/evil')  # only the documented names are read

    def test_setup_failure_is_recoverable_and_does_not_download(self):
        with patch.object(self.app, 'run_process', side_effect=RuntimeError('pip failed')):
            self.app.setup()
        self.assertFalse(self.app.state['ready'])
        self.assertEqual(self.app.state['status'], 'error')

    def test_structured_events_and_log_output_from_real_child(self):
        import sys
        self.app.run_process([sys.executable, '-c', 'print(\'@highiv {"phase":"scan","completed":7,"total":20}\'); print("working")'], events=True)
        self.assertEqual(self.app.state['completed'], 7)
        self.assertEqual(self.app.state['total'], 20)
        self.assertIn('working', self.app.state['logs'])

    def test_schedule_persists_and_runs_once_after_time_even_after_restart(self):
        self.app.save_settings({'mode': 'auto', 'time': '11:00'})
        self.app.update(status='idle', ready=True)
        with patch('highiv.app.threading.Thread'):
            self.assertFalse(self.app.scheduled_tick(datetime(2026, 9, 18, 10, 59)))
            self.assertTrue(self.app.scheduled_tick(datetime(2026, 9, 18, 11, 0)))
            self.app.update(status='error')
            self.assertFalse(self.app.scheduled_tick(datetime(2026, 9, 18, 12, 0)))
            restarted = App(self.app.root)
            restarted.update(status='idle', ready=True)
            self.assertFalse(restarted.scheduled_tick(datetime(2026, 9, 18, 18, 0)))
            self.assertFalse(restarted.scheduled_tick(datetime(2026, 9, 19, 18, 0)))
            self.assertTrue(restarted.scheduled_tick(datetime(2026, 9, 21, 18, 0)))

    def test_schedule_does_not_overlap_manual_download_or_run_when_disabled(self):
        now = datetime(2026, 9, 18, 18, 0)
        self.app.update(status='idle', ready=True)
        self.assertFalse(self.app.scheduled_tick(now))
        self.app.save_settings({'mode': 'auto', 'time': '16:30'})
        self.app.update(status='running')
        self.assertFalse(self.app.scheduled_tick(now))
        self.assertIsNone(self.app.settings['last_scheduled_date'])
        self.app.update(status='idle')
        with patch('highiv.app.threading.Thread'):
            self.assertTrue(self.app.scheduled_tick(now))

    def test_invalid_schedule_does_not_replace_saved_settings(self):
        for bad in ({'mode':'auto','time':'25:00'}, {'mode':'invalid','time':'11:00'},
                    {'mode':'auto','time':'1:00'}, {'mode':'auto','time':None}, []):
            with self.assertRaises(ValueError):
                self.app.save_settings(bad)
        self.assertEqual(self.app.settings['mode'], 'manual')

    def test_completion_event_only_after_success(self):
        (self.app.root/'output').mkdir()
        (self.app.root/'output/dashboard.html').write_text('saved')
        self.app.started = 1
        with patch.object(self.app, 'run_process', side_effect=RuntimeError('failed')):
            self.app.download('refresh')
        self.assertNotIn('completion_id', self.app.snapshot())
        with patch.object(self.app, 'run_process'):
            self.app.download('refresh')
        self.assertTrue(self.app.snapshot()['completion_id'])


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = App(Path(self.tmp.name))
        (self.app.root/'templates').mkdir()
        (self.app.root/'templates/launcher.html').write_text('Welcome')
        shutil.copy2(Path(__file__).resolve().parents[1]/'templates/app-controls.html', self.app.root/'templates/app-controls.html')
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler(self.app))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.origin = f'http://127.0.0.1:{self.server.server_port}'

    def request(self, method, path, body=None, headers=None):
        client = HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        try:
            client.request(method, path, body=body, headers=headers or {})
            response = client.getresponse()
            return response.status, response.read()
        finally:
            client.close()

    def test_first_run_page_status_and_no_private_file_access(self):
        self.assertEqual(self.request('GET', '/'), (200, b'Welcome'))
        status, body = self.request('GET', '/api/status')
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(body)['has_dashboard'])
        self.assertEqual(self.request('GET', '/data/highiv.sqlite')[0], 404)
        self.assertEqual(self.request('GET', '/../../requirements.txt')[0], 404)

    def test_external_origin_host_or_missing_token_cannot_start_job(self):
        body = json.dumps({'action':'refresh'})
        good = {'Origin':self.origin, 'X-App-Token':self.app.token}
        for bad in [{}, {**good,'Origin':'https://evil.example'}, {**good,'Host':'evil.example'}, {**good,'X-App-Token':'wrong'}]:
            self.assertEqual(self.request('POST', '/api/start', body, bad)[0], 403)
        self.assertEqual(self.request('GET', '/api/status', headers={'Host':'evil.example'})[0], 403)
        with patch.object(self.app, 'start', return_value=True) as start:
            self.assertEqual(self.request('POST', '/api/start', body, good)[0], 202)
            start.assert_called_once_with('refresh')
        self.assertEqual(self.request('POST', '/api/start', '{', good)[0], 400)

    def test_saved_dashboard_opens_directly_and_download_page_remains_accessible(self):
        (self.app.root/'output').mkdir()
        (self.app.root/'output/dashboard.html').write_text('<html><body>Saved stocks</body></html>')
        status, body = self.request('GET', '/')
        self.assertEqual(status, 200)
        self.assertIn(b'Saved stocks', body)
        self.assertIn(b'href="/download"', body)
        self.assertEqual(self.request('GET', '/download'), (200, b'Welcome'))
        self.assertEqual(self.request('GET', '/dashboard.html'), (status, body))

    def test_settings_endpoint_requires_local_authorization(self):
        body = json.dumps({'mode': 'auto', 'time': '11:00'})
        self.assertEqual(self.request('POST', '/api/settings', body)[0], 403)
        good = {'Origin': self.origin, 'X-App-Token': self.app.token}
        self.assertEqual(self.request('POST', '/api/settings', body, good)[0], 200)
        self.assertEqual(self.app.settings['time'], '11:00')
        self.assertEqual(self.request('POST', '/api/settings', '[]', good)[0], 400)
        self.assertEqual(self.request('POST', '/api/settings', '{"mode":"auto","time":"99:00"}', good)[0], 400)

    def test_aaii_week_needs_local_authorization_is_validated_and_shows_on_the_saved_report(self):
        good = {'Origin': self.origin, 'X-App-Token': self.app.token}
        week = json.dumps({'week_ending': '2026-09-16', 'bullish': 28.8, 'neutral': 17.9, 'bearish': 53.3})
        self.assertEqual(self.request('POST', '/api/aaii', week)[0], 403)
        with patch.object(self.app, 'eastern_now', return_value=datetime(2026, 9, 19, 9, 0)):
            status, body = self.request('POST', '/api/aaii', json.dumps({**json.loads(week), 'bearish': 43.3}), good)
            self.assertEqual((status, json.loads(body)['error']), (400, 'Bullish, neutral and bearish add up to 90%, not 100%.'))
            status, body = self.request('POST', '/api/aaii', week, good)
        self.assertEqual((status, json.loads(body)['reading']), (200, '-24.5 pp'))
        templates = Path(__file__).resolve().parents[1]/'templates'
        for name in ('dashboard.html', 'dashboard.js', 'dashboard.css', 'favicon.svg'):
            shutil.copy2(templates/name, self.app.root/'templates'/name)
        (self.app.root/'output').mkdir()
        payload = {'rows': [], 'macro_sentiment': {'cards': [{'key': 'aaii', 'name': 'AAII sentiment', 'status': 'unavailable'}]}}
        (self.app.root/'output/dashboard.html').write_text(f'<html><body><script id="payload" type="application/json">{json.dumps(payload)}</script></body></html>')
        status, body = self.request('GET', '/dashboard.html')
        self.assertEqual(status, 200)
        self.assertIn(b'"reading": "-24.5 pp"', body)

    def test_saved_report_gets_current_controls_and_cached_webp_without_market_refresh(self):
        templates = Path(__file__).resolve().parents[1]/'templates'
        for name in ('dashboard.html', 'dashboard.js', 'dashboard.css', 'favicon.svg'):
            shutil.copy2(templates/name, self.app.root/'templates'/name)
        (self.app.root/'output').mkdir()
        report = self.app.root/'output/dashboard.html'
        original = '<html><body><script id="payload" type="application/json">{"rows":[{"symbol":"AAPL"}]}</script></body></html>'
        report.write_text(original)
        (self.app.root/'data/logos').mkdir(parents=True)
        (self.app.root/'data/logos/AAPL.webp').write_bytes(b'webp')
        status, body = self.request('GET', '/')
        self.assertEqual(status, 200)
        self.assertIn(b'id="local-controls"', body)
        self.assertIn(b'id="cap-watch"', body)
        self.assertIn(b'"logo_webp": "d2VicA=="', body)
        self.assertEqual(report.read_text(), original)

    def test_update_waits_for_scan_and_reserves_idle_app(self):
        good = {'Origin': self.origin, 'X-App-Token': self.app.token}
        self.app.update(status='running', ready=True)
        code, body = self.request('POST', '/api/prepare-update', '{}', good)
        self.assertEqual(code, 200)
        self.assertFalse(json.loads(body)['ready'])
        self.assertTrue(self.app.snapshot()['update_waiting'])
        self.app.update(status='done')
        self.assertFalse(self.app.start('refresh'))
        self.app.settings.update(mode='auto', time='00:00')
        self.assertFalse(self.app.scheduled_tick(datetime(2026, 9, 18, 18)))
        code, body = self.request('POST', '/api/prepare-update', '{}', good)
        self.assertTrue(json.loads(body)['ready'])
        self.assertEqual(self.request('POST', '/api/cancel-update', '{}', good)[0], 200)
        with patch('highiv.app.threading.Thread'):
            self.assertTrue(self.app.start('resume'))

    def test_update_reservation_requires_local_authentication(self):
        for endpoint in ('/api/prepare-update', '/api/cancel-update'):
            self.assertEqual(self.request('POST', endpoint, '{}')[0], 403)
        self.assertFalse(self.app.update_requested)

    def test_windows_shared_data_root_preserves_settings_across_versions(self):
        shared = self.app.root/'shared'
        with patch.dict('os.environ', {'HIGHIV_DATA_ROOT': str(shared)}):
            first = App(self.app.root/'version1')
            first.save_settings({'mode':'auto','time':'11:00'})
            second = App(self.app.root/'version2')
            self.assertEqual(second.settings['time'], '11:00')
            self.assertEqual(second.data_root, shared)
            self.assertNotEqual(first.identity,second.identity)

    def test_shutdown_requires_local_authentication(self):
        self.assertEqual(self.request('POST','/api/shutdown','{}')[0],403)
