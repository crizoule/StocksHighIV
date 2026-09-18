"""User-selected symbols stored alongside local market data, independent of rankings."""
import json
import os
import re
from . import config


def normalize(symbol):
    if not isinstance(symbol, str):
        raise ValueError('Enter a ticker.')
    symbol = symbol.strip().upper()
    if not re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,19}', symbol):
        raise ValueError('Use a US ticker or a Canadian Yahoo ticker such as SHOP.TO.')
    return symbol


def load(root=None):
    path = root/'data/watchlist.json' if root is not None else config.DATA_DIR/'watchlist.json'
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return list(dict.fromkeys(normalize(s) for s in data))
    except (OSError, ValueError, TypeError):
        return []


def change(root, symbol, action):
    symbol = normalize(symbol)
    symbols = load(root)
    if action == 'add' and symbol not in symbols:
        if len(symbols) >= 100:
            raise ValueError('The watchlist supports up to 100 tickers.')
        symbols.append(symbol)
    elif action == 'remove':
        symbols = [s for s in symbols if s != symbol]
    elif action != 'add':
        raise ValueError('Unknown watchlist action.')
    path = root/'data/watchlist.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(symbols))
    os.replace(temporary, path)
    return symbols


def include(stocks, symbols):
    by_symbol = {s['symbol']: dict(s) for s in stocks if not s.get('watch_only')}
    for symbol in symbols:
        if symbol in by_symbol:
            continue
        canada = symbol.endswith(('.TO', '.V'))
        by_symbol[symbol] = dict(symbol=symbol, yahoo=symbol, name=symbol,
            cboe=None if canada else symbol.replace('-', '.'),
            mx=symbol.rsplit('.', 1)[0].split('-')[0] if canada else None,
            market='CA' if canada else 'US', exchange='TSXV' if symbol.endswith('.V') else 'TSX' if canada else '',
            market_cap_usd=0, hq_country=None, sector=None, also_listed=None, watch_only=True)
    return list(by_symbol.values())
