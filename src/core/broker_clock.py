"""
src/core/broker_clock.py
========================
Authoritative broker clock for CE(S)T timezone management.

INVARIANT 5: Daily reset occurs at 00:00 CE(S)T (Europe/Prague).
DST transitions (CET UTC+1 / CEST UTC+2) are handled dynamically
via the IANA timezone database — no static UTC offset is ever hardcoded.

Public API
----------
    get_broker_now()          → datetime in Europe/Prague
    get_utc_now()             → datetime in UTC
    to_broker_time(dt)        → convert any aware datetime to CE(S)T
    get_daily_reset_utc()     → today's 00:00 CE(S)T expressed as UTC datetime
    seconds_to_next_reset()   → seconds until next 00:00 CE(S)T
    broker_date()             → current date according to CE(S)T clock
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytz

_BROKER_TZ = pytz.timezone("Europe/Prague")
_UTC = pytz.utc

_RESET_TIME = time(0, 0, 0)  # 00:00:00 CE(S)T


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------

def get_utc_now() -> datetime:
    """Return current UTC datetime (timezone-aware)."""
    return datetime.now(tz=_UTC)


def get_broker_now() -> datetime:
    """Return current datetime in Europe/Prague timezone (CE(S)T or CET)."""
    return datetime.now(tz=_BROKER_TZ)


def to_broker_time(dt: datetime) -> datetime:
    """
    Convert any timezone-aware datetime to Europe/Prague local time.

    Raises
    ------
    ValueError
        If *dt* is a naive (timezone-unaware) datetime.
    """
    if dt.tzinfo is None:
        raise ValueError("Cannot convert naive datetime — provide a timezone-aware object.")
    return dt.astimezone(_BROKER_TZ)


def broker_date() -> date:
    """Return today's date as seen from the broker timezone (CE(S)T)."""
    return get_broker_now().date()


# ---------------------------------------------------------------------------
# Daily reset helpers
# ---------------------------------------------------------------------------

def get_daily_reset_utc(reference: datetime | None = None) -> datetime:
    """
    Return the most recent 00:00 CE(S)T reset boundary expressed in UTC.

    Parameters
    ----------
    reference:
        Timezone-aware datetime to compute from (defaults to now).
    """
    now_broker = to_broker_time(reference or get_utc_now())
    reset_broker = _BROKER_TZ.localize(
        datetime.combine(now_broker.date(), _RESET_TIME),
        is_dst=None,  # raises if ambiguous — use nonexistent/ambiguous guard
    )
    # If the computed reset is in the future, go back one day
    if reset_broker > now_broker:
        reset_broker = _BROKER_TZ.localize(
            datetime.combine(now_broker.date() - timedelta(days=1), _RESET_TIME),
            is_dst=None,
        )
    return reset_broker.astimezone(_UTC)


def get_next_reset_utc(reference: datetime | None = None) -> datetime:
    """
    Return the upcoming 00:00 CE(S)T reset boundary expressed in UTC.

    Parameters
    ----------
    reference:
        Timezone-aware datetime to compute from (defaults to now).
    """
    now_broker = to_broker_time(reference or get_utc_now())
    next_date = now_broker.date() + timedelta(days=1)
    next_reset_broker = _BROKER_TZ.localize(
        datetime.combine(next_date, _RESET_TIME),
        is_dst=None,
    )
    return next_reset_broker.astimezone(_UTC)


def seconds_to_next_reset() -> float:
    """Return the number of seconds until the next 00:00 CE(S)T daily reset."""
    now_utc = get_utc_now()
    next_reset = get_next_reset_utc(reference=now_utc)
    delta = next_reset - now_utc
    return max(0.0, delta.total_seconds())


def is_new_day_since(last_reset_utc: datetime) -> bool:
    """
    Return True if the current CE(S)T date is later than the date of
    *last_reset_utc* (i.e., a new daily accounting period has started).
    """
    last_broker = to_broker_time(last_reset_utc)
    now_broker = get_broker_now()
    return now_broker.date() > last_broker.date()
