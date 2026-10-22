"""Compare an NBFC's source schema with the reference (base) schema, and check which source columns the base
mapping uses that the NBFC does not have. The second part tells you exactly what the tenant mapping must override.

Usage:  python -m submissions.onboarding.introspect --schema nbfc_002 [--reference nbfc_001]
                                                   [--mapping clients/_base/mapping.yaml]
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import Engine, inspect

from submissions.adapters.mapping import Mapping, load_mapping

DEFAULT_REFERENCE = "nbfc_001"
DEFAULT_MAPPING = Path("clients/_base/mapping.yaml")
_FROM_RE = re.compile(r"\{schema\}\.(\w+)(?:\s+(?:AS\s+)?(\w+))?", re.IGNORECASE)
_COLUMN_RE = re.compile(r"\b([A-Za-z_]\w*)\.([A-Za-z_]\w*)\b")


@dataclass
class TableDiff:
    table: str
    missing_columns: list[str] = field(default_factory=list)    # in reference, not in target
    extra_columns: list[str] = field(default_factory=list)      # in target, not in reference
    type_changes: list[str] = field(default_factory=list)


@dataclass
class IntrospectReport:
    schema: str
    reference: str
    missing_tables: list[str] = field(default_factory=list)
    extra_tables: list[str] = field(default_factory=list)
    tables: list[TableDiff] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)          # "entity.select.column -> table.col" not in target

    @property
    def needs_overrides(self) -> bool:
        return bool(self.unresolved)


def _columns(engine: Engine, schema: str) -> dict[str, dict[str, str]]:
    insp = inspect(engine)
    return {t: {c["name"]: str(c["type"]) for c in insp.get_columns(t, schema=schema)}
            for t in insp.get_table_names(schema=schema)}


def mapping_references(mapping: Mapping) -> list[tuple[str, str, str]]:
    """(where, table, column) for every alias.column used in the mapping's SQL, resolved through FROM aliases."""
    refs = []
    for entity, em in mapping.entities.items():
        for sel in em.selects:
            texts = [(f"{entity}.{sel.name}.from", sel.from_)]
            texts += [(f"{entity}.{sel.name}.where", sel.where)] if sel.where else []
            texts += [(f"{entity}.{sel.name}.{col}", expr.expr) for col, expr in sel.columns.items()]
            aliases = {(m.group(2) or m.group(1)): m.group(1)       # FROM aliases, incl. sub-selects in expressions
                       for _, text in texts for m in _FROM_RE.finditer(text)}
            for where, text in texts:
                for alias, column in _COLUMN_RE.findall(text.replace("{schema}.", "")):
                    if alias in aliases:
                        refs.append((where, aliases[alias], column))
    return refs


def introspect(engine: Engine, schema: str, reference: str, mapping: Mapping) -> IntrospectReport:
    target, ref = _columns(engine, schema), _columns(engine, reference)
    report = IntrospectReport(schema, reference, sorted(set(ref) - set(target)), sorted(set(target) - set(ref)))
    for table in sorted(set(ref) & set(target)):
        diff = TableDiff(table, sorted(set(ref[table]) - set(target[table])), sorted(set(target[table]) - set(ref[table])),
                         [f"{c}: {ref[table][c]} -> {target[table][c]}" for c in sorted(set(ref[table]) & set(target[table]))
                          if ref[table][c] != target[table][c]])
        if diff.missing_columns or diff.extra_columns or diff.type_changes:
            report.tables.append(diff)
    seen = set()
    for where, table, column in mapping_references(mapping):
        if column not in target.get(table, {}) and (where, table, column) not in seen:
            seen.add((where, table, column))
            report.unresolved.append(f"{where}: {table}.{column}")
    return report


def format_report(r: IntrospectReport) -> str:
    lines = [f"Schema {r.schema} compared with reference {r.reference}", ""]
    for label, items in (("Tables missing", r.missing_tables), ("Extra tables", r.extra_tables)):
        if items:
            lines.append(f"{label}: {', '.join(items)}")
    for t in r.tables:
        lines.append(f"Table {t.table}:")
        lines += [f"  missing column  {c}" for c in t.missing_columns]
        lines += [f"  extra column    {c}" for c in t.extra_columns]
        lines += [f"  type changed    {c}" for c in t.type_changes]
    lines.append("")
    if r.unresolved:
        lines.append("Base mapping uses columns this schema does not have. Override these in the tenant mapping:")
        lines += [f"  {u}" for u in r.unresolved]
    else:
        lines.append("The base mapping resolves fully against this schema (codes may still differ: run check/dry_run).")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    from submissions.config import Settings
    from submissions.db import make_engine

    ap = argparse.ArgumentParser(description="Compare an NBFC schema with the reference schema")
    ap.add_argument("--schema", required=True)
    ap.add_argument("--reference", default=DEFAULT_REFERENCE)
    ap.add_argument("--mapping", type=Path, default=DEFAULT_MAPPING, help="mapping to resolve (default: base)")
    args = ap.parse_args(argv)
    engine = make_engine(Settings.from_env().source_database_url)
    report = introspect(engine, args.schema, args.reference, load_mapping(args.mapping))
    print(format_report(report))
    return 1 if report.needs_overrides else 0


if __name__ == "__main__":
    raise SystemExit(main())
