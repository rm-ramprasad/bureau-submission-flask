"""Validate one canonical snapshot: run every enabled rule, decide each record (cascade), summarise.

Results go to validation_finding, record_outcome and validation_summary. Re-running replaces the previous
results for the snapshot, so a snapshot can be re-validated after a policy change.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date, timedelta

from sqlalchemy import Connection, Engine, delete, func, insert, select, text

from submissions.canonical.schema import ENTITY_TABLES
from submissions.enums import Outcome, RecordOutcome, SnapshotStatus
from submissions.snapshot import SnapshotRepository
from submissions.validation.parameters import load_parameters, render_message
from submissions.validation.python_rules import PYTHON_RULES, RuleContext
from submissions.validation.rules import Rule
from submissions.validation.tables import record_outcome, validation_finding, validation_summary

QUALIFYING_ID_TYPES = {"01", "02", "03", "04", "05", "06", "09", "10"}
MOBILE = "01"
SEGMENT_OUTCOMES = {Outcome.REJECT_SEGMENT}
RECORD_OUTCOMES = {Outcome.REJECT_RECORD}

_INSERT_SQL_FINDINGS = """INSERT INTO validation_finding
    (snapshot_id, rule_id, entity, row_id, account_key, party_key, segment, field, outcome, value, message)
SELECT :snapshot_id, :rule_id, :entity, q.row_id, q.account_key, q.party_key, :segment, :field, :outcome,
       SUBSTR(CAST(q.value AS CHAR), 1, 500), :message
FROM (
{sql}
) q"""


class ValidationError(Exception):
    pass


@dataclass(frozen=True)
class ValidationResult:
    snapshot_id: int
    records: int
    accepted: int
    rejected: int
    file_rejected: bool
    findings: dict[str, int]           # outcome -> count


def _params(snapshot_id: int, as_of_date: date, parameters: dict) -> dict:
    """Bind values for SQL rules: the snapshot, dates, and the rule parameters (:name)."""
    return {**parameters, "snapshot_id": snapshot_id, "as_of_date": as_of_date,
            "one_year_before": as_of_date - timedelta(days=365)}


def _run_sql_rule(conn: Connection, rule: Rule, params: dict) -> None:
    try:
        conn.execute(text(_INSERT_SQL_FINDINGS.format(sql=rule.sql.strip().rstrip(";"))),
                     {**params, "rule_id": rule.rule_id, "entity": rule.entity, "segment": rule.segment,
                      "field": rule.field, "outcome": str(rule.outcome), "message": rule.message[:500]})
    except Exception as exc:
        raise ValidationError(f"Rule {rule.rule_id} failed: {exc}") from exc


def _run_python_rule(conn: Connection, rule: Rule, ctx: RuleContext) -> None:
    rows = [dict(snapshot_id=ctx.snapshot_id, rule_id=rule.rule_id, entity=rule.entity, row_id=f.row_id,
                 account_key=f.account_key, party_key=f.party_key, segment=rule.segment, field=rule.field,
                 outcome=str(rule.outcome), value=None if f.value is None else str(f.value)[:500],
                 message=(f"{rule.message}: {f.detail}" if f.detail else rule.message)[:500])
            for f in PYTHON_RULES[rule.python](ctx)]
    if rows:
        conn.execute(insert(validation_finding), rows)


def _applies(f, account_key: str, party_key: str) -> bool:
    if f.account_key is None and f.party_key is None:
        return False
    return f.account_key in (None, account_key) and f.party_key in (None, party_key)


def _cascade(conn: Connection, snapshot_id: int, rules: dict[str, Rule], parameters: dict) -> tuple[int, int]:
    """Decide ACCEPTED/REJECTED per record (account x holder) and write record_outcome."""
    def rows(entity, *cols):
        t = ENTITY_TABLES[entity]
        return conn.execute(select(*(t.c[c] for c in cols)).where(t.c.snapshot_id == snapshot_id)).all()

    opened = {r.account_key: r.date_opened for r in rows("account", "account_key", "date_opened")}
    phones, ids, addresses = defaultdict(list), defaultdict(list), defaultdict(list)
    for r in rows("party_phone", "row_id", "party_key", "phone_type"):
        phones[r.party_key].append(r)
    for r in rows("party_identifier", "row_id", "party_key", "id_type"):
        ids[r.party_key].append(r)
    for r in rows("party_address", "row_id", "party_key"):
        addresses[r.party_key].append(r)

    f = validation_finding.c
    findings = conn.execute(select(f.rule_id, f.entity, f.row_id, f.account_key, f.party_key, f.outcome).where(
        f.snapshot_id == snapshot_id,
        f.outcome.in_([str(o) for o in SEGMENT_OUTCOMES | RECORD_OUTCOMES]) | (f.rule_id == "UCRF.PN.SINGLE_WORD_NAME"),
    )).all()
    record_rejects = [x for x in findings if x.outcome in RECORD_OUTCOMES]
    segment_rejects = defaultdict(list)                    # (entity, row_id) -> account restriction (None = all)
    for x in findings:
        if x.outcome in SEGMENT_OUTCOMES and x.row_id is not None:
            segment_rejects[(x.entity, x.row_id)].append(x.account_key)
    single_word = {x.party_key for x in findings if x.rule_id == "UCRF.PN.SINGLE_WORD_NAME"}

    holders = {}
    for r in rows("account_holder", "row_id", "account_key", "party_key"):
        holders.setdefault((r.account_key, r.party_key), r.row_id)

    def enabled(rule_id):
        rule = rules.get(rule_id)
        return rule if rule is not None and rule.enabled else None

    outcomes, cascade_findings, accepted = [], [], 0
    for (account_key, party_key), holder_row in holders.items():
        def ok(entity, row_id):
            return not any(a is None or a == account_key for a in segment_rejects.get((entity, row_id), []))

        reasons = [x.rule_id for x in record_rejects if _applies(x, account_key, party_key)]
        valid_ids = [i for i in ids[party_key] if ok("party_identifier", i.row_id)]
        valid_phones = [p for p in phones[party_key] if ok("party_phone", p.row_id)]
        checks = [
            ("UCRF.REC.NO_VALID_ADDRESS", not any(ok("party_address", a.row_id) for a in addresses[party_key])),
            ("UCRF.REC.NO_PHONE_OR_ID", (opened.get(account_key) or date.min) >= parameters["phone_or_id_required_from"]
             and not valid_phones and not any(i.id_type in QUALIFYING_ID_TYPES for i in valid_ids)),
            ("UCRF.REC.SINGLE_WORD_NAME", party_key in single_word and not valid_ids
             and not any(p.phone_type == MOBILE for p in valid_phones)),
        ]
        for rule_id, failed in checks:
            rule = enabled(rule_id)
            if failed and rule:
                cascade_findings.append(dict(snapshot_id=snapshot_id, rule_id=rule_id, entity=rule.entity,
                                             row_id=holder_row, account_key=account_key, party_key=party_key,
                                             segment=rule.segment, field=rule.field, outcome=str(rule.outcome),
                                             value=None, message=rule.message[:500]))
                if rule.outcome in RECORD_OUTCOMES:
                    reasons.append(rule_id)
        rejected = bool(reasons)
        accepted += not rejected
        outcomes.append(dict(snapshot_id=snapshot_id, account_key=account_key, party_key=party_key,
                             outcome=str(RecordOutcome.REJECTED if rejected else RecordOutcome.ACCEPTED),
                             reason_rule_id=reasons[0] if reasons else None))
    if cascade_findings:
        conn.execute(insert(validation_finding), cascade_findings)
    if outcomes:
        conn.execute(insert(record_outcome), outcomes)
    return accepted, len(outcomes) - accepted


def _clear(conn: Connection, snapshot_id: int) -> None:
    for t in (validation_finding, validation_summary, record_outcome):
        conn.execute(delete(t).where(t.c.snapshot_id == snapshot_id))


def run_validation(conn: Connection, snapshot_id: int, as_of_date: date, rules: list[Rule],
                   parameters: dict) -> ValidationResult:
    _clear(conn, snapshot_id)
    rules = [replace(r, message=render_message(r.message, parameters)) for r in rules]   # {parameter} in messages
    params, ctx = _params(snapshot_id, as_of_date, parameters), RuleContext(conn, snapshot_id, as_of_date, parameters)
    active = [r for r in rules if r.enabled]
    for rule in active:
        if rule.kind == "sql":
            _run_sql_rule(conn, rule, params)
        elif rule.kind == "python":
            _run_python_rule(conn, rule, ctx)
    accepted, rejected = _cascade(conn, snapshot_id, {r.rule_id: r for r in rules}, parameters)
    f = validation_finding.c
    conn.execute(insert(validation_summary).from_select(
        ["snapshot_id", "rule_id", "outcome", "finding_count"],
        select(f.snapshot_id, f.rule_id, f.outcome, func.count()).where(f.snapshot_id == snapshot_id)
        .group_by(f.snapshot_id, f.rule_id, f.outcome)))
    s = validation_summary.c
    by_outcome = {o: n for o, n in conn.execute(select(s.outcome, func.sum(s.finding_count)).where(
        s.snapshot_id == snapshot_id).group_by(s.outcome))}
    return ValidationResult(snapshot_id, accepted + rejected, accepted, rejected,
                            bool(by_outcome.get(str(Outcome.REJECT_FILE))), {k: int(v) for k, v in by_outcome.items()})


def snapshot_result(engine: Engine, snapshot_id: int) -> ValidationResult:
    """Read the stored result of an already validated snapshot (used when a snapshot is reused)."""
    s, o = validation_summary.c, record_outcome.c
    with engine.connect() as conn:
        by_outcome = {k: int(v) for k, v in conn.execute(select(s.outcome, func.sum(s.finding_count)).where(
            s.snapshot_id == snapshot_id).group_by(s.outcome))}
        records = dict(conn.execute(select(o.outcome, func.count()).where(o.snapshot_id == snapshot_id)
                                    .group_by(o.outcome)).all())
    accepted, rejected = records.get(str(RecordOutcome.ACCEPTED), 0), records.get(str(RecordOutcome.REJECTED), 0)
    return ValidationResult(snapshot_id, accepted + rejected, accepted, rejected,
                            bool(by_outcome.get(str(Outcome.REJECT_FILE))), by_outcome)


def validate_snapshot(engine: Engine, snapshots: SnapshotRepository, snapshot_id: int,
                      rules: list[Rule], parameters: dict | None = None) -> ValidationResult:
    """Validate an EXTRACTED (or re-validate a VALIDATED) snapshot and mark it VALIDATED.

    `parameters` default to the snapshot tenant's effective rule parameters (pack defaults + overrides).
    """
    snap = snapshots.get(snapshot_id)
    if snap.status not in (SnapshotStatus.EXTRACTED, SnapshotStatus.VALIDATED):
        raise ValidationError(f"Snapshot {snapshot_id} is {snap.status}; only extracted snapshots can be validated")
    if parameters is None:
        parameters = load_parameters(engine, snap.tenant_id, snap.submission_type)
    try:
        with engine.begin() as conn:
            result = run_validation(conn, snapshot_id, snap.as_of_date, rules, parameters)
    except Exception as exc:
        snapshots.mark_failed(snapshot_id, f"validation: {exc}")
        raise
    snapshots.mark_validated(snapshot_id)
    return result
