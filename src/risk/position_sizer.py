"""
src/risk/position_sizer.py
==========================
Deterministic position sizer — INVARIANT 1 & INVARIANT 2.

Formula:
    lot_size = (balance * risk_pct) / (sl_pips * pip_value_per_lot)

The LLM NEVER touches this calculation. All inputs are rule-based.
Output is capped to ensure risk_usd ≤ 25.00 USD (0.5% of 5,000 USD).

Public API
----------
    compute_lot_size(balance, risk_pct, sl_distance_price, symbol_info) → float
    validate_risk_usd(lot_size, sl_distance_price, symbol_info, balance) → None
"""

from __future__ import annotations

import math

from src.core.exceptions import SizingCapBreached


# ---------------------------------------------------------------------------
# Symbol information needed for lot sizing
# ---------------------------------------------------------------------------

class SymbolInfo:
    """
    Minimal symbol metadata required for lot size computation.
    In live mode, populated from mt5_client.get_symbol_info().
    In backtest/test mode, constructed from known constants.
    """

    def __init__(
        self,
        symbol: str,
        tick_size: float,       # Minimum price movement (e.g., 0.00001 for EURUSD)
        tick_value: float,      # Value of one tick per standard lot in account currency
        volume_min: float,      # Minimum lot size
        volume_max: float,      # Maximum lot size
        volume_step: float,     # Lot size rounding step
    ) -> None:
        self.symbol = symbol
        self.tick_size = tick_size
        self.tick_value = tick_value
        self.volume_min = volume_min
        self.volume_max = volume_max
        self.volume_step = volume_step

    @property
    def pip_value_per_lot(self) -> float:
        """USD value of 1 pip (10 ticks) for a standard lot."""
        return self.tick_value * 10  # Standard definition: 1 pip = 10 ticks


# ---------------------------------------------------------------------------
# Core calculation
# ---------------------------------------------------------------------------

def _round_to_step(value: float, step: float) -> float:
    """Round *value* down to the nearest *step* increment."""
    return math.floor(value / step) * step


def compute_lot_size(
    balance: float,
    risk_pct: float,
    sl_distance_price: float,
    symbol_info: SymbolInfo,
) -> float:
    """
    Compute the deterministic position size for a given risk budget.

    Parameters
    ----------
    balance           : Current account balance in USD.
    risk_pct          : Risk fraction (e.g., 0.005 for 0.5%).
    sl_distance_price : Distance from entry to stop loss in price units.
    symbol_info       : Symbol metadata (tick size, tick value, volume constraints).

    Returns
    -------
    float
        Rounded lot size conforming to broker volume_step.

    Raises
    ------
    SizingCapBreached
        If the resulting risk in USD would exceed 0.5% of the initial balance
        (hardcoded floor of 25 USD based on 5,000 USD account).
    ValueError
        If SL distance is zero or negative.
    """
    if sl_distance_price <= 0:
        raise ValueError(f"SL distance must be positive, got {sl_distance_price}.")

    max_risk_usd = balance * risk_pct

    # Convert price SL distance to pips
    sl_pips = sl_distance_price / symbol_info.tick_size / 10  # ticks → pips

    # Lot size formula
    raw_lot = max_risk_usd / (sl_pips * symbol_info.pip_value_per_lot)

    # Round down to volume step
    lot_size = _round_to_step(raw_lot, symbol_info.volume_step)

    # Clamp to broker limits
    lot_size = max(symbol_info.volume_min, min(lot_size, symbol_info.volume_max))

    # Final risk verification (INVARIANT 2)
    actual_risk_usd = sl_pips * symbol_info.pip_value_per_lot * lot_size
    max_allowed = balance * 0.005  # Hard cap: 0.5% of current balance

    if actual_risk_usd > max_allowed + 0.01:  # 1-cent tolerance for float rounding
        raise SizingCapBreached(
            f"Computed risk {actual_risk_usd:.2f} USD exceeds cap of "
            f"{max_allowed:.2f} USD (0.5% of {balance:.2f} USD balance). "
            f"Lot size: {lot_size}, SL pips: {sl_pips:.1f}"
        )

    return lot_size


def validate_risk_usd(
    lot_size: float,
    sl_distance_price: float,
    symbol_info: SymbolInfo,
    balance: float,
) -> None:
    """
    Post-hoc validation: confirm that a given lot_size does not breach INVARIANT 2.
    Call this before dispatching any order as a final safety check.

    Raises
    ------
    SizingCapBreached
        If the lot/SL combination would risk more than 0.5% of balance.
    """
    sl_pips = sl_distance_price / symbol_info.tick_size / 10
    actual_risk_usd = sl_pips * symbol_info.pip_value_per_lot * lot_size
    max_allowed = balance * 0.005

    if actual_risk_usd > max_allowed + 0.01:
        raise SizingCapBreached(
            f"Pre-dispatch risk validation failed: {actual_risk_usd:.2f} USD > "
            f"{max_allowed:.2f} USD cap."
        )
