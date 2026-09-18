"""Settings for the high-IV screener. Edit the thresholds here."""
import re
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNTIME_ROOT = Path(os.environ.get("HIGHIV_DATA_ROOT", ROOT))
DATA_DIR = RUNTIME_ROOT / "data"
SNAPSHOT_DIR = DATA_DIR / "snapshots"
OUTPUT_DIR = RUNTIME_ROOT / "output"
TEMPLATE_DIR = ROOT / "templates"
DB_PATH = DATA_DIR / "highiv.sqlite"

# Screen
MIN_MARKET_CAP_USD = 1_000_000_000
LARGE_MARKET_CAP_USD = 100_000_000_000
CAP_BANDS = ("mid", "large")
TOP_N = 100
MAX_DETAIL_LOOKUPS = 600      # Per cap band, so the smaller companies cannot exhaust the larger band's budget

# Industries left out of the screen, matched case-insensitively against Nasdaq's industry label
# (US universe, before scanning) and Yahoo's industry (every stock that reaches the list).
EXCLUDED_INDUSTRIES = (
    r"pharmaceutical preparations",   # Nasdaq: clinical-stage and specialty pharma
    r"biological products",           # Nasdaq: biotech drug developers
    r"medicinal chemicals",
    r"other pharmaceuticals",
    r"^biotechnology$",               # Yahoo
    r"drug manufacturer",             # Yahoo: general, speciality & generic
)
EXCLUDED_LABEL = "pharmaceutical and biotechnology drug developers"
EXCLUDED_SHORT = "no pharma or biotech"
_EXCLUDED_RE = re.compile("|".join(EXCLUDED_INDUSTRIES), re.I)


def excluded_industry(industry: str | None) -> bool:
    """Medical devices, diagnostics, research services and health providers stay in the screen."""
    return bool(industry and _EXCLUDED_RE.search(industry.strip()))

# IV rank / percentile look-back (calendar days ~ 52 weeks)
IV_LOOKBACK_DAYS = 365
MIN_IV_HISTORY_POINTS = 20    # below this, rank is shown as "building"

# Short-squeeze heuristic. A level applies when short interest (fraction of float) and days to cover
# both clear their bars, or short interest alone clears `short_pct_alone`.
SQUEEZE_HIGH = {"short_pct_float": 0.20, "days_to_cover": 5.0, "short_pct_alone": 0.30}
SQUEEZE_ELEVATED = {"short_pct_float": 0.10, "days_to_cover": 3.0, "short_pct_alone": 0.20}
SMALL_FLOAT_SHARES = 50_000_000

# Why-this-IV column: require a recent, sourced, company-specific event headline.
NEWS_LOOKBACK_DAYS = 7
NEWS_FETCH_COUNT = 30
EARNINGS_WHY_DAYS = 14

# Politeness: CBOE blocks for 60 s after ~60 requests/minute
CBOE_MAX_PER_MINUTE = 55
MX_MIN_INTERVAL_S = 1.0
ALPHAQUERY_MIN_INTERVAL_S = 1.0
YAHOO_MIN_INTERVAL_S = 0.25

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
