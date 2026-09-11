"""
tests/test_kill_switch.py
=========================
Validates INVARIANT 4: Kill-switch triggers at -4.20% equity loss,
cancels all orders, and flattens all positions immediately.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
import pytz

from src.core.exceptions import DailyLossLimitBreached, OverallLossLimitBreached
from src.risk.equity_watchdog import EquitySnapshot, EquityWatchdog
from src.risk.kill_switch import KillSwitch, KillSwitchState

_UTC = pytz.utc


def make_snapshot(balance: float, open_pnl: float) -> EquitySnapshot:
    return EquitySnapshot(
        timestamp=datetime.now(tz=_UTC),
        balance=balance,
        open_pnl=open_pnl,
        commissions=0.0,
        swap=0.0,
    )


class TestEquityWatchdog:
    """Tests for tick-by-tick equity monitoring."""

    def setup_method(self):
        reset_time = datetime(2024, 1, 16, 23, 0, tzinfo=_UTC)  # 00:00 CET
        self.watchdog = EquityWatchdog(
            initial_balance=5000.0,
            reset_time_utc=reset_time,
        )

    def test_healthy_equity_passes(self):
        snap = make_snapshot(5000.0, -100.0)  # -$100 = -2% (safe)
        self.watchdog.on_tick(snap)  # Should not raise

    def test_kill_switch_triggers_at_4_2_pct(self):
        snap = make_snapshot(5000.0, -210.0)  # -$210 = -4.2% — triggers
        with pytest.raises(DailyLossLimitBreached):
            self.watchdog.on_tick(snap)
        assert self.watchdog.is_kill_switch_active

    def test_kill_switch_not_triggered_below_threshold(self):
        snap = make_snapshot(5000.0, -209.0)  # -$209 = just under 4.2%
        self.watchdog.on_tick(snap)
        assert not self.watchdog.is_kill_switch_active

    def test_overall_loss_limit_triggers(self):
        snap = make_snapshot(4500.0, -1.0)  # balance dropped to 4500 → -10% overall
        with pytest.raises(OverallLossLimitBreached):
            self.watchdog.on_tick(snap)

    def test_kill_switch_callback_invoked(self):
        callback = MagicMock()
        self.watchdog.register_kill_switch_callback(callback)
        snap = make_snapshot(5000.0, -210.0)
        with pytest.raises(DailyLossLimitBreached):
            self.watchdog.on_tick(snap)
        callback.assert_called_once_with("DAILY_KILL_SWITCH")

    def test_daily_reset_clears_kill_switch(self):
        snap = make_snapshot(5000.0, -210.0)
        with pytest.raises(DailyLossLimitBreached):
            self.watchdog.on_tick(snap)
        assert self.watchdog.is_kill_switch_active

        # Reset for new day
        self.watchdog.reset_for_new_day(
            new_balance=5000.0,
            reset_time_utc=datetime.now(tz=_UTC),
        )
        assert not self.watchdog.is_kill_switch_active


class TestKillSwitch:
    """Tests for the hard kill-switch executor."""

    def setup_method(self):
        self.broker_mock = MagicMock()
        self.broker_mock.cancel_all_pending_orders.return_value = 3
        self.broker_mock.flatten_all_positions.return_value = 1
        self.ks = KillSwitch(broker=self.broker_mock, symbol="EURUSD")

    def test_initially_inactive(self):
        assert not self.ks.is_active

    def test_execute_arms_kill_switch(self):
        self.ks.execute("DAILY_KILL_SWITCH")
        assert self.ks.is_active

    def test_execute_cancels_orders(self):
        self.ks.execute("DAILY_KILL_SWITCH")
        self.broker_mock.cancel_all_pending_orders.assert_called_once_with("EURUSD")

    def test_execute_flattens_positions(self):
        self.ks.execute("DAILY_KILL_SWITCH")
        self.broker_mock.flatten_all_positions.assert_called_once_with("EURUSD")

    def test_execute_is_idempotent(self):
        self.ks.execute("DAILY_KILL_SWITCH")
        self.ks.execute("DAILY_KILL_SWITCH")  # Second call should be ignored
        assert self.broker_mock.cancel_all_pending_orders.call_count == 1

    def test_reset_deactivates(self):
        self.ks.execute("DAILY_KILL_SWITCH")
        assert self.ks.is_active
        self.ks.reset_for_new_day()
        assert not self.ks.is_active
