"""SQLAlchemy Core definitions of the operations tables (mirror migrations/005_operations.sql)."""

from __future__ import annotations

from sqlalchemy import BigInteger, Column, DateTime, Index, Integer, String, Table, Text

from submissions.db import metadata

_PK = BigInteger().with_variant(Integer(), "sqlite")

worker_heartbeat = Table(
    "worker_heartbeat", metadata,
    Column("worker_id", String(100), primary_key=True),
    Column("host", String(100)),
    Column("started_at", DateTime, nullable=False),
    Column("last_seen_at", DateTime, nullable=False),
    Column("processed_count", Integer, nullable=False, default=0),
    Index("ix_worker_heartbeat_seen", "last_seen_at"),
)

audit_event = Table(
    "audit_event", metadata,
    Column("event_id", _PK, primary_key=True, autoincrement=True),
    Column("occurred_at", DateTime, nullable=False),
    Column("actor", String(100), nullable=False),
    Column("action", String(50), nullable=False),
    Column("tenant_id", String(30)),
    Column("target", String(100)),
    Column("details", Text),
    Index("ix_audit_event_time", "occurred_at"),
    Index("ix_audit_event_tenant", "tenant_id", "occurred_at"),
)
