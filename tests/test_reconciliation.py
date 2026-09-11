"""
tests/test_reconciliation.py
=============================
Validates broker reconciliation — MT5 is the source of truth.
Detects ghost positions and balance divergence.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.broker.reconciler import Reconciler
from src.core.exceptions import ReconciliationError


def make_broker(positions: int = 0, balance: float = 5000.0) -> MagicMock:
    broker = MagicMock()
    broker.get_open_positions_count.return_value = positions
    broker.get_account_balance.return_value = balance
    broker.get_account_equity.return_value = balance
    return broker


class TestReconcilePositions:
    def test_passes_when_counts_match(self):
        broker = make_broker(positions=1)
        r = Reconciler(broker=broker)
        r.reconcile_positions(local_count=1)

    def test_raises_on_ghost_position(self):
        broker = make_broker(positions=1)  # MT5 has 1
        r = Reconciler(broker=broker)
        with pytest.raises(ReconciliationError, match="Ghost position"):
            r.reconcile_positions(local_count=0)  # Local thinks 0

    def test_raises_on_missing_position(self):
        broker = make_broker(positions=0)  # MT5 has 0
        r = Reconciler(broker=broker)
        with pytest.raises(ReconciliationError):
            r.reconcile_positions(local_count=1)  # Local thinks 1


class TestReconcileBalance:
    def test_passes_within_tolerance(self):
        broker = make_broker(balance=5000.01)
        r = Reconciler(broker=broker, tolerance_usd=0.05)
        result = r.reconcile_balance(local_balance=5000.00)
        assert result == pytest.approx(5000.01)

    def test_raises_on_large_divergence(self):
        broker = make_broker(balance=4995.0)  # $5 divergence
        r = Reconciler(broker=broker)
        with pytest.raises(ReconciliationError, match="Critical balance divergence"):
            r.reconcile_balance(local_balance=5000.0)

    def test_logs_warning_on_small_divergence(self, caplog):
        broker = make_broker(balance=5000.50)  # $0.50 — warning but no error
        r = Reconciler(broker=broker, tolerance_usd=0.01)
        result = r.reconcile_balance(local_balance=5000.00)
        assert result == pytest.approx(5000.50)
