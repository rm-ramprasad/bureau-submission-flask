"""Dry run for onboarding: extract + validate one tenant into a trial snapshot and report on data quality,
without creating a submission request. The trial snapshot is deleted afterwards unless --keep is given.

Usage:  python -m submissions.onboarding.dry_run --tenant nbfc_002 [--as-of 2026-09-30] [--keep] [--json]
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from sqlalchemy import Engine, delete, func, select

from submissions.adapters.extract import run_extract
from submissions.adapters.mapping import load_mapping
from submissions.canonical.definition import canonical_model
from submissions.canonical.schema import ENTITY_TABLES, extract_snapshot
from submissions.enums import SubmissionType
from submissions.snapshot import SnapshotRepository
from submissions.tenants import TenantRepository
from submissions.validation.engine import validate_snapshot
from submissions.validation.results import record_counts, summary
from submissions.validation.rules import load_rule_set
from submissions.validation.tables import validation_finding


@dataclass
class DryRunReport:
    tenant_id: str
    as_of_date: str
    snapshot_id: int | None
    mapping_version: str
    rows: dict[str, int]
    empty_columns: dict[str, dict[str, float]]                 # entity -> column -> % NULL (only those > 0)
    unmapped_codes: dict[str, list[str]] = field(default_factory=dict)   # entity.column -> distinct invalid values
    records: dict[str, int] = field(default_factory=dict)
    top_rules: list[tuple[str, str, int]] = field(default_factory=list)


def _null_rates(engine: Engine, snapshot_id: int) -> dict[str, dict[str, float]]:
    out = {}
    with engine.connect() as conn:
        for entity, edef in canonical_model().entities.items():
            t = ENTITY_TABLES[entity]
            counts = conn.execute(select(func.count(), *(func.count(t.c[c.name]) for c in edef.columns))
                                  .where(t.c.snapshot_id == snapshot_id)).one()
            total = counts[0]
            if total:
                rates = {c.name: round(100 * (total - n) / total, 1) for c, n in zip(edef.columns, counts[1:])
                         if n < total}
                if rates:
                    out[entity] = rates
    return out


def _unmapped(engine: Engine, snapshot_id: int, limit: int = 20) -> dict[str, list[str]]:
    """Values flagged by the generated ENUM rules: usually client codes missing from a mapping lookup."""
    f = validation_finding.c
    with engine.connect() as conn:
        rows = conn.execute(select(f.rule_id, f.value).where(f.snapshot_id == snapshot_id,
                                                             f.rule_id.like("UCRF.ENUM.%")).distinct()).all()
    out: dict[str, list[str]] = {}
    for rule_id, value in rows:
        key = rule_id.removeprefix("UCRF.ENUM.")
        if len(out.setdefault(key, [])) < limit:
            out[key].append(value)
    return {k: sorted(v) for k, v in sorted(out.items())}


def dry_run(*, app_engine: Engine, source_engine: Engine, tenant_id: str, as_of_date: date, base_dir: Path,
            submission_type: SubmissionType | str = SubmissionType.CONSUMER, keep: bool = False) -> DryRunReport:
    tenant = TenantRepository(app_engine).get(tenant_id)
    if not tenant.mapping_path:
        raise ValueError(f"Tenant {tenant_id} has no mapping file configured")
    snapshots = SnapshotRepository(app_engine)
    extract = run_extract(tenant=tenant, mapping=load_mapping(base_dir / tenant.mapping_path),
                          submission_type=submission_type, as_of_date=as_of_date, app_engine=app_engine,
                          source_engine=source_engine, snapshots=snapshots, owner="dry-run")
    sid = extract.snapshot_id
    try:
        validate_snapshot(app_engine, snapshots, sid, load_rule_set(app_engine, tenant_id, submission_type))
        report = DryRunReport(
            tenant_id=tenant_id, as_of_date=as_of_date.isoformat(), snapshot_id=sid if keep else None,
            mapping_version=extract.mapping_version, rows=extract.row_counts,
            empty_columns=_null_rates(app_engine, sid), unmapped_codes=_unmapped(app_engine, sid),
            records=record_counts(app_engine, sid),
            top_rules=[(r.rule_id, r.outcome, r.finding_count) for r in summary(app_engine, sid)[:15]])
    finally:
        if not keep:
            with app_engine.begin() as conn:                 # cascades to c_* rows and validation results
                conn.execute(delete(extract_snapshot).where(extract_snapshot.c.snapshot_id == sid))
    return report


def format_report(r: DryRunReport) -> str:
    lines = [f"Dry run {r.tenant_id} as of {r.as_of_date}  (mapping {r.mapping_version})",
             f"Snapshot kept: #{r.snapshot_id}" if r.snapshot_id else "Trial snapshot deleted", "", "Rows extracted:"]
    lines += [f"  {k:22} {v}" for k, v in r.rows.items()]
    lines += ["", f"Records: {r.records.get('ACCEPTED', 0)} accepted, {r.records.get('REJECTED', 0)} rejected", ""]
    if r.unmapped_codes:
        lines.append("Invalid / untranslated code values (add them to the mapping lookups):")
        lines += [f"  {k}: {', '.join(v)}" for k, v in r.unmapped_codes.items()]
        lines.append("")
    lines.append("Empty columns (% NULL):")
    for entity, cols in r.empty_columns.items():
        lines.append(f"  {entity}: " + ", ".join(f"{c} {p}%" for c, p in cols.items()))
    lines += ["", "Top findings:"]
    lines += [f"  {count:6}  {outcome:15} {rule}" for rule, outcome, count in r.top_rules]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    from submissions.config import Settings
    from submissions.db import make_engine

    ap = argparse.ArgumentParser(description="Extract + validate a tenant without creating a request")
    ap.add_argument("--tenant", required=True)
    ap.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    ap.add_argument("--keep", action="store_true", help="keep the trial snapshot (visible in the UI findings page)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    settings = Settings.from_env()
    app = make_engine(settings.database_url)
    source = app if settings.source_database_url == settings.database_url else make_engine(settings.source_database_url)
    report = dry_run(app_engine=app, source_engine=source, tenant_id=args.tenant, as_of_date=args.as_of,
                     base_dir=Path.cwd(), keep=args.keep)
    print(json.dumps(asdict(report), indent=2) if args.json else format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
