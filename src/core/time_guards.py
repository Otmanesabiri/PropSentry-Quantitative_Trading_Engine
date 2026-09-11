"""
src/core/time_guards.py
=======================
Session window and weekend protection guards.

Enforces the session filters defined in config/prop_firm_rules.yaml:
  - London Open:   08:00 – 11:00 UTC
  - New York Open: 13:30 – 16:30 UTC
  - Weekend:       Flatten all positions by Friday 20:00 UTC; no new trades Sat/Sun

Raises PreflightGateError subclasses (SessionWindowGateError,
WeekendProtectionGateError) so the pre-flight pipeline can intercept them.
"""

from __future__ import annotations

from datetime import datetime, time

import pytz

from src.core.broker_clock import get_utc_now
from src.core.exceptions import SessionWindowGateError, WeekendProtectionGateError

_UTC = pytz.utc

# Session windows (UTC)
_LONDON_OPEN_START = time(8, 0)
_LONDON_OPEN_END = time(11, 0)
_NY_OPEN_START = time(13, 30)
_NY_OPEN_END = time(16, 30)

# Weekend protection
_FRIDAY_FLATTEN_TIME = time(20, 0)  # UTC

# Weekday constants (Monday=0, Sunday=6)
_FRIDAY = 4
_SATURDAY = 5
_SUNDAY = 6


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _time_in_window(t: time, start: time, end: time) -> bool:
    """Return True if *t* falls within [start, end) (inclusive start, exclusive end)."""
    return start <= t < end


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_in_trading_session(reference: datetime | None = None) -> bool:
    """
    Return True if the current UTC time falls within an allowed trading window.

    Windows: London Open (08:00–11:00 UTC) OR New York Open (13:30–16:30 UTC).
    """
    now = (reference or get_utc_now()).astimezone(_UTC)
    t = now.time()
    return _time_in_window(t, _LONDON_OPEN_START, _LONDON_OPEN_END) or \
           _time_in_window(t, _NY_OPEN_START, _NY_OPEN_END)


def is_weekend_protection_active(reference: datetime | None = None) -> bool:
    """
    Return True if weekend protection should block new trade entries.

    Triggers on:
      - Saturday (all day)
      - Sunday (all day)
      - Friday after 20:00 UTC
    """
    now = (reference or get_utc_now()).astimezone(_UTC)
    weekday = now.weekday()
    if weekday == _SATURDAY or weekday == _SUNDAY:
        return True
    if weekday == _FRIDAY and now.time() >= _FRIDAY_FLATTEN_TIME:
        return True
    return False


def should_flatten_for_weekend(reference: datetime | None = None) -> bool:
    """
    Return True if open positions should be flattened immediately.
    This is True from Friday 20:00 UTC through Sunday 23:59 UTC.
    """
    return is_weekend_protection_active(reference)


def assert_trading_session_open(reference: datetime | None = None) -> None:
    """
    Assert that the current time is inside an allowed trading session.

    Raises
    ------
    WeekendProtectionGateError
        If weekend protection is active.
    SessionWindowGateError
        If outside London/NY trading windows.
    """
    now = reference or get_utc_now()

    if is_weekend_protection_active(now):
        raise WeekendProtectionGateError(
            f"Weekend protection active at {now.isoformat()} UTC — no new entries allowed."
        )

    if not is_in_trading_session(now):
        raise SessionWindowGateError(
            f"Outside trading sessions at {now.strftime('%H:%M')} UTC. "
            f"Allowed: London 08:00–11:00 | NY 13:30–16:30."
        )
