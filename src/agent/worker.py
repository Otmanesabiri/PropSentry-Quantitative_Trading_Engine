"""
src/agent/worker.py
===================
Asynchronous background worker that periodically refreshes the
MarketContextState in Redis — INVARIANT 7.

Runs off the critical path. Writes context every N minutes with a 15-min TTL.
If this worker is down, the Redis key expires and gate_8 blocks all entries.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime

import pytz
import redis.asyncio as aioredis
from loguru import logger

from src.agent.llm_auditor import LLMAuditor
from src.agent.news_fetcher import NewsFetcher
from src.agent.news_gate import NewsGate
from src.agent.regime_classifier import classify_regime
from src.core.schemas.candles import Candle
from src.core.schemas.context import MarketContextState

_UTC = pytz.utc

_REFRESH_INTERVAL_S = 600  # Refresh every 10 min (TTL is 15 min — 5 min safety margin)
_CONTEXT_TTL_S = 900        # 15 minutes


class ContextWorker:
    """
    Async background task that:
      1. Classifies market regime from candles.
      2. Checks news blackout.
      3. Calls LLM auditor for qualitative assessment.
      4. Writes MarketContextState to Redis with 15-min TTL.

    Parameters
    ----------
    redis_client    : Async Redis client.
    redis_key       : Redis key for the context (default: "market_context_state").
    auditor         : LLMAuditor instance.
    news_gate       : NewsGate instance.
    candle_provider : Async callable returning recent candles.
    refresh_interval: Seconds between refreshes (default 600).
    """

    def __init__(
        self,
        redis_client: aioredis.Redis,
        auditor: LLMAuditor,
        news_gate: NewsGate,
        candle_provider: object,  # Callable[[], Awaitable[list[Candle]]]
        redis_key: str = "market_context_state",
        refresh_interval: int = _REFRESH_INTERVAL_S,
    ) -> None:
        self._redis = redis_client
        self._auditor = auditor
        self._news_gate = news_gate
        self._candle_provider = candle_provider
        self._redis_key = redis_key
        self._refresh_interval = refresh_interval
        self._running = False

    async def run(self) -> None:
        """Main loop — runs until stopped."""
        self._running = True
        logger.info("[CONTEXT WORKER] Started. Refreshing every "
                    f"{self._refresh_interval}s with TTL={_CONTEXT_TTL_S}s.")

        while self._running:
            try:
                await self._refresh_context()
            except Exception as exc:
                logger.error(f"[CONTEXT WORKER] Refresh error: {exc}")
            await asyncio.sleep(self._refresh_interval)

    async def stop(self) -> None:
        """Gracefully stop the worker loop."""
        self._running = False
        logger.info("[CONTEXT WORKER] Stopped.")

    async def _refresh_context(self) -> None:
        """Perform a single context refresh cycle."""
        candles: list[Candle] = await self._candle_provider()  # type: ignore[misc]

        # Regime classification
        regime, confidence = classify_regime(candles)

        # News check
        news_active = await self._news_gate.is_blackout_active()
        next_event = await self._news_gate.get_next_event()
        news_summary = (
            f"{next_event.title} ({next_event.currency}) @ {next_event.scheduled_at.strftime('%H:%M UTC')}"
            if next_event else ""
        )

        market_summary = (
            f"Regime={regime.value} confidence={confidence:.2f}, "
            f"bars_analyzed={len(candles)}, news_blackout={news_active}"
        )

        # LLM analysis
        context: MarketContextState = await self._auditor.analyze(
            market_summary=market_summary,
            regime=regime,
            regime_confidence=confidence,
            news_nearby=news_active,
            news_summary=news_summary,
        )

        # Write to Redis with TTL
        context_json = context.model_dump_json()
        await self._redis.setex(self._redis_key, _CONTEXT_TTL_S, context_json)

        logger.info(
            f"[CONTEXT WORKER] Context written to Redis | "
            f"trading_allowed={context.trading_allowed} | "
            f"regime={context.regime.value} | "
            f"expires_at={context.expires_at.isoformat()}"
        )


async def read_context_from_redis(
    redis_client: aioredis.Redis,
    redis_key: str = "market_context_state",
) -> MarketContextState | None:
    """
    Read and deserialize the current MarketContextState from Redis.

    Returns None if the key is missing or cannot be parsed.
    """
    try:
        raw = await redis_client.get(redis_key)
        if raw is None:
            return None
        return MarketContextState.model_validate_json(raw)
    except Exception as exc:
        logger.error(f"[CONTEXT WORKER] Failed to read context from Redis: {exc}")
        return None
