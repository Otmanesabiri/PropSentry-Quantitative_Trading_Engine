"""
src/engine/parity_gate.py
=========================
Strict parity validation between backtest (replay) and live runner outputs.

INVARIANT 8: Backtest and live execution MUST use the exact same calculation
logic via runner.py. This module provides the assertion function that
verifies numerical identity between the two execution paths.

Usage
-----
    from src.engine.parity_gate import assert_output_parity
    assert_output_parity(live_output, replay_output, tolerance=1e-9)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from src.core.exceptions import ParityViolationError


@dataclass(frozen=True)
class RunnerOutput:
    """
    Snapshot of a single runner bar output for parity comparison.
    Both live and replay runners must produce this structure.
    """
    bar_index: int
    atr: float
    fvg_count: int
    sweep_count: int
    signal_generated: bool
    lot_size: float | None       # None if no signal
    entry_price: float | None
    stop_loss: float | None
    take_profit: float | None


def _floats_equal(a: float | None, b: float | None, tolerance: float) -> bool:
    """Return True if two optional floats are numerically equal within tolerance."""
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return math.isclose(a, b, rel_tol=tolerance, abs_tol=tolerance)


def assert_output_parity(
    live: RunnerOutput,
    replay: RunnerOutput,
    tolerance: float = 1e-9,
) -> None:
    """
    Assert that live and replay runner outputs are numerically identical.

    Parameters
    ----------
    live    : Output from the live execution path.
    replay  : Output from the historical replay path.
    tolerance : Maximum relative/absolute tolerance for float comparisons.

    Raises
    ------
    ParityViolationError
        If any field differs beyond the specified tolerance.
    """
    mismatches: list[str] = []

    if live.bar_index != replay.bar_index:
        mismatches.append(f"bar_index: live={live.bar_index}, replay={replay.bar_index}")

    if not _floats_equal(live.atr, replay.atr, tolerance):
        mismatches.append(f"atr: live={live.atr}, replay={replay.atr}")

    if live.fvg_count != replay.fvg_count:
        mismatches.append(f"fvg_count: live={live.fvg_count}, replay={replay.fvg_count}")

    if live.sweep_count != replay.sweep_count:
        mismatches.append(f"sweep_count: live={live.sweep_count}, replay={replay.sweep_count}")

    if live.signal_generated != replay.signal_generated:
        mismatches.append(
            f"signal_generated: live={live.signal_generated}, replay={replay.signal_generated}"
        )

    for field in ("lot_size", "entry_price", "stop_loss", "take_profit"):
        lv = getattr(live, field)
        rv = getattr(replay, field)
        if not _floats_equal(lv, rv, tolerance):
            mismatches.append(f"{field}: live={lv}, replay={rv}")

    if mismatches:
        raise ParityViolationError(
            f"Runner parity violation on bar {live.bar_index}:\n"
            + "\n".join(f"  - {m}" for m in mismatches)
        )
