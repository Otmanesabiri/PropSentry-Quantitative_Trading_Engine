"""
scripts/run_paper.py
====================
Paper (shadow) trading on a demo MT5 account.

Same as live mode but:
  - All Telegram alerts are auto-approved (no operator confirmation).
  - Useful for validating live engine logic without real risk.

Usage:
    python scripts/run_paper.py
    # or override Docker command:
    docker compose run engine python scripts/run_paper.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger

from config.loader import get_settings
from src.engine.live_engine import LiveEngine


async def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cfg = get_settings()

    logger.remove()
    logger.add(sys.stdout, level=cfg.system.log_level, colorize=True,
               format="<green>{time:HH:mm:ss}</green> | <level>{level:<8}</level> | {message}")
    logger.add("logs/paper.log", level="DEBUG", rotation="1 day", retention="7 days")

    logger.info("═" * 55)
    logger.info("  PropSentry — PAPER Trading Mode")
    logger.info("  All signals auto-approved. No real money.")
    logger.info(f"  Symbol: {cfg.broker.symbol} | TF: {cfg.engine.bar_timeframe}")
    logger.info("═" * 55)

    engine = LiveEngine(mode="paper")
    await engine.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("[PAPER] Shutdown.")
    except Exception as exc:
        logger.critical(f"[PAPER] Fatal error: {exc}")
        sys.exit(1)
