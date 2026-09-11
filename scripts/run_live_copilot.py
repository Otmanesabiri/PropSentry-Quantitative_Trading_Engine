"""
scripts/run_live_copilot.py
============================
Production entry point — fully wired live semi-automated copilot.

Starts the complete engine stack:
  - MT5 bridge health check
  - Equity watchdog + kill-switch
  - LLM context worker (Redis)
  - Telegram approval copilot
  - Heartbeat + dead-man's switch
  - Main bar-by-bar loop

Usage:
    python scripts/run_live_copilot.py
    # or in Docker:
    docker compose up engine
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger

from config.loader import get_settings
from src.engine.live_engine import LiveEngine


def setup_logging(cfg) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logger.remove()  # Remove default handler
    logger.add(
        sys.stdout,
        level=cfg.system.log_level,
        format="<green>{time:HH:mm:ss}</green> | <level>{level:<8}</level> | {message}",
        colorize=True,
    )
    logger.add(
        cfg.system.log_file,
        level="DEBUG",
        rotation=cfg.system.log_rotation,
        retention=cfg.system.log_retention,
        compression="gz",
    )


async def main() -> None:
    cfg = get_settings()
    setup_logging(cfg)

    logger.info("═" * 55)
    logger.info("  PropSentry — Live Copilot Engine")
    logger.info(f"  Symbol   : {cfg.broker.symbol}")
    logger.info(f"  Account  : {cfg.broker.login} @ {cfg.broker.server}")
    logger.info(f"  Mode     : {cfg.system.environment.upper()}")
    logger.info("═" * 55)

    engine = LiveEngine(mode="live")
    await engine.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("[COPILOT] Shutdown requested by user.")
    except Exception as exc:
        logger.critical(f"[COPILOT] Fatal error: {exc}")
        sys.exit(1)
