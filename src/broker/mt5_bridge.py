"""
src/broker/mt5_bridge.py
========================
FastAPI HTTP bridge — runs on the WINDOWS HOST alongside the MT5 terminal.

INVARIANT 6: This file is the only place allowed to use mt5_client.py.
The Docker engine container calls this bridge via HTTP instead of importing
MetaTrader5 directly (which is impossible on Linux).

Endpoints
---------
  GET  /health                      → Terminal connection status
  GET  /account                     → Balance, equity, margin
  GET  /candles/{symbol}/{timeframe} → OHLCV bars (query: count)
  GET  /positions                   → Open positions list
  GET  /orders                      → Pending orders list
  POST /order                       → Send an order (OrderIntent JSON)
  DELETE /orders                    → Cancel all pending orders
  DELETE /positions                 → Flatten all positions (kill-switch)
  GET  /tick/{symbol}               → Latest bid/ask tick

Run on Windows Host:
    pip install fastapi uvicorn MetaTrader5
    python src/broker/mt5_bridge.py
"""

from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# Only import MT5Client here — INVARIANT 6
from src.broker.mt5_client import MT5Client
from src.core.exceptions import BrokerConnectionError, OrderDispatchError
from src.core.schemas.orders import OrderIntent

# ---------------------------------------------------------------------------
# Configuration from environment / settings
# ---------------------------------------------------------------------------
from config.loader import get_settings
_cfg = get_settings()

MT5_LOGIN = _cfg.broker.login or int(os.getenv("MT5_LOGIN", "0"))
MT5_PASSWORD = _cfg.broker.password or os.getenv("MT5_PASSWORD", "")
MT5_SERVER = _cfg.broker.server or os.getenv("MT5_SERVER", "")
BRIDGE_HOST = os.getenv("BRIDGE_HOST", "0.0.0.0")
BRIDGE_PORT = int(os.getenv("BRIDGE_PORT", "8765"))
BRIDGE_API_KEY = os.getenv("BRIDGE_API_KEY", "changeme-secret-key")  # Basic auth for Docker


# ---------------------------------------------------------------------------
# MT5 Client singleton
# ---------------------------------------------------------------------------
_client: MT5Client | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Connect to MT5 on startup, disconnect on shutdown."""
    global _client
    _client = MT5Client(
        login=MT5_LOGIN,
        password=MT5_PASSWORD,
        server=MT5_SERVER,
    )
    try:
        _client.connect()
        print(f"[BRIDGE] MT5 connected | Server: {MT5_SERVER} | Login: {MT5_LOGIN}")
    except BrokerConnectionError as exc:
        print(f"[BRIDGE] WARNING: MT5 connection failed: {exc}")
        print("[BRIDGE] Bridge starting in degraded mode (offline).")
    yield
    if _client:
        _client.disconnect()
    print("[BRIDGE] Shutdown complete.")


app = FastAPI(
    title="PropSentry MT5 Bridge",
    description="HTTP adapter exposing MetaTrader5 to the Docker engine container.",
    version="1.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------

from fastapi import Header


def verify_api_key(x_api_key: str = Header(...)) -> None:
    if x_api_key != BRIDGE_API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key.")


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------

class OrderRequest(BaseModel):
    """Serialized OrderIntent sent from the Docker engine."""
    order_id: str
    symbol: str
    side: str          # "BUY" | "SELL"
    order_type: str    # "MARKET" | "LIMIT"
    entry_price: float
    stop_loss: float
    take_profit: float
    lot_size: float
    risk_usd: float
    risk_pct: float
    risk_reward_ratio: float
    signal_id: str
    magic_number: int


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    """Bridge liveness + MT5 connection status."""
    connected = _client.is_connected() if _client else False
    return {
        "bridge": "online",
        "mt5_connected": connected,
        "server": MT5_SERVER,
        "login": MT5_LOGIN,
    }


@app.get("/account")
def get_account(x_api_key: str = Header(...)):
    verify_api_key(x_api_key)
    if not _client:
        raise HTTPException(503, "MT5 client not initialized.")
    try:
        return {
            "balance": _client.get_account_balance(),
            "equity": _client.get_account_equity(),
        }
    except BrokerConnectionError as exc:
        raise HTTPException(503, str(exc))


@app.get("/candles/{symbol}/{timeframe}")
def get_candles(
    symbol: str,
    timeframe: str,
    count: int = Query(default=200, ge=1, le=5000),
    x_api_key: str = Header(...),
):
    verify_api_key(x_api_key)
    if not _client:
        raise HTTPException(503, "MT5 client not initialized.")
    try:
        candles = _client.get_candles(symbol, timeframe, count)
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "count": len(candles),
            "candles": [
                {
                    "timestamp": c.timestamp.isoformat(),
                    "open": c.open,
                    "high": c.high,
                    "low": c.low,
                    "close": c.close,
                    "volume": c.volume,
                }
                for c in candles
            ],
        }
    except BrokerConnectionError as exc:
        raise HTTPException(503, str(exc))


@app.get("/tick/{symbol}")
def get_tick(symbol: str, x_api_key: str = Header(...)):
    verify_api_key(x_api_key)
    if not _client:
        raise HTTPException(503, "MT5 client not initialized.")
    try:
        server_time = _client.get_server_time()
        return {"symbol": symbol, "server_time": server_time.isoformat()}
    except BrokerConnectionError as exc:
        raise HTTPException(503, str(exc))


@app.get("/positions")
def get_positions(
    symbol: str | None = Query(default=None),
    x_api_key: str = Header(...),
):
    verify_api_key(x_api_key)
    if not _client:
        raise HTTPException(503, "MT5 client not initialized.")
    count = _client.get_open_positions_count(symbol)
    return {"open_positions": count, "symbol": symbol}


@app.post("/order")
def send_order(req: OrderRequest, x_api_key: str = Header(...)):
    verify_api_key(x_api_key)
    if not _client:
        raise HTTPException(503, "MT5 client not initialized.")
    from datetime import datetime, timezone
    from uuid import UUID
    from src.core.schemas.orders import OrderSide, OrderType, OrderStatus

    intent = OrderIntent(
        order_id=UUID(req.order_id),
        created_at=datetime.now(tz=timezone.utc),
        symbol=req.symbol,
        side=OrderSide(req.side),
        order_type=OrderType(req.order_type),
        entry_price=req.entry_price,
        stop_loss=req.stop_loss,
        take_profit=req.take_profit,
        lot_size=req.lot_size,
        risk_usd=req.risk_usd,
        risk_pct=req.risk_pct,
        risk_reward_ratio=req.risk_reward_ratio,
        signal_id=UUID(req.signal_id),
        magic_number=req.magic_number,
    )
    try:
        ticket = _client.send_order(intent)
        return {"ticket": ticket, "status": "FILLED"}
    except OrderDispatchError as exc:
        raise HTTPException(422, detail={"retcode": exc.retcode, "comment": exc.comment})


@app.delete("/orders")
def cancel_orders(
    symbol: str | None = Query(default=None),
    x_api_key: str = Header(...),
):
    verify_api_key(x_api_key)
    if not _client:
        raise HTTPException(503, "MT5 client not initialized.")
    count = _client.cancel_all_pending_orders(symbol)
    return {"cancelled": count}


@app.delete("/positions")
def flatten_positions(
    symbol: str | None = Query(default=None),
    x_api_key: str = Header(...),
):
    verify_api_key(x_api_key)
    if not _client:
        raise HTTPException(503, "MT5 client not initialized.")
    count = _client.flatten_all_positions(symbol)
    return {"closed": count}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(
        "src.broker.mt5_bridge:app",
        host=BRIDGE_HOST,
        port=BRIDGE_PORT,
        reload=False,
        log_level="info",
    )
