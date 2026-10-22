"""Check a tenant mapping against the live source schema before onboarding.

Reports:
  errors    mapping invalid, or a compiled SELECT fails on the source
  warnings  source code values with no lookup entry (they would surface as validation findings),
            optional canonical entities not mapped
  rows      row count per canonical entity for the as-of date

Usage:  python -m submissions.adapters.check clients/nbfc_002/mapping.yaml --schema nbfc_002 [--as-of 2026-09-30]
        (reads the source via SUBMISSIONS_SOURCE_DATABASE_URL, falling back to SUBMISSIONS_DATABASE_URL)
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from sqlalchemy import Engine, text

from submissions.adapters.compiler import AS_OF_PARAM, compile_mapping
from submissions.adapters.mapping import Mapping, MappingError, load_mapping
from submissions.canonical.definition import canonical_model


@dataclass
class CheckReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    rows: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors


def _params(sql: str, as_of_date: date) -> dict:
    return {"as_of_date": as_of_date} if AS_OF_PARAM in sql else {}


def check_mapping(mapping: Mapping, schema: str, source: Engine, as_of_date: date) -> CheckReport:
    report = CheckReport()
    compiled = compile_mapping(mapping, schema)
    model = canonical_model()
    for entity in model.entities:
        if entity not in compiled.sql:
            report.warnings.append(f"{entity}: not mapped (no rows will be extracted)")
    with source.connect() as conn:
        for entity, sql in compiled.sql.items():
            try:
                report.rows[entity] = conn.execute(text(f"SELECT COUNT(*) FROM (\n{sql}\n) q"),
                                                   _params(sql, as_of_date)).scalar()
            except Exception as exc:  # noqa: BLE001  report every failing entity, not just the first
                report.errors.append(f"{entity}: SELECT fails on the source: {exc}")
                conn.rollback()
        for entity, em in mapping.entities.items():
            for sel in em.selects:
                for col, expr in sel.columns.items():
                    if not expr.lookup:
                        continue
                    sql = f"SELECT DISTINCT ({expr.expr}) AS v FROM {sel.from_}"
                    if sel.where:
                        sql += f" WHERE {sel.where}"
                    sql = sql.replace("{schema}", schema)
                    try:
                        values = {r.v for r in conn.execute(text(sql), _params(sql, as_of_date))}
                    except Exception:  # noqa: BLE001  already reported as an entity error
                        conn.rollback()
                        continue
                    unmapped = sorted(str(v) for v in values if v is not None and str(v) not in mapping.lookups[expr.lookup])
                    if unmapped:
                        report.warnings.append(f"{entity}.{sel.name}.{col}: values with no '{expr.lookup}' lookup "
                                               f"entry: {unmapped}")
    return report


def main(argv: list[str] | None = None) -> int:
    from submissions.config import Settings
    from submissions.db import make_engine

    ap = argparse.ArgumentParser(description="Check a tenant mapping against its source schema")
    ap.add_argument("mapping", type=Path)
    ap.add_argument("--schema", required=True)
    ap.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    args = ap.parse_args(argv)
    try:
        mapping = load_mapping(args.mapping)
    except MappingError as exc:
        print(f"ERROR   {exc}")
        return 1
    report = check_mapping(mapping, args.schema, make_engine(Settings.from_env().source_database_url), args.as_of)
    for entity, n in report.rows.items():
        print(f"ROWS    {entity:22} {n}")
    for w in report.warnings:
        print(f"WARNING {w}")
    for e in report.errors:
        print(f"ERROR   {e}")
    print("OK" if report.ok else "FAILED")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
