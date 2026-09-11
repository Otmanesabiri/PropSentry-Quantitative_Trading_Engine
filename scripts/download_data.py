"""
scripts/download_data.py
========================
Downloads historical OHLCV data directly from MetaTrader 5 and saves
to data/historical/{symbol}_{timeframe}.parquet and .csv for backtesting.

Usage:
    python scripts/download_data.py --symbol EURUSD --timeframe M15 --bars 50000
    python scripts/download_data.py --symbol GBPUSD --timeframe M15 --bars 30000
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
from loguru import logger

from config.loader import get_settings


TIMEFRAME_MAP = {
    "M1": 1,
    "M5": 5,
    "M15": 15,
    "M30": 30,
    "H1": 16385,
    "H4": 16388,
    "D1": 16408,
}


def download_historical_data(symbol: str = "EURUSD", timeframe: str = "M15", bars: int = 50000) -> Path:
    """Download OHLCV bars from MT5 and save to data/historical/."""
    try:
        import MetaTrader5 as mt5
    except ImportError:
        logger.error("MetaTrader5 package not installed. Run: pip install MetaTrader5")
        sys.exit(1)

    cfg = get_settings()

    login = cfg.broker.login or 0
    password = cfg.broker.password or ""
    server = cfg.broker.server or ""

    if login:
        connected = mt5.initialize(login=login, password=password, server=server)
    else:
        connected = mt5.initialize()

    if not connected:
        err = mt5.last_error()
        logger.error(f"Failed to connect to MetaTrader 5: {err}")
        sys.exit(1)

    tf_const = TIMEFRAME_MAP.get(timeframe.upper(), mt5.TIMEFRAME_M15)

    logger.info(f"[DOWNLOAD] Fetching {bars:,} bars of {symbol} {timeframe} from MT5...")
    rates = mt5.copy_rates_from_pos(symbol, tf_const, 0, bars)
    mt5.shutdown()

    if rates is None or len(rates) == 0:
        logger.error(f"No rates returned for {symbol} {timeframe}. Check symbol name in MT5.")
        sys.exit(1)

    df = pd.DataFrame(rates)
    df["timestamp"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df["volume"] = df["tick_volume"].astype(float)

    cols = ["timestamp", "open", "high", "low", "close", "volume"]
    df = df[cols].sort_values("timestamp").reset_index(drop=True)

    out_dir = Path("data/historical")
    out_dir.mkdir(parents=True, exist_ok=True)

    csv_path = out_dir / f"{symbol}_{timeframe}.csv"
    parquet_path = out_dir / f"{symbol}_{timeframe}.parquet"

    df.to_csv(csv_path, index=False)
    try:
        df.to_parquet(parquet_path, index=False)
        logger.info(f"[DOWNLOAD] Saved Parquet: {parquet_path}")
    except Exception as exc:
        logger.warning(f"Could not save parquet (fastparquet/pyarrow missing): {exc}")

    logger.info(f"[DOWNLOAD] Saved CSV: {csv_path}")
    logger.info(
        f"[DOWNLOAD] ✅ {len(df):,} bars successfully downloaded!\n"
        f"           Range: {df['timestamp'].min().strftime('%Y-%m-%d')} → "
        f"{df['timestamp'].max().strftime('%Y-%m-%d')}"
    )

    return csv_path


def main() -> None:
    parser = argparse.ArgumentParser(description="PropSentry MT5 Historical Data Downloader")
    parser.add_argument("--symbol", default="EURUSD", help="Symbol (default: EURUSD)")
    parser.add_argument("--timeframe", default="M15", help="Timeframe (default: M15)")
    parser.add_argument("--bars", type=int, default=50000, help="Number of bars to fetch (default: 50,000)")
    args = parser.parse_args()

    download_historical_data(args.symbol, args.timeframe, args.bars)


if __name__ == "__main__":
    main()
