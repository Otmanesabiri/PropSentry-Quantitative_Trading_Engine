"""
src/engine/live_engine.py
=========================
Main async orchestrator — wires ALL components together for live trading.

Startup sequence
----------------
  1. Connect to MT5 bridge (health check)
  2. Fetch initial account state → equity watchdog
  3. Start context worker (LLM → Redis, background)
  4. Start Telegram copilot (background)
  5. Start heartbeat + dead-man's switch (background)
  6. Enter main tick loop:
       a. Equity tick → watchdog (raises on kill-switch threshold)
       b. Bar detection → runner.process_bar()
       c. OrderIntent → Telegram alert → await operator approval
       d. On approval → dispatch via bridge → persist to DB
       e. Breakeven management on open positions
       f. Daily reset at 00:00 CE(S)T → reset watchdog + kill-switch

Components
----------
  BridgeClient      → all MT5 communication (HTTP to Windows host)
  Runner            → bar-by-bar signal pipeline (same as backtest)
  EquityWatchdog    → tick-by-tick equity monitoring (INVARIANT 3)
  KillSwitch        → hard stop at -4.20% (INVARIANT 4)
  TelegramCopilot   → operator approval gate
  ContextWorker     → async LLM context refresh (INVARIANT 7)
  TradingRepository → append-only audit log
  Heartbeat         → liveness monitoring
  DeadmanSwitch     → external HTTP ping (5 min)
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime
from typing import Any

import pytz
import redis.asyncio as aioredis
from loguru import logger

from config.loader import get_settings
from src.agent.llm_auditor import LLMAuditor
from src.agent.news_fetcher import NewsFetcher
from src.agent.news_gate import NewsGate
from src.agent.worker import ContextWorker, read_context_from_redis
from src.broker.bridge_client import BridgeClient
from src.copilot.telegram_bot import TelegramCopilot
from src.core.broker_clock import get_daily_reset_utc, get_utc_now, is_new_day_since
from src.core.exceptions import (
    DailyLossLimitBreached,
    OverallLossLimitBreached,
    PreflightGateError,
)
from src.core.schemas.candles import Candle
from src.core.schemas.orders import OrderIntent, OrderStatus
from src.engine.preflight_gates import EngineState
from src.engine.runner import Runner, RunnerConfig
from src.risk.equity_watchdog import EquitySnapshot, EquityWatchdog
from src.risk.kill_switch import KillSwitch
from src.risk.position_sizer import SymbolInfo, compute_lot_size
from src.storage.db import get_session, init_db
from src.storage.repository import TradingRepository
from src.supervisor.backup_service import BackupService
from src.supervisor.deadman_switch import DeadmanSwitch
from src.supervisor.heartbeat import Heartbeat

_UTC = pytz.utc


# ---------------------------------------------------------------------------
# Async kill-switch adapter
# ---------------------------------------------------------------------------

class _AsyncBrokerAdapter:
    """Synchronous wrapper around BridgeClient for use with KillSwitch."""

    def __init__(self, bridge: BridgeClient, symbol: str) -> None:
        self._bridge = bridge
        self._symbol = symbol

    def cancel_all_pending_orders(self, symbol: str | None = None) -> int:
        return asyncio.get_event_loop().run_until_complete(
            self._bridge.cancel_all_pending_orders(symbol or self._symbol)
        )

    def flatten_all_positions(self, symbol: str | None = None) -> int:
        return asyncio.get_event_loop().run_until_complete(
            self._bridge.flatten_all_positions(symbol or self._symbol)
        )


# ---------------------------------------------------------------------------
# Live Engine
# ---------------------------------------------------------------------------

class LiveEngine:
    """
    Full async trading engine for live / paper modes.

    Parameters
    ----------
    mode : "live" | "paper"  — paper = auto-approve all Telegram alerts.
    """

    def __init__(self, mode: str = "live") -> None:
        self._mode = mode
        self._cfg = get_settings()
        self._running = False

        # Broker bridge
        bridge_host = os.getenv("MT5_BRIDGE_HOST", "localhost")
        bridge_port = int(os.getenv("MT5_BRIDGE_PORT", "8765"))
        bridge_key = os.getenv("MT5_BRIDGE_API_KEY", "changeme-secret-key")
        self._bridge = BridgeClient(
            base_url=f"http://{bridge_host}:{bridge_port}",
            api_key=bridge_key,
        )


        # Redis
        self._redis: aioredis.Redis | None = None

        # Engine state
        self._current_balance: float = self._cfg.broker.__dict__.get("initial_balance", 5000.0)
        self._daily_trades: int = 0
        self._last_bar_time: datetime | None = None
        self._last_reset_utc: datetime = get_daily_reset_utc()
        self._kill_switch: KillSwitch | None = None
        self._watchdog: EquityWatchdog | None = None

        # Approved orders queue (Telegram callback → dispatch)
        self._approved_queue: asyncio.Queue[OrderIntent] = asyncio.Queue()

        logger.info(f"[ENGINE] LiveEngine initialized | mode={mode}")

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Initialize all components and start the main loop."""
        logger.info("[ENGINE] Starting up...")

        # 1. Database
        init_db(self._cfg.database.url)
        logger.info(f"[ENGINE] Database ready: {self._cfg.database.url}")

        # 2. Bridge health check
        await self._check_bridge_health()

        # 3. Initial account state
        self._current_balance = await self._bridge.get_account_balance()
        self._last_reset_utc = get_daily_reset_utc()
        logger.info(f"[ENGINE] Account balance: ${self._current_balance:.2f}")

        # 4. Risk components
        self._watchdog = EquityWatchdog(
            initial_balance=self._current_balance,
            reset_time_utc=self._last_reset_utc,
        )
        broker_adapter = _AsyncBrokerAdapter(self._bridge, self._cfg.broker.symbol)
        self._kill_switch = KillSwitch(broker=broker_adapter, symbol=self._cfg.broker.symbol)
        self._watchdog.register_kill_switch_callback(self._on_kill_switch_triggered)

        # 5. Redis
        self._redis = await aioredis.from_url(
            f"redis://{self._cfg.redis.host}:{self._cfg.redis.port}/{self._cfg.redis.db}"
        )
        logger.info(f"[ENGINE] Redis connected: {self._cfg.redis.host}:{self._cfg.redis.port}")

        # 6. Runner
        symbol_info = SymbolInfo(
            symbol=self._cfg.broker.symbol,
            tick_size=0.00001, tick_value=1.0,
            volume_min=0.01, volume_max=100.0, volume_step=0.01,
        )

        def position_sizer_fn(balance: float, risk_pct: float, sl_dist: float, tick_val: float) -> float:
            si = SymbolInfo(
                symbol=self._cfg.broker.symbol,
                tick_size=0.00001, tick_value=tick_val / 10,
                volume_min=0.01, volume_max=100.0, volume_step=0.01,
            )
            return compute_lot_size(balance, risk_pct, sl_dist, si)

        self._runner = Runner(
            config=RunnerConfig(
                symbol=self._cfg.broker.symbol,
                timeframe=self._cfg.engine.bar_timeframe,
                initial_balance=self._current_balance,
                risk_per_trade_pct=0.005,
                min_risk_reward=2.0,
                breakeven_trigger_r=1.0,
                max_open_positions=1,
                max_daily_trades=3,
                magic_number=self._cfg.broker.magic_number,
            ),
            position_sizer=position_sizer_fn,
            context_provider=self._get_context_sync,
            state_provider=self._get_engine_state,
            on_order_intent=self._on_order_intent,
        )

        # 7. Background tasks
        tasks = await self._start_background_tasks()

        # 8. Main tick loop
        self._running = True
        logger.info(
            f"[ENGINE] 🟢 LIVE ENGINE STARTED | "
            f"Symbol={self._cfg.broker.symbol} | TF={self._cfg.engine.bar_timeframe} | "
            f"Mode={self._mode.upper()}"
        )

        try:
            await self._main_loop()
        finally:
            self._running = False
            for task in tasks:
                task.cancel()
            if self._redis:
                await self._redis.aclose()
            logger.info("[ENGINE] Shutdown complete.")

    # ------------------------------------------------------------------
    # Main tick loop
    # ------------------------------------------------------------------

    async def _main_loop(self) -> None:
        tick_interval = self._cfg.engine.tick_interval_ms / 1000.0

        while self._running:
            try:
                await self._tick()
            except (DailyLossLimitBreached, OverallLossLimitBreached) as exc:
                logger.critical(f"[ENGINE] 🛑 KILL-SWITCH TRIGGERED: {exc}")
                # Kill-switch already executed by watchdog callback — just stop loop
                break
            except Exception as exc:
                logger.error(f"[ENGINE] Tick error: {exc}")

            # Process approved orders
            while not self._approved_queue.empty():
                intent = self._approved_queue.get_nowait()
                await self._dispatch_order(intent)

            if self._heartbeat:
                self._heartbeat.beat()

            await asyncio.sleep(tick_interval)

    async def _tick(self) -> None:
        """Single tick: equity check + bar detection + signal processing."""

        # ── Daily reset check ──────────────────────────────────────────
        if is_new_day_since(self._last_reset_utc):
            await self._daily_reset()

        # ── Equity monitoring (INVARIANT 3) ───────────────────────────
        equity = await self._bridge.get_account_equity()
        balance = await self._bridge.get_account_balance()
        snap = EquitySnapshot(
            timestamp=get_utc_now(),
            balance=balance,
            open_pnl=equity - balance,
            commissions=0.0,
            swap=0.0,
        )
        if self._watchdog:
            self._watchdog.on_tick(snap)  # raises DailyLossLimitBreached if -4.2%

        # ── Bar detection ──────────────────────────────────────────────
        candles = await self._bridge.get_candles(
            self._cfg.broker.symbol,
            self._cfg.engine.bar_timeframe,
            count=self._cfg.engine.warmup_bars + 5,
        )
        if not candles:
            return

        latest_bar_time = candles[-1].timestamp
        if latest_bar_time == self._last_bar_time:
            return  # No new bar yet

        self._last_bar_time = latest_bar_time
        logger.debug(
            f"[ENGINE] New bar: {latest_bar_time.strftime('%Y-%m-%d %H:%M UTC')} | "
            f"Close={candles[-1].close:.5f}"
        )

        # ── Runner processing (INVARIANT 8 — same math as backtest) ───
        self._runner._current_balance = balance
        self._cached_context = await read_context_from_redis(
            self._redis, self._cfg.redis.market_context_key
        )
        self._cached_pos_count = await self._bridge.get_open_positions_count(
            self._cfg.broker.symbol
        )
        result = self._runner.process_bar(candles)

        if result.gate_rejection:
            logger.info(f"[ENGINE] Gate rejected: {result.gate_rejection}")

        # Breakeven management on existing positions
        await self._check_breakeven(candles[-1].close)

    # ------------------------------------------------------------------
    # Daily reset
    # ------------------------------------------------------------------

    async def _daily_reset(self) -> None:
        logger.info("[ENGINE] ── DAILY RESET ──")
        new_balance = await self._bridge.get_account_balance()
        self._last_reset_utc = get_daily_reset_utc()
        self._daily_trades = 0

        if self._watchdog:
            self._watchdog.reset_for_new_day(new_balance, self._last_reset_utc)
        if self._kill_switch:
            self._kill_switch.reset_for_new_day()

        self._current_balance = new_balance

        # Daily snapshot
        try:
            session = get_session()
            repo = TradingRepository(session)
            from src.storage.models import DailySnapshot
            snap = DailySnapshot(
                snapshot_date=str(get_utc_now().date()),
                reset_time_utc=self._last_reset_utc,
                balance_open=new_balance,
                equity_open=await self._bridge.get_account_equity(),
            )
            repo.save_daily_snapshot(snap)
            session.close()
        except Exception as exc:
            logger.error(f"[ENGINE] Failed to save daily snapshot: {exc}")

        # Backup
        try:
            BackupService().run_backup()
        except Exception as exc:
            logger.error(f"[ENGINE] Backup failed: {exc}")

    # ------------------------------------------------------------------
    # Order lifecycle
    # ------------------------------------------------------------------

    def _on_order_intent(self, intent: OrderIntent) -> None:
        """Called by Runner when a valid signal passes all 8 gates."""
        if self._mode == "paper":
            # Paper mode: auto-approve without Telegram
            self._approved_queue.put_nowait(intent)
            logger.info(f"[ENGINE] [PAPER] Auto-approved order {intent.order_id}")
        else:
            # Live mode: send Telegram alert (approval handled by callback)
            asyncio.create_task(self._send_telegram_alert(intent))

    async def _send_telegram_alert(self, intent: OrderIntent) -> None:
        if self._copilot:
            await self._copilot.send_trade_alert(intent)
        session = get_session()
        TradingRepository(session).save_order(intent)
        session.close()

    async def _on_operator_approved(self, intent: OrderIntent) -> None:
        """Callback from Telegram: operator clicked Approve."""
        self._approved_queue.put_nowait(intent)

    async def _on_operator_rejected(self, intent: OrderIntent, reason: str) -> None:
        """Callback from Telegram: operator clicked Reject or timeout."""
        logger.info(f"[ENGINE] Order {intent.order_id} rejected ({reason})")
        session = get_session()
        TradingRepository(session).update_order_status(intent.order_id, OrderStatus.REJECTED.value)
        session.close()

    async def _dispatch_order(self, intent: OrderIntent) -> None:
        """Dispatch an approved order to the MT5 bridge."""
        try:
            ticket = await self._bridge.send_order(intent)
            self._daily_trades += 1
            session = get_session()
            TradingRepository(session).update_order_status(
                intent.order_id, OrderStatus.FILLED.value, broker_ticket=ticket
            )
            session.close()
            logger.info(
                f"[ENGINE] ✅ Order filled | Ticket={ticket} | "
                f"{intent.side.value} {intent.lot_size} {intent.symbol} @ {intent.entry_price:.5f}"
            )
        except Exception as exc:
            logger.error(f"[ENGINE] Dispatch failed: {exc}")
            session = get_session()
            TradingRepository(session).update_order_status(
                intent.order_id, OrderStatus.REJECTED.value
            )
            session.close()

    # ------------------------------------------------------------------
    # Breakeven management
    # ------------------------------------------------------------------

    async def _check_breakeven(self, current_price: float) -> None:
        """Placeholder — PositionManager integration in Phase 4 completion."""
        pass

    # ------------------------------------------------------------------
    # Kill-switch callback
    # ------------------------------------------------------------------

    def _on_kill_switch_triggered(self, reason: str) -> None:
        """Invoked by EquityWatchdog on threshold breach."""
        asyncio.create_task(self._async_kill_switch_execute(reason))

    async def _async_kill_switch_execute(self, reason: str) -> None:
        logger.critical(f"[ENGINE] 🛑 Executing kill-switch: {reason}")
        try:
            cancelled = await self._bridge.cancel_all_pending_orders(self._cfg.broker.symbol)
            closed = await self._bridge.flatten_all_positions(self._cfg.broker.symbol)
            logger.critical(
                f"[ENGINE] Kill-switch complete | Cancelled={cancelled} | Closed={closed}"
            )
        except Exception as exc:
            logger.error(f"[ENGINE] Kill-switch broker error: {exc}")

        if self._kill_switch:
            self._kill_switch._state.arm(reason)

        # Notify via Telegram
        if self._copilot and self._cfg.telegram.chat_id:
            from telegram import Bot
            try:
                bot = Bot(token=self._cfg.telegram.bot_token)
                await bot.send_message(
                    chat_id=self._cfg.telegram.chat_id,
                    text=f"🛑 <b>KILL-SWITCH ACTIVATED</b>\nReason: <code>{reason}</code>\n"
                         "All positions closed. Trading disabled until 00:00 CE(S)T.",
                    parse_mode="HTML",
                )
            except Exception as exc:
                logger.error(f"[ENGINE] Telegram kill-switch alert failed: {exc}")

    # ------------------------------------------------------------------
    # State providers (sync) — called by Runner
    # ------------------------------------------------------------------

    def _get_context_sync(self):
        """Sync wrapper — returns pre-fetched cached context from Redis."""
        return getattr(self, "_cached_context", None)

    def _get_engine_state(self) -> EngineState:
        from src.engine.preflight_gates import EngineState as ES
        pos_count = getattr(self, "_cached_pos_count", 0)
        return ES(
            kill_switch_active=self._kill_switch.is_active if self._kill_switch else False,
            open_positions_count=pos_count,
            daily_trades_count=self._daily_trades,
            max_open_positions=1,
            max_daily_trades=3,
            news_blackout_active=False,
        )

    # ------------------------------------------------------------------
    # Bridge health check
    # ------------------------------------------------------------------

    async def _check_bridge_health(self) -> None:
        logger.info("[ENGINE] Checking MT5 bridge connectivity...")
        try:
            data = await self._bridge.health()
            if not data.get("mt5_connected"):
                raise ConnectionError("MT5 terminal not connected to bridge.")
            logger.info(
                f"[ENGINE] Bridge OK | MT5 connected | "
                f"Server={data.get('server')} | Login={data.get('login')}"
            )
        except Exception as exc:
            logger.critical(f"[ENGINE] Bridge unreachable: {exc}")
            raise

    # ------------------------------------------------------------------
    # Background tasks
    # ------------------------------------------------------------------

    async def _start_background_tasks(self) -> list[asyncio.Task]:
        tasks: list[asyncio.Task] = []

        # Heartbeat
        self._heartbeat = Heartbeat(
            interval_s=self._cfg.supervisor.heartbeat_interval_s,
            max_missed=self._cfg.supervisor.max_missed_heartbeats,
        )
        tasks.append(asyncio.create_task(self._heartbeat.monitor(), name="heartbeat"))

        # Dead-man's switch (only if URL is configured)
        deadman_url = self._cfg.system.__dict__.get("deadman_url", "")
        if deadman_url:
            dm = DeadmanSwitch(
                ping_url=deadman_url,
                interval_s=self._cfg.supervisor.deadman_ping_interval_s,
            )
            tasks.append(asyncio.create_task(dm.run(), name="deadman"))

        # LLM context worker
        news_fetcher = NewsFetcher(api_key=self._cfg.news.finnhub_api_key)
        news_gate = NewsGate(fetcher=news_fetcher)
        llm_auditor = LLMAuditor(
            api_url="https://api.openai.com/v1/chat/completions",
            api_key=self._cfg.system.__dict__.get("llm_api_key", ""),
        )

        async def candle_provider():
            return await self._bridge.get_candles(
                self._cfg.broker.symbol, self._cfg.engine.bar_timeframe, 100
            )

        context_worker = ContextWorker(
            redis_client=self._redis,
            auditor=llm_auditor,
            news_gate=news_gate,
            candle_provider=candle_provider,
            redis_key=self._cfg.redis.market_context_key,
        )
        tasks.append(asyncio.create_task(context_worker.run(), name="context_worker"))

        # Telegram copilot
        self._copilot: TelegramCopilot | None = None
        if self._cfg.telegram.bot_token and self._mode == "live":
            self._copilot = TelegramCopilot(
                bot_token=self._cfg.telegram.bot_token,
                chat_id=self._cfg.telegram.chat_id,
                alert_timeout_s=self._cfg.telegram.alert_timeout_s,
                on_approved=self._on_operator_approved,
                on_rejected=self._on_operator_rejected,
            )
            tasks.append(asyncio.create_task(self._copilot.start(), name="telegram"))
            logger.info("[ENGINE] Telegram copilot started.")
        else:
            logger.info("[ENGINE] Telegram copilot disabled (paper mode or no token).")

        logger.info(f"[ENGINE] {len(tasks)} background tasks started.")
        return tasks
