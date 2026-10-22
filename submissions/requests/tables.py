"""SQLAlchemy Core definition of submission_request.

Mirrors migrations/001 + 002. The SQL files are the source of truth for MySQL;
this definition is used for queries and to build throwaway test databases.
"""

from __future__ import annotations

from sqlalchemy import BigInteger, Column, Date, DateTime, ForeignKey, Index, Integer, String, Table, Text

import submissions.tenants.tables  # noqa: F401  (tenant table must exist for the foreign key)
from submissions.db import metadata

# BIGINT does not auto-increment in SQLite (used by tests); INTEGER does.
_PK = BigInteger().with_variant(Integer(), "sqlite")

submission_request = Table(
    "submission_request", metadata,
    Column("request_id", _PK, primary_key=True, autoincrement=True),
    Column("batch_id", String(40), nullable=False),
    Column("tenant_id", String(30), ForeignKey("tenant.tenant_id"), nullable=False),
    Column("run_mode", String(10), nullable=False),
    Column("as_of_date", Date, nullable=False),
    Column("submission_type", String(20), nullable=False),
    Column("bureau_code", String(20)),
    Column("snapshot_id", BigInteger),
    Column("reporting_cycle", String(2)),
    Column("requested_by", String(100)),
    Column("requested_at", DateTime, nullable=False),
    Column("status", String(12), nullable=False),
    Column("active_key", String(150), unique=True),
    Column("worker_id", String(100)),
    Column("attempt_count", Integer, nullable=False, default=0),
    Column("started_at", DateTime),
    Column("finished_at", DateTime),
    Column("output_file_name", String(255)),
    Column("output_file_path", String(500)),
    Column("exceptions_file_path", String(500)),
    Column("total_records", Integer),
    Column("accepted_records", Integer),
    Column("rejected_records", Integer),
    Column("duplicate_records", Integer),
    Column("excluded_accounts", Integer),        # loans removed by user exclusions (migration 006)
    Column("overridden_accounts", Integer),      # loans with user overrides
    Column("error_message", Text),
    Index("ix_submission_request_batch", "batch_id"),
    Index("ix_submission_request_status", "status", "requested_at"),
    Index("ix_submission_request_tenant", "tenant_id", "requested_at"),
)
