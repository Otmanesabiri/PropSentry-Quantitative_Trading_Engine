"""
src/engine/indicators.py
========================
Pure mathematical indicator functions (no side effects, no I/O).

All functions are stateless and deterministic — identical inputs MUST
produce identical outputs in both backtest and live contexts (INVARIANT 8).

Functions
---------
    atr(candles, period)       → float   Average True Range
    ema(values, period)        → float   Exponential Moving Average (last value)
    session_high_low(candles)  → tuple   (high, low) for a candle window
    detect_fvg(candles)        → list[FVGSignal]
    detect_liquidity_sweep(candles, lookback) → list[LiquiditySweepSignal]
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence

import numpy as np

from src.core.schemas.candles import Candle
from src.core.schemas.signals import (
    FVGSignal,
    LiquiditySweepSignal,
    SignalDirection,
)


# ---------------------------------------------------------------------------
# ATR
# ---------------------------------------------------------------------------

def true_range(candle: Candle, prev_close: float) -> float:
    """Compute the True Range for a single candle given the prior close."""
    return max(
        candle.high - candle.low,
        abs(candle.high - prev_close),
        abs(candle.low - prev_close),
    )


def atr(candles: Sequence[Candle], period: int = 14) -> float:
    """
    Compute the Average True Range (Wilder's smoothing) for the last *period* bars.

    Parameters
    ----------
    candles : Sequence[Candle]
        Ordered sequence of candles (oldest first). Must have at least period+1 bars.
    period  : int
        ATR period (default 14).

    Returns
    -------
    float
        ATR value for the most recent bar.

    Raises
    ------
    ValueError
        If insufficient bars are provided.
    """
    if len(candles) < period + 1:
        raise ValueError(
            f"ATR({period}) requires at least {period + 1} candles, got {len(candles)}."
        )

    bars = list(candles[-(period + 1):])
    tr_values: list[float] = []
    for i in range(1, len(bars)):
        tr_values.append(true_range(bars[i], bars[i - 1].close))

    # Wilder's initial ATR = simple average of first `period` TR values
    atr_val = float(np.mean(tr_values[:period]))

    # Apply Wilder smoothing for any remaining bars (here only one by construction)
    for tr in tr_values[period:]:
        atr_val = (atr_val * (period - 1) + tr) / period

    return atr_val


# ---------------------------------------------------------------------------
# EMA
# ---------------------------------------------------------------------------

def ema(values: Sequence[float], period: int) -> float:
    """
    Compute the Exponential Moving Average and return the LAST value.

    Parameters
    ----------
    values : Sequence[float]
        Price series (oldest first). Must have at least *period* elements.
    period : int
        EMA period.
    """
    if len(values) < period:
        raise ValueError(f"EMA({period}) requires at least {period} values, got {len(values)}.")

    arr = np.array(values, dtype=np.float64)
    k = 2.0 / (period + 1)
    ema_val = float(np.mean(arr[:period]))  # Seed with SMA
    for price in arr[period:]:
        ema_val = price * k + ema_val * (1 - k)
    return ema_val


# ---------------------------------------------------------------------------
# Session High / Low
# ---------------------------------------------------------------------------

def session_high_low(candles: Sequence[Candle]) -> tuple[float, float]:
    """
    Return (highest_high, lowest_low) across all provided candles.

    Useful for identifying liquidity levels within a session window.
    """
    if not candles:
        raise ValueError("Cannot compute high/low on empty candle sequence.")
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    return max(highs), min(lows)


# ---------------------------------------------------------------------------
# Fair Value Gap (FVG) Detection
# ---------------------------------------------------------------------------

def detect_fvg(
    candles: Sequence[Candle],
    symbol: str,
    timeframe: str,
) -> list[FVGSignal]:
    """
    Detect all unmitigated Fair Value Gaps in the provided candle sequence.

    A **bullish FVG** forms when:
        candles[i-1].high < candles[i+1].low   (gap above bearish candle)

    A **bearish FVG** forms when:
        candles[i-1].low > candles[i+1].high   (gap below bullish candle)

    Parameters
    ----------
    candles   : Sequence of Candle (oldest first, minimum 3 bars).
    symbol    : Instrument symbol string.
    timeframe : Timeframe string (e.g. "M15").

    Returns
    -------
    list[FVGSignal]
        All detected FVG signals (oldest first), not yet mitigated.
    """
    bars = list(candles)
    if len(bars) < 3:
        return []

    signals: list[FVGSignal] = []

    for i in range(1, len(bars) - 1):
        prev, curr, nxt = bars[i - 1], bars[i], bars[i + 1]

        # Bullish FVG
        if prev.high < nxt.low:
            signals.append(
                FVGSignal(
                    detected_at=curr.timestamp,
                    symbol=symbol,
                    timeframe=timeframe,
                    direction=SignalDirection.LONG,
                    gap_high=nxt.low,
                    gap_low=prev.high,
                    anchor_high=curr.high,
                    anchor_low=curr.low,
                )
            )

        # Bearish FVG
        elif prev.low > nxt.high:
            signals.append(
                FVGSignal(
                    detected_at=curr.timestamp,
                    symbol=symbol,
                    timeframe=timeframe,
                    direction=SignalDirection.SHORT,
                    gap_high=prev.low,
                    gap_low=nxt.high,
                    anchor_high=curr.high,
                    anchor_low=curr.low,
                )
            )

    return signals


# ---------------------------------------------------------------------------
# Liquidity Sweep Detection
# ---------------------------------------------------------------------------

def detect_liquidity_sweep(
    candles: Sequence[Candle],
    symbol: str,
    timeframe: str,
    lookback: int = 20,
) -> list[LiquiditySweepSignal]:
    """
    Detect liquidity sweeps (stop hunts) in the provided candle sequence.

    A **bearish sweep** (bullish expected move after): the current candle's
    wick exceeds the highest high of the prior *lookback* candles then closes
    below that high (wick above, body below).

    A **bullish sweep** (bearish expected move after): the current candle's
    wick dips below the lowest low of the prior *lookback* candles then closes
    above that low.

    Parameters
    ----------
    candles  : Candle sequence (oldest first, minimum lookback+1 bars).
    symbol   : Instrument symbol.
    timeframe: Timeframe string.
    lookback : Number of prior bars to establish the liquidity level (default 20).

    Returns
    -------
    list[LiquiditySweepSignal]
    """
    bars = list(candles)
    if len(bars) < lookback + 1:
        return []

    signals: list[LiquiditySweepSignal] = []

    for i in range(lookback, len(bars)):
        window = bars[i - lookback: i]
        curr = bars[i]

        prior_high = max(c.high for c in window)
        prior_low = min(c.low for c in window)

        # High sweep: wick above prior high, close below it (bearish sweep → long expected)
        if curr.high > prior_high and curr.close < prior_high:
            signals.append(
                LiquiditySweepSignal(
                    detected_at=curr.timestamp,
                    symbol=symbol,
                    timeframe=timeframe,
                    direction=SignalDirection.SHORT,  # Expected reversal direction
                    swept_level=prior_high,
                    sweep_candle_high=curr.high,
                    sweep_candle_low=curr.low,
                    reversal_confirmed=curr.is_bearish,
                )
            )

        # Low sweep: wick below prior low, close above it (bullish sweep → short expected)
        elif curr.low < prior_low and curr.close > prior_low:
            signals.append(
                LiquiditySweepSignal(
                    detected_at=curr.timestamp,
                    symbol=symbol,
                    timeframe=timeframe,
                    direction=SignalDirection.LONG,  # Expected reversal direction
                    swept_level=prior_low,
                    sweep_candle_high=curr.high,
                    sweep_candle_low=curr.low,
                    reversal_confirmed=curr.is_bullish,
                )
            )

    return signals
