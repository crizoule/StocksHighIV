"""Small, locally cached company icons; unavailable images never block a report."""
import base64
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import os
import re
import time
from urllib.parse import quote

import httpx
from PIL import Image, UnidentifiedImageError

from . import config, progress

MAX_SOURCE_BYTES = 256_000
MAX_WEBP_BYTES = 12_000
CACHE_DAYS = 90


def fetch(symbol):
    # Keep listing suffixes: Canadian and US listings can share a ticker.
    if not isinstance(symbol, str) or not re.fullmatch(r'[A-Z0-9.\-]{1,24}', symbol):
        return None
    directory = config.DATA_DIR/'logos'
    target = directory/(symbol + '.webp')
    missing = directory/(symbol + '.missing')
    try:
        if target.exists() and time.time() - target.stat().st_mtime < CACHE_DAYS * 86400:
            return target.read_bytes()
        if missing.exists() and time.time() - missing.stat().st_mtime < 86400:
            return target.read_bytes() if target.exists() else None
        directory.mkdir(parents=True, exist_ok=True)
        with httpx.stream('GET', f'https://financialmodelingprep.com/image-stock/{quote(symbol)}.png',
                          timeout=5, follow_redirects=True) as response:
            response.raise_for_status()
            raw = bytearray()
            for chunk in response.iter_bytes():
                raw.extend(chunk)
                if len(raw) > MAX_SOURCE_BYTES:
                    raise ValueError('Logo exceeds size limit')
        with Image.open(BytesIO(raw)) as source:
            if source.width > 2048 or source.height > 2048:
                raise ValueError('Logo exceeds dimensions limit')
            icon = source.convert('RGBA')
            icon.thumbnail((48, 48), Image.Resampling.LANCZOS)
            output = BytesIO()
            icon.save(output, format='WEBP', quality=80, method=6)
            content = output.getvalue()
        if len(content) > MAX_WEBP_BYTES:
            raise ValueError('Encoded logo exceeds size limit')
        temporary = target.with_suffix('.tmp')
        temporary.write_bytes(content)
        os.replace(temporary, target)
        missing.unlink(missing_ok=True)
        return content
    except (httpx.HTTPError, OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
        try:
            missing.touch()
            return target.read_bytes() if target.exists() else None
        except OSError:
            return None


def apply(rows):
    progress.emit(activity='Downloading small company logos (cached locally)')
    symbols = list(dict.fromkeys(row.get('yahoo_symbol') or row['symbol'] for row in rows))
    with ThreadPoolExecutor(max_workers=4) as pool:
        images = dict(zip(symbols, pool.map(fetch, symbols)))
    for row in rows:
        content = images.get(row.get('yahoo_symbol') or row['symbol'])
        row['logo_webp'] = base64.b64encode(content).decode('ascii') if content else None
        row['logo_source'] = 'Financial Modeling Prep' if content else None
