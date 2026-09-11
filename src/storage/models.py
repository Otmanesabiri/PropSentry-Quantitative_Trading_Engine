"""
src/storage/models.py
=====================
SQLAlchemy ORM models for the local SQLite audit database.

Tables
------
- orders     : All OrderIntents dispatched (append-only audit log).
- daily_snaps: Daily equity snapshots at 00:00 CE(S)T reset.
- audits     : General system event log (gate rejections, kill-switch events, etc.).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class OrderRecord(Base):
    """Append-only record of every OrderIntent processed by the engine."""
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    side: Mapped[str] = mapped_column(String(10), nullable=False)
    order_type: Mapped[str] = mapped_column(String(20), nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    stop_loss: Mapped[float] = mapped_column(Float, nullable=False)
    take_profit: Mapped[float] = mapped_column(Float, nullable=False)
    lot_size: Mapped[float] = mapped_column(Float, nullable=False)
    risk_usd: Mapped[float] = mapped_column(Float, nullable=False)
    risk_pct: Mapped[float] = mapped_column(Float, nullable=False)
    risk_reward_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    signal_id: Mapped[str] = mapped_column(String(36), nullable=False)
    magic_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    broker_ticket: Mapped[int | None] = mapped_column(Integer, nullable=True)
    operator_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DailySnapshot(Base):
    """Daily balance/equity snapshot at 00:00 CE(S)T reset."""
    __tablename__ = "daily_snaps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_date: Mapped[str] = mapped_column(String(10), unique=True, nullable=False)  # YYYY-MM-DD
    reset_time_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    balance_open: Mapped[float] = mapped_column(Float, nullable=False)
    equity_open: Mapped[float] = mapped_column(Float, nullable=False)
    trades_count: Mapped[int] = mapped_column(Integer, default=0)
    balance_close: Mapped[float | None] = mapped_column(Float, nullable=True)
    equity_close: Mapped[float | None] = mapped_column(Float, nullable=True)
    daily_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    kill_switch_triggered: Mapped[bool] = mapped_column(Boolean, default=False)


class AuditEvent(Base):
    """General append-only system event log."""
    __tablename__ = "audits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)  # e.g., "GATE_REJECTED", "KILL_SWITCH"
    severity: Mapped[str] = mapped_column(String(10), nullable=False, default="INFO")  # INFO | WARNING | CRITICAL
    details: Mapped[str] = mapped_column(Text, nullable=False, default="")
    order_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
