"""Generate the MySQL migration for the canonical model from the same Table objects the code uses.

Usage:
  python -m submissions.canonical.generate                      # print to stdout
  python -m submissions.canonical.generate --out migrations/003_canonical_model.sql
  python -m submissions.canonical.generate --check migrations/003_canonical_model.sql   # exit 1 if stale
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import Table, insert
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateIndex, CreateTable

from submissions.canonical.schema import (
    ENTITY_TABLES, MODEL, extract_snapshot, ref_code, ref_code_rows, ref_state_pin, ref_state_pin_rows,
)

_DIALECT = mysql.dialect()


def _clean(sql) -> str:
    return "\n".join(line.rstrip() for line in str(sql).strip().splitlines())


def _create(table: Table) -> list[str]:
    statements = [_clean(CreateTable(table).compile(dialect=_DIALECT))]
    statements += [_clean(CreateIndex(ix).compile(dialect=_DIALECT))
                   for ix in sorted(table.indexes, key=lambda ix: ix.name)]
    return statements


def _insert(table: Table, rows: list[dict]) -> str:
    stmt = insert(table).values(rows)
    return _clean(stmt.compile(dialect=_DIALECT, compile_kwargs={"literal_binds": True}))


def generate_sql() -> str:
    tables = [extract_snapshot, ref_code, ref_state_pin, *ENTITY_TABLES.values()]
    statements = [s for t in tables for s in _create(t)]
    statements += [_insert(ref_code, ref_code_rows()), _insert(ref_state_pin, ref_state_pin_rows())]
    header = (
        f"-- GENERATED from submissions/canonical/model.yaml (canonical_version {MODEL.version}). Do not edit.\n"
        "-- Regenerate: python -m submissions.canonical.generate --out <this file>\n"
        "-- Canonical snapshot tables (c_*), snapshot header and UCRF reference data.\n"
    )
    sql = header + "\n" + "\n\n".join(s + ";" for s in statements) + "\n"
    if any(";" in line[:-1] for line in sql.splitlines()):
        raise ValueError("Generated SQL has a ';' inside a line; the migration runner would mis-split it")
    return sql


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--out", type=Path, help="write the migration file")
    group.add_argument("--check", type=Path, help="fail if the file differs from the model")
    args = ap.parse_args(argv)
    sql = generate_sql()
    if args.out:
        args.out.write_text(sql, encoding="utf-8", newline="\n")
        print(f"wrote {args.out}")
    elif args.check:
        if not args.check.exists() or args.check.read_text(encoding="utf-8") != sql:
            print(f"{args.check} is out of date with model.yaml; regenerate it", file=sys.stderr)
            return 1
        print(f"{args.check} is up to date")
    else:
        sys.stdout.write(sql)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
