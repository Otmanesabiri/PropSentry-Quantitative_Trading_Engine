"""
src/broker/position_manager.py
==============================
Dynamic position management — breakeven and trailing stop logic.

Manages the lifecycle of open positions:
  - Move Stop Loss to breakeven when floating PnL reaches +1R (INVARIANT breakeven_trigger_r).
  - Track breakeven state to avoid redundant modifications.
"""

from __future__ import annotations

from typing import Protocol

from loguru import logger

from src.core.schemas.orders import OrderIntent


class PositionModifier(Protocol):
    """Minimal broker interface for modifying open positions."""
    def modify_sl(self, ticket: int, new_sl: float) -> bool: ...


class PositionManager:
    """
    Monitors open positions and applies breakeven management.

    Parameters
    ----------
    modifier           : PositionModifier implementation.
    breakeven_trigger_r: R-multiple at which to move SL to breakeven (default 1.0).
    """

    def __init__(
        self,
        modifier: PositionModifier,
        breakeven_trigger_r: float = 1.0,
    ) -> None:
        self._modifier = modifier
        self._breakeven_trigger_r = breakeven_trigger_r
        self._breakeven_applied: set[int] = set()  # broker tickets

    def check_breakeven(
        self,
        ticket: int,
        intent: OrderIntent,
        current_price: float,
    ) -> bool:
        """
        Check if breakeven should be applied and modify SL if needed.

        Parameters
        ----------
        ticket        : Broker ticket ID of the open position.
        intent        : Original OrderIntent (for entry/SL reference).
        current_price : Current bid/ask price.

        Returns
        -------
        bool
            True if breakeven was applied, False otherwise.
        """
        if ticket in self._breakeven_applied:
            return False

        risk = abs(intent.entry_price - intent.stop_loss)
        target_price = (
            intent.entry_price + risk * self._breakeven_trigger_r
            if intent.side.value == "BUY"
            else intent.entry_price - risk * self._breakeven_trigger_r
        )

        be_triggered = (
            current_price >= target_price if intent.side.value == "BUY"
            else current_price <= target_price
        )

        if be_triggered:
            success = self._modifier.modify_sl(ticket, intent.entry_price)
            if success:
                self._breakeven_applied.add(ticket)
                logger.info(
                    f"[POS MANAGER] Breakeven applied | Ticket={ticket} | "
                    f"New SL={intent.entry_price:.5f} | Trigger price={target_price:.5f}"
                )
            return success

        return False
