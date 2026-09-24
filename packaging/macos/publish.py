"""Publish a built Mac update atomically: keep the release draft until both assets exist."""
import hashlib
import json
import re
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
REPO = 'crizoule/StocksHighIV'


def main():
    release = json.loads((ROOT/'packaging/macos/release.json').read_text())
    version = release['version']
    dist = ROOT/'dist/macos'
    archive, feed = dist/'StocksHighIV-macOS.zip', dist/'appcast.xml'
    enclosure = ET.parse(feed).find('./channel/item/enclosure')
    if enclosure is None or enclosure.attrib['length'] != str(archive.stat().st_size):
        raise SystemExit('Build the signed release first: feed/archive mismatch.')
    expected = f'https://github.com/{REPO}/releases/download/v{version}/StocksHighIV-macOS.zip'
    if enclosure.attrib['url'] != expected:
        raise SystemExit('Release version and appcast URL disagree.')
    signature = enclosure.attrib['{http://www.andymatuschak.org/xml-namespaces/sparkle}edSignature']
    verifier = ['xcrun', 'swift', '-module-cache-path', str(ROOT/'dist/.macos-build/module-cache'),
                str(ROOT/'packaging/macos/VerifyUpdate.swift')]
    subprocess.run([*verifier, str(archive), release['update_public_key'], signature], check=True)
    feed_signature = re.search(rb'<!-- sparkle-signatures:\s*edSignature: (\S+)\s+length: (\d+)\s*-->\s*$', feed.read_bytes())
    if not feed_signature:
        raise SystemExit('Missing feed signature')
    subprocess.run([*verifier, str(feed), release['update_public_key'], feed_signature[1].decode(), feed_signature[2].decode()], check=True)
    windows_archive = ROOT/'dist/windows/StocksHighIV-Windows.zip'
    windows_feed = ROOT/'dist/windows/windows-update.json'
    windows = json.loads(windows_feed.read_text())
    if (windows['version'] != version or windows['build'] != int(release['build'])
            or windows['sha256'] != hashlib.sha256(windows_archive.read_bytes()).hexdigest()
            or windows['url'] != f'https://github.com/{REPO}/releases/download/v{version}/StocksHighIV-Windows.zip'):
        raise SystemExit('Windows archive/feed mismatch: rebuild and sign both platforms before publishing.')
    subprocess.run([*verifier, str(windows_archive), release['update_public_key'], windows['signature']], check=True)
    # Refuse accidentally publishing code that has not been committed and pushed.
    if subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True).strip():
        raise SystemExit('Commit and push changes before publishing.')
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    notes = '''A new Commodities tab charts 31 commodity futures markets, each price above the CFTC's weekly Commitments of Traders positions for the same market: managed money (hedge funds and commodity trading advisers) and producers. Markets are grouped into energy, grains, oilseeds, softs, metals, livestock, dairy and lumber, with the most-held markets first. Each card shows the price and its 1-month and 1-year change, managed money's net position and its three-year COT index (crowded long at 80 or above, crowded short at 20 or below), and a small chart. Select a card to see that market on the full chart. The other 243 contracts in the report, such as regional natural-gas swaps, electricity and emissions, are listed below with their open interest.

The Searches tab is now marked Beta: Google Trends data is sampled, often refused, and revised between downloads.

Refresh your data once after updating to collect the commodity history.

Mac 1.1.0+ and packaged Windows 1.2.0+ users can use Check for Updates. Updates preserve watchlists, settings, and saved market data.

For a new installation, download the ZIP for your platform, extract it, and open StocksHighIV.app or StocksHighIV.exe. Requires Python 3.11+. The Mac app is signed and Apple-notarized. Windows supports Windows 10/11 x64; the executable is not Authenticode-signed.'''
    subprocess.run(['gh', 'release', 'create', f'v{version}', str(archive), str(feed), str(windows_archive), str(windows_feed), '--repo', REPO,
                    '--target', commit, '--draft', '--title', f'StocksHighIV {version}', '--notes', notes], check=True)
    subprocess.run(['gh', 'release', 'edit', f'v{version}', '--repo', REPO, '--draft=false', '--latest'], check=True)


if __name__ == '__main__':
    main()
