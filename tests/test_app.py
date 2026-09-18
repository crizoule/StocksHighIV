"""Launcher workflow, progress and local HTTP boundaries without market requests."""
import json
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


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = App(Path(self.tmp.name))
        (self.app.root/'templates').mkdir()
        (self.app.root/'templates/launcher.html').write_text('Welcome')
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
