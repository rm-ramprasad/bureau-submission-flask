"""Read validation results (for the UI and exports). No writes here."""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from dataclasses import dataclass

from sqlalchemy import Engine, func, select

from submissions.validation.tables import record_outcome, validation_finding, validation_summary

OUTCOME_ORDER = ["REJECT_FILE", "REJECT_RECORD", "REJECT_SEGMENT", "REJECT_FIELD", "WARN"]
EXPORT_COLUMNS = ["rule_id", "outcome", "segment", "field", "account_key", "party_key", "value", "message"]


@dataclass(frozen=True)
class FindingFilter:
    rule_id: str | None = None
    outcome: str | None = None
    account_key: str | None = None
    party_key: str | None = None

    def where(self, snapshot_id: int):
        f = validation_finding.c
        clauses = [f.snapshot_id == snapshot_id]
        for name in ("rule_id", "outcome", "account_key", "party_key"):
            value = getattr(self, name)
            if value:
                clauses.append(f[name] == value)
        return clauses


def summary(engine: Engine, snapshot_id: int) -> list:
    """Findings per rule and outcome, most severe first."""
    s = validation_summary.c
    with engine.connect() as conn:
        rows = conn.execute(select(s.rule_id, s.outcome, s.finding_count).where(s.snapshot_id == snapshot_id)).all()
    rank = {o: i for i, o in enumerate(OUTCOME_ORDER)}
    return sorted(rows, key=lambda r: (rank.get(r.outcome, 99), -r.finding_count, r.rule_id))


def record_counts(engine: Engine, snapshot_id: int) -> dict[str, int]:
    o = record_outcome.c
    with engine.connect() as conn:
        return dict(conn.execute(select(o.outcome, func.count()).where(o.snapshot_id == snapshot_id)
                                 .group_by(o.outcome)).all())


def findings(engine: Engine, snapshot_id: int, flt: FindingFilter, limit: int = 200, offset: int = 0) -> tuple[list, int]:
    f = validation_finding.c
    with engine.connect() as conn:
        total = conn.execute(select(func.count()).select_from(validation_finding).where(*flt.where(snapshot_id))).scalar()
        rows = conn.execute(select(validation_finding).where(*flt.where(snapshot_id))
                            .order_by(f.account_key, f.party_key, f.rule_id, f.finding_id)
                            .limit(limit).offset(offset)).all()
    return rows, total


def findings_csv(engine: Engine, snapshot_id: int, flt: FindingFilter) -> Iterator[str]:
    """CSV export, streamed in chunks."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(EXPORT_COLUMNS)
    yield buf.getvalue()
    f = validation_finding.c
    with engine.connect() as conn:
        result = conn.execution_options(stream_results=True).execute(
            select(*(f[c] for c in EXPORT_COLUMNS)).where(*flt.where(snapshot_id))
            .order_by(f.account_key, f.party_key, f.rule_id, f.finding_id))
        for chunk in result.partitions(1000):
            buf.seek(0)
            buf.truncate()
            writer.writerows(chunk)
            yield buf.getvalue()
