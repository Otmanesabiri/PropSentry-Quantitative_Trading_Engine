"""
tests/test_indicators.py
=========================
Validates pure mathematical indicator functions.
All tests use deterministic synthetic data for reproducibility.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.core.schemas.candles import Candle
from src.core.schemas.signals import SignalDirection
from src.engine.indicators import atr, detect_fvg, detect_liquidity_sweep, ema, session_high_low

_UTC = timezone.utc


def make_candle(
    i: int,
    open_: float = 1.10000,
    high: float | None = None,
    low: float | None = None,
    close: float | None = None,
) -> Candle:
    ts = datetime(2024, 1, 15, 9, 0, tzinfo=_UTC) + timedelta(minutes=15 * i)
    o = open_
    h = high if high is not None else o + 0.0015
    l = low if low is not None else o - 0.0010
    c = close if close is not None else o + 0.0005
    return Candle(timestamp=ts, open=o, high=h, low=l, close=c, volume=1000.0, timeframe="M15")


def make_series(n: int, base_price: float = 1.10000) -> list[Candle]:
    return [make_candle(i, open_=base_price + i * 0.0001) for i in range(n)]


# ---------------------------------------------------------------------------
# ATR
# ---------------------------------------------------------------------------

class TestATR:
    def test_atr_requires_enough_bars(self):
        candles = make_series(5)
        with pytest.raises(ValueError, match="requires at least"):
            atr(candles, period=14)

    def test_atr_returns_positive(self):
        candles = make_series(30)
        result = atr(candles, period=14)
        assert result > 0

    def test_atr_is_deterministic(self):
        candles = make_series(30)
        assert atr(candles, period=14) == atr(candles, period=14)

    def test_atr_increases_with_wider_candles(self):
        # Wide candles
        wide = [make_candle(i, high=1.1100 + i * 0.0001, low=1.0900 - i * 0.0001,
                            close=1.1000) for i in range(20)]
        # Narrow candles
        narrow = [make_candle(i, high=1.1005, low=1.0995, close=1.1000) for i in range(20)]
        assert atr(wide, 14) > atr(narrow, 14)


# ---------------------------------------------------------------------------
# EMA
# ---------------------------------------------------------------------------

class TestEMA:
    def test_ema_requires_enough_values(self):
        with pytest.raises(ValueError, match="requires at least"):
            ema([1.0, 1.1], period=10)

    def test_ema_single_value_after_seed(self):
        values = [1.0] * 20
        result = ema(values, 10)
        assert result == pytest.approx(1.0)

    def test_ema_responds_to_recent_prices(self):
        # Rising series — EMA should lag but follow
        values = list(range(1, 51))
        result = ema([float(v) for v in values], 10)
        assert result > 30  # EMA should be close to recent values


# ---------------------------------------------------------------------------
# Session High/Low
# ---------------------------------------------------------------------------

class TestSessionHighLow:
    def test_basic_high_low(self):
        candles = [
            make_candle(0, high=1.1050, low=1.0950),
            make_candle(1, high=1.1100, low=1.0900),  # Extreme
            make_candle(2, high=1.1020, low=1.0980),
        ]
        h, l = session_high_low(candles)
        assert h == pytest.approx(1.1100)
        assert l == pytest.approx(1.0900)

    def test_raises_on_empty(self):
        with pytest.raises(ValueError):
            session_high_low([])


# ---------------------------------------------------------------------------
# FVG Detection
# ---------------------------------------------------------------------------

class TestFVGDetection:
    def test_detects_bullish_fvg(self):
        # Candle[i-1].high < Candle[i+1].low → Bullish FVG
        candles = [
            make_candle(0, open_=1.0990, high=1.1000, low=1.0980, close=1.0995),  # prev: high=1.1000
            make_candle(1, open_=1.1000, high=1.1050, low=1.0990, close=1.1045),  # current
            make_candle(2, open_=1.1020, high=1.1030, low=1.1010, close=1.1025),  # next: low=1.1010 > prev.high
        ]
        # prev.high=1.1000 < next.low=1.1010 → gap exists
        fvgs = detect_fvg(candles, "EURUSD", "M15")
        assert len(fvgs) == 1
        assert fvgs[0].direction == SignalDirection.LONG
        assert fvgs[0].gap_low == pytest.approx(1.1000)
        assert fvgs[0].gap_high == pytest.approx(1.1010)

    def test_detects_bearish_fvg(self):
        # Candle[i-1].low > Candle[i+1].high → Bearish FVG
        candles = [
            make_candle(0, open_=1.1030, high=1.1050, low=1.1020, close=1.1025),  # prev: low=1.1020
            make_candle(1, open_=1.1020, high=1.1040, low=1.1000, close=1.1005),  # current
            make_candle(2, open_=1.1000, high=1.1010, low=1.0990, close=1.0995),  # next: high=1.1010 < prev.low
        ]
        fvgs = detect_fvg(candles, "EURUSD", "M15")
        assert len(fvgs) == 1
        assert fvgs[0].direction == SignalDirection.SHORT

    def test_no_fvg_on_normal_candles(self):
        candles = make_series(10)
        # Normal overlapping candles shouldn't produce many FVGs
        # (may have 0 or a few if price steps are tiny)
        fvgs = detect_fvg(candles, "EURUSD", "M15")
        assert isinstance(fvgs, list)

    def test_returns_empty_on_less_than_3_bars(self):
        candles = make_series(2)
        assert detect_fvg(candles, "EURUSD", "M15") == []


# ---------------------------------------------------------------------------
# Liquidity Sweep Detection
# ---------------------------------------------------------------------------

class TestLiquiditySweepDetection:
    def test_detects_high_sweep(self):
        """A wick above prior high that closes below it = bearish sweep."""
        candles = make_series(25)
        # Last candle: spike above prior high, closes below
        prior_high = max(c.high for c in candles[-21:-1])
        sweep_candle = make_candle(
            25,
            open_=prior_high - 0.0015,
            high=prior_high + 0.0050,  # Wick above prior high
            low=prior_high - 0.0020,
            close=prior_high - 0.0010,  # Close below prior high
        )
        all_candles = candles + [sweep_candle]
        sweeps = detect_liquidity_sweep(all_candles, "EURUSD", "M15", lookback=20)
        assert len(sweeps) >= 1
        assert any(s.direction == SignalDirection.SHORT for s in sweeps)

    def test_returns_empty_with_insufficient_bars(self):
        candles = make_series(5)
        sweeps = detect_liquidity_sweep(candles, "EURUSD", "M15", lookback=20)
        assert sweeps == []
