"""
src/agent/news_fetcher.py
=========================
Economic calendar ingestion from Finnhub / ForexFactory.
Fetches upcoming High-Impact events and caches them for the news gate.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any

import httpx
import pytz
from loguru import logger
from pydantic import BaseModel, Field

_UTC = pytz.utc


class NewsEvent(BaseModel):
    """A single economic calendar event."""
    model_config = {"frozen": True}

    event_id: str
    title: str
    currency: str
    impact: str                # "HIGH_IMPACT" | "MEDIUM_IMPACT" | "LOW_IMPACT"
    scheduled_at: datetime     # UTC
    actual: str | None = None
    forecast: str | None = None


class NewsFetcher:
    """
    Fetches economic calendar events from Finnhub.

    Parameters
    ----------
    api_key         : Finnhub API key.
    monitored_impact: Impact levels to track (default: ["HIGH_IMPACT"]).
    timeout_s       : HTTP request timeout in seconds.
    """

    FINNHUB_URL = "https://finnhub.io/api/v1/calendar/economic"

    def __init__(
        self,
        api_key: str,
        monitored_impact: list[str] | None = None,
        timeout_s: float = 10.0,
    ) -> None:
        self._api_key = api_key
        self._monitored = monitored_impact or ["HIGH_IMPACT"]
        self._timeout = timeout_s
        self._cache: list[NewsEvent] = []
        self._last_fetched: datetime | None = None

    async def fetch(self, from_dt: datetime, to_dt: datetime) -> list[NewsEvent]:
        """
        Fetch economic events in the given UTC time range.

        Returns cached data if fetched within the last hour.
        """
        now_utc = datetime.now(tz=_UTC)
        if not self._api_key or not self._api_key.strip():
            return []

        if self._last_fetched and (now_utc - self._last_fetched) < timedelta(hours=1):
            return self._cache


        params = {
            "from": from_dt.strftime("%Y-%m-%d"),
            "to": to_dt.strftime("%Y-%m-%d"),
            "token": self._api_key,
        }

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(self.FINNHUB_URL, params=params)
                response.raise_for_status()
                data = response.json()
        except Exception as exc:
            logger.warning(f"[NEWS FETCHER] Failed to fetch calendar: {exc}")
            return self._cache  # Return stale cache on failure

        events: list[NewsEvent] = []
        for item in data.get("economicCalendar", []):
            impact_raw = item.get("impact", "").upper()
            impact = "HIGH_IMPACT" if impact_raw in ("HIGH", "3") else \
                     "MEDIUM_IMPACT" if impact_raw in ("MEDIUM", "2") else "LOW_IMPACT"

            if impact not in self._monitored:
                continue

            try:
                events.append(NewsEvent(
                    event_id=str(item.get("id", "")),
                    title=item.get("event", "Unknown"),
                    currency=item.get("currency", ""),
                    impact=impact,
                    scheduled_at=datetime.fromisoformat(
                        item.get("time", "").replace("Z", "+00:00")
                    ),
                ))
            except Exception as exc:
                logger.debug(f"[NEWS FETCHER] Skipped malformed event: {exc}")

        self._cache = events
        self._last_fetched = now_utc
        logger.info(f"[NEWS FETCHER] Fetched {len(events)} high-impact events.")
        return events
