"""Build a universal Mac launcher; optionally sign, notarize, staple and verify it."""
import argparse
import hashlib
from pathlib import Path
import plistlib
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def run(*args):
    subprocess.run([str(arg) for arg in args], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--identity', help='Developer ID Application identity or certificate SHA-1')
    parser.add_argument('--notary-profile', help='Existing notarytool Keychain profile name')
    args = parser.parse_args()
    if args.notary_profile and not args.identity:
        parser.error('--notary-profile requires --identity')
    if args.identity and not args.notary_profile:
        parser.error('Release builds require both --identity and --notary-profile')
    dist = ROOT / 'dist/macos'
    dist.mkdir(parents=True, exist_ok=True)
    work = ROOT / 'dist/.macos-build'
    work.mkdir(parents=True, exist_ok=True)
    # Keep intermediate executables out of the user-facing release folder.
    for name in ('arm64', 'x86_64', 'module-cache', 'StocksHighIV-macOS-UNSIGNED.zip'):
        old = dist / name
        if old.exists() and not (work / name).exists():
            shutil.move(str(old), work / name)
    bundle = dist / 'StocksHighIV.app'
    if bundle.exists():
        shutil.rmtree(bundle)
    contents = bundle / 'Contents'
    binaries = contents / 'MacOS'
    payload = contents / 'Resources/project'
    binaries.mkdir(parents=True)
    payload.mkdir(parents=True)
    for name in ('highiv', 'templates'):
        shutil.copytree(ROOT / name, payload / name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.DS_Store'))
    for name in ('launch.py', 'requirements.txt'):
        shutil.copy2(ROOT / name, payload / name)
    digest = hashlib.sha256()
    for path in sorted(payload.rglob('*')):
        if path.is_file():
            digest.update(str(path.relative_to(payload)).encode() + b'\0' + path.read_bytes())
    info = dict(CFBundleExecutable='StocksHighIV', CFBundleIdentifier='com.crizoule.stockshighiv',
                CFBundleName='StocksHighIV', CFBundleDisplayName='StocksHighIV',
                CFBundlePackageType='APPL', CFBundleShortVersionString='1.0.0', CFBundleVersion='1',
                LSMinimumSystemVersion='12.0', NSHighResolutionCapable=True,
                StocksHighIVRevision=digest.hexdigest()[:20])
    (contents / 'Info.plist').write_bytes(plistlib.dumps(info))
    for arch in ('arm64', 'x86_64'):
        run('xcrun', 'swiftc', '-O', '-target', f'{arch}-apple-macosx12.0',
            '-module-cache-path', work / 'module-cache', ROOT / 'packaging/macos/Launcher.swift',
            '-o', work / arch)
    run('xcrun', 'lipo', '-create', work / 'arm64', work / 'x86_64', '-output', binaries / 'StocksHighIV')
    identity = args.identity or '-'
    sign_args = ['codesign', '--force', '--options', 'runtime', '--sign', identity]
    if args.identity:
        sign_args.append('--timestamp')
    run(*sign_args, bundle)
    run('codesign', '--verify', '--deep', '--strict', '--verbose=2', bundle)
    archive = dist / ('StocksHighIV-macOS.zip' if args.identity else 'StocksHighIV-macOS-UNSIGNED.zip')
    archive.unlink(missing_ok=True)
    run('ditto', '-c', '-k', '--keepParent', bundle, archive)
    if args.identity:
        run('xcrun', 'notarytool', 'submit', archive, '--keychain-profile', args.notary_profile, '--wait')
        run('xcrun', 'stapler', 'staple', bundle)
        run('xcrun', 'stapler', 'validate', bundle)
        run('spctl', '--assess', '--type', 'execute', '--verbose=2', bundle)
        archive.unlink()
        run('ditto', '-c', '-k', '--keepParent', bundle, archive)
        print(f'Notarized release: {archive}')
    else:
        print(f'LOCAL TEST ONLY; not signed with Developer ID or notarized: {archive}')


if __name__ == '__main__':
    main()
