"""Loopback-only launcher with dependency setup and a single background download job."""
from __future__ import annotations

import argparse
import base64
import re
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import urlopen
import webbrowser

from . import config, watchlist

APP_ID = 'stockshighiv-local-v1'


class App:
    def __init__(self, root=config.ROOT):
        self.root = Path(root)
        self.data_root = Path(os.environ.get("HIGHIV_DATA_ROOT", self.root))
        self.identity = hashlib.sha256(str(self.root.resolve()).encode()).hexdigest()
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.RLock()
        self.process = None
        self.stop_scheduler = threading.Event()
        self.settings = {'mode': 'manual', 'time': '16:30', 'last_scheduled_date': None}
        try:
            saved_settings = json.loads((self.data_root/'data/launcher-settings.json').read_text(encoding="utf-8"))
            self.validate_settings(saved_settings)
            self.settings.update(saved_settings)
        except (OSError, ValueError, TypeError):
            pass
        self.closing = False
        self.update_requested = False
        self.started = self.phase_started = None
        self.last_activity = time.monotonic()
        self.state = dict(status='setup', phase='setup', activity='Preparing Python dependencies',
                          completed=0, total=None, logs=[], ready=False, errors=0, with_iv=0, reused=0)

    @property
    def python(self):
        return self.root / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')

    def update(self, **fields):
        with self.lock:
            if fields.get('phase') != self.state.get('phase') and 'phase' in fields:
                self.phase_started = time.monotonic()
                self.state.update(completed=0, total=None)
            self.state.update(fields)
            self.last_activity = time.monotonic()

    def log(self, line):
        with self.lock:
            self.state['logs'] = (self.state['logs'] + [line.rstrip()[:1000]])[-40:]

    def snapshot(self):
        with self.lock:
            now = time.monotonic()
            active = self.state['status'] in ('setup', 'running')
            elapsed = now - self.started if self.started and active else self.state.get('elapsed', 0)
            duration = now - self.phase_started if self.phase_started else 0
            count = self.state['completed']
            rate = count / duration * 60 if duration > 0 and count and active else None
            total = self.state['total']
            eta = (total - count) / (rate / 60) if rate and count >= 5 and total else None
            saved = self.data_root / 'output/dashboard.html'
            return {**self.state, 'logs': list(self.state['logs']), 'app': APP_ID,
                    'update_waiting': self.update_requested,
                    'settings': dict(self.settings), 'watchlist': watchlist.load(self.data_root),
                    'identity': self.identity, 'token': self.token, 'elapsed': elapsed,
                    'rate': rate, 'eta': eta, 'last_activity_seconds': now-self.last_activity,
                    'has_dashboard': saved.exists(),
                    'dashboard_saved_at': saved.stat().st_mtime if saved.exists() else None}

    @staticmethod
    def validate_settings(settings):
        if not isinstance(settings, dict) or settings.get('mode') not in ('manual', 'auto'):
            raise ValueError('Choose manual or automatic downloads.')
        value = settings.get('time', '')
        if not isinstance(value, str) or len(value) != 5 or value[2] != ':':
            raise ValueError('Choose a valid time.')
        try:
            hour, minute = map(int, value.split(':'))
        except ValueError:
            raise ValueError('Choose a valid time.') from None
        if not (0 <= hour < 24 and 0 <= minute < 60) or value != f'{hour:02}:{minute:02}':
            raise ValueError('Choose a valid time.')

    def persist_settings(self, settings):
        path = self.data_root/'data/launcher-settings.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(settings), encoding='utf-8')
        os.replace(temporary, path)
        self.settings = settings

    def save_settings(self, settings):
        self.validate_settings(settings)
        with self.lock:
            self.persist_settings({**self.settings, 'mode': settings['mode'], 'time': settings['time']})

    def eastern_now(self):
        try:
            zone = ZoneInfo('America/New_York')
        except ZoneInfoNotFoundError:
            # The launcher itself uses system Python. On Windows the installed
            # tzdata package is in the app's venv, not system Python's search path.
            candidates = list((self.root/'.venv').glob('**/tzdata/zoneinfo/America/New_York'))
            if not candidates:
                raise RuntimeError('Time-zone data is not installed yet.')
            with candidates[0].open('rb') as source:
                zone = ZoneInfo.from_file(source)
        return datetime.now(zone)

    def scheduled_tick(self, now=None):
        with self.lock:
            if self.closing or self.update_requested or not self.state['ready'] or self.settings['mode'] != 'auto':
                return False
            if self.state['status'] in ('setup', 'running'):
                return False
            now = now or self.eastern_now()
            day = now.date().isoformat()
            if now.weekday() >= 5 or now.strftime('%H:%M') < self.settings['time']:
                return False
            if self.settings.get('last_scheduled_date') == day:
                return False
            # Persist before starting, so restart or a failed download cannot
            # trigger a retry loop. Manual Resume remains available after errors.
            self.persist_settings({**self.settings, 'last_scheduled_date': day})
            return self.start('refresh')

    def scheduler_loop(self):
        while not self.stop_scheduler.wait(15):
            try:
                self.scheduled_tick()
            except Exception as exc:
                self.log(f'Automatic download could not start: {exc}')

    def run_process(self, command, *, events=False):
        env = {**os.environ, 'PYTHONUNBUFFERED': '1', 'PYTHONIOENCODING': 'utf-8', 'PYTHONUTF8': '1'}
        if events:
            env['HIGHIV_PROGRESS'] = '1'
        with self.lock:
            if self.closing:
                raise RuntimeError('Application is closing')
            proc = subprocess.Popen(command, cwd=self.root, env=env, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')
            self.process = proc
        try:
            for line in proc.stdout:
                if events and line.startswith('@highiv '):
                    self.update(**json.loads(line[len('@highiv '):]))
                else:
                    self.log(line)
            if proc.wait() != 0:
                raise RuntimeError(f'Process exited with code {proc.returncode}. See the activity log below.')
        finally:
            proc.stdout.close()
            with self.lock:
                self.process = None

    def setup(self):
        self.started = self.phase_started = time.monotonic()
        try:
            if not self.python.exists():
                self.log('Creating the local Python environment. This can take a minute.')
                self.run_process([sys.executable, '-m', 'venv', str(self.root / '.venv')])
            # Verify both requirements fingerprint and imports; repair incomplete installations on retry.
            fingerprint = hashlib.sha256((self.root/'requirements.txt').read_bytes()).hexdigest()
            marker = self.root / '.venv/.highiv-requirements'
            valid = marker.exists() and marker.read_text(encoding="utf-8") == fingerprint
            if valid:
                result = subprocess.run([str(self.python), '-c', 'import httpx,yfinance,pandas,openpyxl,tzdata,PIL'],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                valid = result.returncode == 0
            if not valid:
                self.update(activity='Installing dependencies — download details appear below')
                pip = subprocess.run([str(self.python), '-m', 'pip', '--version'],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if pip.returncode != 0:
                    self.log('Preparing the Python package installer.')
                    self.run_process([str(self.python), '-m', 'ensurepip', '--upgrade'])
                self.run_process([str(self.python), '-m', 'pip', 'install', '--progress-bar', 'off', '-r', 'requirements.txt'])
                marker.write_text(fingerprint)
            self.update(status='idle', phase='ready', ready=True, activity='Ready to download market data', elapsed=0)
        except Exception as exc:
            self.log(str(exc))
            self.update(status='error', activity='Setup failed. Check the log, fix the issue, then retry setup.', ready=False,
                        elapsed=time.monotonic()-self.started)

    def start(self, action):
        with self.lock:
            if self.closing or self.update_requested:
                return False
            if self.state['status'] in ('setup', 'running'):
                return False
            if action == 'setup':
                self.update(status='setup', phase='setup', activity='Retrying setup')
                threading.Thread(target=self.setup, daemon=True).start()
                return True
            if not self.state['ready']:
                return False
            self.started = self.phase_started = time.monotonic()
            self.update(status='running', phase='universe', completed=0, total=None, logs=[],
                        errors=0, with_iv=0, reused=0, activity='Starting market data download')
            threading.Thread(target=self.download, args=(action,), daemon=True).start()
            return True

    def download(self, action):
        try:
            command = [str(self.python), '-m', 'highiv', 'run']
            if action == 'refresh':
                command.append('--refresh-quotes')
            self.run_process(command, events=True)
            if not (self.data_root/'output/dashboard.html').exists():
                raise RuntimeError('The download ended without producing a dashboard.')
            self.update(completion_id=str(time.time_ns()), status='done', phase='done', activity='Dashboard updated — ready to open',
                        elapsed=time.monotonic()-self.started)
        except Exception as exc:
            self.log(str(exc))
            self.update(status='error', activity='Download interrupted. Resume to retry unfinished stocks.',
                        elapsed=time.monotonic()-self.started)

    def close(self):
        with self.lock:
            self.closing = True
            self.stop_scheduler.set()
            proc = self.process
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


def handler(app):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, code, body, content_type='application/json'):
            if isinstance(body, str):
                body = body.encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(body)

        def local_host(self):
            return self.headers.get('Host') == f'127.0.0.1:{self.server.server_port}'

        def do_GET(self):
            if not self.local_host():
                return self.send(403, '{}')
            path = self.path.split('?', 1)[0]
            if path == '/api/status':
                return self.send(200, json.dumps(app.snapshot()))
            landing = 'output/dashboard.html' if (app.data_root/'output/dashboard.html').exists() else 'templates/launcher.html'
            files = {'/': (landing, 'text/html; charset=utf-8'),
                     '/download': ('templates/launcher.html', 'text/html; charset=utf-8'),
                     '/dashboard.html': ('output/dashboard.html', 'text/html; charset=utf-8'),
                     '/app-controls.js': ('templates/app-controls.js', 'text/javascript; charset=utf-8'),
                     '/favicon.svg': ('templates/favicon.svg', 'image/svg+xml')}
            if path not in files:
                return self.send(404, '{}')
            filename, content_type = files[path]
            try:
                body = ((app.data_root if filename.startswith('output/') else app.root)/filename).read_bytes()
                if filename == 'output/dashboard.html':
                    # Upgrade presentation while retaining the exact saved market data.
                    payload_match = re.search(rb'<script id="payload" type="application/json">(.*?)</script>', body, re.S)
                    if payload_match:
                        saved_payload = json.loads(payload_match[1])
                        for row in saved_payload.get('rows', []):
                            symbol = row.get('yahoo_symbol') or row.get('symbol', '')
                            if not row.get('logo_webp') and re.fullmatch(r'[A-Z0-9.\-]{1,24}', symbol):
                                logo = app.data_root/'data/logos'/(symbol+'.webp')
                                try:
                                    if logo.stat().st_size <= 12000:
                                        row['logo_webp'] = base64.b64encode(logo.read_bytes()).decode('ascii')
                                except OSError:
                                    pass
                        saved_json = json.dumps(saved_payload, ensure_ascii=False).replace('</', '<\\/')
                        template = (app.root/'templates/dashboard.html').read_text(encoding="utf-8")
                        template = template.replace('/*__CSS__*/', (app.root/'templates/dashboard.css').read_text(encoding="utf-8"))
                        template = template.replace('/*__JS__*/', (app.root/'templates/dashboard.js').read_text(encoding="utf-8"))
                        template = template.replace('__DATA_JSON__', saved_json)
                        template = template.replace('__FAVICON_BASE64__', base64.b64encode((app.root/'templates/favicon.svg').read_bytes()).decode())
                        body = ('<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"></head><body>' + template + '</body></html>').encode('utf-8')
                if content_type.startswith('text/html') and b'<body>' in body:
                    controls = (app.root/'templates/app-controls.html').read_bytes()
                    saved = app.data_root/'output/dashboard.html'
                    stamp = str(saved.stat().st_mtime if saved.exists() else 0).encode()
                    controls = controls.replace(b'REPORT_STAMP', stamp)
                    body = body.replace(b'<body>', b'<body>'+controls, 1)
                self.send(200, body, content_type)
            except FileNotFoundError:
                self.send(404, 'No dashboard yet. Return to the start page to download data.', 'text/plain')

        def do_POST(self):
            expected_origin = f'http://127.0.0.1:{self.server.server_port}'
            if not self.local_host() or self.headers.get('Origin') != expected_origin or not secrets.compare_digest(self.headers.get('X-App-Token', ''), app.token):
                return self.send(403, '{}')
            if self.path not in ('/api/start', '/api/settings', '/api/watchlist', '/api/prepare-update', '/api/cancel-update', '/api/shutdown'):
                return self.send(404, '{}')
            try:
                length = int(self.headers.get('Content-Length', 0))
                if not 0 < length <= 1000:
                    raise ValueError()
                payload = json.loads(self.rfile.read(length))
                if self.path == '/api/shutdown':
                    self.send(200, '{}')
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    return
                if self.path in ('/api/prepare-update', '/api/cancel-update'):
                    with app.lock:
                        app.update_requested = self.path == '/api/prepare-update'
                        ready = app.state['status'] not in ('setup', 'running')
                    return self.send(200, json.dumps({'ready': ready}))
                if self.path == '/api/watchlist':
                    with app.lock:
                        symbols = watchlist.change(app.data_root, payload.get('symbol'), payload.get('action'))
                    return self.send(200, json.dumps({'watchlist': symbols}))
                if self.path == '/api/settings':
                    app.save_settings(payload)
                    return self.send(200, '{}')
                action = payload.get('action')
                if action not in ('refresh', 'resume', 'setup'):
                    raise ValueError()
            except OSError:
                return self.send(500, json.dumps({'error': 'Could not save settings.'}))
            except (ValueError, AttributeError, TypeError):
                return self.send(400, '{}')
            self.send(202 if app.start(action) else 409, '{}')
    return Handler


def main():
    parser = argparse.ArgumentParser(description='Launch StocksHighIV locally')
    parser.add_argument('--port', type=int, default=8932)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    app = App()
    url = f'http://127.0.0.1:{args.port}/'
    try:
        with urlopen(url+'api/status', timeout=1) as response:
            existing = json.load(response)
        if existing.get('app') == APP_ID and existing.get('identity') == app.identity:
            print(f'Already running: {url}', flush=True)
            if not args.no_browser:
                webbrowser.open(url)
            return
    except Exception:
        pass
    try:
        server = ThreadingHTTPServer(('127.0.0.1', args.port), handler(app))
    except OSError:
        raise SystemExit(f'Port {args.port} is in use. Close the other app or run: python launch.py --port 8933')
    print(f'StocksHighIV: {url}\nKeep this window open. Press Ctrl+C to stop. Downloads resume after interruption.', flush=True)
    threading.Thread(target=app.setup, daemon=True).start()
    threading.Thread(target=app.scheduler_loop, daemon=True).start()
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.close()
        server.server_close()
