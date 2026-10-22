"""Tenant mapping files: how an NBFC schema reaches the canonical model.

File format (YAML):

    canonical_version: "1.0"
    extends: ../_base/mapping.yaml          # optional; this file deep-merges over it
    lookups:                                # client code -> UCRF code
      gender: {M: "2", F: "1"}
    entities:
      party:                                # single SELECT ...
        from: "{schema}.borrower_demographics b"
        where: "b.customer_id IS NOT NULL"  # optional; may use :as_of_date
        distinct: false                     # optional
        columns:
          party_key: b.customer_id                          # SQL expression
          gender: {expr: b.sex, lookup: gender}             # translated through a lookup
          address_category: {expr: b.addr_type, lookup: address_category, default: "04"}
      party_identifier:                     # ... or several named SELECTs combined with UNION ALL
        selects:
          pan: {from: ..., where: ..., columns: {...}}
          aadhaar: {from: ..., columns: {...}}

Deep merge with `extends`: mappings merge key by key, any other value replaces, and `null` removes a key
(e.g. `selects: {ckyc: null}` drops an inherited SELECT). Unmapped canonical columns become NULL.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path

import yaml

from submissions.canonical.definition import CanonicalModel, canonical_model

REQUIRED_ENTITIES = ("party", "account", "account_holder")
_SELECT_KEYS = {"from", "where", "distinct", "columns"}


class MappingError(ValueError):
    pass


@dataclass(frozen=True)
class ColumnExpr:
    expr: str
    lookup: str | None = None
    default: str | None = None


@dataclass(frozen=True)
class SelectMapping:
    name: str
    from_: str
    where: str | None
    distinct: bool
    columns: dict[str, ColumnExpr]


@dataclass(frozen=True)
class EntityMapping:
    entity: str
    selects: tuple[SelectMapping, ...]


@dataclass(frozen=True)
class Mapping:
    path: Path
    canonical_version: str
    lookups: dict[str, dict[str, str]]
    entities: dict[str, EntityMapping]


def deep_merge(base: dict, override: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if value is None:
            result.pop(key, None)
        elif isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _read_raw(path: Path, seen: tuple[Path, ...] = ()) -> dict:
    path = path.resolve()
    if path in seen:
        raise MappingError(f"Circular 'extends': {' -> '.join(map(str, (*seen, path)))}")
    if not path.exists():
        raise MappingError(f"Mapping file not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise MappingError(f"{path}: top level must be a mapping")
    parent = raw.pop("extends", None)
    if parent:
        raw = deep_merge(_read_raw(path.parent / parent, (*seen, path)), raw)
    return raw


def _column(entity: str, col: str, spec, lookups: dict) -> ColumnExpr:
    where = f"entities.{entity}.columns.{col}"
    if isinstance(spec, (str, int, float)):
        return ColumnExpr(str(spec))
    if not isinstance(spec, dict) or "expr" not in spec:
        raise MappingError(f"{where}: give an SQL expression or {{expr, lookup, default}}")
    unknown = set(spec) - {"expr", "lookup", "default"}
    if unknown:
        raise MappingError(f"{where}: unknown keys {sorted(unknown)}")
    if spec.get("lookup") and spec["lookup"] not in lookups:
        raise MappingError(f"{where}: unknown lookup {spec['lookup']!r}")
    default = spec.get("default")
    return ColumnExpr(str(spec["expr"]), spec.get("lookup"), None if default is None else str(default))


def _select(entity: str, name: str, spec, model: CanonicalModel, lookups: dict) -> SelectMapping:
    where = f"entities.{entity}" + (f".selects.{name}" if name != "main" else "")
    if not isinstance(spec, dict):
        raise MappingError(f"{where}: must be a mapping")
    unknown = set(spec) - _SELECT_KEYS
    if unknown:
        raise MappingError(f"{where}: unknown keys {sorted(unknown)}")
    if not spec.get("from") or not spec.get("columns"):
        raise MappingError(f"{where}: 'from' and 'columns' are required")
    entity_def = model.entities[entity]
    known = {c.name for c in entity_def.columns}
    columns = {}
    for col, col_spec in spec["columns"].items():
        if col not in known:
            raise MappingError(f"{where}.columns.{col}: not a column of canonical {entity}")
        if col_spec is not None:
            columns[col] = _column(entity, col, col_spec, lookups)
    missing = [c.name for c in entity_def.columns if c.required and c.name not in columns]
    if missing:
        raise MappingError(f"{where}: required canonical columns not mapped: {missing}")
    return SelectMapping(name, str(spec["from"]), spec.get("where"), bool(spec.get("distinct", False)), columns)


def parse_mapping(raw: dict, path: Path, model: CanonicalModel | None = None) -> Mapping:
    model = model or canonical_model()
    if str(raw.get("canonical_version")) != model.version:
        raise MappingError(f"{path}: canonical_version {raw.get('canonical_version')!r} does not match the "
                           f"model ({model.version})")
    unknown = set(raw) - {"canonical_version", "lookups", "entities"}
    if unknown:
        raise MappingError(f"{path}: unknown top-level keys {sorted(unknown)}")
    lookups = {}
    for name, values in (raw.get("lookups") or {}).items():
        for k, v in (values or {}).items():
            if isinstance(k, bool) or isinstance(v, bool) or v is None:
                raise MappingError(f"lookups.{name}: entry {k!r}: {v!r} is not text. Quote it: YAML reads "
                                   "unquoted on/off/yes/no/true/false as booleans")
        lookups[name] = {str(k): str(v) for k, v in (values or {}).items()}
    entities = {}
    for entity, spec in (raw.get("entities") or {}).items():
        if entity not in model.entities:
            raise MappingError(f"{path}: unknown canonical entity {entity!r}")
        if spec is None:
            continue
        if "selects" in spec:
            if set(spec) - {"selects"}:
                raise MappingError(f"entities.{entity}: use either 'selects' or a single from/columns, not both")
            selects = [_select(entity, n, s, model, lookups) for n, s in spec["selects"].items() if s is not None]
        else:
            selects = [_select(entity, "main", spec, model, lookups)]
        if selects:
            entities[entity] = EntityMapping(entity, tuple(selects))
    missing = [e for e in REQUIRED_ENTITIES if e not in entities]
    if missing:
        raise MappingError(f"{path}: required entities not mapped: {missing}")
    return Mapping(path, model.version, lookups, entities)


def load_mapping(path: str | Path, model: CanonicalModel | None = None) -> Mapping:
    path = Path(path)
    return parse_mapping(_read_raw(path), path, model)
