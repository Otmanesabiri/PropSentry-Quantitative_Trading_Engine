"""
src/broker/bridge_client.py
============================
HTTP client used by the Docker engine to communicate with mt5_bridge.py
running on the Windows Host.

This replaces MT5Client inside the Docker container — all calls go via
HTTP to the bridge instead of importing MetaTrader5 (INVARIANT 6 preserved).

Usage
-----
    client = BridgeClient(base_url="http://host.docker.internal:8765", api_key="...")
    candles = await client.get_candles("EURUSD", "M15", 200)
    ticket  = await client.send_order(intent)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx
import pytz
from loguru import logger

from src.core.exceptions import BrokerConnectionError, OrderDispatchError
from src.core.schemas.candles import Candle
from src.core.schemas.orders import OrderIntent

_UTC = pytz.utc


class BridgeClient:
    """
    Async HTTP client for the MT5 bridge (Docker → Windows Host).

    Parameters
    ----------
    base_url : Bridge URL, e.g. "http://host.docker.internal:8765"
    api_key  : Shared secret matching BRIDGE_API_KEY on the bridge process.
    timeout_s: HTTP request timeout in seconds.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout_s: float = 10.0,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._headers = {"X-Api-Key": api_key}
        self._timeout = timeout_s

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    async def health(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout) as c:
            r = await c.get(f"{self._base}/health")
            r.raise_for_status()
            return r.json()

    async def is_connected(self) -> bool:
        try:
            data = await self.health()
            return data.get("mt5_connected", False)
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Account
    # ------------------------------------------------------------------

    async def get_account_balance(self) -> float:
        async with httpx.AsyncClient(timeout=self._timeout, headers=self._headers) as c:
            r = await c.get(f"{self._base}/account")
            self._raise_for_broker_error(r)
            return float(r.json()["balance"])

    async def get_account_equity(self) -> float:
        async with httpx.AsyncClient(timeout=self._timeout, headers=self._headers) as c:
            r = await c.get(f"{self._base}/account")
            self._raise_for_broker_error(r)
            return float(r.json()["equity"])

    # ------------------------------------------------------------------
    # Market data
    # ------------------------------------------------------------------

    async def get_candles(
        self, symbol: str, timeframe: str, count: int = 200
    ) -> list[Candle]:
        async with httpx.AsyncClient(timeout=self._timeout, headers=self._headers) as c:
            r = await c.get(
                f"{self._base}/candles/{symbol}/{timeframe}",
                params={"count": count},
            )
            self._raise_for_broker_error(r)
            data = r.json()

        candles: list[Candle] = []
        for bar in data["candles"]:
            candles.append(Candle(
                timestamp=datetime.fromisoformat(bar["timestamp"]),
                open=bar["open"],
                high=bar["high"],
                low=bar["low"],
                close=bar["close"],
                volume=bar["volume"],
                timeframe=timeframe,
            ))
        return candles

    async def get_server_time(self) -> datetime:
        async with httpx.AsyncClient(timeout=self._timeout, headers=self._headers) as c:
            r = await c.get(f"{self._base}/tick/EURUSD")
            self._raise_for_broker_error(r)
            return datetime.fromisoformat(r.json()["server_time"])

    # ------------------------------------------------------------------
    # Positions
    # ------------------------------------------------------------------

    async def get_open_positions_count(self, symbol: str | None = None) -> int:
        params = {"symbol": symbol} if symbol else {}
        async with httpx.AsyncClient(timeout=self._timeout, headers=self._headers) as c:
            r = await c.get(f"{self._base}/positions", params=params)
            self._raise_for_broker_error(r)
            return int(r.json()["open_positions"])

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

    async def send_order(self, intent: OrderIntent) -> int:
        payload = {
            "order_id": str(intent.order_id),
            "symbol": intent.symbol,
            "side": intent.side.value,
            "order_type": intent.order_type.value,
            "entry_price": intent.entry_price,
            "stop_loss": intent.stop_loss,
            "take_profit": intent.take_profit,
            "lot_size": intent.lot_size,
            "risk_usd": intent.risk_usd,
            "risk_pct": intent.risk_pct,
            "risk_reward_ratio": intent.risk_reward_ratio,
            "signal_id": str(intent.signal_id),
            "magic_number": intent.magic_number,
        }
        async with httpx.AsyncClient(timeout=self._timeout, headers=self._headers) as c:
            r = await c.post(f"{self._base}/order", json=payload)
            if r.status_code == 422:
                detail = r.json().get("detail", {})
                raise OrderDispatchError(
                    retcode=detail.get("retcode", -1),
                    comment=detail.get("comment", "Bridge dispatch error"),
                )
            self._raise_for_broker_error(r)
            return int(r.json()["ticket"])

    async def cancel_all_pending_orders(self, symbol: str | None = None) -> int:
        params = {"symbol": symbol} if symbol else {}
        async with httpx.AsyncClient(timeout=self._timeout, headers=self._headers) as c:
            r = await c.delete(f"{self._base}/orders", params=params)
            self._raise_for_broker_error(r)
            return int(r.json()["cancelled"])

    async def flatten_all_positions(self, symbol: str | None = None) -> int:
        params = {"symbol": symbol} if symbol else {}
        async with httpx.AsyncClient(timeout=self._timeout, headers=self._headers) as c:
            r = await c.delete(f"{self._base}/positions", params=params)
            self._raise_for_broker_error(r)
            return int(r.json()["closed"])

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _raise_for_broker_error(self, response: httpx.Response) -> None:
        if response.status_code == 503:
            raise BrokerConnectionError(f"Bridge returned 503: {response.text}")
        if response.status_code == 403:
            raise BrokerConnectionError("Bridge API key rejected.")
        response.raise_for_status()
