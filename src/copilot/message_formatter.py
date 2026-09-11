"""
src/copilot/message_formatter.py
=================================
Rich Telegram alert message formatter with risk metrics and AI context.
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from src.core.schemas.orders import OrderIntent, OrderSide


def format_order_alert(
    intent: OrderIntent,
    callback_approve: str,
    callback_reject: str,
) -> tuple[str, InlineKeyboardMarkup]:
    """
    Format a rich HTML Telegram message for a trade alert.

    Returns
    -------
    tuple[str, InlineKeyboardMarkup]
        Formatted HTML message text and inline keyboard.
    """
    direction_emoji = "🟢 LONG" if intent.side == OrderSide.BUY else "🔴 SHORT"
    rr_str = f"{intent.risk_reward_ratio:.1f}R"
    risk_pct_str = f"{intent.risk_pct * 100:.2f}%"

    text = (
        f"🚨 <b>TRADE SIGNAL — {direction_emoji}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 <b>Symbol:</b> <code>{intent.symbol}</code>\n"
        f"🎯 <b>Entry:</b>  <code>{intent.entry_price:.5f}</code>\n"
        f"🛡 <b>Stop Loss:</b> <code>{intent.stop_loss:.5f}</code>\n"
        f"✅ <b>Take Profit:</b> <code>{intent.take_profit:.5f}</code>\n"
        f"📦 <b>Lot Size:</b> <code>{intent.lot_size:.2f}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 <b>Risk:</b> <code>{intent.risk_usd:.2f} USD</code> "
        f"(<code>{risk_pct_str}</code>)\n"
        f"📊 <b>R:R Ratio:</b> <code>1:{rr_str}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔑 <b>Order ID:</b> <code>{str(intent.order_id)[:8]}...</code>\n"
        f"⏰ <b>Signal At:</b> <code>{intent.created_at.strftime('%H:%M:%S UTC')}</code>\n"
        f"\n<i>⏱ Auto-rejects in 2 minutes if no response.</i>"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Validate & Execute", callback_data=callback_approve),
            InlineKeyboardButton("❌ Reject", callback_data=callback_reject),
        ]
    ])

    return text, keyboard
