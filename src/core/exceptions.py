"""
src/core/exceptions.py
=======================
Domain-specific exception hierarchy for the trading engine.

All exceptions here represent either:
  - Invariant breaches (hard failures that must halt execution)
  - Pre-flight gate failures (soft rejections blocking a single trade attempt)
  - Infrastructure errors (broker connectivity, time sync issues)

Raising any InvariantBreachError must trigger the kill-switch pipeline.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class TradingEngineError(Exception):
    """Root exception for all engine-level errors."""


# ---------------------------------------------------------------------------
# Invariant Breaches — must trigger kill-switch
# ---------------------------------------------------------------------------

class InvariantBreachError(TradingEngineError):
    """
    Raised when a non-negotiable system invariant is violated.
    Any handler catching this MUST escalate to the kill-switch.
    """


class DailyLossLimitBreached(InvariantBreachError):
    """Floating equity has crossed the daily kill-switch threshold (-4.20%)."""


class OverallLossLimitBreached(InvariantBreachError):
    """Floating equity has crossed the max overall drawdown floor (-10%)."""


class SizingCapBreached(InvariantBreachError):
    """Computed lot size would risk more than 0.50% of initial balance."""


class ParityViolationError(InvariantBreachError):
    """Backtest and live runner produced different results for identical input."""


class ArchitectureIsolationError(InvariantBreachError):
    """A module outside mt5_client.py attempted to import MetaTrader5."""


# ---------------------------------------------------------------------------
# Pre-flight Gate Failures — reject single trade, log and continue
# ---------------------------------------------------------------------------

class PreflightGateError(TradingEngineError):
    """
    Raised by any of the 8 pre-flight gates.
    Does NOT halt the engine — only blocks the current trade attempt.
    """

    def __init__(self, gate_name: str, reason: str) -> None:
        self.gate_name = gate_name
        self.reason = reason
        super().__init__(f"[{gate_name}] {reason}")


class SessionWindowGateError(PreflightGateError):
    """Trade attempted outside the allowed London/NY session windows."""

    def __init__(self, reason: str) -> None:
        super().__init__("SESSION_WINDOW", reason)


class NewsBlackoutGateError(PreflightGateError):
    """Trade attempted within the news blackout window (±30 min High-Impact)."""

    def __init__(self, reason: str) -> None:
        super().__init__("NEWS_BLACKOUT", reason)


class WeekendProtectionGateError(PreflightGateError):
    """Trade attempted after Friday flatten time or on weekend."""

    def __init__(self, reason: str) -> None:
        super().__init__("WEEKEND_PROTECTION", reason)


class MaxPositionsGateError(PreflightGateError):
    """Maximum concurrent open positions already reached."""

    def __init__(self, reason: str) -> None:
        super().__init__("MAX_POSITIONS", reason)


class MaxDailyTradesGateError(PreflightGateError):
    """Maximum daily trade count already reached."""

    def __init__(self, reason: str) -> None:
        super().__init__("MAX_DAILY_TRADES", reason)


class KillSwitchActiveGateError(PreflightGateError):
    """Kill-switch is currently active — no new entries allowed."""

    def __init__(self, reason: str) -> None:
        super().__init__("KILL_SWITCH_ACTIVE", reason)


class LLMContextStaleGateError(PreflightGateError):
    """Redis LLM context is stale, missing, or has trading_allowed=false."""

    def __init__(self, reason: str) -> None:
        super().__init__("LLM_CONTEXT_STALE", reason)


class RiskRewardGateError(PreflightGateError):
    """Signal does not meet minimum 1:2 risk/reward ratio."""

    def __init__(self, reason: str) -> None:
        super().__init__("RISK_REWARD", reason)


# ---------------------------------------------------------------------------
# Infrastructure / Broker Errors
# ---------------------------------------------------------------------------

class BrokerDriftError(TradingEngineError):
    """
    MT5 server time drifts beyond acceptable tolerance vs broker clock.
    Triggers a reconciliation cycle.
    """


class BrokerConnectionError(TradingEngineError):
    """Cannot establish or maintain connection to the MT5 terminal."""


class OrderDispatchError(TradingEngineError):
    """Order was rejected or returned an unexpected result code from MT5."""

    def __init__(self, retcode: int, comment: str) -> None:
        self.retcode = retcode
        self.comment = comment
        super().__init__(f"MT5 order failed — retcode={retcode}, comment={comment!r}")


class ReconciliationError(TradingEngineError):
    """Local state is inconsistent with MT5 account state (ghost position detected)."""
