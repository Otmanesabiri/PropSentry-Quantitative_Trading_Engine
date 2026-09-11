"""
src/agent/news_gate.py
======================
News blackout gate — blocks trading within ±30 min of High-Impact news.

Queries cached NewsEvent list from NewsFetcher and returns
a boolean indicating whether the blackout window is currently active.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytz
from loguru import logger

from src.agent.news_fetcher import NewsFetcher, NewsEvent

_UTC = pytz.utc


class NewsGate:
    """
    Checks if the current time falls within a news blackout window.

    Parameters
    ----------
    fetcher           : NewsFetcher instance.
    lookahead_minutes : Minutes before a news event to block (default 30).
    lookbehind_minutes: Minutes after a news event to block (default 30).
    """

    def __init__(
        self,
        fetcher: NewsFetcher,
        lookahead_minutes: int = 30,
        lookbehind_minutes: int = 30,
    ) -> None:
        self._fetcher = fetcher
        self._lookahead = timedelta(minutes=lookahead_minutes)
        self._lookbehind = timedelta(minutes=lookbehind_minutes)

    async def is_blackout_active(
        self,
        reference: datetime | None = None,
        currencies: list[str] | None = None,
    ) -> bool:
        """
        Return True if the current time is within a news blackout window.

        Parameters
        ----------
        reference  : UTC reference time (defaults to now).
        currencies : Currency codes to filter (e.g., ['USD', 'EUR']). None = all.
        """
        now = reference or datetime.now(tz=_UTC)
        window_start = now - timedelta(days=1)
        window_end = now + timedelta(days=1)

        events = await self._fetcher.fetch(window_start, window_end)

        for event in events:
            if currencies and event.currency not in currencies:
                continue

            blackout_start = event.scheduled_at - self._lookahead
            blackout_end = event.scheduled_at + self._lookbehind

            if blackout_start <= now <= blackout_end:
                logger.info(
                    f"[NEWS GATE] Blackout active for event: '{event.title}' "
                    f"({event.currency}) @ {event.scheduled_at.isoformat()} UTC"
                )
                return True

        return False

    async def get_next_event(
        self,
        reference: datetime | None = None,
    ) -> NewsEvent | None:
        """Return the next upcoming High-Impact event, or None if none found."""
        now = reference or datetime.now(tz=_UTC)
        events = await self._fetcher.fetch(now, now + timedelta(days=7))
        future = [e for e in events if e.scheduled_at > now]
        return min(future, key=lambda e: e.scheduled_at, default=None)
