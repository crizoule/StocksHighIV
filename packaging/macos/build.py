"""Build a universal Mac launcher; optionally sign, notarize, staple and verify it."""
import argparse
import hashlib
import json
import tarfile
from urllib.request import urlopen
from xml.sax.saxutils import escape
from pathlib import Path
import plistlib
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[2]
RELEASE = json.loads((ROOT/'packaging/macos/release.json').read_text())


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
    sparkle = work / 'sparkle'
    source_archive = work / 'Sparkle.tar.xz'
    if not source_archive.exists() or hashlib.sha256(source_archive.read_bytes()).hexdigest() != RELEASE['sparkle_sha256']:
        url = f"https://github.com/sparkle-project/Sparkle/releases/download/{RELEASE['sparkle_version']}/Sparkle-{RELEASE['sparkle_version']}.tar.xz"
        with urlopen(url, timeout=60) as response:
            source_archive.write_bytes(response.read())
    if hashlib.sha256(source_archive.read_bytes()).hexdigest() != RELEASE['sparkle_sha256']:
        raise RuntimeError('Sparkle download checksum mismatch')
    if sparkle.exists():
        shutil.rmtree(sparkle)
    sparkle.mkdir()
    with tarfile.open(source_archive) as archive_file:
        archive_file.extractall(sparkle, filter='data')
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
                CFBundlePackageType='APPL', CFBundleShortVersionString=RELEASE['version'], CFBundleVersion=RELEASE['build'],
                SUFeedURL='https://github.com/crizoule/StocksHighIV/releases/latest/download/appcast.xml',
                SUPublicEDKey=RELEASE['update_public_key'], SUEnableAutomaticChecks=True,
                SUAutomaticallyUpdate=False, SUAllowsAutomaticUpdates=False,
                SUScheduledCheckInterval=86400, SUVerifyUpdateBeforeExtraction=True,
                CFBundleIconFile='StocksHighIV.icns',
                LSMinimumSystemVersion='12.0', NSHighResolutionCapable=True,
                StocksHighIVRevision=digest.hexdigest()[:20])
    (contents / 'Info.plist').write_bytes(plistlib.dumps(info))
    run('xcrun', 'swift', '-module-cache-path', work / 'module-cache',
        ROOT / 'packaging/macos/Icon.swift', ROOT / 'templates/favicon.svg', work / 'StocksHighIV.iconset')
    run('iconutil', '-c', 'icns', work / 'StocksHighIV.iconset', '-o', contents / 'Resources/StocksHighIV.icns')
    frameworks = contents / 'Frameworks'
    frameworks.mkdir()
    framework = frameworks / 'Sparkle.framework'
    shutil.copytree(sparkle / 'Sparkle.framework', framework, symlinks=True)
    shutil.copy2(sparkle / 'LICENSE', contents / 'Resources/Sparkle-LICENSE')
    for arch in ('arm64', 'x86_64'):
        run('xcrun', 'swiftc', '-O', '-target', f'{arch}-apple-macosx12.0',
            '-module-cache-path', work / 'module-cache', '-F', frameworks, '-framework', 'Sparkle',
            '-Xlinker', '-rpath', '-Xlinker', '@executable_path/../Frameworks', ROOT / 'packaging/macos/Launcher.swift',
            '-o', work / arch)
    run('xcrun', 'lipo', '-create', work / 'arm64', work / 'x86_64', '-output', binaries / 'StocksHighIV')
    identity = args.identity or '-'
    sign_args = ['codesign', '--force', '--options', 'runtime', '--sign', identity]
    if args.identity:
        sign_args.append('--timestamp')
    # Sign nested code from the inside out; preserve the framework's symlinks.
    for nested in sorted(framework.rglob('*'), key=lambda path: len(path.parts), reverse=True):
        if nested.is_symlink():
            continue
        if nested.is_file():
            with nested.open('rb') as source:
                magic = source.read(4)
            if magic in (b'\xcf\xfa\xed\xfe', b'\xce\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\xca\xfe\xba\xbf'):
                run(*sign_args, nested)
        elif nested.suffix in ('.app', '.xpc'):
            run(*sign_args, nested)
    run(*sign_args, framework)
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
        # Sign the final ZIP only after stapling, then produce a feed for this release.
        sign_tool = sparkle / 'bin/sign_update'
        signature = subprocess.check_output([str(sign_tool), '--account', RELEASE['keychain_account'], '-p', str(archive)], text=True).strip()
        run(sign_tool, '--account', RELEASE['keychain_account'], '--verify', archive, signature)
        url = f"https://github.com/crizoule/StocksHighIV/releases/download/v{RELEASE['version']}/StocksHighIV-macOS.zip"
        feed = f'''<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle"><channel>
<title>StocksHighIV updates</title><item><title>StocksHighIV {escape(RELEASE['version'])}</title>
<sparkle:version>{escape(RELEASE['build'])}</sparkle:version>
<sparkle:shortVersionString>{escape(RELEASE['version'])}</sparkle:shortVersionString>
<sparkle:minimumSystemVersion>12.0</sparkle:minimumSystemVersion>
<enclosure url="{escape(url)}" length="{archive.stat().st_size}" type="application/octet-stream" sparkle:edSignature="{signature}"/>
</item></channel></rss>'''
        appcast = dist / 'appcast.xml'
        appcast.write_text(feed)
        run(sign_tool, '--account', RELEASE['keychain_account'], appcast)
        run(sign_tool, '--account', RELEASE['keychain_account'], '--verify', appcast)
        print(f'Notarized release: {archive}; signed update feed: {appcast}')
    else:
        print(f'LOCAL TEST ONLY; not signed with Developer ID or notarized: {archive}')


if __name__ == '__main__':
    main()
