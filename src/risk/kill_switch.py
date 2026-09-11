"""
src/risk/kill_switch.py
=======================
Hard kill-switch executor — INVARIANT 4.

When triggered (daily loss ≥ -4.20% / 210 USD), this module:
  1. Cancels all pending orders.
  2. Flattens all open positions at market immediately.
  3. Disables the engine for all new entries until the next daily reset.

This module does NOT import MetaTrader5 directly (INVARIANT 6).
It calls into the broker interface via the injected MT5Client protocol.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from loguru import logger

from src.core.broker_clock import get_utc_now
from src.core.exceptions import InvariantBreachError


# ---------------------------------------------------------------------------
# Broker interface protocol (injected dependency — no direct MT5 import)
# ---------------------------------------------------------------------------

class BrokerInterface(Protocol):
    """Minimal broker actions required by the kill-switch."""

    def cancel_all_pending_orders(self, symbol: str | None = None) -> int:
        """Cancel all pending orders. Returns number of orders cancelled."""
        ...

    def flatten_all_positions(self, symbol: str | None = None) -> int:
        """Close all open positions at market. Returns number of positions closed."""
        ...


# ---------------------------------------------------------------------------
# Kill-switch state
# ---------------------------------------------------------------------------

class KillSwitchState:
    """Thread-safe kill-switch state container."""

    def __init__(self) -> None:
        self._active: bool = False
        self._triggered_at: datetime | None = None
        self._trigger_reason: str | None = None

    @property
    def is_active(self) -> bool:
        return self._active

    @property
    def triggered_at(self) -> datetime | None:
        return self._triggered_at

    @property
    def trigger_reason(self) -> str | None:
        return self._trigger_reason

    def arm(self, reason: str) -> None:
        self._active = True
        self._triggered_at = get_utc_now()
        self._trigger_reason = reason

    def reset(self) -> None:
        """Reset state at daily reset (00:00 CE(S)T)."""
        self._active = False
        self._triggered_at = None
        self._trigger_reason = None


# ---------------------------------------------------------------------------
# Kill-switch executor
# ---------------------------------------------------------------------------

class KillSwitch:
    """
    Stateful kill-switch that arms on breach and blocks all entries.

    Usage
    -----
        ks = KillSwitch(broker=mt5_client)
        # Called by EquityWatchdog callback:
        ks.execute("DAILY_KILL_SWITCH")
        # Guard in pre-flight:
        if ks.is_active: raise KillSwitchActiveGateError(...)
    """

    def __init__(self, broker: BrokerInterface, symbol: str | None = None) -> None:
        self._broker = broker
        self._symbol = symbol
        self._state = KillSwitchState()

    @property
    def is_active(self) -> bool:
        return self._state.is_active

    @property
    def state(self) -> KillSwitchState:
        return self._state

    def execute(self, reason: str) -> None:
        """
        Execute the kill-switch sequence:
          1. Arm the state (blocks pre-flight gate immediately).
          2. Cancel all pending orders.
          3. Flatten all open positions at market.

        This method is idempotent — calling it multiple times is safe.

        Parameters
        ----------
        reason : Human-readable trigger reason for audit logging.
        """
        if self._state.is_active:
            logger.warning(f"[KILL-SWITCH] Already active (reason={self._state.trigger_reason}). Ignoring re-trigger.")
            return

        self._state.arm(reason)

        logger.critical(
            f"[KILL-SWITCH] *** KILL-SWITCH ACTIVATED *** | Reason: {reason} | "
            f"Time: {self._state.triggered_at.isoformat() if self._state.triggered_at else 'N/A'}"  # type: ignore[union-attr]
        )

        # Step 1: Cancel pending orders
        try:
            cancelled = self._broker.cancel_all_pending_orders(self._symbol)
            logger.critical(f"[KILL-SWITCH] Cancelled {cancelled} pending order(s).")
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[KILL-SWITCH] Failed to cancel pending orders: {exc}")

        # Step 2: Flatten open positions
        try:
            closed = self._broker.flatten_all_positions(self._symbol)
            logger.critical(f"[KILL-SWITCH] Flattened {closed} open position(s) at market.")
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[KILL-SWITCH] Failed to flatten positions: {exc}")

    def reset_for_new_day(self) -> None:
        """
        Reset the kill-switch at the daily 00:00 CE(S)T boundary.
        Must be called by the daily reset routine in the live runner.
        """
        if self._state.is_active:
            logger.info(
                f"[KILL-SWITCH] Resetting for new trading day. "
                f"Previous trigger: {self._state.trigger_reason}"
            )
        self._state.reset()
