"""Rule catalogue: built-in packs (YAML, with generated REQUIRED/ENUM rules), tenant custom rules, tenant policy."""

from __future__ import annotations

from dataclasses import dataclass, replace
from functools import cache
from pathlib import Path

import yaml
from sqlalchemy import Engine, delete, select, update

from submissions.canonical.definition import canonical_model
from submissions.db import utcnow
from submissions.enums import Outcome, SubmissionType
from submissions.validation.tables import validation_policy, validation_rule

PACKS_DIR = Path(__file__).with_name("packs")
PACK_FILES = {SubmissionType.CONSUMER: "ucrf_consumer_3_74.yaml"}
BUILTIN_PREFIX = "UCRF."

# (account_key, party_key) expressions for generated rules, per canonical entity (alias x)
_KEYS = {"party": ("NULL", "x.party_key"), "party_identifier": ("NULL", "x.party_key"),
         "party_phone": ("NULL", "x.party_key"), "party_email": ("NULL", "x.party_key"),
         "party_address": ("NULL", "x.party_key"), "account": ("x.account_key", "NULL"),
         "account_holder": ("x.account_key", "x.party_key"), "account_history": ("x.account_key", "NULL"),
         "extension_attribute": ("NULL", "NULL")}


class RuleDefinitionError(ValueError):
    pass


@dataclass(frozen=True)
class Rule:
    rule_id: str
    source: str                       # pack name (e.g. UCRF-3.74) or CUSTOM
    entity: str
    outcome: Outcome
    message: str
    segment: str | None = None
    field: str | None = None
    sql: str | None = None
    python: str | None = None
    cascade: bool = False
    enabled: bool = True

    @property
    def kind(self) -> str:
        return "sql" if self.sql else "python" if self.python else "cascade"


def _check_rule(rule: Rule) -> Rule:
    if rule.entity not in canonical_model().entities:
        raise RuleDefinitionError(f"{rule.rule_id}: unknown entity {rule.entity!r}")
    if sum(map(bool, (rule.sql, rule.python, rule.cascade))) != 1:
        raise RuleDefinitionError(f"{rule.rule_id}: give exactly one of sql, python, cascade")
    if rule.sql and ":snapshot_id" not in rule.sql:
        raise RuleDefinitionError(f"{rule.rule_id}: SQL rules must filter on :snapshot_id")
    if rule.python:
        from submissions.validation.python_rules import PYTHON_RULES
        if rule.python not in PYTHON_RULES:
            raise RuleDefinitionError(f"{rule.rule_id}: unknown python rule {rule.python!r}")
    return rule


def _generated_rules(pack: str, cfg: dict, segments: dict) -> list[Rule]:
    model, rules = canonical_model(), []
    for entity, edef in model.entities.items():
        acct, party = _KEYS[entity]
        for col in edef.columns:
            base = dict(source=pack, entity=entity, segment=segments.get(entity), field=col.name)
            if col.required:
                blank = f"(x.{col.name} IS NULL OR TRIM(x.{col.name}) = '')" if col.type in ("string", "code") \
                    else f"x.{col.name} IS NULL"
                rules.append(Rule(
                    rule_id=f"UCRF.REQUIRED.{entity}.{col.name}", outcome=Outcome(cfg["required_outcome"][entity]),
                    message=f"{entity}.{col.name} is required",
                    sql=(f"SELECT x.row_id, {acct} AS account_key, {party} AS party_key, NULL AS value\n"
                         f"FROM {edef.table} x WHERE x.snapshot_id = :snapshot_id AND {blank}"), **base))
            if col.code_list:
                outcome = cfg.get("enum_outcome", {}).get(f"{entity}.{col.name}", cfg["enum_outcome_default"])
                rules.append(Rule(
                    rule_id=f"UCRF.ENUM.{entity}.{col.name}", outcome=Outcome(outcome),
                    message=f"{entity}.{col.name} is not a valid {col.code_list} code",
                    sql=(f"SELECT x.row_id, {acct} AS account_key, {party} AS party_key, x.{col.name} AS value\n"
                         f"FROM {edef.table} x WHERE x.snapshot_id = :snapshot_id AND x.{col.name} IS NOT NULL\n"
                         f"  AND x.{col.name} NOT IN (SELECT r.code FROM ref_code r WHERE r.code_list = "
                         f"'{col.code_list}' AND r.is_active = 1)"), **base))
    return rules


def load_pack(path: Path) -> list[Rule]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    pack, segments = raw["pack"], raw.get("segments", {})
    rules = _generated_rules(pack, raw["generated"], segments)
    for spec in raw["rules"]:
        unknown = set(spec) - {"id", "entity", "field", "outcome", "message", "sql", "python", "cascade"}
        if unknown:
            raise RuleDefinitionError(f"{spec.get('id')}: unknown keys {sorted(unknown)}")
        if not str(spec["id"]).startswith(BUILTIN_PREFIX):
            raise RuleDefinitionError(f"{spec['id']}: built-in rule ids start with {BUILTIN_PREFIX}")
        rules.append(Rule(rule_id=spec["id"], source=pack, entity=spec["entity"], outcome=Outcome(spec["outcome"]),
                          message=spec["message"], segment=segments.get(spec["entity"]), field=spec.get("field"),
                          sql=spec.get("sql"), python=spec.get("python"), cascade=bool(spec.get("cascade"))))
    ids = [r.rule_id for r in rules]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise RuleDefinitionError(f"{path.name}: duplicate rule ids {duplicates}")
    return [_check_rule(r) for r in rules]


@cache
def builtin_rules(submission_type: SubmissionType | str) -> tuple[Rule, ...]:
    name = PACK_FILES.get(SubmissionType(submission_type))
    if name is None:
        raise RuleDefinitionError(f"No built-in rule pack for {submission_type}")
    return tuple(load_pack(PACKS_DIR / name))


# ---------------------------------------------------------------- tenant custom rules and policy
def save_custom_rule(engine: Engine, tenant_id: str, submission_type: SubmissionType | str, rule: Rule) -> None:
    """Insert or update a tenant's custom SQL rule (validated first)."""
    if rule.rule_id.startswith(BUILTIN_PREFIX):
        raise RuleDefinitionError(f"{rule.rule_id}: custom rule ids must not start with {BUILTIN_PREFIX}")
    if not rule.sql:
        raise RuleDefinitionError(f"{rule.rule_id}: custom rules are SQL rules")
    _check_rule(rule)
    values = dict(tenant_id=tenant_id, submission_type=str(SubmissionType(submission_type)), entity=rule.entity,
                  segment=rule.segment, field=rule.field, outcome=str(rule.outcome), message=rule.message,
                  sql_text=rule.sql, is_enabled=rule.enabled, updated_at=utcnow())
    with engine.begin() as conn:
        if conn.execute(update(validation_rule).where(validation_rule.c.rule_id == rule.rule_id)
                        .values(**values)).rowcount == 0:
            conn.execute(validation_rule.insert().values(rule_id=rule.rule_id, **values))


def set_policy(engine: Engine, tenant_id: str, rule_id: str, *, is_enabled: bool | None = None,
               outcome: Outcome | str | None = None) -> None:
    """Override a rule's enabled flag and/or outcome for one tenant (None keeps the rule's own value)."""
    values = dict(is_enabled=is_enabled, outcome=str(Outcome(outcome)) if outcome else None, updated_at=utcnow())
    t = validation_policy
    with engine.begin() as conn:
        if conn.execute(update(t).where(t.c.tenant_id == tenant_id, t.c.rule_id == rule_id)
                        .values(**values)).rowcount == 0:
            conn.execute(t.insert().values(tenant_id=tenant_id, rule_id=rule_id, **values))


def clear_policy(engine: Engine, tenant_id: str, rule_id: str) -> bool:
    """Back to the rule's own settings for this tenant. True if there was an override."""
    t = validation_policy
    with engine.begin() as conn:
        return conn.execute(delete(t).where(t.c.tenant_id == tenant_id, t.c.rule_id == rule_id)).rowcount > 0


def policies(engine: Engine, tenant_id: str) -> dict[str, dict]:
    """rule_id -> {is_enabled, outcome, updated_at} overrides of one tenant."""
    t = validation_policy
    with engine.connect() as conn:
        return {r.rule_id: dict(is_enabled=r.is_enabled, outcome=r.outcome, updated_at=r.updated_at)
                for r in conn.execute(select(t).where(t.c.tenant_id == tenant_id))}


def _own_rules(engine: Engine, tenant_id: str, submission_type: SubmissionType | str) -> list[Rule]:
    """Built-in + tenant custom rules with their own settings (no policy applied)."""
    rules = list(builtin_rules(submission_type))
    with engine.connect() as conn:
        custom = conn.execute(select(validation_rule).where(
            validation_rule.c.tenant_id == tenant_id,
            validation_rule.c.submission_type == str(SubmissionType(submission_type)))).all()
    return rules + [Rule(rule_id=r.rule_id, source="CUSTOM", entity=r.entity, outcome=Outcome(r.outcome),
                         message=r.message, segment=r.segment, field=r.field, sql=r.sql_text,
                         enabled=bool(r.is_enabled)) for r in custom]


def _apply(rule: Rule, p) -> Rule:
    if p is None:
        return rule
    return replace(rule, enabled=rule.enabled if p["is_enabled"] is None else bool(p["is_enabled"]),
                   outcome=Outcome(p["outcome"]) if p["outcome"] else rule.outcome)


@dataclass(frozen=True)
class CatalogueEntry:
    default: Rule                     # the rule's own settings
    effective: Rule                   # with this tenant's policy applied
    policy: dict | None               # the tenant's override, if any

    @property
    def changed(self) -> bool:
        return (self.default.enabled, self.default.outcome) != (self.effective.enabled, self.effective.outcome)


def catalogue(engine: Engine, tenant_id: str, submission_type: SubmissionType | str) -> list[CatalogueEntry]:
    """Every rule for this tenant and type, with its default and effective settings (Rules & policies page)."""
    pol = policies(engine, tenant_id)
    return [CatalogueEntry(r, _apply(r, pol.get(r.rule_id)), pol.get(r.rule_id))
            for r in _own_rules(engine, tenant_id, submission_type)]


def load_rule_set(engine: Engine, tenant_id: str, submission_type: SubmissionType | str) -> list[Rule]:
    """Built-in + tenant custom rules, with the tenant's policy applied. Disabled rules are included (enabled=False)."""
    pol = policies(engine, tenant_id)
    return [_apply(r, pol.get(r.rule_id)) for r in _own_rules(engine, tenant_id, submission_type)]
