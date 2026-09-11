"""
src/broker/mt5_client.py
========================
Singleton MetaTrader 5 IPC gateway — INVARIANT 6.

THIS IS THE ONLY MODULE ALLOWED TO IMPORT MetaTrader5.
Any other module importing MetaTrader5 violates architecture isolation
and must be rejected during code review and CI linting.

Responsibilities
----------------
- Initialize and maintain the connection to the MT5 terminal process.
- Provide a typed Python API for all broker operations.
- Translate MT5 raw structures into domain schemas (Candle, OrderIntent, etc.).
- Enforce retry logic and connection health checks.

Architecture
------------
All other modules depend on the BrokerInterface protocol (from kill_switch.py
or their own local Protocol definitions), never on this class directly.
This allows full mocking in unit tests without an active MT5 terminal.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime
from typing import Any

import pytz
from loguru import logger

from src.core.exceptions import BrokerConnectionError, OrderDispatchError
from src.core.schemas.candles import Candle
from src.core.schemas.orders import OrderIntent, OrderSide, OrderStatus

# ---------------------------------------------------------------------------
# MT5 import — isolated here ONLY (INVARIANT 6)
# ---------------------------------------------------------------------------
try:
    import MetaTrader5 as _mt5  # type: ignore[import]
except ImportError:
    _mt5 = None  # Will be detected in __init__ and raise BrokerConnectionError

_UTC = pytz.utc


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

class MT5Client:
    """
    Singleton gateway to the MetaTrader 5 terminal.

    Parameters
    ----------
    login    : MT5 account number.
    password : MT5 account password.
    server   : Broker server name (e.g., 'FTMO-Demo').
    timeout  : Connection timeout in milliseconds.
    """

    _instance: "MT5Client | None" = None

    def __new__(cls, *args: Any, **kwargs: Any) -> "MT5Client":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(
        self,
        login: int,
        password: str,
        server: str,
        timeout: int = 10_000,
    ) -> None:
        if self._initialized:  # type: ignore[has-type]
            return
        self._login = login
        self._password = password
        self._server = server
        self._timeout = timeout
        self._initialized = True

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """
        Initialize the MT5 terminal connection.

        Raises
        ------
        BrokerConnectionError
            If MT5 is not installed, or connection/login fails.
        """
        if _mt5 is None:
            raise BrokerConnectionError(
                "MetaTrader5 package is not installed. "
                "Run: pip install MetaTrader5"
            )

        if not _mt5.initialize(
            login=self._login,
            password=self._password,
            server=self._server,
            timeout=self._timeout,
        ):
            error = _mt5.last_error()
            raise BrokerConnectionError(
                f"MT5 initialization failed: {error}"
            )

        logger.info(
            f"[MT5] Connected | Account: {self._login} | Server: {self._server} | "
            f"Version: {_mt5.version()}"
        )

    def disconnect(self) -> None:
        """Gracefully shut down the MT5 connection."""
        if _mt5 is not None:
            _mt5.shutdown()
            logger.info("[MT5] Disconnected.")

    def is_connected(self) -> bool:
        """Return True if the MT5 terminal is currently reachable."""
        if _mt5 is None:
            return False
        info = _mt5.terminal_info()
        return info is not None and info.connected

    # ------------------------------------------------------------------
    # Account data
    # ------------------------------------------------------------------

    def get_account_balance(self) -> float:
        """Return current account cash balance (USD)."""
        info = _mt5.account_info()
        if info is None:
            raise BrokerConnectionError("Cannot retrieve account info from MT5.")
        return float(info.balance)

    def get_account_equity(self) -> float:
        """Return current floating equity (USD)."""
        info = _mt5.account_info()
        if info is None:
            raise BrokerConnectionError("Cannot retrieve account info from MT5.")
        return float(info.equity)

    def get_server_time(self) -> datetime:
        """Return the broker's current server time (UTC-aware)."""
        tick = _mt5.symbol_info_tick("EURUSD")  # Use any liquid symbol
        if tick is None:
            raise BrokerConnectionError("Cannot retrieve server tick for time sync.")
        return datetime.fromtimestamp(tick.time, tz=_UTC)

    # ------------------------------------------------------------------
    # Market data
    # ------------------------------------------------------------------

    def get_candles(
        self,
        symbol: str,
        timeframe_str: str,
        count: int,
    ) -> list[Candle]:
        """
        Retrieve historical OHLCV bars and return them as validated Candle objects.

        Parameters
        ----------
        symbol        : MT5 symbol string (e.g., 'EURUSD').
        timeframe_str : Timeframe string (e.g., 'M15', 'H1').
        count         : Number of bars to retrieve (most recent).
        """
        tf = self._parse_timeframe(timeframe_str)
        rates = _mt5.copy_rates_from_pos(symbol, tf, 0, count)
        if rates is None or len(rates) == 0:
            raise BrokerConnectionError(
                f"MT5 returned no candles for {symbol} {timeframe_str}."
            )

        candles: list[Candle] = []
        for r in rates:
            candles.append(
                Candle(
                    timestamp=datetime.fromtimestamp(r["time"], tz=_UTC),
                    open=float(r["open"]),
                    high=float(r["high"]),
                    low=float(r["low"]),
                    close=float(r["close"]),
                    volume=float(r["tick_volume"]),
                    timeframe=timeframe_str,
                )
            )
        return candles

    # ------------------------------------------------------------------
    # Order management
    # ------------------------------------------------------------------

    def send_order(self, intent: OrderIntent) -> int:
        """
        Dispatch an OrderIntent to MT5 and return the broker ticket ID.

        Raises
        ------
        OrderDispatchError
            If MT5 returns a non-zero retcode.
        """
        action = _mt5.TRADE_ACTION_DEAL if intent.order_type.value == "MARKET" \
                 else _mt5.TRADE_ACTION_PENDING

        order_type = (
            _mt5.ORDER_TYPE_BUY if intent.side == OrderSide.BUY else _mt5.ORDER_TYPE_SELL
        )

        request: dict[str, Any] = {
            "action": action,
            "symbol": intent.symbol,
            "volume": intent.lot_size,
            "type": order_type,
            "price": intent.entry_price,
            "sl": intent.stop_loss,
            "tp": intent.take_profit,
            "deviation": 10,
            "magic": intent.magic_number,
            "comment": f"PropSentry|{intent.order_id}",
            "type_time": _mt5.ORDER_TIME_GTC,
            "type_filling": _mt5.ORDER_FILLING_IOC,
        }

        result = _mt5.order_send(request)

        if result is None or result.retcode != _mt5.TRADE_RETCODE_DONE:
            retcode = result.retcode if result else -1
            comment = result.comment if result else "No result returned"
            raise OrderDispatchError(retcode=retcode, comment=comment)

        logger.info(
            f"[MT5] Order filled | Ticket: {result.order} | "
            f"{intent.side.value} {intent.lot_size} {intent.symbol} @ {intent.entry_price}"
        )
        return int(result.order)

    def cancel_all_pending_orders(self, symbol: str | None = None) -> int:
        """Cancel all pending orders. Returns count of orders cancelled."""
        orders = _mt5.orders_get(symbol=symbol) if symbol else _mt5.orders_get()
        if orders is None:
            return 0

        cancelled = 0
        for order in orders:
            request = {
                "action": _mt5.TRADE_ACTION_REMOVE,
                "order": order.ticket,
            }
            result = _mt5.order_send(request)
            if result and result.retcode == _mt5.TRADE_RETCODE_DONE:
                cancelled += 1
                logger.info(f"[MT5] Cancelled pending order #{order.ticket}")
            else:
                logger.error(f"[MT5] Failed to cancel order #{order.ticket}: {result}")

        return cancelled

    def flatten_all_positions(self, symbol: str | None = None) -> int:
        """Close all open positions at market. Returns count of positions closed."""
        positions = _mt5.positions_get(symbol=symbol) if symbol else _mt5.positions_get()
        if positions is None:
            return 0

        closed = 0
        for pos in positions:
            close_type = (
                _mt5.ORDER_TYPE_SELL if pos.type == _mt5.ORDER_TYPE_BUY
                else _mt5.ORDER_TYPE_BUY
            )
            tick = _mt5.symbol_info_tick(pos.symbol)
            price = tick.bid if close_type == _mt5.ORDER_TYPE_SELL else tick.ask

            request = {
                "action": _mt5.TRADE_ACTION_DEAL,
                "symbol": pos.symbol,
                "volume": pos.volume,
                "type": close_type,
                "position": pos.ticket,
                "price": price,
                "deviation": 10,
                "magic": pos.magic,
                "comment": "PropSentry|KillSwitch",
                "type_filling": _mt5.ORDER_FILLING_IOC,
            }
            result = _mt5.order_send(request)
            if result and result.retcode == _mt5.TRADE_RETCODE_DONE:
                closed += 1
                logger.info(f"[MT5] Closed position #{pos.ticket} ({pos.symbol})")
            else:
                logger.error(f"[MT5] Failed to close position #{pos.ticket}: {result}")

        return closed

    def get_open_positions_count(self, symbol: str | None = None) -> int:
        """Return current count of open positions."""
        positions = _mt5.positions_get(symbol=symbol) if symbol else _mt5.positions_get()
        return len(positions) if positions else 0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_timeframe(tf_str: str) -> int:
        """Convert a timeframe string to an MT5 TIMEFRAME constant."""
        mapping = {
            "M1": 1, "M5": 5, "M15": 15, "M30": 30,
            "H1": 16385, "H4": 16388, "D1": 16408,
        }
        if tf_str not in mapping:
            raise ValueError(f"Unknown timeframe: {tf_str!r}. Valid: {list(mapping)}")
        return mapping[tf_str]
