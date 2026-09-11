"""
src/core/schemas/candles.py
===========================
Validated OHLCV candle data contracts.

All market data consumed by the engine MUST be cast to these schemas
before processing. This ensures type safety and prevents NaN/None
propagation into mathematical functions.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class Candle(BaseModel):
    """A single validated OHLCV candle bar."""

    model_config = {"frozen": True}  # Immutable after construction

    timestamp: datetime = Field(..., description="Bar open time (timezone-aware UTC).")
    open: float = Field(..., gt=0, description="Open price.")
    high: float = Field(..., gt=0, description="High price.")
    low: float = Field(..., gt=0, description="Low price.")
    close: float = Field(..., gt=0, description="Close price.")
    volume: float = Field(..., ge=0, description="Tick volume.")
    timeframe: str = Field(..., description="MT5 timeframe string, e.g. 'M15'.")

    @field_validator("high")
    @classmethod
    def high_gte_open_close(cls, v: float, info: object) -> float:
        data = info.data  # type: ignore[attr-defined]
        for field in ("open", "low", "close"):
            if field in data and v < data[field]:
                raise ValueError(f"high ({v}) must be >= {field} ({data[field]})")
        return v

    @field_validator("low")
    @classmethod
    def low_lte_open_close(cls, v: float, info: object) -> float:
        data = info.data  # type: ignore[attr-defined]
        for field in ("open", "close"):
            if field in data and v > data[field]:
                raise ValueError(f"low ({v}) must be <= {field} ({data[field]})")
        return v

    @property
    def body_size(self) -> float:
        """Absolute candle body size (|close - open|)."""
        return abs(self.close - self.open)

    @property
    def full_range(self) -> float:
        """Full candle range (high - low)."""
        return self.high - self.low

    @property
    def is_bullish(self) -> bool:
        """True if close > open."""
        return self.close > self.open

    @property
    def is_bearish(self) -> bool:
        """True if close < open."""
        return self.close < self.open


class CandleSeries(BaseModel):
    """An ordered sequence of candles (oldest first)."""

    model_config = {"frozen": True}

    candles: list[Candle] = Field(..., min_length=1)
    symbol: str
    timeframe: str

    @field_validator("candles")
    @classmethod
    def candles_are_ordered(cls, v: list[Candle]) -> list[Candle]:
        for i in range(1, len(v)):
            if v[i].timestamp <= v[i - 1].timestamp:
                raise ValueError(
                    f"Candles must be strictly ordered by timestamp. "
                    f"Violation at index {i}: {v[i].timestamp} <= {v[i-1].timestamp}"
                )
        return v

    def __len__(self) -> int:
        return len(self.candles)

    def __getitem__(self, index: int) -> Candle:
        return self.candles[index]

    @property
    def latest(self) -> Candle:
        """Most recent (last) candle."""
        return self.candles[-1]
