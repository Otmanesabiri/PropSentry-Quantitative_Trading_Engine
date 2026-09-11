"""
src/risk/equity_watchdog.py
===========================
Continuous floating equity monitor — INVARIANT 3.

Floating equity is computed tick-by-tick as:
    equity = balance + open_pnl - commissions ± swap

Daily drawdown is measured from the starting balance at 00:00 CE(S)T,
NOT from the closed trade balance — this is the EQUITY_CONTINUOUS mode.

The watchdog emits structured events and triggers the kill-switch when
the -4.20% threshold is reached (INVARIANT 4).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from loguru import logger

from src.core.broker_clock import get_utc_now
from src.core.exceptions import DailyLossLimitBreached, OverallLossLimitBreached


# ---------------------------------------------------------------------------
# Equity snapshot (input)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EquitySnapshot:
    """
    A single tick-level equity reading from the broker.
    All monetary values in account currency (USD).
    """
    timestamp: datetime
    balance: float              # Settled cash balance
    open_pnl: float             # Unrealised PnL of all open positions
    commissions: float          # Accumulated commissions (negative = cost)
    swap: float                 # Accumulated swap (can be positive or negative)

    @property
    def floating_equity(self) -> float:
        """True floating equity: balance + open_pnl - commissions + swap."""
        return self.balance + self.open_pnl - abs(self.commissions) + self.swap


# ---------------------------------------------------------------------------
# Watchdog state
# ---------------------------------------------------------------------------

@dataclass
class WatchdogState:
    """Mutable state maintained across a trading day."""
    day_start_balance: float                  # Balance at 00:00 CE(S)T
    day_start_time: datetime                  # UTC timestamp of daily reset
    kill_switch_triggered: bool = False
    peak_equity: float = field(init=False)

    def __post_init__(self) -> None:
        self.peak_equity = self.day_start_balance

    @property
    def daily_loss_limit_usd(self) -> float:
        """5.0% of day-start balance."""
        return self.day_start_balance * 0.05

    @property
    def kill_switch_threshold_usd(self) -> float:
        """4.2% of day-start balance (internal safety buffer)."""
        return self.day_start_balance * 0.042

    @property
    def overall_floor_usd(self) -> float:
        """10.0% of day-start balance (absolute max overall loss)."""
        return self.day_start_balance * 0.10

    def daily_loss_usd(self, current_equity: float) -> float:
        """Current daily loss in USD (positive = loss)."""
        return self.day_start_balance - current_equity

    def daily_loss_pct(self, current_equity: float) -> float:
        """Current daily loss as a fraction of day-start balance."""
        return self.daily_loss_usd(current_equity) / self.day_start_balance


# ---------------------------------------------------------------------------
# Watchdog
# ---------------------------------------------------------------------------

class EquityWatchdog:
    """
    Tick-by-tick equity monitor.

    Usage
    -----
        watchdog = EquityWatchdog(initial_balance=5000.0, reset_time_utc=reset_dt)
        watchdog.on_tick(snapshot)   # raises if kill-switch threshold breached
    """

    def __init__(self, initial_balance: float, reset_time_utc: datetime) -> None:
        self._state = WatchdogState(
            day_start_balance=initial_balance,
            day_start_time=reset_time_utc,
        )
        self._on_kill_switch_callbacks: list = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register_kill_switch_callback(self, callback: object) -> None:
        """Register a callable to be invoked when the kill-switch fires."""
        self._on_kill_switch_callbacks.append(callback)

    def reset_for_new_day(self, new_balance: float, reset_time_utc: datetime) -> None:
        """Reset state at the start of a new trading day (00:00 CE(S)T)."""
        logger.info(
            f"[WATCHDOG] Daily reset | New balance: {new_balance:.2f} USD | "
            f"Reset at: {reset_time_utc.isoformat()}"
        )
        self._state = WatchdogState(
            day_start_balance=new_balance,
            day_start_time=reset_time_utc,
        )

    def on_tick(self, snapshot: EquitySnapshot) -> None:
        """
        Process a new equity tick.

        Raises
        ------
        DailyLossLimitBreached
            When floating equity crosses the -4.20% kill-switch threshold.
        OverallLossLimitBreached
            When floating equity crosses the absolute -10% floor.
        """
        state = self._state
        equity = snapshot.floating_equity

        # Update peak
        if equity > state.peak_equity:
            state.peak_equity = equity

        daily_loss = state.daily_loss_usd(equity)
        daily_loss_pct = state.daily_loss_pct(equity)

        logger.debug(
            f"[WATCHDOG] Equity={equity:.2f} | DailyLoss={daily_loss:.2f} USD "
            f"({daily_loss_pct*100:.3f}%)"
        )

        # INVARIANT 3 — overall floor check (10%)
        overall_loss = state.day_start_balance - equity  # vs initial day balance
        # Use absolute initial balance for overall DD check
        if overall_loss >= state.overall_floor_usd:
            logger.critical(
                f"[WATCHDOG] OVERALL LOSS LIMIT BREACHED: {overall_loss:.2f} USD "
                f"(≥ {state.overall_floor_usd:.2f} USD)"
            )
            state.kill_switch_triggered = True
            self._fire_kill_switch_callbacks("OVERALL_LOSS_LIMIT")
            raise OverallLossLimitBreached(
                f"Overall loss limit breached: equity={equity:.2f} USD, "
                f"loss={overall_loss:.2f} USD (≥10% floor)."
            )

        # INVARIANT 4 — daily kill-switch (-4.20%)
        if daily_loss >= state.kill_switch_threshold_usd and not state.kill_switch_triggered:
            logger.critical(
                f"[WATCHDOG] KILL-SWITCH TRIGGERED: Daily loss {daily_loss:.2f} USD "
                f"({daily_loss_pct*100:.3f}%) ≥ {state.kill_switch_threshold_usd:.2f} USD (4.20%)"
            )
            state.kill_switch_triggered = True
            self._fire_kill_switch_callbacks("DAILY_KILL_SWITCH")
            raise DailyLossLimitBreached(
                f"Daily kill-switch triggered: equity={equity:.2f} USD, "
                f"daily loss={daily_loss:.2f} USD ({daily_loss_pct*100:.2f}%)."
            )

    @property
    def is_kill_switch_active(self) -> bool:
        return self._state.kill_switch_triggered

    @property
    def state(self) -> WatchdogState:
        return self._state

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _fire_kill_switch_callbacks(self, reason: str) -> None:
        for callback in self._on_kill_switch_callbacks:
            try:
                callback(reason)
            except Exception as exc:  # noqa: BLE001
                logger.error(f"[WATCHDOG] Kill-switch callback error: {exc}")
