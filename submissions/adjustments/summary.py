"""Read what the adjustments did to a snapshot (from the frozen copy and its effects)."""

from __future__ import annotations

from sqlalchemy import Engine, func, select, text

from submissions.adjustments.models import ApplyResult
from submissions.adjustments.tables import snapshot_adjustment_effect as e

_V_NOT_FOUND = "SELECT COUNT(*) FROM v_snapshot_adjustment WHERE snapshot_id = :snapshot_id AND loans_affected = 0"


def applied_summary(engine: Engine, snapshot_id: int) -> ApplyResult:
    excluded_keys = select(e.c.account_key).where(e.c.snapshot_id == snapshot_id, e.c.action == "EXCLUDE")
    with engine.connect() as conn:
        excluded = conn.execute(select(func.count(e.c.account_key.distinct())).where(
            e.c.snapshot_id == snapshot_id, e.c.action == "EXCLUDE")).scalar()
        overridden = conn.execute(select(func.count(e.c.account_key.distinct())).where(   # excluded loans don't count
            e.c.snapshot_id == snapshot_id, e.c.action == "OVERRIDE", e.c.account_key.notin_(excluded_keys))).scalar()
        not_found = conn.execute(text(_V_NOT_FOUND), {"snapshot_id": snapshot_id}).scalar()
    return ApplyResult(int(excluded), int(overridden), int(not_found))
