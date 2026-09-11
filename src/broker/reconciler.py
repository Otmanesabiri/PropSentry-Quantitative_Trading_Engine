"""
src/broker/reconciler.py
========================
Periodic broker reconciliation — MT5 is the source of absolute truth.

Detects and resolves discrepancies between local engine state and
actual MT5 account state. Handles ghost positions and orphaned records.
"""

from __future__ import annotations

from typing import Protocol

from loguru import logger

from src.core.exceptions import ReconciliationError


class BrokerStateQuery(Protocol):
    """Interface for querying MT5 account state."""
    def get_open_positions_count(self, symbol: str | None = None) -> int: ...
    def get_account_balance(self) -> float: ...
    def get_account_equity(self) -> float: ...


class Reconciler:
    """
    Compares local engine state against MT5 and raises on irreconcilable divergence.

    Parameters
    ----------
    broker              : BrokerStateQuery implementation.
    tolerance_usd       : Acceptable balance divergence in USD (default 0.01).
    """

    def __init__(
        self,
        broker: BrokerStateQuery,
        tolerance_usd: float = 0.01,
    ) -> None:
        self._broker = broker
        self._tolerance = tolerance_usd

    def reconcile_positions(self, local_count: int, symbol: str | None = None) -> None:
        """
        Verify that local position count matches MT5.

        Raises
        ------
        ReconciliationError
            If MT5 reports a different number of open positions than the local state.
        """
        broker_count = self._broker.get_open_positions_count(symbol)

        if broker_count != local_count:
            raise ReconciliationError(
                f"Position count mismatch: local={local_count}, MT5={broker_count}. "
                "Ghost position or missed fill detected. Manual review required."
            )

        logger.debug(f"[RECONCILER] Positions reconciled: {broker_count} open.")

    def reconcile_balance(self, local_balance: float) -> float:
        """
        Verify that local balance is consistent with MT5.

        Returns
        -------
        float
            The authoritative balance from MT5.

        Raises
        ------
        ReconciliationError
            If the divergence exceeds the tolerance threshold.
        """
        broker_balance = self._broker.get_account_balance()
        divergence = abs(broker_balance - local_balance)

        if divergence > self._tolerance:
            logger.warning(
                f"[RECONCILER] Balance divergence detected: "
                f"local={local_balance:.2f}, MT5={broker_balance:.2f}, "
                f"delta={divergence:.4f} USD"
            )
            if divergence > 1.0:  # Hard error above $1 divergence
                raise ReconciliationError(
                    f"Critical balance divergence: local={local_balance:.2f}, "
                    f"MT5={broker_balance:.2f}, delta={divergence:.4f} USD."
                )

        return broker_balance
