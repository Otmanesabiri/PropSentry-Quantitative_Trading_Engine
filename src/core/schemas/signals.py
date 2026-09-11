"""
src/core/schemas/signals.py
===========================
Typed signal contracts for FVG and Liquidity Sweep detections.

INVARIANT 1: Signals carry price LEVELS (informational), never execution
instructions. The broker adapter computes final entry/sl/tp independently.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class SignalDirection(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class SignalType(str, Enum):
    FVG = "FVG"                          # Fair Value Gap
    LIQUIDITY_SWEEP = "LIQUIDITY_SWEEP"  # Stop hunt / sweep of liquidity
    COMBINED = "COMBINED"                # FVG confirmed by a sweep


class FVGSignal(BaseModel):
    """
    A Fair Value Gap (imbalance) detected on the price chart.

    A bullish FVG exists when candle[i-1].high < candle[i+1].low.
    A bearish FVG exists when candle[i-1].low > candle[i+1].high.

    Fields
    ------
    gap_high / gap_low : The price boundaries of the imbalance zone.
    is_filled          : True once price has traded back through the zone.
    """

    model_config = {"frozen": True}

    signal_id: UUID = Field(default_factory=uuid4)
    detected_at: datetime
    symbol: str
    timeframe: str
    direction: SignalDirection

    # Imbalance price zone
    gap_high: float = Field(..., gt=0)
    gap_low: float = Field(..., gt=0)

    # Anchor candle context
    anchor_high: float = Field(..., gt=0)
    anchor_low: float = Field(..., gt=0)

    is_filled: bool = False

    @property
    def gap_midpoint(self) -> float:
        return (self.gap_high + self.gap_low) / 2


class LiquiditySweepSignal(BaseModel):
    """
    A detected liquidity sweep (stop hunt) above a prior high or below a prior low.
    """

    model_config = {"frozen": True}

    signal_id: UUID = Field(default_factory=uuid4)
    detected_at: datetime
    symbol: str
    timeframe: str
    direction: SignalDirection  # Direction of expected move AFTER the sweep

    swept_level: float = Field(..., gt=0, description="Price level that was swept.")
    sweep_candle_high: float = Field(..., gt=0)
    sweep_candle_low: float = Field(..., gt=0)
    reversal_confirmed: bool = False


class CompositeSignal(BaseModel):
    """
    A high-confidence signal combining a liquidity sweep with an FVG entry.
    This is the primary signal type submitted to the pre-flight pipeline.

    INVARIANT 1 enforcement: entry_zone_* are reference levels only.
    The position sizer and broker adapter compute executable values.
    """

    model_config = {"frozen": True}

    signal_id: UUID = Field(default_factory=uuid4)
    created_at: datetime
    symbol: str
    timeframe: str
    direction: SignalDirection
    signal_type: SignalType = SignalType.COMBINED

    # Component signals
    fvg: FVGSignal | None = None
    sweep: LiquiditySweepSignal | None = None

    # Indicative price levels (NOT execution targets)
    entry_zone_high: float = Field(..., gt=0)
    entry_zone_low: float = Field(..., gt=0)
    stop_loss_level: float = Field(..., gt=0)
    take_profit_level: float = Field(..., gt=0)

    # ATR context
    atr_value: float = Field(..., gt=0, description="ATR at signal time.")

    @property
    def indicative_risk_reward(self) -> float:
        """Indicative R:R — used for pre-flight screening only."""
        entry = (self.entry_zone_high + self.entry_zone_low) / 2
        if self.direction == SignalDirection.LONG:
            risk = entry - self.stop_loss_level
            reward = self.take_profit_level - entry
        else:
            risk = self.stop_loss_level - entry
            reward = entry - self.take_profit_level
        return reward / risk if risk > 0 else 0.0
