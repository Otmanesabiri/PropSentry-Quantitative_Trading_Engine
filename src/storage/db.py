"""
src/storage/db.py
=================
SQLAlchemy async engine and session factory for the local SQLite database.
Includes idempotent schema migration (create-if-not-exists).
"""

from __future__ import annotations

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from src.storage.models import Base

_engine = None
_SessionLocal = None


def init_db(database_url: str = "sqlite:///data/trading.db", echo: bool = False) -> None:
    """
    Initialize the database engine and create all tables (idempotent).

    Parameters
    ----------
    database_url : SQLAlchemy connection string.
    echo         : If True, log all SQL statements.
    """
    global _engine, _SessionLocal

    _engine = create_engine(
        database_url,
        echo=echo,
        connect_args={"check_same_thread": False},  # SQLite-specific
    )

    # Idempotent schema creation
    Base.metadata.create_all(bind=_engine)

    _SessionLocal = sessionmaker(bind=_engine, autocommit=False, autoflush=False)


def get_session() -> Session:
    """Return a new SQLAlchemy session. Caller is responsible for closing it."""
    if _SessionLocal is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _SessionLocal()
