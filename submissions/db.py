"""Engine creation and the shared table metadata (SQLAlchemy Core, no ORM)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Engine, MetaData, create_engine

metadata = MetaData()


def make_engine(database_url: str, **kwargs) -> Engine:
    # pool_pre_ping avoids "MySQL server has gone away" on idle worker connections
    return create_engine(database_url, pool_pre_ping=True, future=True, **kwargs)


def utcnow() -> datetime:
    """Naive UTC timestamp; all DATETIME columns store UTC."""
    return datetime.now(UTC).replace(tzinfo=None)
