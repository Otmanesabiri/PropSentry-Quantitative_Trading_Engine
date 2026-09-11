"""
tests/test_parity.py
====================
Validates INVARIANT 8: Backtest and live runner produce identical outputs
for identical candle inputs.

Uses RunnerOutput + assert_output_parity to verify numerical identity.
"""

from __future__ import annotations

import pytest

from src.engine.parity_gate import RunnerOutput, assert_output_parity
from src.core.exceptions import ParityViolationError


def make_output(**kwargs) -> RunnerOutput:
    defaults = dict(
        bar_index=1,
        atr=0.0015,
        fvg_count=2,
        sweep_count=1,
        signal_generated=True,
        lot_size=0.10,
        entry_price=1.10000,
        stop_loss=1.09800,
        take_profit=1.10400,
    )
    defaults.update(kwargs)
    return RunnerOutput(**defaults)


class TestParityGate:
    def test_identical_outputs_pass(self):
        live = make_output()
        replay = make_output()
        assert_output_parity(live, replay)  # Should not raise

    def test_atr_mismatch_raises(self):
        live = make_output(atr=0.0015)
        replay = make_output(atr=0.0016)  # Different ATR
        with pytest.raises(ParityViolationError, match="atr"):
            assert_output_parity(live, replay)

    def test_lot_size_mismatch_raises(self):
        live = make_output(lot_size=0.10)
        replay = make_output(lot_size=0.11)
        with pytest.raises(ParityViolationError, match="lot_size"):
            assert_output_parity(live, replay)

    def test_entry_price_mismatch_raises(self):
        live = make_output(entry_price=1.10000)
        replay = make_output(entry_price=1.10001)
        with pytest.raises(ParityViolationError, match="entry_price"):
            assert_output_parity(live, replay)

    def test_signal_generated_mismatch_raises(self):
        live = make_output(signal_generated=True)
        replay = make_output(signal_generated=False)
        with pytest.raises(ParityViolationError, match="signal_generated"):
            assert_output_parity(live, replay)

    def test_none_lot_size_both_none_passes(self):
        live = make_output(signal_generated=False, lot_size=None, entry_price=None,
                           stop_loss=None, take_profit=None)
        replay = make_output(signal_generated=False, lot_size=None, entry_price=None,
                             stop_loss=None, take_profit=None)
        assert_output_parity(live, replay)

    def test_none_vs_value_raises(self):
        live = make_output(lot_size=None)
        replay = make_output(lot_size=0.10)
        with pytest.raises(ParityViolationError, match="lot_size"):
            assert_output_parity(live, replay)

    def test_float_tolerance_passes(self):
        """Tiny floating point epsilon should not cause a parity failure."""
        live = make_output(atr=0.001500000000001)
        replay = make_output(atr=0.001500000000002)
        assert_output_parity(live, replay, tolerance=1e-9)
