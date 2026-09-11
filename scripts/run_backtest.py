"""
scripts/run_backtest.py
=======================
Historical replay using the unified BacktestEngine.

Loads Parquet/CSV data from data/historical/ and processes bar-by-bar
through the same Runner used in live mode (INVARIANT 8).

Usage:
    python scripts/run_backtest.py --symbol EURUSD --timeframe M15 --from 2024-01-01 --to 2024-06-30
    python scripts/run_backtest.py --symbol EURUSD --timeframe M15 --from 2024-01-01 --to 2024-06-30 --export results/bt.csv

Data format (data/historical/EURUSD_M15.parquet or .csv):
    timestamp (UTC ISO string), open, high, low, close, volume
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger

from config.loader import get_settings
from src.engine.backtest_engine import BacktestEngine
from src.storage.db import init_db


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        description="PropSentry Historical Backtest",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/run_backtest.py --from 2024-01-01 --to 2024-06-30
  python scripts/run_backtest.py --symbol GBPUSD --timeframe H1 --from 2023-01-01 --to 2023-12-31 --export results/gbp_2023.csv
        """,
    )
    parser.add_argument("--symbol", default="EURUSD", help="Instrument symbol (default: EURUSD)")
    parser.add_argument("--timeframe", default="M15", help="Timeframe (default: M15)")
    parser.add_argument("--from", dest="date_from", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--to", dest="date_to", required=True, help="End date YYYY-MM-DD")
    parser.add_argument("--balance", type=float, default=5000.0, help="Initial balance USD (default: 5000)")
    parser.add_argument("--export", default=None, help="Export trade log to CSV path")
    parser.add_argument("--no-auto-close", action="store_true", help="Disable TP/SL auto-close simulation")
    args = parser.parse_args()

    logger.remove()
    logger.add(sys.stdout, level="INFO",
               format="<green>{time:HH:mm:ss}</green> | <level>{level:<8}</level> | {message}",
               colorize=True)

    cfg = get_settings()
    init_db(cfg.database.url)

    engine = BacktestEngine(
        symbol=args.symbol,
        timeframe=args.timeframe,
        date_from=args.date_from,
        date_to=args.date_to,
        initial_balance=args.balance,
        auto_close=not args.no_auto_close,
    )

    try:
        report = engine.run()
    except FileNotFoundError as exc:
        logger.error(str(exc))
        sys.exit(1)

    if args.export:
        report.to_csv(args.export)
        logger.info(f"Results exported to: {args.export}")


if __name__ == "__main__":
    main()
