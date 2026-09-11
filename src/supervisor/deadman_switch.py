"""
src/supervisor/deadman_switch.py
================================
Dead-man's switch — periodic HTTP ping to an external uptime service.

If the engine process freezes or crashes, pings stop and the external
service can alert the operator via SMS/email/webhook.

Compatible with UptimeRobot, Healthchecks.io, Cronitor, etc.
"""

from __future__ import annotations

import asyncio

import httpx
from loguru import logger


class DeadmanSwitch:
    """
    Sends periodic HTTP GET pings to an external health-check URL.

    Parameters
    ----------
    ping_url        : Full URL to ping (e.g., Healthchecks.io unique URL).
    interval_s      : Seconds between pings (default 300 = 5 min).
    timeout_s       : HTTP request timeout.
    """

    def __init__(
        self,
        ping_url: str,
        interval_s: int = 300,
        timeout_s: float = 10.0,
    ) -> None:
        self._url = ping_url
        self._interval = interval_s
        self._timeout = timeout_s
        self._running = False

    async def run(self) -> None:
        """Async loop that pings the external service every interval_s."""
        self._running = True
        logger.info(f"[DEADMAN] Started. Pinging {self._url} every {self._interval}s.")

        while self._running:
            await self._ping()
            await asyncio.sleep(self._interval)

    async def stop(self) -> None:
        self._running = False

    async def _ping(self) -> None:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(self._url)
                logger.debug(f"[DEADMAN] Ping OK | Status: {response.status_code}")
        except Exception as exc:
            logger.error(f"[DEADMAN] Ping FAILED: {exc}")
