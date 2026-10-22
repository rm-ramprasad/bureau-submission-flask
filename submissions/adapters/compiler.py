"""Compile a tenant mapping into plain MySQL: one SELECT (or UNION ALL) per canonical entity.

The generated SQL is what actually runs, so it is written out for review:
  python -m submissions.adapters.compiler clients/nbfc_001/mapping.yaml --schema nbfc_001 --out clients/nbfc_001/generated
"""

from __future__ import annotations

import argparse
import hashlib
from dataclasses import dataclass
from pathlib import Path

from submissions.adapters.mapping import ColumnExpr, EntityMapping, Mapping, SelectMapping, load_mapping
from submissions.canonical.definition import CanonicalModel, canonical_model
from submissions.tenants.models import SCHEMA_RE

AS_OF_PARAM = ":as_of_date"


@dataclass(frozen=True)
class CompiledMapping:
    schema: str
    sql: dict[str, str]          # canonical entity -> SELECT statement (canonical column order)
    version: str                 # hash of the compiled SQL, recorded on each snapshot

    def uses_as_of(self, entity: str) -> bool:
        return AS_OF_PARAM in self.sql[entity]


def quote(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


def compile_column(col: ColumnExpr, lookups: dict[str, dict[str, str]]) -> str:
    sql = f"({col.expr})"
    if col.lookup:
        cases = " ".join(f"WHEN {quote(src)} THEN {quote(dst)}" for src, dst in lookups[col.lookup].items())
        # Unmapped values pass through unchanged, so validation reports them instead of losing them.
        sql = f"CASE {sql} {cases} ELSE {sql} END"
    if col.default is not None:
        sql = f"COALESCE({sql}, {quote(col.default)})"
    return sql


def compile_select(sel: SelectMapping, entity: str, model: CanonicalModel, lookups: dict, schema: str) -> str:
    cols = [f"    {compile_column(sel.columns[c.name], lookups) if c.name in sel.columns else 'NULL'} AS {c.name}"
            for c in model.entities[entity].columns]
    sql = f"SELECT{' DISTINCT' if sel.distinct else ''}  -- {entity}.{sel.name}\n" + ",\n".join(cols)
    sql += f"\nFROM {sel.from_}"
    if sel.where:
        sql += f"\nWHERE {sel.where}"
    return sql.replace("{schema}", schema)


def compile_entity(em: EntityMapping, model: CanonicalModel, lookups: dict, schema: str) -> str:
    return "\nUNION ALL\n".join(compile_select(s, em.entity, model, lookups, schema) for s in em.selects)


def compile_mapping(mapping: Mapping, schema: str, model: CanonicalModel | None = None) -> CompiledMapping:
    if not SCHEMA_RE.match(schema):
        raise ValueError(f"Invalid schema name {schema!r}")
    model = model or canonical_model()
    sql = {name: compile_entity(mapping.entities[name], model, mapping.lookups, schema)
           for name in model.entities if name in mapping.entities}       # canonical order, deterministic
    digest = hashlib.sha256("\n".join(f"{k}\n{v}" for k, v in sql.items()).encode()).hexdigest()[:16]
    return CompiledMapping(schema, sql, digest)


def write_generated(compiled: CompiledMapping, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.sql"):
        old.unlink()
    paths = []
    for entity, sql in compiled.sql.items():
        path = out_dir / f"{entity}.sql"
        path.write_text(f"-- GENERATED from the tenant mapping (version {compiled.version}). Do not edit.\n"
                        f"{sql};\n", encoding="utf-8", newline="\n")
        paths.append(path)
    return paths


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Compile a tenant mapping to SQL")
    ap.add_argument("mapping", type=Path)
    ap.add_argument("--schema", required=True)
    ap.add_argument("--out", type=Path, help="directory for generated/*.sql (default: print)")
    args = ap.parse_args(argv)
    compiled = compile_mapping(load_mapping(args.mapping), args.schema)
    if args.out:
        for p in write_generated(compiled, args.out):
            print(f"wrote {p}")
    else:
        for entity, sql in compiled.sql.items():
            print(f"-- ===== {entity}\n{sql};\n")
    print(f"-- mapping version {compiled.version}")


if __name__ == "__main__":
    main()
