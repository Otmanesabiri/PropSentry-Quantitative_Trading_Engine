"""
src/supervisor/heartbeat.py
===========================
Internal heartbeat monitor detecting frozen event loops and stuck tasks.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime

from loguru import logger


class Heartbeat:
    """
    Monitors that the main async loop is alive and responsive.

    Raises an alert if the loop misses more than max_missed heartbeats.

    Parameters
    ----------
    interval_s          : Seconds between heartbeats.
    max_missed          : Max consecutive missed beats before alert.
    on_stuck_callback   : Callable invoked when engine appears stuck.
    """

    def __init__(
        self,
        interval_s: int = 30,
        max_missed: int = 3,
        on_stuck_callback: object = None,
    ) -> None:
        self._interval = interval_s
        self._max_missed = max_missed
        self._on_stuck = on_stuck_callback
        self._last_beat: float = time.monotonic()
        self._missed: int = 0
        self._running = False

    def beat(self) -> None:
        """Record a heartbeat. Call this from the main loop each iteration."""
        self._last_beat = time.monotonic()
        self._missed = 0

    async def monitor(self) -> None:
        """Async monitoring loop — run as a background task."""
        self._running = True
        while self._running:
            await asyncio.sleep(self._interval)
            elapsed = time.monotonic() - self._last_beat

            if elapsed > self._interval * 1.5:
                self._missed += 1
                logger.warning(
                    f"[HEARTBEAT] Missed beat #{self._missed} | "
                    f"Last beat: {elapsed:.1f}s ago"
                )
                if self._missed >= self._max_missed and self._on_stuck:
                    logger.critical("[HEARTBEAT] Engine appears stuck — triggering alert.")
                    await self._on_stuck()  # type: ignore[misc]
            else:
                logger.debug(f"[HEARTBEAT] OK | Last beat: {elapsed:.1f}s ago")

    def stop(self) -> None:
        self._running = False
