"""
src/engine/preflight_gates.py
=============================
8 Mandatory pre-flight verification gates.

Every gate must PASS before an OrderIntent can be created and dispatched.
Each gate raises a specific PreflightGateError subclass on failure so the
caller can log the rejection reason with full context.

Gate order (mirrors enforcement priority):
  1. KILL_SWITCH_ACTIVE     — Hard stop: kill-switch is engaged
  2. WEEKEND_PROTECTION     — Weekend / Friday 20:00 UTC flatten
  3. SESSION_WINDOW         — Outside London/NY session
  4. NEWS_BLACKOUT          — Within ±30 min High-Impact news
  5. MAX_POSITIONS          — Already at max concurrent positions
  6. MAX_DAILY_TRADES       — Daily trade count exhausted
  7. RISK_REWARD            — Signal RR < 2.0
  8. LLM_CONTEXT_STALE      — Redis context missing/stale/trading_allowed=False

Usage
-----
    from src.engine.preflight_gates import run_all_gates
    run_all_gates(signal=signal, context=ctx, state=state)
    # Raises PreflightGateError on first failure, returns None on success.
"""

from __future__ import annotations

from datetime import datetime

from src.core.exceptions import (
    KillSwitchActiveGateError,
    LLMContextStaleGateError,
    MaxDailyTradesGateError,
    MaxPositionsGateError,
    NewsBlackoutGateError,
    RiskRewardGateError,
    SessionWindowGateError,
    WeekendProtectionGateError,
)
from src.core.schemas.context import MarketContextState
from src.core.schemas.signals import CompositeSignal
from src.core.time_guards import assert_trading_session_open, is_weekend_protection_active


# ---------------------------------------------------------------------------
# System state snapshot passed to all gates
# ---------------------------------------------------------------------------

class EngineState:
    """
    Snapshot of the engine's current operational state consumed by gates.
    Populated by the live runner before pre-flight checks.
    """

    def __init__(
        self,
        kill_switch_active: bool,
        open_positions_count: int,
        daily_trades_count: int,
        max_open_positions: int,
        max_daily_trades: int,
        news_blackout_active: bool,
        reference_time: datetime | None = None,
    ) -> None:
        self.kill_switch_active = kill_switch_active
        self.open_positions_count = open_positions_count
        self.daily_trades_count = daily_trades_count
        self.max_open_positions = max_open_positions
        self.max_daily_trades = max_daily_trades
        self.news_blackout_active = news_blackout_active
        self.reference_time = reference_time  # Injected in backtest; None uses wall-clock


# ---------------------------------------------------------------------------
# Individual gate functions
# ---------------------------------------------------------------------------

def gate_1_kill_switch(state: EngineState) -> None:
    """Gate 1: Reject if the hard kill-switch is currently active."""
    if state.kill_switch_active:
        raise KillSwitchActiveGateError(
            "Kill-switch is active. All new entries blocked until next daily reset."
        )


def gate_2_weekend_protection(state: EngineState) -> None:
    """Gate 2: Reject if weekend protection is active."""
    if is_weekend_protection_active(state.reference_time):
        raise WeekendProtectionGateError(
            "Weekend protection active — no new positions allowed."
        )


def gate_3_session_window(state: EngineState) -> None:
    """Gate 3: Reject if outside allowed London/NY trading session."""
    assert_trading_session_open(state.reference_time)


def gate_4_news_blackout(state: EngineState) -> None:
    """Gate 4: Reject if within ±30 min of a High-Impact news event."""
    if state.news_blackout_active:
        raise NewsBlackoutGateError(
            "High-Impact news blackout window active (±30 min). Trade rejected."
        )


def gate_5_max_positions(state: EngineState) -> None:
    """Gate 5: Reject if maximum concurrent open positions reached."""
    if state.open_positions_count >= state.max_open_positions:
        raise MaxPositionsGateError(
            f"Max open positions reached: {state.open_positions_count}/{state.max_open_positions}."
        )


def gate_6_max_daily_trades(state: EngineState) -> None:
    """Gate 6: Reject if daily trade count is exhausted."""
    if state.daily_trades_count >= state.max_daily_trades:
        raise MaxDailyTradesGateError(
            f"Daily trade limit reached: {state.daily_trades_count}/{state.max_daily_trades}."
        )


def gate_7_risk_reward(signal: CompositeSignal, min_rr: float = 2.0) -> None:
    """Gate 7: Reject if signal's indicative R:R is below minimum threshold."""
    rr = signal.indicative_risk_reward
    if rr < min_rr:
        raise RiskRewardGateError(
            f"Signal R:R {rr:.2f} is below minimum {min_rr:.1f}. Trade rejected."
        )


def gate_8_llm_context(context: MarketContextState | None) -> None:
    """
    Gate 8: Reject if the LLM context is absent, stale, or explicitly blocks trading.

    Raises LLMContextStaleGateError in all three cases so the caller
    logs the specific reason (missing vs expired vs forbidden).
    """
    if context is None:
        raise LLMContextStaleGateError(
            "No MarketContextState found in Redis. LLM worker may not have run yet."
        )
    if context.is_expired:
        raise LLMContextStaleGateError(
            f"MarketContextState expired at {context.expires_at.isoformat()} UTC. "
            "Waiting for LLM worker refresh."
        )
    if not context.trading_allowed:
        raise LLMContextStaleGateError(
            f"LLM context explicitly set trading_allowed=False. Reason: {context.reasoning}"
        )


# ---------------------------------------------------------------------------
# Aggregate entry point
# ---------------------------------------------------------------------------

def run_all_gates(
    signal: CompositeSignal,
    context: MarketContextState | None,
    state: EngineState,
    min_rr: float = 2.0,
) -> None:
    """
    Run all 8 pre-flight gates in priority order.

    Parameters
    ----------
    signal  : The candidate CompositeSignal to evaluate.
    context : Current MarketContextState from Redis (None if missing).
    state   : Current EngineState snapshot.
    min_rr  : Minimum acceptable R:R ratio (default 2.0).

    Raises
    ------
    PreflightGateError
        On the first gate that fails. Execution stops at first failure.

    Returns
    -------
    None
        All 8 gates passed — the signal is cleared for order intent creation.
    """
    gate_1_kill_switch(state)
    gate_2_weekend_protection(state)
    gate_3_session_window(state)
    gate_4_news_blackout(state)
    gate_5_max_positions(state)
    gate_6_max_daily_trades(state)
    gate_7_risk_reward(signal, min_rr)
    gate_8_llm_context(context)
