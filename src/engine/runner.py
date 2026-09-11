"""
src/engine/runner.py
====================
Unified bar-by-bar execution loop — the single source of truth for
both live trading and historical backtesting (INVARIANT 8).

This module MUST contain the full signal → risk → intent pipeline.
Live mode feeds bars from the MT5 broker; replay mode feeds bars from
Parquet files. The math is IDENTICAL in both cases.

Architecture
------------
                ┌─────────────────┐
    candles ──► │  indicators.py  │ ──► ATR, FVGs, Sweeps
                └─────────────────┘
                        │
                        ▼
                ┌─────────────────┐
    context ──► │ preflight_gates │ ──► PASS / PreflightGateError
                └─────────────────┘
                        │
                        ▼
                ┌─────────────────┐
                │ position_sizer  │ ──► lot_size, risk_usd
                └─────────────────┘
                        │
                        ▼
                ┌─────────────────┐
                │  OrderIntent    │ ──► Telegram copilot / dispatcher
                └─────────────────┘
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Sequence

from loguru import logger

from src.core.exceptions import PreflightGateError
from src.core.schemas.candles import Candle
from src.core.schemas.context import MarketContextState
from src.core.schemas.orders import OrderIntent, OrderSide, OrderType
from src.core.schemas.signals import CompositeSignal, SignalDirection
from src.engine.indicators import atr, ema, detect_fvg, detect_liquidity_sweep
from src.engine.parity_gate import RunnerOutput
from src.engine.preflight_gates import EngineState, run_all_gates


# ---------------------------------------------------------------------------
# Runner configuration
# ---------------------------------------------------------------------------

@dataclass
class RunnerConfig:
    """Immutable configuration injected into the runner at startup."""
    symbol: str
    timeframe: str
    initial_balance: float          # 5000.00 USD
    risk_per_trade_pct: float = 0.005
    min_risk_reward: float = 2.0
    breakeven_trigger_r: float = 1.0
    max_open_positions: int = 1
    max_daily_trades: int = 3
    atr_period: int = 14
    fvg_lookback: int = 50
    sweep_lookback: int = 15
    max_sweep_age_bars: int = 8
    ema_trend_period: int = 50
    min_fvg_size_pips: float = 1.5
    magic_number: int = 202401


# ---------------------------------------------------------------------------
# Runner result per bar
# ---------------------------------------------------------------------------

@dataclass
class BarResult:
    """Output produced by processing a single bar."""
    bar_index: int
    timestamp: datetime
    atr_value: float
    fvg_signals: list = field(default_factory=list)
    sweep_signals: list = field(default_factory=list)
    composite_signal: CompositeSignal | None = None
    order_intent: OrderIntent | None = None
    gate_rejection: str | None = None   # Gate name if rejected

    def to_runner_output(self) -> RunnerOutput:
        """Convert to parity-comparable RunnerOutput."""
        intent = self.order_intent
        return RunnerOutput(
            bar_index=self.bar_index,
            atr=self.atr_value,
            fvg_count=len(self.fvg_signals),
            sweep_count=len(self.sweep_signals),
            signal_generated=self.composite_signal is not None,
            lot_size=intent.lot_size if intent else None,
            entry_price=intent.entry_price if intent else None,
            stop_loss=intent.stop_loss if intent else None,
            take_profit=intent.take_profit if intent else None,
        )


# ---------------------------------------------------------------------------
# Unified runner
# ---------------------------------------------------------------------------

class Runner:
    """
    Bar-by-bar processing engine shared by live and replay modes.

    Parameters
    ----------
    config          : RunnerConfig
    position_sizer  : Callable[[float, float, float, float], float]
                      Signature: (balance, risk_pct, sl_distance, tick_value) → lot_size
    context_provider: Callable[[], MarketContextState | None]
                      Returns current LLM context from Redis (or None).
    state_provider  : Callable[[], EngineState]
                      Returns current engine operational state.
    on_order_intent : Callable[[OrderIntent], None]
                      Callback invoked when a valid OrderIntent is ready.
    """

    def __init__(
        self,
        config: RunnerConfig,
        position_sizer: Callable[[float, float, float, float], float],
        context_provider: Callable[[], MarketContextState | None],
        state_provider: Callable[[], EngineState],
        on_order_intent: Callable[[OrderIntent], None],
    ) -> None:
        self.config = config
        self._position_sizer = position_sizer
        self._context_provider = context_provider
        self._state_provider = state_provider
        self._on_order_intent = on_order_intent

        self._bar_index: int = 0
        self._current_balance: float = config.initial_balance
        self._used_fvg_timestamps: set[datetime] = set()

    def process_bar(self, candles: Sequence[Candle]) -> BarResult:
        """
        Process a single new bar using the full candle history (oldest first).

        Parameters
        ----------
        candles : Full candle history including the just-closed bar (oldest first).

        Returns
        -------
        BarResult
            Complete output for this bar, regardless of whether a signal was found.
        """
        latest = candles[-1]
        self._bar_index += 1

        # --- Indicators ---
        atr_val = atr(candles, self.config.atr_period)
        ema_val = (
            ema([c.close for c in candles], self.config.ema_trend_period)
            if len(candles) >= self.config.ema_trend_period
            else None
        )
        fvgs = detect_fvg(candles, self.config.symbol, self.config.timeframe)
        sweeps = detect_liquidity_sweep(
            candles, self.config.symbol, self.config.timeframe, self.config.sweep_lookback
        )

        result = BarResult(
            bar_index=self._bar_index,
            timestamp=latest.timestamp,
            atr_value=atr_val,
            fvg_signals=fvgs,
            sweep_signals=sweeps,
        )

        # --- Signal composition ---
        composite = self._compose_signal(fvgs, sweeps, atr_val, latest, ema_val)
        if composite is None:
            return result

        result.composite_signal = composite

        # --- Pre-flight gates ---
        context = self._context_provider()
        state = self._state_provider()

        try:
            run_all_gates(
                signal=composite,
                context=context,
                state=state,
                min_rr=self.config.min_risk_reward,
            )
        except PreflightGateError as exc:
            logger.warning(f"[GATE REJECTED] {exc}")
            result.gate_rejection = exc.gate_name
            return result

        # --- Position sizing ---
        if self.config.symbol.endswith("USD"):
            tick_value = 10.0  # Standard lot = $10/pip for xxxUSD pairs
        else:
            tick_value = 10.0  # Simplified; live runner queries MT5 for actual value

        # Entry price: midpoint of entry zone (consistent with CompositeSignal.indicative_risk_reward)
        entry = (composite.entry_zone_high + composite.entry_zone_low) / 2
        sl = composite.stop_loss_level
        tp = composite.take_profit_level

        # Safety clamp to strictly satisfy OrderIntent invariants
        min_dist = max(composite.atr_value * 0.5, 0.0002)
        if composite.direction == SignalDirection.LONG:
            if sl >= entry:
                sl = entry - min_dist
            if tp <= entry:
                tp = entry + (entry - sl) * self.config.min_risk_reward
            sl_distance = entry - sl
        else:
            if sl <= entry:
                sl = entry + min_dist
            if tp >= entry:
                tp = entry - (sl - entry) * self.config.min_risk_reward
            sl_distance = sl - entry

        lot_size = self._position_sizer(
            self._current_balance, self.config.risk_per_trade_pct, sl_distance, tick_value
        )
        risk_usd = sl_distance * lot_size * tick_value * 100  # in USD

        # --- Build OrderIntent ---
        side = OrderSide.BUY if composite.direction == SignalDirection.LONG else OrderSide.SELL
        rr = abs(tp - entry) / sl_distance if sl_distance > 0 else self.config.min_risk_reward
        rr = max(rr, self.config.min_risk_reward, 2.0)

        intent = OrderIntent(
            created_at=latest.timestamp,
            symbol=self.config.symbol,
            side=side,
            order_type=OrderType.LIMIT,
            entry_price=entry,
            stop_loss=sl,
            take_profit=tp,
            lot_size=lot_size,
            risk_usd=min(risk_usd, 25.0),
            risk_pct=min(risk_usd / self._current_balance, 0.005),
            risk_reward_ratio=rr,
            signal_id=composite.signal_id,
            magic_number=self.config.magic_number,
        )

        result.order_intent = intent
        self._on_order_intent(intent)

        # Mark FVG as used to prevent duplicate triggers
        if composite.fvg:
            self._used_fvg_timestamps.add(composite.fvg.detected_at)
            if len(self._used_fvg_timestamps) > 500:
                self._used_fvg_timestamps = set(list(self._used_fvg_timestamps)[-250:])

        logger.info(
            f"[RUNNER] Bar {self._bar_index} | Signal: {composite.direction.value} | "
            f"Entry={entry:.5f} SL={sl:.5f} "
            f"TP={tp:.5f} Lot={lot_size:.2f} RR={rr:.2f}"
        )

        return result

    def _compose_signal(
        self,
        fvgs: list,
        sweeps: list,
        atr_val: float,
        latest: Candle,
        ema_val: float | None = None,
    ) -> CompositeSignal | None:
        """
        Attempt to compose a high-probability CompositeSignal from recent FVGs and sweeps.

        Strategy:
        1. Confirmed liquidity sweep within max_sweep_age_bars.
        2. Trend alignment: price above EMA(50) for LONG, below EMA(50) for SHORT.
        3. Fresh matching FVG formed after or at the sweep, >= min_fvg_size.
        4. Current candle retraces/taps into the FVG entry zone.
        """
        from src.core.schemas.signals import CompositeSignal, SignalType

        # Find the most recent confirmed sweep
        confirmed_sweeps = [s for s in sweeps if s.reversal_confirmed]
        if not confirmed_sweeps:
            return None

        latest_sweep = confirmed_sweeps[-1]

        # 1. Sweep freshness (must be within max_sweep_age_bars)
        sweep_age_bars = (latest.timestamp - latest_sweep.detected_at).total_seconds() / 900.0
        if sweep_age_bars > self.config.max_sweep_age_bars or sweep_age_bars < 0:
            return None

        # 2. Trend filter (EMA)
        if ema_val is not None:
            if latest_sweep.direction == SignalDirection.LONG and latest.close < ema_val:
                return None
            if latest_sweep.direction == SignalDirection.SHORT and latest.close > ema_val:
                return None

        # 3. Fresh FVG matching the sweep direction that formed after/at the sweep
        min_fvg_size = self.config.min_fvg_size_pips * 0.00010
        matching_fvgs = [
            f for f in fvgs
            if f.direction == latest_sweep.direction
            and f.detected_at >= latest_sweep.detected_at
            and abs(f.gap_high - f.gap_low) >= min_fvg_size
            and f.detected_at not in self._used_fvg_timestamps
        ]
        if not matching_fvgs:
            return None

        latest_fvg = matching_fvgs[-1]

        # 4. Retracement trigger: current bar must touch/dip into the FVG zone
        if latest_sweep.direction == SignalDirection.LONG:
            if not (latest.low <= latest_fvg.gap_high and latest.high >= latest_fvg.gap_low):
                return None
        else:
            if not (latest.high >= latest_fvg.gap_low and latest.low <= latest_fvg.gap_high):
                return None

        entry_high = latest_fvg.gap_high
        entry_low = latest_fvg.gap_low
        entry_midpoint = (entry_high + entry_low) / 2

        min_buffer = max(atr_val * 0.5, 0.0002)
        if latest_sweep.direction == SignalDirection.LONG:
            sl = min(latest_sweep.sweep_candle_low - atr_val * 0.5, entry_midpoint - min_buffer)
            risk_dist = max(entry_midpoint - sl, min_buffer)
            tp = entry_midpoint + risk_dist * self.config.min_risk_reward
        else:
            sl = max(latest_sweep.sweep_candle_high + atr_val * 0.5, entry_midpoint + min_buffer)
            risk_dist = max(sl - entry_midpoint, min_buffer)
            tp = entry_midpoint - risk_dist * self.config.min_risk_reward

        return CompositeSignal(
            created_at=latest.timestamp,
            symbol=self.config.symbol,
            timeframe=self.config.timeframe,
            direction=latest_sweep.direction,
            signal_type=SignalType.COMBINED,
            fvg=latest_fvg,
            sweep=latest_sweep,
            entry_zone_high=entry_high,
            entry_zone_low=entry_low,
            stop_loss_level=sl,
            take_profit_level=tp,
            atr_value=atr_val,
        )
