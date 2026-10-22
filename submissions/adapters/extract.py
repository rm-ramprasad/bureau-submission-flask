"""Extract one tenant's source data into a canonical snapshot.

Strategies (tenant.extract_strategy):
  stream         read the compiled SELECTs on the source engine, insert batches into c_* on the app engine
  insert_select  source schema is on the same server as the app tables: one INSERT ... SELECT per entity
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import Engine, delete, text

from submissions.adapters.compiler import CompiledMapping, compile_mapping
from submissions.adapters.mapping import Mapping
from submissions.canonical.definition import ColumnDef
from submissions.canonical.schema import ENTITY_TABLES, MODEL
from submissions.enums import ExtractStrategy, SubmissionType
from submissions.snapshot import SnapshotRepository
from submissions.tenants.models import Tenant

BATCH_SIZE = 1000


class ExtractError(Exception):
    pass


@dataclass(frozen=True)
class ExtractResult:
    snapshot_id: int
    mapping_version: str
    row_counts: dict[str, int]


def coerce(col: ColumnDef, value):
    """Convert a source value to the canonical column type (drivers differ, e.g. SQLite returns dates as text)."""
    if value is None:
        return None
    try:
        if col.type in ("string", "code"):
            return value if isinstance(value, str) else str(value)
        if col.type == "date":
            if isinstance(value, datetime):
                return value.date()
            return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])
        if col.type == "decimal":
            return value if isinstance(value, Decimal) else Decimal(str(value))
        return int(value)
    except (ValueError, TypeError, InvalidOperation) as exc:
        raise ExtractError(f"cannot convert {value!r} to {col.type} for column {col.name}") from exc


def _params(compiled: CompiledMapping, entity: str, as_of_date: date) -> dict:
    return {"as_of_date": as_of_date} if compiled.uses_as_of(entity) else {}


def _stream(entity: str, sql: str, params: dict, snapshot_id: int, source: Engine, app: Engine) -> int:
    table = ENTITY_TABLES[entity]
    cols = MODEL.entities[entity].columns
    count = 0
    with source.connect() as src, app.begin() as dst:
        result = src.execution_options(stream_results=True).execute(text(sql), params)
        for chunk in result.partitions(BATCH_SIZE):
            rows = []
            for r in chunk:
                m = r._mapping
                try:
                    rows.append({"snapshot_id": snapshot_id, **{c.name: coerce(c, m[c.name]) for c in cols}})
                except ExtractError as exc:
                    raise ExtractError(f"{entity}: {exc}") from exc
            dst.execute(table.insert(), rows)
            count += len(rows)
    return count


def _insert_select(entity: str, sql: str, params: dict, snapshot_id: int, app: Engine) -> int:
    table = ENTITY_TABLES[entity]
    names = [c.name for c in MODEL.entities[entity].columns]
    stmt = (f"INSERT INTO {table.name} (snapshot_id, {', '.join(names)})\n"
            f"SELECT :snapshot_id, {', '.join('q.' + n for n in names)}\nFROM (\n{sql}\n) q")
    with app.begin() as conn:
        return conn.execute(text(stmt), {**params, "snapshot_id": snapshot_id}).rowcount


def purge_rows(app: Engine, snapshot_id: int) -> None:
    """Delete a snapshot's canonical rows (the snapshot header stays)."""
    with app.begin() as conn:
        for table in ENTITY_TABLES.values():
            conn.execute(delete(table).where(table.c.snapshot_id == snapshot_id))


def run_extract(*, tenant: Tenant, mapping: Mapping, submission_type: SubmissionType | str, as_of_date: date,
                app_engine: Engine, source_engine: Engine, snapshots: SnapshotRepository, owner: str,
                batch_id: str | None = None) -> ExtractResult:
    """Create a snapshot and fill its canonical tables. On failure the rows are removed, the snapshot is FAILED."""
    compiled = compile_mapping(mapping, tenant.source_schema)
    snapshot_id = snapshots.create(tenant.tenant_id, submission_type, as_of_date, batch_id, compiled.version, owner)
    counts = {name: 0 for name in MODEL.entities}
    try:
        for entity, sql in compiled.sql.items():
            params = _params(compiled, entity, as_of_date)
            try:
                if tenant.extract_strategy is ExtractStrategy.INSERT_SELECT:
                    counts[entity] = _insert_select(entity, sql, params, snapshot_id, app_engine)
                else:
                    counts[entity] = _stream(entity, sql, params, snapshot_id, source_engine, app_engine)
            except ExtractError:
                raise
            except Exception as exc:
                raise ExtractError(f"{entity}: {exc}") from exc
        snapshots.mark_extracted(snapshot_id, counts)
    except Exception as exc:
        purge_rows(app_engine, snapshot_id)
        snapshots.mark_failed(snapshot_id, str(exc))
        raise
    return ExtractResult(snapshot_id, compiled.version, counts)
