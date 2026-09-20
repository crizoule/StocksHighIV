"""Fear & Greed replica from public data. CNN's own feed stays the primary reading.

CNN averages seven 0–100 component scores. The scoring rule was recovered from CNN's published
history: each input is z-scored against its trailing 125 sessions, and the latest z-score is ranked
against the trailing 500 (share strictly below). CNN's volatility score stays at 50 unless that rank
signals extreme fear. Inputs are public stand-ins for CNN's vendor data, so the replica tracks CNN
closely but is never presented as CNN's reading. No network access here; see sentiment.py.
"""
from __future__ import annotations

import math
import re

import numpy as np
import pandas as pd

Z_WINDOW = 125
RANK_WINDOW = 500
VIX_Z_WINDOW = 50
VIX_FEAR_BELOW = 12.5          # CNN's volatility score leaves 50 only in extreme fear
MIN_COMPONENTS = 5
MIN_NYSE_STOCKS = 500
MAX_LAG_DAYS = 4               # a component older than this, relative to the S&P close, is excluded
PUT_CALL_SESSIONS = RANK_WINDOW + Z_WINDOW + 4 + 20   # 5-session averages, plus slack for unpublished sessions
INDEX_SYMBOLS = ("^GSPC", "^VIX", "IEF", "HYG", "LQD")
PC_GROUPS = {"total": "SUM OF ALL PRODUCTS", "index": "INDEX OPTIONS",
             "etp": "EXCHANGE TRADED PRODUCTS", "equity": "EQUITY OPTIONS"}
COMPONENTS = {
    "momentum": ("Market momentum", "Yahoo ^GSPC"),
    "strength": ("Stock price strength", "Yahoo daily bars · NYSE stocks in the screen universe"),
    "breadth": ("Stock price breadth", "Yahoo daily bars · NYSE stocks in the screen universe"),
    "put_call": ("Put and call options", "Cboe daily market statistics"),
    "volatility": ("Market volatility", "Yahoo ^VIX"),
    "junk": ("Junk bond demand", "Yahoo HYG / LQD prices and distributions"),
    "safe_haven": ("Safe haven demand", "Yahoo ^GSPC / IEF"),
}
METHOD = (
    "Replica of CNN's seven-input index from public data, not CNN's reading. Each input is z-scored against its "
    "trailing 125 sessions and ranked against its trailing 500 z-scores (CNN's rule, recovered from its published "
    "history); volatility stays at 50 unless it ranks in extreme fear. Scores are relative to each input's recent "
    "history, so a positive input can still rank as fear. Score = average of available components. "
    "NYSE breadth uses the screen's NYSE stocks (≥ $1B), not every NYSE issue."
)


def rating(score):
    return ("Extreme fear" if score < 25 else "Fear" if score < 45 else "Neutral" if score < 55
            else "Greed" if score < 75 else "Extreme greed")


def zscore(series, window=Z_WINDOW):
    values = series.dropna()
    return ((values - values.rolling(window).mean()) / values.rolling(window).std()).dropna()


def rank_scores(series, window=RANK_WINDOW):
    """Share of the trailing `window` values strictly below each value, 0–100."""
    values = series.dropna()
    if len(values) < window:
        return pd.Series(dtype=float)
    data = values.to_numpy(dtype=float)
    windows = np.lib.stride_tricks.sliding_window_view(data, window)
    return pd.Series(100 * (windows < data[window - 1:, None]).sum(axis=1) / window, values.index[window - 1:])


def component_scores(inputs):
    scores = {}
    for key, raw in inputs.items():
        if raw is None or raw.dropna().empty:
            continue
        if key == "volatility":
            score = rank_scores(-zscore(raw, VIX_Z_WINDOW))
            scores[key] = score.where(score < VIX_FEAR_BELOW, 50.0)
        else:
            scores[key] = rank_scores((-1 if key in ("put_call", "junk") else 1) * zscore(raw))
    return scores


def completed(frame, today, market_hour):
    """Drop future rows and today's bar before the 16:00 close, so a partial session never scores."""
    days = pd.Index([d.date() for d in frame.index])
    keep = (days < today) | ((days == today) & (market_hour >= 16))
    return frame[keep]


def trailing_yield(price, dividends, count=12):
    """Last `count` distributions over the close, in percent; NaN until `count` are on record."""
    events = dividends[dividends > 0].dropna()
    paid = np.concatenate([[0.0], np.cumsum(events.to_numpy(dtype=float))])
    seen = np.searchsorted(events.index.to_numpy(), price.index.to_numpy(), side="right")
    total = np.where(seen >= count, paid[seen] - paid[np.maximum(seen - count, 0)], np.nan)
    return 100 * pd.Series(total, price.index) / price


def index_inputs(close, dividends):
    spx, ief = close["^GSPC"].dropna(), close["IEF"].dropna()
    gap = (trailing_yield(close["HYG"].dropna(), dividends["HYG"])
           - trailing_yield(close["LQD"].dropna(), dividends["LQD"]))
    return {
        "momentum": spx,
        "volatility": close["^VIX"].dropna(),
        # 20 sessions inclusive: today against 19 sessions earlier.
        "safe_haven": (100 * (spx.pct_change(19) - ief.pct_change(19))).dropna(),
        # CNN's credit series matches the prior session's distribution yields, so lag it one session.
        "junk": gap.dropna().shift(1).dropna(),
    }


def nyse_counts(close, high, low, volume):
    """One group of stocks reduced to daily market-wide counts.

    The counts add up across groups, so the caller folds the market in group by group instead of holding
    twenty years of every stock at once.
    """
    prior_high = high.shift(1).rolling(251).max()
    prior_low = low.shift(1).rolling(251).min()
    listed = close.notna() & prior_high.notna() & prior_low.notna()
    change = close.diff()
    return pd.DataFrame({
        "count": listed.sum(axis=1),
        "highs": ((high >= prior_high) & listed).sum(axis=1),
        "lows": ((low <= prior_low) & listed).sum(axis=1),
        "traded": change.notna().sum(axis=1),
        "up": volume.where(change > 0).sum(axis=1),
        "down": volume.where(change < 0).sum(axis=1),
    })


def nyse_inputs(totals):
    """Strength and breadth from the summed daily counts of every NYSE group."""
    count = totals["count"]
    strength = (100 * (totals["highs"] - totals["lows"]) / count.where(count >= MIN_NYSE_STOCKS)).rolling(20).mean()
    up, down = totals["up"], totals["down"]
    net = (1000 * (up - down) / (up + down).where(totals["traded"] >= MIN_NYSE_STOCKS)).dropna()
    # Ratio-adjusted McClellan volume oscillator (19/39-day EMAs), summed; the first 100 sessions warm the EMAs.
    breadth = (net.ewm(alpha=0.1, adjust=False).mean() - net.ewm(alpha=0.05, adjust=False).mean()).cumsum()
    return {"strength": strength.dropna(), "breadth": breadth.iloc[100:]}, int(count.iloc[-1]) if len(count) else 0


def parse_put_call_volumes(html):
    """Put and call volumes for one dated Cboe session, by product group."""
    decoded = html.replace('\\"', '"')
    dates = set(re.findall(r'"selectedDate"\s*:\s*"(\d{4}-\d{2}-\d{2})"', decoded))
    if len(dates) != 1:
        raise ValueError("Missing or ambiguous options session")
    volumes = {}
    for key, name in PC_GROUPS.items():
        match = re.search(r'"' + name + r'"\s*:\s*\[\s*\{\s*"name"\s*:\s*"VOLUME"\s*,\s*"call"\s*:\s*(\d+)\s*,\s*"put"\s*:\s*(\d+)', decoded)
        if match:
            volumes[key] = [int(match[1]), int(match[2])]
    if not {"equity", "etp"} <= volumes.keys() or not all(volumes[k][0] > 0 for k in ("equity", "etp")):
        raise ValueError("Missing put/call volumes")
    return dates.pop(), volumes


def put_call_ratio(volumes):
    # CNN's series tracks equity plus exchange-traded-product options, not index options.
    calls = volumes["equity"][0] + volumes["etp"][0]
    return (volumes["equity"][1] + volumes["etp"][1]) / calls


def put_call_input(history, sessions, archive=()):
    """Five-session average on the trading calendar; a missing session leaves that average undefined.

    `archive` holds Cboe's discontinued daily ratios (2003–2019) for the years before the app's own stored volumes.
    """
    daily = {pd.Timestamp(day): ratio for day, ratio in archive}
    for day, volumes in history.items():
        try:
            daily[pd.Timestamp(day)] = put_call_ratio(volumes)
        except (KeyError, TypeError, ZeroDivisionError):
            continue
    series = pd.Series(daily, dtype=float).reindex(pd.DatetimeIndex(sessions))
    return series.rolling(5).mean().dropna()


def _reading(key, inputs):
    raw = inputs[key].dropna()
    value = raw.iloc[-1]
    if key == "momentum":
        average = raw.rolling(Z_WINDOW).mean().iloc[-1]
        return f"S&P 500 {100 * (value / average - 1):+.1f}% vs 125-day average"
    if key == "volatility":
        return f"VIX {value:.2f} vs 50-day average {raw.rolling(VIX_Z_WINDOW).mean().iloc[-1]:.2f}"
    return {
        "strength": f"Net new 52-week highs {value:+.1f}% of stocks (20-session average)",
        "breadth": f"McClellan volume summation {zscore(raw).iloc[-1]:+.1f} SD vs 125 sessions",
        "put_call": f"5-session put/call {value:.2f} (equity + ETP)",
        "junk": f"HYG − LQD distribution yield {value:.2f} pp",
        "safe_haven": f"Stocks {value:+.1f} pp vs 7–10y Treasuries over 20 sessions",
    }[key]


def replica(inputs, notes=None):
    """Latest replica reading; components that are missing or stale are listed, never scored as 50."""
    notes = notes or {}
    scores = component_scores(inputs)
    as_of = inputs["momentum"].dropna().index[-1].date()
    parts = []
    for key, (name, source) in COMPONENTS.items():
        series = scores.get(key)
        part = dict(key=key, name=name, source=source, score=None, as_of=None, detail=notes.get(key))
        if series is not None and not series.empty:
            when = series.index[-1].date()
            part["as_of"] = when.isoformat()
            if 0 <= (as_of - when).days <= MAX_LAG_DAYS:
                score = round(float(series.iloc[-1]), 1)
                part.update(score=score, rating=rating(score), reading=_reading(key, inputs))
            else:
                part["detail"] = "Latest input is stale relative to the S&P 500 close; excluded."
        elif not part["detail"]:
            part["detail"] = f"Needs {RANK_WINDOW + Z_WINDOW} sessions of history; unavailable."
        parts.append(part)
    available = [p["score"] for p in parts if p["score"] is not None]
    if len(available) < MIN_COMPONENTS:
        raise ValueError("Too few replica components")
    value = sum(available) / len(available)
    missing = [p["name"] for p in parts if p["score"] is None]
    return dict(as_of=as_of.isoformat(), value=value, reading=f"{value:.1f}/100", signal=rating(value),
                direction=1 if value >= 55 else -1 if value <= 45 else 0, components=parts,
                coverage=len(available), method=METHOD,
                detail=f"{len(available)}/7 components{' (missing: ' + ', '.join(missing) + ')' if missing else ''}. "
                       "Checked against CNN's published history in September 2026: about 3 points apart on average, "
                       "same rating on about 4 of 5 sessions. Includes the volatility and options inputs shown here, "
                       "so it is not an independent confirmation.")


def history(inputs, require=len(COMPONENTS)):
    """Daily replica scores for validation: components plus their average on sessions with `require` of them."""
    frame = pd.DataFrame(component_scores(inputs))
    frame["score"] = frame.mean(axis=1).where(frame.notna().sum(axis=1) >= require)
    return frame


def compare(cnn, replica_scores):
    """Agreement between CNN's published daily scores and the replica on shared sessions."""
    both = pd.concat([cnn.rename("cnn"), replica_scores.rename("replica")], axis=1, sort=True).dropna()
    if len(both) < 20:
        raise ValueError("Too few shared sessions to compare")
    gap = both["replica"] - both["cnn"]
    same = sum(rating(a) == rating(b) for a, b in zip(both["cnn"], both["replica"]))
    return dict(start=both.index[0].date().isoformat(), end=both.index[-1].date().isoformat(), sessions=len(both),
                correlation=float(both["cnn"].corr(both["replica"])), mean_abs_gap=float(gap.abs().mean()),
                p90_abs_gap=float(gap.abs().quantile(0.9)), max_abs_gap=float(gap.abs().max()),
                same_rating=same / len(both), bias=float(gap.mean()))


def cnn_series(data, key="fear_and_greed_historical"):
    """CNN's published daily points for one series. Past sessions are stamped at 00:00 UTC of the market date."""
    points = {}
    for point in data[key]["data"]:
        value = float(point["y"])
        if math.isfinite(value):
            points[pd.Timestamp(point["x"], unit="ms").normalize()] = value
    return pd.Series(points, dtype=float).sort_index()
