"""
src/agent/llm_auditor.py
========================
LLM context synthesizer — INVARIANT 7.

Calls the LLM (via HTTP) to produce a MarketContextState JSON.
The LLM MUST NOT output prices or lot sizes — only qualitative context.

Features
--------
- 5-second HTTP timeout with fallback to deny-all context.
- Structured JSON output enforced via system prompt.
- Validates response against MarketContextState schema.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta

import httpx
import pytz
from loguru import logger

from src.core.schemas.context import MarketContextState, MarketRegime

_UTC = pytz.utc
_LLM_TIMEOUT_S = 5.0
_CONTEXT_TTL_MINUTES = 15


SYSTEM_PROMPT = """You are a qualitative market context analyst for a prop firm trading system.
Your role is to assess whether market conditions are suitable for trading.

CRITICAL CONSTRAINTS:
- You MUST NOT output any specific prices, entry levels, stop losses, or lot sizes.
- You MUST NOT give trading instructions or execution guidance.
- You MUST output ONLY a JSON object matching the specified schema.
- Output only qualitative regime assessment and trading permission.

Output JSON schema:
{
  "trading_allowed": bool,
  "regime": "TRENDING_BULLISH|TRENDING_BEARISH|RANGING|VOLATILE|UNKNOWN",
  "regime_confidence": float (0.0 to 1.0),
  "high_impact_news_nearby": bool,
  "news_summary": "max 200 chars, no prices",
  "reasoning": "max 500 chars explanation of trading_allowed decision, no prices"
}"""


class LLMAuditor:
    """
    Calls a language model endpoint to produce MarketContextState.

    Parameters
    ----------
    api_url   : LLM API endpoint URL.
    api_key   : Bearer token for authentication.
    model     : Model identifier string.
    timeout_s : Request timeout (default 5s per INVARIANT 7).
    """

    def __init__(
        self,
        api_url: str,
        api_key: str,
        model: str = "gemini-2.0-flash",
        timeout_s: float = _LLM_TIMEOUT_S,
    ) -> None:
        self._api_url = api_url
        self._api_key = api_key
        self._model = model
        self._timeout = timeout_s

    async def analyze(
        self,
        market_summary: str,
        regime: MarketRegime,
        regime_confidence: float,
        news_nearby: bool,
        news_summary: str,
    ) -> MarketContextState:
        """
        Request LLM analysis and return a validated MarketContextState.

        Falls back to a deny-all context on timeout or parse failure.
        """
        now_utc = datetime.now(tz=_UTC)
        expires_at = now_utc + timedelta(minutes=_CONTEXT_TTL_MINUTES)

        user_prompt = (
            f"Market summary: {market_summary}\n"
            f"Detected regime: {regime.value} (confidence: {regime_confidence:.2f})\n"
            f"High-impact news nearby: {news_nearby}\n"
            f"News context: {news_summary}\n\n"
            "Assess whether trading is currently appropriate. Respond with JSON only."
        )

        if not self._api_key or not self._api_key.strip():
            # Rule-based context fallback: allow trading unless high-impact news or extreme volatility
            allowed = not news_nearby and regime != MarketRegime.VOLATILE
            reason = (
                f"Rule-based mode (no LLM key): regime={regime.value}, news_nearby={news_nearby}."
            )
            return MarketContextState(
                trading_allowed=allowed,
                generated_at=now_utc,
                expires_at=expires_at,
                llm_model="rule-based-fallback",
                regime=regime,
                regime_confidence=regime_confidence,
                high_impact_news_nearby=news_nearby,
                news_summary=news_summary,
                reasoning=reason,
            )

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    self._api_url,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json={
                        "model": self._model,
                        "messages": [
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": user_prompt},
                        ],
                        "response_format": {"type": "json_object"},
                    },
                )
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
                parsed = json.loads(content)

        except asyncio.TimeoutError:
            logger.warning("[LLM AUDITOR] Timeout — applying deny-all fallback context.")
            return self._deny_all_context(now_utc, expires_at, "LLM timeout after 5s.")

        except Exception as exc:
            logger.error(f"[LLM AUDITOR] Error: {exc} — applying deny-all fallback.")
            return self._deny_all_context(now_utc, expires_at, f"LLM error: {str(exc)[:100]}")

        try:
            return MarketContextState(
                trading_allowed=bool(parsed.get("trading_allowed", False)),
                generated_at=now_utc,
                expires_at=expires_at,
                llm_model=self._model,
                regime=MarketRegime(parsed.get("regime", "UNKNOWN")),
                regime_confidence=float(parsed.get("regime_confidence", 0.0)),
                high_impact_news_nearby=bool(parsed.get("high_impact_news_nearby", False)),
                news_summary=str(parsed.get("news_summary", ""))[:200],
                reasoning=str(parsed.get("reasoning", ""))[:500],
            )
        except Exception as exc:
            logger.error(f"[LLM AUDITOR] Schema validation failed: {exc}")
            return self._deny_all_context(now_utc, expires_at, f"Invalid LLM response schema.")

    def _deny_all_context(
        self,
        now_utc: datetime,
        expires_at: datetime,
        reason: str,
    ) -> MarketContextState:
        """Return a deny-all fallback context when LLM is unavailable."""
        return MarketContextState(
            trading_allowed=False,
            generated_at=now_utc,
            expires_at=expires_at,
            llm_model=self._model,
            regime=MarketRegime.UNKNOWN,
            regime_confidence=0.0,
            high_impact_news_nearby=False,
            news_summary="",
            reasoning=f"[FALLBACK] {reason}",
        )
