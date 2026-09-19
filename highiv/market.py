"""Market-hours calendar: when a session's closing quotes are final and when the next one opens (New York time).

Weekends only; exchange holidays count as sessions here, so on a holiday data is simply fetched again.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

MARKET_TZ = ZoneInfo("America/New_York")
SETTLED = time(16, 30)  # closing prints, VIX, option IV and Cboe's daily statistics are in by then
OPEN = time(9, 30)


def settled_at(day: date) -> datetime:
    return datetime.combine(day, SETTLED, MARKET_TZ)


def last_session(now: datetime) -> tuple[date, datetime]:
    """The latest weekday session whose close has settled, and the next opening bell after it."""
    day = now.astimezone(MARKET_TZ).date()
    if now < settled_at(day):
        day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    following = day + timedelta(days=1)
    while following.weekday() >= 5:
        following += timedelta(days=1)
    return day, datetime.combine(following, OPEN, MARKET_TZ)


def is_open(now: datetime) -> bool:
    """Inside a weekday's regular hours, up to the settle time, when quotes are still moving."""
    local = now.astimezone(MARKET_TZ)
    return local.weekday() < 5 and OPEN <= local.time() < SETTLED
