"""Loopback-only launcher with dependency setup and a single background download job."""
from __future__ import annotations

import argparse
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

from . import config

APP_ID = 'stockshighiv-local-v1'


class App:
    def __init__(self, root=config.ROOT):
        self.root = Path(root)
        self.identity = hashlib.sha256(str(self.root.resolve()).encode()).hexdigest()
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.RLock()
        self.process = None
        self.closing = False
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
            saved = self.root / 'output/dashboard.html'
            return {**self.state, 'logs': list(self.state['logs']), 'app': APP_ID,
                    'identity': self.identity, 'token': self.token, 'elapsed': elapsed,
                    'rate': rate, 'eta': eta, 'last_activity_seconds': now-self.last_activity,
                    'has_dashboard': saved.exists(),
                    'dashboard_saved_at': saved.stat().st_mtime if saved.exists() else None}

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
            valid = marker.exists() and marker.read_text() == fingerprint
            if valid:
                result = subprocess.run([str(self.python), '-c', 'import httpx,yfinance,pandas,openpyxl,tzdata'],
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
            if not (self.root/'output/dashboard.html').exists():
                raise RuntimeError('The download ended without producing a dashboard.')
            self.update(status='done', phase='done', activity='Dashboard updated — ready to open',
                        elapsed=time.monotonic()-self.started)
        except Exception as exc:
            self.log(str(exc))
            self.update(status='error', activity='Download interrupted. Resume to retry unfinished stocks.',
                        elapsed=time.monotonic()-self.started)

    def close(self):
        with self.lock:
            self.closing = True
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
            landing = 'output/dashboard.html' if (app.root/'output/dashboard.html').exists() else 'templates/launcher.html'
            files = {'/': (landing, 'text/html; charset=utf-8'),
                     '/download': ('templates/launcher.html', 'text/html; charset=utf-8'),
                     '/dashboard.html': ('output/dashboard.html', 'text/html; charset=utf-8'),
                     '/favicon.svg': ('templates/favicon.svg', 'image/svg+xml')}
            if path not in files:
                return self.send(404, '{}')
            filename, content_type = files[path]
            try:
                body = (app.root/filename).read_bytes()
                if filename == 'output/dashboard.html':
                    navigation = (
                        '<nav aria-label="Local app" style="padding:12px 24px;background:#dfe5fb;'
                        'color:#122120;font:14px system-ui;display:flex;gap:16px;align-items:center;flex-wrap:wrap">'
                        '<a href="/download" style="color:#2338ad;font-weight:600">Refresh data / download progress</a>'
                        '<span>Showing your saved market data</span></nav>'
                    ).encode('utf-8')
                    body = body.replace(b'<body>', b'<body>'+navigation, 1)
                self.send(200, body, content_type)
            except FileNotFoundError:
                self.send(404, 'No dashboard yet. Return to the start page to download data.', 'text/plain')

        def do_POST(self):
            expected_origin = f'http://127.0.0.1:{self.server.server_port}'
            if not self.local_host() or self.headers.get('Origin') != expected_origin or not secrets.compare_digest(self.headers.get('X-App-Token', ''), app.token):
                return self.send(403, '{}')
            if self.path != '/api/start':
                return self.send(404, '{}')
            try:
                length = int(self.headers.get('Content-Length', 0))
                if not 0 < length <= 1000:
                    raise ValueError()
                action = json.loads(self.rfile.read(length)).get('action')
                if action not in ('refresh', 'resume', 'setup'):
                    raise ValueError()
            except (ValueError, AttributeError):
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
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.close()
        server.server_close()
