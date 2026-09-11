"""
src/supervisor/backup_service.py
=================================
Automated daily backup of the SQLite database.
Runs at 00:05 CE(S)T (5 minutes after daily reset) to capture the full day.
Retains up to 14 backups and prunes older files automatically.
"""

from __future__ import annotations

import asyncio
import shutil
from datetime import datetime
from pathlib import Path

import pytz
from loguru import logger

from src.core.broker_clock import get_broker_now

_BROKER_TZ = pytz.timezone("Europe/Prague")


class BackupService:
    """
    Copies the SQLite database to a daily backup file.

    Parameters
    ----------
    db_path         : Path to the source SQLite database file.
    backup_dir      : Directory where backups are stored.
    retention_days  : Number of backups to keep (oldest pruned first).
    """

    def __init__(
        self,
        db_path: str = "data/trading.db",
        backup_dir: str = "data/backups",
        retention_days: int = 14,
    ) -> None:
        self._db_path = Path(db_path)
        self._backup_dir = Path(backup_dir)
        self._retention = retention_days

    def run_backup(self) -> Path:
        """
        Perform a backup and prune old files.

        Returns
        -------
        Path
            Path to the created backup file.
        """
        self._backup_dir.mkdir(parents=True, exist_ok=True)

        now_broker = get_broker_now()
        filename = f"trading_{now_broker.strftime('%Y%m%d_%H%M%S')}.db"
        dest = self._backup_dir / filename

        shutil.copy2(self._db_path, dest)
        logger.info(f"[BACKUP] Created: {dest}")

        self._prune_old_backups()
        return dest

    def _prune_old_backups(self) -> None:
        """Delete oldest backup files beyond the retention limit."""
        backups = sorted(
            self._backup_dir.glob("trading_*.db"),
            key=lambda p: p.stat().st_mtime,
        )
        excess = len(backups) - self._retention
        for i in range(max(0, excess)):
            backups[i].unlink()
            logger.debug(f"[BACKUP] Pruned: {backups[i].name}")

    async def run_daily(self) -> None:
        """Async loop that runs the backup once per day at startup."""
        while True:
            self.run_backup()
            await asyncio.sleep(86400)  # 24 hours
