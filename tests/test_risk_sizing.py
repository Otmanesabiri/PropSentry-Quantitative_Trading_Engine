"""
tests/test_risk_sizing.py
=========================
Validates INVARIANT 2: Risk per trade strictly capped at 0.5% of balance (≤25 USD).
"""

from __future__ import annotations

import pytest

from src.core.exceptions import SizingCapBreached
from src.risk.position_sizer import SymbolInfo, compute_lot_size, validate_risk_usd

# Standard EURUSD symbol info for testing
EURUSD = SymbolInfo(
    symbol="EURUSD",
    tick_size=0.00001,
    tick_value=1.0,      # $1 per tick per standard lot
    volume_min=0.01,
    volume_max=100.0,
    volume_step=0.01,
)


class TestComputeLotSize:
    def test_standard_20pip_sl(self):
        """20-pip SL on 5000 USD balance → max $25 risk → 0.125 std lots."""
        lot = compute_lot_size(
            balance=5000.0,
            risk_pct=0.005,
            sl_distance_price=0.00200,  # 20 pips
            symbol_info=EURUSD,
        )
        # Expected: (5000 * 0.005) / (20 * 10) = 25 / 200 = 0.125 → 0.12 (stepped)
        assert 0.01 <= lot <= 0.13
        assert round(lot / EURUSD.volume_step, 8) % 1 == 0.0  # Must be on step

    def test_risk_never_exceeds_cap(self):
        """Regardless of SL, risk must never exceed 0.5% of balance."""
        for sl_pips in [5, 10, 20, 50, 100]:
            lot = compute_lot_size(
                balance=5000.0,
                risk_pct=0.005,
                sl_distance_price=sl_pips * EURUSD.tick_size * 10,
                symbol_info=EURUSD,
            )
            actual_risk = (sl_pips) * EURUSD.pip_value_per_lot * lot
            assert actual_risk <= 25.01, (
                f"Risk {actual_risk:.2f} USD exceeds 25 USD cap for {sl_pips}-pip SL, "
                f"lot={lot}"
            )

    def test_min_lot_floor_respected(self):
        """Very tight SL might produce lot below minimum — should clamp to volume_min."""
        lot = compute_lot_size(
            balance=5000.0,
            risk_pct=0.005,
            sl_distance_price=0.00001,  # 0.1 pip — extreme
            symbol_info=EURUSD,
        )
        assert lot >= EURUSD.volume_min

    def test_raises_on_zero_sl(self):
        with pytest.raises(ValueError, match="SL distance must be positive"):
            compute_lot_size(5000.0, 0.005, 0.0, EURUSD)

    def test_lot_is_rounded_to_step(self):
        lot = compute_lot_size(5000.0, 0.005, 0.00150, EURUSD)
        remainder = round(lot / EURUSD.volume_step, 8) % 1
        assert remainder == 0.0


class TestValidateRiskUsd:
    def test_passes_within_cap(self):
        validate_risk_usd(
            lot_size=0.10,
            sl_distance_price=0.00200,  # 20 pips
            symbol_info=EURUSD,
            balance=5000.0,
        )  # 20 pips * $10/pip * 0.10 lot = $20 ≤ $25

    def test_raises_when_over_cap(self):
        with pytest.raises(SizingCapBreached):
            validate_risk_usd(
                lot_size=10.0,  # Clearly too large
                sl_distance_price=0.00200,
                symbol_info=EURUSD,
                balance=5000.0,
            )
