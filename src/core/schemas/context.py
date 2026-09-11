"""
src/core/schemas/context.py
===========================
MarketContextState — the JSON contract produced by the asynchronous LLM worker
and consumed by the pre-flight gate as a read-only execution permission.

INVARIANT 7: The LLM writes this object into Redis (TTL 15 min).
If the context is stale or trading_allowed=False, the gate BLOCKS all entries.

The LLM MUST NOT set prices or lot sizes — only qualitative regime context.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class MarketRegime(str, Enum):
    TRENDING_BULLISH = "TRENDING_BULLISH"
    TRENDING_BEARISH = "TRENDING_BEARISH"
    RANGING = "RANGING"
    VOLATILE = "VOLATILE"       # Abnormal volatility — high caution
    UNKNOWN = "UNKNOWN"         # Insufficient data for classification


class MarketContextState(BaseModel):
    """
    Asynchronous LLM market context written to Redis by the agent worker.

    This object is READ-ONLY by the execution pipeline.
    The LLM MUST NOT include prices, lot sizes, or entry instructions.
    """

    model_config = {"frozen": True}

    # Core permission gate
    trading_allowed: bool = Field(
        ...,
        description="Master gate: False blocks ALL new entries regardless of signals.",
    )

    # Context metadata
    generated_at: datetime = Field(..., description="UTC timestamp when LLM produced this.")
    expires_at: datetime = Field(..., description="UTC timestamp after which context is stale.")
    llm_model: str = Field(..., description="Model identifier used for this analysis.")

    # Qualitative assessment
    regime: MarketRegime = Field(default=MarketRegime.UNKNOWN)
    regime_confidence: float = Field(
        default=0.0, ge=0.0, le=1.0,
        description="Confidence score [0,1] for the regime classification.",
    )

    # News and macro context
    high_impact_news_nearby: bool = Field(
        default=False,
        description="True if a High-Impact event is within the blackout window.",
    )
    news_summary: str = Field(
        default="",
        description="Short textual summary of nearby news (max 200 chars, no prices).",
        max_length=200,
    )

    # Reasoning transparency
    reasoning: str = Field(
        default="",
        description="Brief explanation for the trading_allowed decision (no prices).",
        max_length=500,
    )

    @property
    def is_expired(self) -> bool:
        """True if the context has passed its TTL expiry."""
        from src.core.broker_clock import get_utc_now
        return get_utc_now() > self.expires_at

    @property
    def is_actionable(self) -> bool:
        """True only if context is fresh AND trading is explicitly permitted."""
        return self.trading_allowed and not self.is_expired
