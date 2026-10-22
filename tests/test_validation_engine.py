from collections import Counter
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import func, select

from mock_data.generate_mock_data import tenant_rows
from submissions.adapters.extract import run_extract
from submissions.adapters.mapping import load_mapping
from submissions.enums import Outcome, RecordOutcome, SnapshotStatus
from submissions.snapshot import SnapshotRepository
from submissions.validation.engine import ValidationError, validate_snapshot
from submissions.validation.rules import (
    Rule, RuleDefinitionError, builtin_rules, load_rule_set, save_custom_rule, set_policy,
)
from submissions.validation.tables import record_outcome, validation_finding, validation_summary

CLIENTS = Path(__file__).resolve().parent.parent / "clients"
AS_OF = date(2026, 9, 30)
TENANTS = [("nbfc_001", 1, False), ("nbfc_002", 2, True)]
CONFIG_RULES = ["UCRF.TL.GUARANTEE_INVOKED_NOT_GUARANTOR", "UCRF.TL.WRITTEN_OFF_AMOUNTS_MISSING"]

# scenario -> rule that must reject the record (REJECT_RECORD) or the segment/field (REJECT_SEGMENT/FIELD)
EXPECTED_RULE = {
    30: "UCRF.TL.GUARANTEE_INVOKED_NOT_GUARANTOR", 40: "UCRF.REC.SINGLE_WORD_NAME", 41: "UCRF.PN.NAME_DIGITS",
    42: "UCRF.REC.NO_PHONE_OR_ID", 43: "UCRF.ID.NUMBER_FORMAT", 44: "UCRF.PT.NUMBER_INVALID",
    45: "UCRF.EC.EMAIL_INVALID", 46: "UCRF.REC.NO_VALID_ADDRESS", 47: "UCRF.TL.CLOSED_WITH_BALANCE",
    48: "UCRF.TL.DPD_WITHOUT_OVERDUE", 49: "UCRF.TL.WRITTEN_OFF_AMOUNTS_MISSING", 50: "UCRF.TL.EMI_NOT_ALLOWED",
    51: "UCRF.TL.DPD_AND_ASSET_CLASS_MISSING", 52: "UCRF.PN.DOB_AFTER_DATE_OPENED",
}


def extract(engine, lms_engine, tenant_repo, tenant_id):
    return run_extract(tenant=tenant_repo.get(tenant_id), mapping=load_mapping(CLIENTS / tenant_id / "mapping.yaml"),
                       submission_type="CONSUMER", as_of_date=AS_OF, app_engine=engine, source_engine=lms_engine,
                       snapshots=SnapshotRepository(engine), owner="test").snapshot_id


def validate(engine, tenant_id, snapshot_id):
    return validate_snapshot(engine, SnapshotRepository(engine), snapshot_id,
                             load_rule_set(engine, tenant_id, "CONSUMER"))


def outcomes(engine, snapshot_id) -> dict[tuple[str, str], str]:
    with engine.connect() as conn:
        return {(r.account_key, r.party_key): r.outcome for r in conn.execute(
            select(record_outcome).where(record_outcome.c.snapshot_id == snapshot_id))}


def findings(engine, snapshot_id, **where):
    f = validation_finding.c
    with engine.connect() as conn:
        return conn.execute(select(validation_finding).where(
            f.snapshot_id == snapshot_id, *(f[k] == v for k, v in where.items()))).all()


def hits(fs, loan, cust):
    return [x for x in fs if x.account_key in (None, loan) and x.party_key in (None, cust)
            and (x.account_key, x.party_key) != (None, None)]


# ---------------------------------------------------------------- catalogue
def test_pack_loads_with_generated_rules():
    rules = builtin_rules("CONSUMER")
    kinds = Counter(r.kind for r in rules)
    assert kinds["python"] == 10 and kinds["cascade"] == 3 and kinds["sql"] > 80
    ids = [r.rule_id for r in rules]
    assert len(ids) == len(set(ids)) and all(i.startswith("UCRF.") for i in ids)
    assert "UCRF.REQUIRED.account.account_number" in ids and "UCRF.ENUM.party_address.state_code" in ids
    assert all(":snapshot_id" in r.sql for r in rules if r.sql)


def test_no_builtin_pack_for_other_types():
    with pytest.raises(RuleDefinitionError):
        builtin_rules("NESL")


@pytest.mark.parametrize("rule,message", [
    (Rule("UCRF.MINE", "CUSTOM", "account", Outcome.WARN, "m", sql="SELECT :snapshot_id"), "must not start"),
    (Rule("C.X", "CUSTOM", "account", Outcome.WARN, "m", sql="SELECT 1"), ":snapshot_id"),
    (Rule("C.X", "CUSTOM", "loan", Outcome.WARN, "m", sql="SELECT :snapshot_id"), "unknown entity"),
    (Rule("C.X", "CUSTOM", "account", Outcome.WARN, "m", python="name_digits"), "SQL rules"),
])
def test_custom_rule_checks(engine, rule, message):
    with pytest.raises(RuleDefinitionError, match=message):
        save_custom_rule(engine, "nbfc_001", "CONSUMER", rule)


# ---------------------------------------------------------------- end to end against the mock oracle
@pytest.mark.parametrize("tenant_id,tenant_no,variant", TENANTS)
def test_mock_scenarios_match_expected_outcomes(engine, lms_engine, tenant_repo, tenant_id, tenant_no, variant):
    sid = extract(engine, lms_engine, tenant_repo, tenant_id)
    result = validate(engine, tenant_id, sid)
    got = outcomes(engine, sid)
    all_findings = findings(engine, sid)
    rows = tenant_rows(tenant_no, variant)["account_details"]
    assert result.records == len(rows) == len(got)

    problems = []
    for r in rows:
        key, expected, no = (r["loan_id"], r["customer_id"]), r["test_expected"], r["test_scenario_number"]
        outcome, mine = got[key], hits(all_findings, *key)
        want_rule = EXPECTED_RULE.get(no)
        if expected in ("ACCEPT", "CONFIG_REJECT_RECORD") or expected.startswith(("REJECT_SEGMENT", "REJECT_FIELD")):
            if outcome != RecordOutcome.ACCEPTED:
                rejecting = sorted({x.rule_id for x in mine if x.outcome == Outcome.REJECT_RECORD})
                problems.append(f"#{no} {key}: expected accepted, got rejected by {rejecting}")
        elif outcome != RecordOutcome.REJECTED:
            problems.append(f"#{no} {key}: expected rejected, got accepted")
        if want_rule and expected != "ACCEPT" and want_rule not in {x.rule_id for x in mine}:
            problems.append(f"#{no} {key}: no finding from {want_rule}")
        if expected.startswith("REJECT_SEGMENT:"):
            seg = expected.split(":")[1]
            if not any(x.segment == seg and x.outcome == Outcome.REJECT_SEGMENT for x in mine):
                problems.append(f"#{no}: no {seg} segment rejected")
    assert not problems, "\n".join(problems)
    assert SnapshotRepository(engine).get(sid).status is SnapshotStatus.VALIDATED
    assert result.accepted + result.rejected == result.records and not result.file_rejected


def test_policy_escalates_configurable_rules(engine, lms_engine, tenant_repo):
    for rule_id in CONFIG_RULES:
        set_policy(engine, "nbfc_001", rule_id, outcome=Outcome.REJECT_RECORD)
    sid = extract(engine, lms_engine, tenant_repo, "nbfc_001")
    validate(engine, "nbfc_001", sid)
    got = outcomes(engine, sid)
    for r in tenant_rows(1, False)["account_details"]:
        key = (r["loan_id"], r["customer_id"])
        if r["test_expected"] == "CONFIG_REJECT_RECORD":
            assert got[key] == RecordOutcome.REJECTED, r["test_scenario_number"]
        if r["test_scenario_number"] == 30 and r["party_role"] == "GUARANTOR":
            assert got[key] == RecordOutcome.ACCEPTED


def test_policy_can_disable_a_rule_for_one_tenant(engine, lms_engine, tenant_repo):
    set_policy(engine, "nbfc_001", "UCRF.TL.ASSET_CLASS_WITH_DPD", is_enabled=False)
    sid = extract(engine, lms_engine, tenant_repo, "nbfc_001")
    validate(engine, "nbfc_001", sid)
    assert findings(engine, sid, rule_id="UCRF.TL.ASSET_CLASS_WITH_DPD") == []
    other = next(r for r in load_rule_set(engine, "nbfc_002", "CONSUMER") if r.rule_id == "UCRF.TL.ASSET_CLASS_WITH_DPD")
    assert other.enabled


def test_custom_rule_on_extension_attribute(engine, lms_engine, tenant_repo):
    save_custom_rule(engine, "nbfc_002", "CONSUMER", Rule(
        "NBFC002.STAFF_SCHEME_REVIEW", "CUSTOM", "account", Outcome.WARN, "Staff scheme loans need review",
        segment="TL", field="scheme_code",
        sql="SELECT a.row_id, a.account_key, NULL AS party_key, e.attr_value AS value\n"
            "FROM c_account a JOIN c_extension_attribute e ON e.snapshot_id = a.snapshot_id\n"
            "  AND e.entity = 'account' AND e.entity_key = a.account_key AND e.attr_name = 'scheme_code'\n"
            "WHERE a.snapshot_id = :snapshot_id AND e.attr_value = 'STAFF'"))
    assert "NBFC002.STAFF_SCHEME_REVIEW" not in {r.rule_id for r in load_rule_set(engine, "nbfc_001", "CONSUMER")}
    sid = extract(engine, lms_engine, tenant_repo, "nbfc_002")
    validate(engine, "nbfc_002", sid)
    staff = {r["loan_id"] for r in tenant_rows(2, True)["loan_ext"] if r["scheme_code"] == "STAFF"}
    assert {x.account_key for x in findings(engine, sid, rule_id="NBFC002.STAFF_SCHEME_REVIEW")} == staff


def test_revalidation_replaces_results_and_summary_matches(engine, lms_engine, tenant_repo):
    sid = extract(engine, lms_engine, tenant_repo, "nbfc_001")
    first = validate(engine, "nbfc_001", sid)
    second = validate(engine, "nbfc_001", sid)
    assert first == second
    with engine.connect() as conn:
        total = conn.execute(select(func.count()).select_from(validation_finding)
                             .where(validation_finding.c.snapshot_id == sid)).scalar()
        summed = conn.execute(select(func.sum(validation_summary.c.finding_count))
                              .where(validation_summary.c.snapshot_id == sid)).scalar()
    assert total == summed == sum(second.findings.values())


def test_only_extracted_snapshots_can_be_validated(engine):
    snapshots = SnapshotRepository(engine)
    sid = snapshots.create("nbfc_001", "CONSUMER", AS_OF, None, "v", "w")   # still BUILDING
    with pytest.raises(ValidationError, match="BUILDING"):
        validate(engine, "nbfc_001", sid)
