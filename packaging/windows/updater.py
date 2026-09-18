"""Signed Windows release download and side-by-side installation, independent of UI."""
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
from urllib.request import Request, urlopen
import zipfile

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

PUBLIC_KEY = '9Uhfk50eEBKwzfWEX3+Qp1A+CR0b8C1w/K+yLoVFb4Y='
FEED = 'https://github.com/crizoule/StocksHighIV/releases/latest/download/windows-update.json'
MAX_ARCHIVE = 100 * 1024 * 1024


def read_url(url, limit):
    request = Request(url, headers={'User-Agent': 'StocksHighIV-Windows-Updater', 'Cache-Control': 'no-cache'})
    with urlopen(request, timeout=30) as response:
        content = response.read(limit + 1)
    if len(content) > limit:
        raise ValueError('Update exceeds download size limit.')
    return content


def validate_manifest(manifest, current_build):
    version = manifest.get('version', '')
    if not isinstance(version, str) or not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('Invalid release version.')
    build = manifest.get('build')
    if type(build) is not int or build <= current_build:
        return None
    expected = f'https://github.com/crizoule/StocksHighIV/releases/download/v{version}/StocksHighIV-Windows.zip'
    if manifest.get('url') != expected or not re.fullmatch(r'[a-f0-9]{64}', manifest.get('sha256', '')):
        raise ValueError('Invalid release location or checksum.')
    signature = base64.b64decode(manifest.get('signature', ''), validate=True)
    if len(signature) != 64:
        raise ValueError('Missing update signature.')
    return manifest


def check(current_build):
    return validate_manifest(json.loads(read_url(FEED, 32_000)), current_build)


def stage(content, manifest, destination, current_build):
    """Verify before extraction; reject mismatches, traversal, links, and oversized archives."""
    if not validate_manifest(manifest, current_build):
        raise ValueError('Update is not newer than the installed version.')
    if len(content) > MAX_ARCHIVE or hashlib.sha256(content).hexdigest() != manifest['sha256']:
        raise ValueError('Update checksum mismatch.')
    Ed25519PublicKey.from_public_bytes(base64.b64decode(PUBLIC_KEY)).verify(
        base64.b64decode(manifest['signature']), content)
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination, prefix='.update-') as temporary:
        temporary = Path(temporary)
        archive = temporary/'release.zip'
        archive.write_bytes(content)
        unpacked = temporary/'unpacked'
        with zipfile.ZipFile(archive) as z:
            total = 0
            names = set()
            for member in z.infolist():
                path = PurePosixPath(member.filename)
                if (path.is_absolute() or '..' in path.parts or '\\' in member.filename or ':' in member.filename
                        or not path.parts or path.parts[0] != 'StocksHighIV'
                        or stat.S_ISLNK(member.external_attr >> 16)):
                    raise ValueError('Unsafe update archive.')
                # Release archives contain only the executable and release metadata.
                if not member.is_dir() and member.filename not in ('StocksHighIV/StocksHighIV.exe', 'StocksHighIV/release.json'):
                    raise ValueError('Unexpected file in update archive.')
                if member.filename in names:
                    raise ValueError('Duplicate archive member.')
                names.add(member.filename)
                total += member.file_size
                if total > 250 * 1024 * 1024:
                    raise ValueError('Unpacked update exceeds size limit.')
            z.extractall(unpacked)
        payload = unpacked/'StocksHighIV'
        metadata = json.loads((payload/'release.json').read_text())
        if metadata.get('version') != manifest['version'] or int(metadata.get('build', 0)) != manifest['build']:
            raise ValueError('Signed package does not match the announced version.')
        if not (payload/'StocksHighIV.exe').is_file():
            raise ValueError('Update is missing its launcher.')
        final = destination/str(manifest['build'])
        if final.exists():
            # A failed/canceled installation can be retried, but never overwrite a version in use.
            if (final/'StocksHighIV.exe').read_bytes() != (payload/'StocksHighIV.exe').read_bytes():
                raise ValueError('Installed version differs from this release.')
        else:
            os.replace(payload, final)
        return final


def download(manifest, destination, current_build):
    return stage(read_url(manifest['url'], MAX_ARCHIVE), manifest, destination, current_build)


def save_pointer(root, build, previous=None):
    temporary = root/'current.tmp'
    temporary.write_text(json.dumps({'build': int(build), 'previous': previous}), encoding='utf-8')
    os.replace(temporary, root/'current.json')
