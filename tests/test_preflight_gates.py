"""
tests/test_preflight_gates.py
==============================
Validates all 8 pre-flight verification gates.
Each gate must correctly PASS valid input and REJECT invalid conditions.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
import pytz

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
from src.core.schemas.context import MarketContextState, MarketRegime
from src.core.schemas.signals import CompositeSignal, SignalDirection, SignalType
from src.engine.preflight_gates import (
    EngineState,
    gate_1_kill_switch,
    gate_2_weekend_protection,
    gate_3_session_window,
    gate_4_news_blackout,
    gate_5_max_positions,
    gate_6_max_daily_trades,
    gate_7_risk_reward,
    gate_8_llm_context,
    run_all_gates,
)

_UTC = pytz.utc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_state(
    kill_switch_active: bool = False,
    open_positions_count: int = 0,
    daily_trades_count: int = 0,
    max_open_positions: int = 1,
    max_daily_trades: int = 3,
    news_blackout_active: bool = False,
    reference_time: datetime | None = None,
) -> EngineState:
    # Default reference: Tuesday 09:30 UTC (inside London session, not weekend)
    if reference_time is None:
        reference_time = datetime(2024, 1, 16, 9, 30, tzinfo=_UTC)  # Tuesday
    return EngineState(
        kill_switch_active=kill_switch_active,
        open_positions_count=open_positions_count,
        daily_trades_count=daily_trades_count,
        max_open_positions=max_open_positions,
        max_daily_trades=max_daily_trades,
        news_blackout_active=news_blackout_active,
        reference_time=reference_time,
    )


def make_signal(rr: float = 2.5) -> CompositeSignal:
    now = datetime(2024, 1, 16, 9, 30, tzinfo=_UTC)
    # Build signal so indicative_risk_reward ≈ rr
    entry = 1.10000
    sl = entry - 0.0020          # 20 pip SL
    tp = entry + 0.0020 * rr     # TP for desired RR
    return CompositeSignal(
        created_at=now,
        symbol="EURUSD",
        timeframe="M15",
        direction=SignalDirection.LONG,
        signal_type=SignalType.COMBINED,
        entry_zone_high=entry,
        entry_zone_low=entry,
        stop_loss_level=sl,
        take_profit_level=tp,
        atr_value=0.0015,
    )


def make_context(trading_allowed: bool = True, expired: bool = False) -> MarketContextState:
    now = datetime.now(tz=_UTC)
    expires = now - timedelta(minutes=1) if expired else now + timedelta(minutes=14)
    return MarketContextState(
        trading_allowed=trading_allowed,
        generated_at=now,
        expires_at=expires,
        llm_model="test",
        regime=MarketRegime.TRENDING_BULLISH,
        regime_confidence=0.75,
    )


# ---------------------------------------------------------------------------
# Gate 1 — Kill-Switch
# ---------------------------------------------------------------------------

class TestGate1KillSwitch:
    def test_passes_when_inactive(self):
        gate_1_kill_switch(make_state(kill_switch_active=False))

    def test_raises_when_active(self):
        with pytest.raises(KillSwitchActiveGateError):
            gate_1_kill_switch(make_state(kill_switch_active=True))


# ---------------------------------------------------------------------------
# Gate 2 — Weekend Protection
# ---------------------------------------------------------------------------

class TestGate2WeekendProtection:
    def test_passes_on_weekday(self):
        tuesday = datetime(2024, 1, 16, 10, 0, tzinfo=_UTC)
        gate_2_weekend_protection(make_state(reference_time=tuesday))

    def test_raises_on_saturday(self):
        saturday = datetime(2024, 1, 20, 10, 0, tzinfo=_UTC)
        with pytest.raises(WeekendProtectionGateError):
            gate_2_weekend_protection(make_state(reference_time=saturday))

    def test_raises_on_friday_after_20_utc(self):
        friday_late = datetime(2024, 1, 19, 20, 30, tzinfo=_UTC)
        with pytest.raises(WeekendProtectionGateError):
            gate_2_weekend_protection(make_state(reference_time=friday_late))

    def test_passes_on_friday_before_20_utc(self):
        friday_early = datetime(2024, 1, 19, 19, 59, tzinfo=_UTC)
        gate_2_weekend_protection(make_state(reference_time=friday_early))


# ---------------------------------------------------------------------------
# Gate 3 — Session Window
# ---------------------------------------------------------------------------

class TestGate3SessionWindow:
    def test_passes_in_london_session(self):
        in_london = datetime(2024, 1, 16, 9, 0, tzinfo=_UTC)
        gate_3_session_window(make_state(reference_time=in_london))

    def test_passes_in_ny_session(self):
        in_ny = datetime(2024, 1, 16, 14, 0, tzinfo=_UTC)
        gate_3_session_window(make_state(reference_time=in_ny))

    def test_raises_between_sessions(self):
        between = datetime(2024, 1, 16, 12, 0, tzinfo=_UTC)
        with pytest.raises(SessionWindowGateError):
            gate_3_session_window(make_state(reference_time=between))

    def test_raises_outside_all_sessions(self):
        asian = datetime(2024, 1, 16, 4, 0, tzinfo=_UTC)
        with pytest.raises(SessionWindowGateError):
            gate_3_session_window(make_state(reference_time=asian))


# ---------------------------------------------------------------------------
# Gate 4 — News Blackout
# ---------------------------------------------------------------------------

class TestGate4NewsBlackout:
    def test_passes_when_no_news(self):
        gate_4_news_blackout(make_state(news_blackout_active=False))

    def test_raises_when_news_active(self):
        with pytest.raises(NewsBlackoutGateError):
            gate_4_news_blackout(make_state(news_blackout_active=True))


# ---------------------------------------------------------------------------
# Gate 5 — Max Positions
# ---------------------------------------------------------------------------

class TestGate5MaxPositions:
    def test_passes_when_below_max(self):
        gate_5_max_positions(make_state(open_positions_count=0, max_open_positions=1))

    def test_raises_when_at_max(self):
        with pytest.raises(MaxPositionsGateError):
            gate_5_max_positions(make_state(open_positions_count=1, max_open_positions=1))


# ---------------------------------------------------------------------------
# Gate 6 — Max Daily Trades
# ---------------------------------------------------------------------------

class TestGate6MaxDailyTrades:
    def test_passes_when_below_limit(self):
        gate_6_max_daily_trades(make_state(daily_trades_count=2, max_daily_trades=3))

    def test_raises_when_at_limit(self):
        with pytest.raises(MaxDailyTradesGateError):
            gate_6_max_daily_trades(make_state(daily_trades_count=3, max_daily_trades=3))


# ---------------------------------------------------------------------------
# Gate 7 — Risk/Reward
# ---------------------------------------------------------------------------

class TestGate7RiskReward:
    def test_passes_with_sufficient_rr(self):
        gate_7_risk_reward(make_signal(rr=2.5), min_rr=2.0)

    def test_raises_with_insufficient_rr(self):
        with pytest.raises(RiskRewardGateError):
            gate_7_risk_reward(make_signal(rr=1.5), min_rr=2.0)

    def test_raises_exactly_at_minimum(self):
        # Should PASS at exactly 2.0
        gate_7_risk_reward(make_signal(rr=2.0), min_rr=2.0)


# ---------------------------------------------------------------------------
# Gate 8 — LLM Context
# ---------------------------------------------------------------------------

class TestGate8LLMContext:
    def test_passes_with_valid_context(self):
        gate_8_llm_context(make_context(trading_allowed=True))

    def test_raises_when_context_is_none(self):
        with pytest.raises(LLMContextStaleGateError):
            gate_8_llm_context(None)

    def test_raises_when_context_expired(self):
        with pytest.raises(LLMContextStaleGateError):
            gate_8_llm_context(make_context(trading_allowed=True, expired=True))

    def test_raises_when_trading_not_allowed(self):
        with pytest.raises(LLMContextStaleGateError):
            gate_8_llm_context(make_context(trading_allowed=False))


# ---------------------------------------------------------------------------
# run_all_gates integration
# ---------------------------------------------------------------------------

class TestRunAllGates:
    def test_all_pass_with_valid_inputs(self, allow_context):
        run_all_gates(
            signal=make_signal(rr=2.5),
            context=allow_context,
            state=make_state(),
            min_rr=2.0,
        )

    def test_stops_at_first_failure(self, allow_context):
        # Kill-switch active should block before reaching other gates
        with pytest.raises(KillSwitchActiveGateError):
            run_all_gates(
                signal=make_signal(rr=2.5),
                context=allow_context,
                state=make_state(kill_switch_active=True),
            )
