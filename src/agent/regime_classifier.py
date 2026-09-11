"""
src/agent/regime_classifier.py
==============================
Qualitative market regime classifier.

Classifies the current market state as:
  TRENDING_BULLISH | TRENDING_BEARISH | RANGING | VOLATILE | UNKNOWN

Uses EMA spread and ATR-to-range ratio as primary signals.
This output feeds into the LLM auditor context (INVARIANT 7).
"""

from __future__ import annotations

from typing import Sequence

from src.core.schemas.candles import Candle
from src.core.schemas.context import MarketRegime
from src.engine.indicators import atr, ema


def classify_regime(
    candles: Sequence[Candle],
    fast_period: int = 20,
    slow_period: int = 50,
    atr_period: int = 14,
    volatile_atr_multiplier: float = 2.0,
) -> tuple[MarketRegime, float]:
    """
    Classify the current market regime from a candle series.

    Parameters
    ----------
    candles                 : Ordered candles (oldest first). Needs ≥ slow_period + atr_period + 1.
    fast_period             : Fast EMA period.
    slow_period             : Slow EMA period.
    atr_period              : ATR period for volatility check.
    volatile_atr_multiplier : ATR multiplier threshold for volatile regime.

    Returns
    -------
    tuple[MarketRegime, float]
        Regime classification and confidence score [0.0, 1.0].
    """
    min_bars = slow_period + atr_period + 1
    if len(candles) < min_bars:
        return MarketRegime.UNKNOWN, 0.0

    closes = [c.close for c in candles]

    fast_ema = ema(closes, fast_period)
    slow_ema = ema(closes, slow_period)
    current_atr = atr(candles, atr_period)

    # Average candle range over last atr_period bars
    recent = list(candles[-atr_period:])
    avg_range = sum(c.full_range for c in recent) / len(recent)

    # Volatile regime: ATR significantly above average range
    if current_atr > avg_range * volatile_atr_multiplier:
        confidence = min(1.0, (current_atr / avg_range - volatile_atr_multiplier) / 2)
        return MarketRegime.VOLATILE, round(confidence, 3)

    # Trending vs ranging: EMA spread relative to ATR
    spread = abs(fast_ema - slow_ema)
    spread_to_atr = spread / current_atr if current_atr > 0 else 0.0

    if spread_to_atr > 0.5:  # Strong trend
        confidence = min(1.0, spread_to_atr / 2.0)
        regime = (
            MarketRegime.TRENDING_BULLISH if fast_ema > slow_ema
            else MarketRegime.TRENDING_BEARISH
        )
        return regime, round(confidence, 3)

    # Ranging
    confidence = max(0.0, 1.0 - spread_to_atr * 2)
    return MarketRegime.RANGING, round(confidence, 3)
