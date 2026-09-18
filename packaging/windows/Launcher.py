"""Windows desktop launcher with signed, side-by-side app updates."""
import hashlib
import ctypes
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox
from urllib.request import Request, urlopen
import webbrowser

import updater

BASE = 'http://127.0.0.1:8932'
RESOURCES = Path(getattr(sys, '_MEIPASS', Path(__file__).parent))
RELEASE = json.loads((RESOURCES/'release.json').read_text())
BUILD = int(RELEASE['build'])
ROOT = Path(os.environ.get('HIGHIV_WINDOWS_HOME', str(Path(os.environ.get('LOCALAPPDATA', Path.home()))/'StocksHighIV')))
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def acquire_mutex(name, timeout):
    kernel = ctypes.windll.kernel32
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    handle = kernel.CreateMutexW(None, False, name)
    if not handle:
        raise OSError('Could not create launcher lock.')
    return handle, kernel.WaitForSingleObject(handle, timeout)


def python_command():
    override = os.environ.get('HIGHIV_PYTHON')
    candidates = [[override]] if override else [['py', '-3'], ['python']]
    for command in candidates:
        try:
            if subprocess.run([*command, '-c', 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)'],
                              creationflags=NO_WINDOW, timeout=15, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
                return command
        except (OSError, subprocess.TimeoutExpired):
            pass
    raise RuntimeError('Install Python 3.11 or newer from python.org, enable Add Python to PATH, and reopen StocksHighIV.')


class Launcher:
    def __init__(self, window, workspace):
        self.window, self.workspace = window, workspace
        self.identity = hashlib.sha256(str(workspace.resolve()).encode()).hexdigest()
        self.messages = queue.Queue()
        self.process = None
        self.starting = True
        self.manifest = None
        self.installing = False
        self.checking = False
        self.cancel_install = threading.Event()
        self.status = tk.StringVar(value='Starting your local dashboard…')
        window.title('StocksHighIV')
        window.geometry('510x250')
        window.protocol('WM_DELETE_WINDOW', self.quit)
        tk.Label(window, text='StocksHighIV', font=('Segoe UI', 20, 'bold')).pack(pady=(18, 2))
        tk.Label(window, text=f"Version {RELEASE['version']} · keep this launcher open").pack()
        tk.Label(window, textvariable=self.status, wraplength=460).pack(pady=12)
        tk.Button(window, text='Open dashboard', command=lambda: webbrowser.open(BASE)).pack()
        self.update_button = tk.Button(window, text='Check for updates', command=self.update_action)
        self.update_button.pack(pady=10)
        window.after(100, self.poll_messages)
        threading.Thread(target=self.start_backend, daemon=True).start()
        window.after(10_000, self.check)

    def send(self, text):
        self.messages.put(('status', text))

    def status_api(self):
        with urlopen(BASE+'/api/status', timeout=5) as response:
            data = json.load(response)
        if data.get('identity') != self.identity:
            raise RuntimeError('Another copy is using the local dashboard port. Quit it before starting this copy.')
        return data

    def post(self, action):
        status = self.status_api()
        request = Request(BASE+'/api/'+action, data=b'{}', method='POST', headers={
            'Content-Type': 'application/json', 'Origin': BASE, 'X-App-Token': status['token']})
        with urlopen(request, timeout=10) as response:
            return json.load(response)

    def start_backend(self):
        self.starting = True
        try:
            command = python_command()
            self.log = (ROOT/'launcher.log').open('ab')
            self.process = subprocess.Popen([*command, str(self.workspace/'launch.py')], cwd=self.workspace,
                env={**os.environ, 'HIGHIV_DATA_ROOT': str(ROOT), 'PYTHONUNBUFFERED': '1'},
                stdout=self.log, stderr=self.log, creationflags=NO_WINDOW)
            for _ in range(60):
                if self.process.poll() is not None:
                    raise RuntimeError('The server stopped. See launcher.log. Another copy may be using port 8932.')
                try:
                    self.status_api()
                    updater.save_pointer(ROOT, BUILD)
                    self.send('Dashboard running. Download progress is shown in your browser.')
                    return
                except Exception:
                    time.sleep(0.5)
            raise RuntimeError('The dashboard did not start. See launcher.log for details.')
        except Exception as error:
            self.send(str(error))
            # Keep the previous installed version available if a new version cannot boot.
            try:
                previous = json.loads((ROOT/'current.json').read_text()).get('previous')
                if previous and previous != BUILD:
                    updater.save_pointer(ROOT, previous)
                    self.send(f'{error} Previous version restored; close and reopen the launcher.')
            except (OSError, ValueError):
                pass

        finally:
            self.starting = False

    def poll_messages(self):
        while not self.messages.empty():
            kind, value = self.messages.get_nowait()
            if kind == 'status': self.status.set(value)
            elif kind == 'available':
                self.manifest = value
                self.update_button.config(text=f"Install version {value['version']}")
            elif kind == 'finished':
                self.window.destroy()
                return
            elif kind == 'reset':
                self.update_button.config(text='Check for updates')
        self.window.after(100, self.poll_messages)

    def check(self):
        if self.checking or self.installing: return
        self.checking = True
        def work():
            try:
                result = updater.check(BUILD)
                if result:
                    self.messages.put(('available', result))
                    self.send(f"Version {result['version']} is available. Choose Install to update.")
                else: self.send('You are running the latest Windows version.')
            except Exception:
                self.send('Could not check for updates. Your dashboard is still available; try again later.')
            finally: self.checking = False
        threading.Thread(target=work, daemon=True).start()
        self.window.after(86400_000, self.check)

    def update_action(self):
        if self.installing:
            self.cancel_install.set()
            self.send('Canceling update…')
        elif self.manifest:
            self.installing = True
            self.cancel_install.clear()
            self.update_button.config(text='Cancel update')
            threading.Thread(target=self.install, daemon=True).start()
        else: self.check()

    def install(self):
        reserved = False
        try:
            self.send('Downloading and verifying the signed update…')
            folder = updater.download(self.manifest, ROOT/'versions', BUILD)
            while not self.cancel_install.is_set():
                response = self.post('prepare-update')
                reserved = True
                if response['ready']: break
                self.send('Update verified. Waiting for your market download to finish…')
                self.cancel_install.wait(5)
            if self.cancel_install.is_set():
                self.send('Update canceled. Your current app remains installed.')
                return
            self.send('Installing update and restarting…')
            self.post('shutdown')
            self.process.wait(timeout=30)
            updater.save_pointer(ROOT, self.manifest['build'], previous=BUILD)
            try:
                subprocess.Popen([str(folder/'StocksHighIV.exe'), '--updating'], creationflags=NO_WINDOW)
            except OSError:
                updater.save_pointer(ROOT, BUILD)
                self.start_backend()
                raise
            self.messages.put(('finished', None))
        except Exception as error:
            self.send(f'Update not installed: {error}. Your saved data is unchanged.')
        finally:
            if reserved:
                try: self.post('cancel-update')
                except Exception: pass
            self.installing = False
            self.manifest = None
            self.messages.put(('reset', None))

    def quit(self):
        if self.starting:
            self.send("Startup is still in progress. Please wait before closing the launcher.")
            return
        if self.installing:
            self.cancel_install.set()
            self.send('Canceling update. Wait for cancellation, then close the launcher.')
            return
        try:
            status = self.status_api()
            if status['status'] in ('running', 'setup') and not messagebox.askyesno('Stop download?', 'A download or setup is running. Stop it and quit? You can resume the download later.'):
                return
            self.post('shutdown')
            if self.process: self.process.wait(timeout=30)
        except Exception as error:
            if self.process and self.process.poll() is None:
                messagebox.showerror('Could not stop the app', str(error))
                return
        self.window.destroy()


def main():
    window = tk.Tk()
    if '--smoke-test' in sys.argv:
        window.withdraw()
        mutex, acquired = acquire_mutex('Local\\StocksHighIV.Smoke.' + str(os.getpid()), 0)
        assert acquired == 0
        assert (RESOURCES/'project/launch.py').is_file()
        assert updater.PUBLIC_KEY
        Path(sys.argv[sys.argv.index('--smoke-test')+1]).write_text(json.dumps({'version': RELEASE['version'], 'ok': True}))
        window.destroy()
        return
    # One launcher owns the server. During an update, the new process waits
    # for the old launcher's mutex rather than opening a duplicate server.
    mutex, acquired = acquire_mutex('Local\\StocksHighIV.Launcher', 30_000 if '--updating' in sys.argv else 0)
    if acquired not in (0, 0x80):
        webbrowser.open(BASE)
        window.destroy(); return
    ROOT.mkdir(parents=True, exist_ok=True)
    try:
        pointer = json.loads((ROOT/'current.json').read_text())
        build = int(pointer['build'])
        if build > BUILD:
            executable = ROOT/'versions'/str(build)/'StocksHighIV.exe'
            if executable.is_file():
                subprocess.Popen([str(executable), '--updating'], creationflags=NO_WINDOW)
                window.destroy(); return
    except (OSError, ValueError, KeyError): pass
    installed = ROOT/'versions'/str(BUILD)
    installed.mkdir(parents=True, exist_ok=True)
    if getattr(sys, 'frozen', False) and not (installed/'StocksHighIV.exe').exists():
        shutil.copy2(sys.executable, installed/'StocksHighIV.exe')
    workspace = installed/'project'
    if not workspace.exists():
        staging = installed/'project.tmp'
        if staging.exists(): shutil.rmtree(staging)
        shutil.copytree(RESOURCES/'project', staging)
        os.replace(staging, workspace)
    try: window.iconbitmap(str(RESOURCES/'StocksHighIV.ico'))
    except tk.TclError: pass
    Launcher(window, workspace)
    window.mainloop()


if __name__ == '__main__':
    main()
