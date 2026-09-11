"""
src/core/schemas/orders.py
==========================
Order intent schema with idempotent UUID tracking.

INVARIANT 1: This schema represents a VALIDATED INTENT, not a raw signal.
All numeric values (entry, sl, tp, lot_size) are computed deterministically
by the risk engine BEFORE this object is created. The LLM never touches these.

The UUID `order_id` ensures idempotent dispatch — retrying with the same
order_id will not double-fill on the broker side (checked by the dispatcher).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"


class OrderStatus(str, Enum):
    PENDING = "PENDING"       # Created, not yet dispatched
    DISPATCHED = "DISPATCHED" # Sent to MT5
    FILLED = "FILLED"         # Confirmed filled
    REJECTED = "REJECTED"     # Broker rejected
    CANCELLED = "CANCELLED"   # Cancelled pre-dispatch (e.g. kill-switch)
    EXPIRED = "EXPIRED"       # Telegram timeout — operator did not approve


class OrderIntent(BaseModel):
    """
    A fully validated, deterministically computed order ready for dispatch.

    Created only AFTER all 8 pre-flight gates pass and operator approves
    via Telegram. Immutable once created.
    """

    model_config = {"frozen": True}

    # Identity
    order_id: UUID = Field(default_factory=uuid4, description="Idempotent order identifier.")
    created_at: datetime = Field(..., description="UTC timestamp of intent creation.")

    # Market parameters
    symbol: str
    side: OrderSide
    order_type: OrderType = OrderType.MARKET

    # Risk-computed execution levels (INVARIANT 1)
    entry_price: float = Field(..., gt=0, description="Deterministic entry price level.")
    stop_loss: float = Field(..., gt=0, description="Stop loss level (rule-based).")
    take_profit: float = Field(..., gt=0, description="Take profit level (rule-based, min 2R).")
    lot_size: float = Field(..., gt=0, description="Position size computed by position_sizer.")

    # Risk metadata (for audit and Telegram display)
    risk_usd: float = Field(..., gt=0, le=25.0, description="Max theoretical risk in USD (≤25 USD).")
    risk_pct: float = Field(..., gt=0, le=0.005, description="Risk as fraction of balance (≤0.5%).")
    risk_reward_ratio: float = Field(..., ge=2.0, description="R:R ratio (≥2.0 enforced).")

    # Tracking
    signal_id: UUID = Field(..., description="Source CompositeSignal that generated this intent.")
    magic_number: int = Field(..., description="MT5 magic number for order identification.")
    status: OrderStatus = Field(default=OrderStatus.PENDING)

    # Optional operator metadata
    operator_approved_at: datetime | None = None
    broker_ticket: int | None = None  # MT5 ticket ID once filled

    @model_validator(mode="after")
    def validate_sl_tp_sides(self) -> "OrderIntent":
        """Ensure SL and TP are on correct sides of entry."""
        if self.side == OrderSide.BUY:
            if self.stop_loss >= self.entry_price:
                raise ValueError(
                    f"BUY order SL ({self.stop_loss}) must be below entry ({self.entry_price})"
                )
            if self.take_profit <= self.entry_price:
                raise ValueError(
                    f"BUY order TP ({self.take_profit}) must be above entry ({self.entry_price})"
                )
        else:
            if self.stop_loss <= self.entry_price:
                raise ValueError(
                    f"SELL order SL ({self.stop_loss}) must be above entry ({self.entry_price})"
                )
            if self.take_profit >= self.entry_price:
                raise ValueError(
                    f"SELL order TP ({self.take_profit}) must be below entry ({self.entry_price})"
                )
        return self
