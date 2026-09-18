"""On the Mac signing machine, sign the Windows artifact produced by GitHub Actions."""
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[2]


def main():
    release = json.loads((ROOT/'packaging/macos/release.json').read_text())
    archive = ROOT/'dist/windows/StocksHighIV-Windows.zip'
    with zipfile.ZipFile(archive) as z:
        metadata = json.loads(z.read('StocksHighIV/release.json'))
    if metadata['version'] != release['version'] or metadata['build'] != release['build']:
        raise SystemExit('Windows artifact version does not match the release. Build it again.')
    tool = ROOT/'dist/.macos-build/sparkle/bin/sign_update'
    signature = subprocess.check_output([str(tool), '--account', release['keychain_account'], '-p', str(archive)], text=True).strip()
    subprocess.run(['xcrun','swift','-module-cache-path',str(ROOT/'dist/.macos-build/module-cache'),
                    str(ROOT/'packaging/macos/VerifyUpdate.swift'),str(archive),release['update_public_key'],signature],check=True)
    manifest = dict(version=release['version'], build=int(release['build']),
        url=f"https://github.com/crizoule/StocksHighIV/releases/download/v{release['version']}/StocksHighIV-Windows.zip",
        sha256=hashlib.sha256(archive.read_bytes()).hexdigest(), signature=signature)
    path = archive.with_name('windows-update.json')
    path.write_text(json.dumps(manifest, indent=2)+'\n')
    print(f'Signed Windows update: {path}')


if __name__ == '__main__':
    main()
