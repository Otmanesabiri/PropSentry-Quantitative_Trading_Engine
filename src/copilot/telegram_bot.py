"""
src/copilot/telegram_bot.py
===========================
Async Telegram bot — operator interface for the semi-automated copilot.

Flow:
  1. Engine generates an OrderIntent.
  2. Bot sends a rich alert message with risk metrics.
  3. Operator clicks [✅ Validate & Execute] or [❌ Reject].
  4. CallbackHandler processes the decision.
  5. On approval: order is dispatched to MT5.
  6. On timeout (2 min): order is auto-rejected and logged.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from loguru import logger
from telegram import Bot, InlineKeyboardMarkup
from telegram.ext import Application, CallbackQueryHandler, CommandHandler

from config.loader import get_settings
from src.copilot.callback_handler import CallbackHandler
from src.copilot.message_formatter import format_order_alert
from src.core.schemas.orders import OrderIntent


class TelegramCopilot:
    """
    Async Telegram bot for semi-automated trade approval.

    Parameters
    ----------
    bot_token         : Telegram BotFather token.
    chat_id           : Operator's Telegram chat ID.
    alert_timeout_s   : Seconds before an unanswered alert auto-rejects (default 120).
    on_approved       : Async callback invoked when operator approves.
    on_rejected       : Async callback invoked when operator rejects or times out.
    """

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        alert_timeout_s: int = 120,
        on_approved: object = None,
        on_rejected: object = None,
    ) -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._timeout = alert_timeout_s
        self._on_approved = on_approved
        self._on_rejected = on_rejected

        self._pending: dict[str, OrderIntent] = {}  # callback_data → OrderIntent
        self._app: Application | None = None

    async def start(self) -> None:
        """Initialize and start polling."""
        self._app = (
            Application.builder()
            .token(self._bot_token)
            .build()
        )

        handler = CallbackHandler(
            pending=self._pending,
            on_approved=self._on_approved,
            on_rejected=self._on_rejected,
        )

        self._app.add_handler(CallbackQueryHandler(handler.handle))
        self._app.add_handler(CommandHandler("status", self._cmd_status))

        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling()
        logger.info(f"[TELEGRAM] Bot started. Chat ID: {self._chat_id}")

    async def stop(self) -> None:
        """Gracefully stop the bot."""
        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
        logger.info("[TELEGRAM] Bot stopped.")

    async def send_trade_alert(self, intent: OrderIntent) -> None:
        """
        Send a trade alert with Approve/Reject inline buttons.

        Registers the intent in pending dict. Auto-rejects after timeout.
        """
        callback_approve = f"approve_{intent.order_id}"
        callback_reject = f"reject_{intent.order_id}"

        self._pending[callback_approve] = intent
        self._pending[callback_reject] = intent

        text, keyboard = format_order_alert(intent, callback_approve, callback_reject)

        bot = Bot(token=self._bot_token)
        await bot.send_message(
            chat_id=self._chat_id,
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )
        logger.info(f"[TELEGRAM] Alert sent for order {intent.order_id}")

        # Schedule auto-rejection after timeout
        asyncio.create_task(self._auto_reject(intent, self._timeout))

    async def _auto_reject(self, intent: OrderIntent, timeout_s: int) -> None:
        """Auto-reject an order if the operator doesn't respond within timeout."""
        await asyncio.sleep(timeout_s)
        key = f"approve_{intent.order_id}"
        if key in self._pending:
            self._pending.pop(f"approve_{intent.order_id}", None)
            self._pending.pop(f"reject_{intent.order_id}", None)
            logger.warning(f"[TELEGRAM] Order {intent.order_id} auto-rejected (timeout={timeout_s}s).")
            if self._on_rejected:
                await self._on_rejected(intent, "TIMEOUT")  # type: ignore[misc]

    async def _cmd_status(self, update: object, context: object) -> None:
        """Handle /status command — send current engine status."""
        bot = Bot(token=self._bot_token)
        await bot.send_message(
            chat_id=self._chat_id,
            text="📊 <b>PropSentry Engine</b>\nStatus: <code>RUNNING</code>\nPending alerts: "
                 f"<code>{len(self._pending) // 2}</code>",
            parse_mode="HTML",
        )
