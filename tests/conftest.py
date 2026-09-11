"""
tests/conftest.py
=================
Pytest shared fixtures for the trading engine test suite.

Key fixture: `mock_mt5`
-----------------------
Stubs out the MetaTrader5 module so ALL broker-related tests run
in headless Linux/Mac/Windows CI environments without an MT5 terminal.

INVARIANT 6: Only mt5_client.py is allowed to import MetaTrader5.
All unit tests must use this fixture — never import MetaTrader5 directly.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import pytz

from src.core.schemas.candles import Candle
from src.core.schemas.context import MarketContextState, MarketRegime
from src.core.schemas.orders import OrderIntent, OrderSide, OrderType
from src.core.schemas.signals import CompositeSignal, SignalDirection, SignalType

_UTC = pytz.utc


# ---------------------------------------------------------------------------
# mock_mt5 fixture — stubs MetaTrader5 module entirely
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_mt5():
    """
    Inject a fully stubbed MetaTrader5 module into sys.modules.

    The stub provides sensible defaults for all MT5 functions used
    by mt5_client.py. Tests can override individual attributes as needed:

        mock_mt5.account_info.return_value = SimpleNamespace(balance=5000.0, equity=4980.0)
    """
    mt5_mock = MagicMock(name="MetaTrader5")

    # Constants
    mt5_mock.TRADE_ACTION_DEAL = 1
    mt5_mock.TRADE_ACTION_PENDING = 5
    mt5_mock.TRADE_ACTION_REMOVE = 8
    mt5_mock.ORDER_TYPE_BUY = 0
    mt5_mock.ORDER_TYPE_SELL = 1
    mt5_mock.ORDER_TIME_GTC = 1
    mt5_mock.ORDER_FILLING_IOC = 1
    mt5_mock.TRADE_RETCODE_DONE = 10009

    # Default account info
    account_mock = MagicMock()
    account_mock.balance = 5000.0
    account_mock.equity = 5000.0
    account_mock.margin = 0.0
    account_mock.margin_free = 5000.0
    mt5_mock.account_info.return_value = account_mock

    # Default terminal info (connected)
    terminal_mock = MagicMock()
    terminal_mock.connected = True
    mt5_mock.terminal_info.return_value = terminal_mock

    # Default version
    mt5_mock.version.return_value = (500, 3680, "11 Jan 2024")

    # Default initialize/shutdown
    mt5_mock.initialize.return_value = True
    mt5_mock.shutdown.return_value = None

    # Default tick (for time sync)
    tick_mock = MagicMock()
    tick_mock.time = int(datetime.now(tz=_UTC).timestamp())
    mt5_mock.symbol_info_tick.return_value = tick_mock

    # Default order_send result
    order_result_mock = MagicMock()
    order_result_mock.retcode = mt5_mock.TRADE_RETCODE_DONE
    order_result_mock.order = 12345678
    order_result_mock.comment = "Request completed"
    mt5_mock.order_send.return_value = order_result_mock

    # Default positions (empty)
    mt5_mock.positions_get.return_value = []
    mt5_mock.orders_get.return_value = []

    # Default candle rates (5 bars of EURUSD-ish data)
    mt5_mock.copy_rates_from_pos.return_value = _make_fake_rates(50)

    # Inject into sys.modules so mt5_client.py's import resolves to our mock
    sys.modules["MetaTrader5"] = mt5_mock

    yield mt5_mock

    # Cleanup: remove from sys.modules after test
    sys.modules.pop("MetaTrader5", None)


def _make_fake_rates(count: int) -> list[dict]:
    """Generate fake OHLCV rate dicts resembling MT5 output."""
    base_time = int(datetime(2024, 1, 15, 9, 0, tzinfo=_UTC).timestamp())
    rates = []
    price = 1.10000
    for i in range(count):
        o = price + (i % 3) * 0.0001
        h = o + 0.0015
        l = o - 0.0010
        c = o + 0.0005
        rates.append({
            "time": base_time + i * 900,  # 15-min bars
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "tick_volume": 1000 + i * 10,
        })
    return rates


# ---------------------------------------------------------------------------
# Candle fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_candles() -> list[Candle]:
    """50 M15 candles with mild uptrend."""
    base_time = datetime(2024, 1, 15, 9, 0, tzinfo=_UTC)
    candles = []
    price = 1.10000
    for i in range(50):
        ts = base_time + timedelta(minutes=15 * i)
        o = price + i * 0.00005
        candles.append(Candle(
            timestamp=ts,
            open=o,
            high=o + 0.0015,
            low=o - 0.0010,
            close=o + 0.0005,
            volume=1000.0,
            timeframe="M15",
        ))
    return candles


# ---------------------------------------------------------------------------
# LLM context fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def allow_context() -> MarketContextState:
    """A valid MarketContextState that permits trading."""
    now = datetime.now(tz=_UTC)
    return MarketContextState(
        trading_allowed=True,
        generated_at=now,
        expires_at=now + timedelta(minutes=15),
        llm_model="test-model",
        regime=MarketRegime.TRENDING_BULLISH,
        regime_confidence=0.8,
        high_impact_news_nearby=False,
        news_summary="No news nearby.",
        reasoning="Regime is trending bullish, conditions are favorable.",
    )


@pytest.fixture
def deny_context() -> MarketContextState:
    """A MarketContextState that blocks trading."""
    now = datetime.now(tz=_UTC)
    return MarketContextState(
        trading_allowed=False,
        generated_at=now,
        expires_at=now + timedelta(minutes=15),
        llm_model="test-model",
        regime=MarketRegime.VOLATILE,
        regime_confidence=0.9,
        high_impact_news_nearby=True,
        news_summary="NFP in 20 minutes.",
        reasoning="High-impact news nearby — blocking all entries.",
    )
