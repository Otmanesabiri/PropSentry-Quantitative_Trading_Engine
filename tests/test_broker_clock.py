"""
tests/test_broker_clock.py
===========================
Validates broker clock DST-awareness and daily reset logic (INVARIANT 5).
No MT5 connection required — pure datetime arithmetic.
"""

from __future__ import annotations

from datetime import datetime

import pytest
import pytz

from src.core.broker_clock import (
    broker_date,
    get_daily_reset_utc,
    get_next_reset_utc,
    is_new_day_since,
    seconds_to_next_reset,
    to_broker_time,
)

_UTC = pytz.utc
_PRAGUE = pytz.timezone("Europe/Prague")


class TestBrokerClock:
    def test_to_broker_time_summer(self):
        """CEST = UTC+2 in summer."""
        utc_dt = datetime(2024, 7, 15, 10, 0, tzinfo=_UTC)
        broker_dt = to_broker_time(utc_dt)
        assert broker_dt.hour == 12  # UTC+2

    def test_to_broker_time_winter(self):
        """CET = UTC+1 in winter."""
        utc_dt = datetime(2024, 1, 15, 10, 0, tzinfo=_UTC)
        broker_dt = to_broker_time(utc_dt)
        assert broker_dt.hour == 11  # UTC+1

    def test_raises_on_naive_datetime(self):
        naive = datetime(2024, 1, 15, 10, 0)  # No tzinfo
        with pytest.raises(ValueError, match="naive datetime"):
            to_broker_time(naive)

    def test_daily_reset_at_midnight_prague_summer(self):
        """00:00 CEST = 22:00 UTC previous day."""
        ref = datetime(2024, 7, 15, 23, 0, tzinfo=_UTC)  # 01:00 CEST
        reset_utc = get_daily_reset_utc(reference=ref)
        reset_broker = to_broker_time(reset_utc)
        assert reset_broker.hour == 0
        assert reset_broker.minute == 0

    def test_daily_reset_at_midnight_prague_winter(self):
        """00:00 CET = 23:00 UTC previous day."""
        ref = datetime(2024, 1, 15, 1, 0, tzinfo=_UTC)  # 02:00 CET
        reset_utc = get_daily_reset_utc(reference=ref)
        reset_broker = to_broker_time(reset_utc)
        assert reset_broker.hour == 0
        assert reset_broker.minute == 0

    def test_next_reset_is_in_future(self):
        now = datetime.now(tz=_UTC)
        next_reset = get_next_reset_utc(reference=now)
        assert next_reset > now

    def test_next_reset_is_within_24h(self):
        from datetime import timedelta
        now = datetime.now(tz=_UTC)
        next_reset = get_next_reset_utc(reference=now)
        assert (next_reset - now).total_seconds() <= 86400

    def test_seconds_to_next_reset_positive(self):
        assert seconds_to_next_reset() > 0

    def test_is_new_day_since_detects_reset(self):
        yesterday = datetime(2024, 1, 14, 22, 0, tzinfo=_UTC)  # Previous day in Prague
        assert is_new_day_since(yesterday) is True

    def test_is_not_new_day_when_same_day(self):
        # Simulate: last reset was today at 00:00 CET → still same broker day
        from src.core.broker_clock import get_daily_reset_utc
        today_reset = get_daily_reset_utc()
        assert is_new_day_since(today_reset) is False
