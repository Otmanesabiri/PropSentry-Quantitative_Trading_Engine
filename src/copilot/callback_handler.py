"""
src/copilot/callback_handler.py
================================
Processes Telegram inline button callbacks for trade approval/rejection.
"""

from __future__ import annotations

from loguru import logger
from telegram import Update
from telegram.ext import ContextTypes

from src.core.schemas.orders import OrderIntent


class CallbackHandler:
    """
    Handles [✅ Validate & Execute] and [❌ Reject] button presses.

    Parameters
    ----------
    pending      : Shared dict of pending order intents (callback_data → OrderIntent).
    on_approved  : Async callable invoked on approval: async (intent: OrderIntent) → None.
    on_rejected  : Async callable invoked on rejection: async (intent: OrderIntent, reason: str) → None.
    """

    def __init__(
        self,
        pending: dict[str, OrderIntent],
        on_approved: object,
        on_rejected: object,
    ) -> None:
        self._pending = pending
        self._on_approved = on_approved
        self._on_rejected = on_rejected

    async def handle(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Route the callback to approve or reject handler."""
        query = update.callback_query
        if query is None:
            return

        await query.answer()  # Acknowledge button press immediately
        data = query.data or ""

        intent = self._pending.pop(data, None)
        if intent is None:
            await query.edit_message_text("⚠️ This alert has already expired or been processed.")
            return

        # Remove both approve and reject keys for this order
        order_str = str(intent.order_id)
        self._pending.pop(f"approve_{order_str}", None)
        self._pending.pop(f"reject_{order_str}", None)

        if data.startswith("approve_"):
            logger.info(f"[CALLBACK] Order {intent.order_id} APPROVED by operator.")
            await query.edit_message_text(
                f"✅ <b>Order APPROVED</b>\n"
                f"Dispatching {intent.side.value} {intent.lot_size} {intent.symbol}...",
                parse_mode="HTML",
            )
            if self._on_approved:
                await self._on_approved(intent)  # type: ignore[misc]

        elif data.startswith("reject_"):
            logger.info(f"[CALLBACK] Order {intent.order_id} REJECTED by operator.")
            await query.edit_message_text(
                f"❌ <b>Order REJECTED</b>\n"
                f"Signal for {intent.symbol} discarded.",
                parse_mode="HTML",
            )
            if self._on_rejected:
                await self._on_rejected(intent, "OPERATOR_REJECTED")  # type: ignore[misc]
