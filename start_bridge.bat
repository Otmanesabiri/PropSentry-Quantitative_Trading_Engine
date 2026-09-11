@echo off
:: ============================================================
:: PropSentry — Windows Host Bridge Launcher
:: ============================================================

echo [PropSentry] Starting MT5 Bridge on Windows Host...

:: Credentials
set MT5_LOGIN=112515977
set MT5_PASSWORD=!8IeXhHg
set MT5_SERVER=MetaQuotes-Demo
set BRIDGE_HOST=0.0.0.0
set BRIDGE_PORT=8765
set BRIDGE_API_KEY=changeme-secret-key

:: Install bridge dependencies if missing
python -c "import MetaTrader5, fastapi, uvicorn" 2>nul
if %errorlevel% neq 0 (
    echo [PropSentry] Installing bridge dependencies...
    pip install MetaTrader5 fastapi uvicorn httpx pydantic loguru pyyaml pytz
)

:: Launch bridge
echo [PropSentry] Bridge starting on http://0.0.0.0:%BRIDGE_PORT%
echo [PropSentry] Docker engine connects via host.docker.internal:%BRIDGE_PORT%
python -m uvicorn src.broker.mt5_bridge:app --host %BRIDGE_HOST% --port %BRIDGE_PORT% --log-level info

pause
