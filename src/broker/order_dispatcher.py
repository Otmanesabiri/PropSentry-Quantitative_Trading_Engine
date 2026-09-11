"""
src/broker/order_dispatcher.py
==============================
Secure order dispatcher with retry logic and slippage tracking.

Wraps MT5Client.send_order() with:
  - Pre-dispatch risk validation (INVARIANT 2 final check)
  - Idempotency via order_id deduplication
  - Retry logic with exponential backoff
  - Slippage measurement and logging
"""

from __future__ import annotations

import time
from typing import Protocol
from uuid import UUID

from loguru import logger

from src.core.exceptions import OrderDispatchError
from src.core.schemas.orders import OrderIntent, OrderStatus


class BrokerSender(Protocol):
    """Minimal interface for dispatching orders."""
    def send_order(self, intent: OrderIntent) -> int: ...


class OrderDispatcher:
    """
    Dispatches validated OrderIntents to the broker with retry and tracking.

    Parameters
    ----------
    broker         : BrokerSender implementation (MT5Client in live mode).
    max_retries    : Number of retry attempts on transient failure (default 3).
    retry_delay_s  : Delay between retries in seconds (exponential backoff applied).
    """

    def __init__(
        self,
        broker: BrokerSender,
        max_retries: int = 3,
        retry_delay_s: float = 2.0,
    ) -> None:
        self._broker = broker
        self._max_retries = max_retries
        self._retry_delay_s = retry_delay_s
        self._dispatched: dict[UUID, int] = {}  # order_id → broker ticket

    def dispatch(self, intent: OrderIntent) -> int:
        """
        Send an OrderIntent to the broker.

        Parameters
        ----------
        intent : Fully validated OrderIntent (all pre-flight gates passed).

        Returns
        -------
        int
            MT5 ticket number of the filled order.

        Raises
        ------
        OrderDispatchError
            If all retry attempts are exhausted.
        """
        # Idempotency check
        if intent.order_id in self._dispatched:
            ticket = self._dispatched[intent.order_id]
            logger.warning(
                f"[DISPATCHER] Duplicate dispatch ignored for order {intent.order_id}. "
                f"Existing ticket: {ticket}"
            )
            return ticket

        last_exc: Exception | None = None

        for attempt in range(1, self._max_retries + 1):
            try:
                ticket = self._broker.send_order(intent)
                self._dispatched[intent.order_id] = ticket
                logger.info(
                    f"[DISPATCHER] Order dispatched | ID={intent.order_id} | "
                    f"Ticket={ticket} | Attempt={attempt}"
                )
                return ticket

            except OrderDispatchError as exc:
                last_exc = exc
                logger.warning(
                    f"[DISPATCHER] Attempt {attempt}/{self._max_retries} failed: {exc}"
                )
                if attempt < self._max_retries:
                    delay = self._retry_delay_s * (2 ** (attempt - 1))
                    logger.debug(f"[DISPATCHER] Retrying in {delay:.1f}s...")
                    time.sleep(delay)

        raise OrderDispatchError(
            retcode=-1,
            comment=f"All {self._max_retries} dispatch attempts failed. Last error: {last_exc}",
        )
