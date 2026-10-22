"""Audit trail: who did what, when (UI actions, scheduler, recovery, purge)."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import datetime

from sqlalchemy import Engine, select

from submissions.db import utcnow
from submissions.ops.tables import audit_event

log = logging.getLogger(__name__)


def record(engine: Engine, actor: str, action: str, *, tenant_id: str | None = None, target: str | None = None,
           clock: Callable[[], datetime] = utcnow, **details) -> None:
    """Write one audit event. Auditing must never break the action itself, so failures are only logged."""
    try:
        with engine.begin() as conn:
            conn.execute(audit_event.insert().values(
                occurred_at=clock(), actor=actor[:100], action=action[:50], tenant_id=tenant_id,
                target=None if target is None else str(target)[:100],
                details=json.dumps(details, default=str, sort_keys=True) if details else None))
    except Exception:  # noqa: BLE001
        log.exception("Could not write audit event %s by %s", action, actor)


def recent(engine: Engine, limit: int = 100, tenant_id: str | None = None, action: str | None = None,
           actor: str | None = None, target: str | None = None) -> list:
    q = select(audit_event).order_by(audit_event.c.event_id.desc()).limit(limit)
    for col, value in ((audit_event.c.tenant_id, tenant_id), (audit_event.c.action, action),
                       (audit_event.c.actor, actor), (audit_event.c.target, target)):
        if value:
            q = q.where(col == value)
    with engine.connect() as conn:
        return conn.execute(q).all()


def choices(engine: Engine, tenant_id: str | None = None) -> tuple[list[str], list[str]]:
    """Distinct actions and actors (for the Audit page filters)."""
    with engine.connect() as conn:
        def distinct(col):
            q = select(col).distinct().order_by(col)
            if tenant_id:
                q = q.where(audit_event.c.tenant_id == tenant_id)
            return list(conn.execute(q).scalars())
        return distinct(audit_event.c.action), distinct(audit_event.c.actor)
