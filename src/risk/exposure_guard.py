"""
src/risk/exposure_guard.py
==========================
Global exposure guard — ensures maximum 1 open position at any time.

Queries the live broker state to verify the actual position count,
not just the local in-memory count. This prevents ghost position
scenarios where local state diverges from broker reality.
"""

from __future__ import annotations

from typing import Protocol

from loguru import logger

from src.core.exceptions import MaxPositionsGateError


class PositionProvider(Protocol):
    """Minimal interface for querying open positions from the broker."""

    def get_open_positions_count(self, symbol: str | None = None) -> int:
        """Return current count of open positions (optionally filtered by symbol)."""
        ...


class ExposureGuard:
    """
    Verifies that opening a new position will not exceed the configured maximum.

    Parameters
    ----------
    position_provider : Live broker query interface (injected).
    max_positions     : Maximum allowed concurrent open positions (default 1).
    symbol            : Optional symbol filter (None = all symbols).
    """

    def __init__(
        self,
        position_provider: PositionProvider,
        max_positions: int = 1,
        symbol: str | None = None,
    ) -> None:
        self._provider = position_provider
        self._max = max_positions
        self._symbol = symbol

    def check(self) -> int:
        """
        Query the broker and return the current open positions count.

        Raises
        ------
        MaxPositionsGateError
            If the count is at or above the configured maximum.
        """
        count = self._provider.get_open_positions_count(self._symbol)
        logger.debug(f"[EXPOSURE GUARD] Open positions: {count}/{self._max}")

        if count >= self._max:
            raise MaxPositionsGateError(
                f"Open position count {count} has reached the maximum of {self._max}. "
                "No new entries allowed until existing position is closed."
            )

        return count

    @property
    def max_positions(self) -> int:
        return self._max
