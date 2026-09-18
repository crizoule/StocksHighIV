"""Build on Windows: a portable GUI executable, with signed updates added by publisher."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]


def main():
    if sys.platform != 'win32':
        raise SystemExit('Build this executable on Windows or use the Windows package GitHub workflow.')
    work = ROOT/'dist/windows-build'
    dist = ROOT/'dist/windows'
    work.mkdir(parents=True, exist_ok=True)
    dist.mkdir(parents=True, exist_ok=True)
    project = work/'project'
    if project.exists(): shutil.rmtree(project)
    project.mkdir()
    for name in ('highiv', 'templates'):
        shutil.copytree(ROOT/name, project/name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.DS_Store'))
    for name in ('launch.py', 'requirements.txt'):
        shutil.copy2(ROOT/name, project/name)
    shutil.copy2(ROOT/'packaging/macos/release.json', work/'release.json')
    with Image.open(ROOT/'packaging/windows/StocksHighIV.png') as source:
        source.save(work/'StocksHighIV.ico', sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)])
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onefile', '--windowed',
        '--name', 'StocksHighIV', '--icon', str(work/'StocksHighIV.ico'),
        '--add-data', f'{project};project', '--add-data', f'{work/"release.json"};.',
        '--add-data', f'{work/"StocksHighIV.ico"};.', '--distpath', str(dist),
        '--workpath', str(work/'pyinstaller'), '--specpath', str(work),
        str(ROOT/'packaging/windows/Launcher.py')], check=True)
    smoke = work/'smoke.json'
    smoke.unlink(missing_ok=True)
    proc = subprocess.Popen([str(dist/'StocksHighIV.exe'), '--smoke-test', str(smoke)])
    proc.wait(timeout=90)
    assert proc.returncode == 0 and json.loads(smoke.read_text())['ok']
    with zipfile.ZipFile(dist/'StocksHighIV-Windows.zip', 'w', zipfile.ZIP_DEFLATED) as z:
        z.write(dist/'StocksHighIV.exe', 'StocksHighIV/StocksHighIV.exe')
        z.write(work/'release.json', 'StocksHighIV/release.json')
    print('Windows launcher built and native GUI smoke test passed.')


if __name__ == '__main__':
    main()
