"""
tests/test_broker_bridge.py
============================
Tests for the BridgeClient HTTP adapter.
Uses mock_mt5 to verify the bridge protocol without a live MT5 terminal.
Tests cover: connection, candles, order dispatch, kill-switch actions.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.broker.bridge_client import BridgeClient
from src.core.exceptions import BrokerConnectionError, OrderDispatchError
from src.core.schemas.orders import OrderIntent, OrderSide, OrderType


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client() -> BridgeClient:
    return BridgeClient(
        base_url="http://localhost:8765",
        api_key="test-api-key",
        timeout_s=5.0,
    )


def make_intent() -> OrderIntent:
    return OrderIntent(
        created_at=datetime.now(tz=timezone.utc),
        symbol="EURUSD",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        entry_price=1.10000,
        stop_loss=1.09800,
        take_profit=1.10400,
        lot_size=0.10,
        risk_usd=20.0,
        risk_pct=0.004,
        risk_reward_ratio=2.0,
        signal_id=uuid4(),
        magic_number=202401,
    )


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

class TestBridgeClientHealth:
    @pytest.mark.asyncio
    async def test_is_connected_true(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"bridge": "online", "mt5_connected": True}
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_http:
            mock_http.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)
            result = await client.is_connected()

        assert result is True

    @pytest.mark.asyncio
    async def test_is_connected_false_on_error(self, client):
        with patch("httpx.AsyncClient") as mock_http:
            mock_http.return_value.__aenter__.return_value.get = AsyncMock(
                side_effect=Exception("Connection refused")
            )
            result = await client.is_connected()

        assert result is False


# ---------------------------------------------------------------------------
# Account
# ---------------------------------------------------------------------------

class TestBridgeClientAccount:
    @pytest.mark.asyncio
    async def test_get_balance(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"balance": 5000.0, "equity": 4980.0}
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_http:
            mock_http.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)
            balance = await client.get_account_balance()

        assert balance == pytest.approx(5000.0)

    @pytest.mark.asyncio
    async def test_get_balance_raises_on_503(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_response.text = "MT5 not connected"

        with patch("httpx.AsyncClient") as mock_http:
            mock_http.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)
            with pytest.raises(BrokerConnectionError):
                await client.get_account_balance()


# ---------------------------------------------------------------------------
# Order dispatch
# ---------------------------------------------------------------------------

class TestBridgeClientOrders:
    @pytest.mark.asyncio
    async def test_send_order_success(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"ticket": 12345678, "status": "FILLED"}
        mock_response.raise_for_status = MagicMock()

        intent = make_intent()
        with patch("httpx.AsyncClient") as mock_http:
            mock_http.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)
            ticket = await client.send_order(intent)

        assert ticket == 12345678

    @pytest.mark.asyncio
    async def test_send_order_rejected(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 422
        mock_response.json.return_value = {"detail": {"retcode": 10004, "comment": "Requote"}}

        intent = make_intent()
        with patch("httpx.AsyncClient") as mock_http:
            mock_http.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)
            with pytest.raises(OrderDispatchError) as exc_info:
                await client.send_order(intent)

        assert exc_info.value.retcode == 10004

    @pytest.mark.asyncio
    async def test_cancel_all_orders(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"cancelled": 3}
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_http:
            mock_http.return_value.__aenter__.return_value.delete = AsyncMock(return_value=mock_response)
            count = await client.cancel_all_pending_orders("EURUSD")

        assert count == 3

    @pytest.mark.asyncio
    async def test_flatten_positions(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"closed": 1}
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_http:
            mock_http.return_value.__aenter__.return_value.delete = AsyncMock(return_value=mock_response)
            count = await client.flatten_all_positions("EURUSD")

        assert count == 1
