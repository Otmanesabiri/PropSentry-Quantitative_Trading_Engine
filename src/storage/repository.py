"""
src/storage/repository.py
=========================
Async-safe write operations for orders, snapshots, and audit events.
All writes are append-only — no UPDATE/DELETE on order or audit records.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from loguru import logger
from sqlalchemy.orm import Session

from src.core.schemas.orders import OrderIntent
from src.storage.models import AuditEvent, DailySnapshot, OrderRecord


class TradingRepository:
    """
    Persistence layer for the trading engine.

    Parameters
    ----------
    session : SQLAlchemy Session (injected for testability).
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

    def save_order(self, intent: OrderIntent) -> None:
        """Persist a new OrderIntent (append-only)."""
        record = OrderRecord(
            order_id=str(intent.order_id),
            created_at=intent.created_at,
            symbol=intent.symbol,
            side=intent.side.value,
            order_type=intent.order_type.value,
            entry_price=intent.entry_price,
            stop_loss=intent.stop_loss,
            take_profit=intent.take_profit,
            lot_size=intent.lot_size,
            risk_usd=intent.risk_usd,
            risk_pct=intent.risk_pct,
            risk_reward_ratio=intent.risk_reward_ratio,
            signal_id=str(intent.signal_id),
            magic_number=intent.magic_number,
            status=intent.status.value,
            broker_ticket=intent.broker_ticket,
            operator_approved_at=intent.operator_approved_at,
        )
        self._session.add(record)
        self._session.commit()
        logger.debug(f"[REPO] Order saved: {intent.order_id}")

    def update_order_status(
        self,
        order_id: UUID,
        status: str,
        broker_ticket: int | None = None,
    ) -> None:
        """Update the status (and optionally ticket) of an existing order."""
        record = self._session.query(OrderRecord).filter_by(
            order_id=str(order_id)
        ).first()
        if record:
            record.status = status
            if broker_ticket is not None:
                record.broker_ticket = broker_ticket
            self._session.commit()

    # ------------------------------------------------------------------
    # Daily snapshots
    # ------------------------------------------------------------------

    def save_daily_snapshot(self, snapshot: DailySnapshot) -> None:
        """Insert or update the daily snapshot record."""
        existing = self._session.query(DailySnapshot).filter_by(
            snapshot_date=snapshot.snapshot_date
        ).first()
        if existing is None:
            self._session.add(snapshot)
        else:
            # Merge closing data into existing record
            existing.balance_close = snapshot.balance_close
            existing.equity_close = snapshot.equity_close
            existing.daily_pnl = snapshot.daily_pnl
            existing.kill_switch_triggered = snapshot.kill_switch_triggered
            existing.trades_count = snapshot.trades_count
        self._session.commit()

    # ------------------------------------------------------------------
    # Audit events
    # ------------------------------------------------------------------

    def log_event(
        self,
        event_type: str,
        details: str,
        severity: str = "INFO",
        order_id: UUID | None = None,
    ) -> None:
        """Append an audit event (append-only)."""
        event = AuditEvent(
            occurred_at=datetime.utcnow().replace(tzinfo=__import__("pytz").utc),
            event_type=event_type,
            severity=severity,
            details=details[:2000],  # Truncate to prevent abuse
            order_id=str(order_id) if order_id else None,
        )
        self._session.add(event)
        self._session.commit()
