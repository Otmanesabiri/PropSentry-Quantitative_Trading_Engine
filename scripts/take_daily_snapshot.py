"""
scripts/take_daily_snapshot.py
================================
Extracts broker account state at 00:00 CE(S)T and saves to the database.
Run via system scheduler (cron / Windows Task Scheduler) at 00:01 CE(S)T.

Usage:
    python scripts/take_daily_snapshot.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger
from config.loader import get_settings
from src.core.broker_clock import broker_date
from src.storage.db import get_session, init_db
from src.storage.models import DailySnapshot
from src.storage.repository import TradingRepository


def main() -> None:
    cfg = get_settings()
    init_db(cfg.database.url)

    today = str(broker_date())
    logger.info(f"[SNAPSHOT] Taking daily snapshot for broker date: {today}")
    logger.warning("[SNAPSHOT] MT5 connection wiring pending Phase 4.")


if __name__ == "__main__":
    main()
