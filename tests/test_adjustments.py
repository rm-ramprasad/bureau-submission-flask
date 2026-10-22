"""Exclusions and overrides of a request: capture rules, the SQL join into the extract, validation reacting."""

from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

from mock_data.generate_mock_data import load_into
from submissions.adapters.extract import ExtractError, run_extract
from submissions.adapters.mapping import load_mapping
from submissions.adjustments import (
    AdjustmentError, AdjustmentRepository, AdjustmentScope, applied_summary, parse_keys, parse_override_value,
)
from submissions.adjustments.tables import snapshot_adjustment
from submissions.bureaus.seed import seed as seed_bureaus
from submissions.canonical.schema import ENTITY_TABLES
from submissions.enums import (
    AdjustmentKind as K, BureauCode as B, ExtractStrategy, OverrideField as F, RequestStatus as S, RunMode as M,
    SubmissionType as T,
)
from submissions.migrate import MIGRATIONS_DIR
from submissions.pipeline import Services
from submissions.requests import InvalidTransitionError
from submissions.snapshot import SnapshotRepository
from submissions.storage import LocalFileStore
from submissions.validation.tables import validation_finding
from submissions.worker.runner import Worker

ROOT = Path(__file__).resolve().parent.parent
AS_OF = date(2026, 9, 30)
JOINT_LOAN = "B122HL10788234"            # L00002, holders C00002 + C00003
MULTI_LOAN_CUSTOMER = "C00001"           # holds B123HL54004637 and B107CC76729014
CLEAN_LOAN = "B107PL79230310"            # 0 DPD, 0 overdue


@pytest.fixture
def adjustments(engine, clock):
    return AdjustmentRepository(engine, clock=clock)


@pytest.fixture
def worker(engine, requests_repo, tenant_repo, bureau_repo, tmp_path):
    """LMS schema on the app server (as on RDS): adjustments are joined into the extract SQL."""
    load_into(engine, ["nbfc_001"])
    tenant_repo.upsert(replace(tenant_repo.get("nbfc_001"), extract_strategy=ExtractStrategy.INSERT_SELECT))
    seed_bureaus(bureau_repo, ["nbfc_001"])
    services = Services(app_engine=engine, source_engine=engine, snapshots=SnapshotRepository(engine),
                        base_dir=ROOT, store=LocalFileStore(tmp_path))
    return Worker(services, requests_repo, tenant_repo, bureau_repo, worker_id="w-test")


def draft(requests_repo, tenant="nbfc_001") -> AdjustmentScope:
    batch = requests_repo.create_batch(tenant, AS_OF, M.VALIDATE, [T.CONSUMER], draft=True)
    return AdjustmentScope.of(requests_repo.list_batch(batch)[0])


def submit(worker, requests_repo, scope):
    requests_repo.submit_draft(scope.batch_id)
    worker.run_once()
    (row,) = requests_repo.list_batch(scope.batch_id)
    assert row.status is S.SUCCEEDED, row.error_message
    return row


def rows(engine, entity, snapshot_id, **where):
    t = ENTITY_TABLES[entity]
    q = select(t).where(t.c.snapshot_id == snapshot_id)
    for col, value in where.items():
        q = q.where(t.c[col] == value)
    with engine.connect() as conn:
        return conn.execute(q).all()


def findings(engine, snapshot_id, rule_id):
    f = validation_finding.c
    with engine.connect() as conn:
        return conn.execute(select(f.account_key, f.value).where(f.snapshot_id == snapshot_id,
                                                                 f.rule_id == rule_id)).all()


def effects(engine, snapshot_id, action):
    return [a for a in AdjustmentRepository(engine).applied(snapshot_id) if a.action == action]


# ---------------------------------------------------------------- input rules
def test_parse_keys_splits_dedupes_and_limits_length():
    assert parse_keys(" A1\nA2, A3;A1\n\n  A4 ") == ["A1", "A2", "A3", "A4"]
    with pytest.raises(AdjustmentError, match="Longer than 50"):
        parse_keys("X" * 51)


@pytest.mark.parametrize("field, raw, expected", [
    (F.DAYS_PAST_DUE, "45", Decimal(45)),
    (F.AMOUNT_OVERDUE, "1,250.50", Decimal("1250.50")),
    (F.CURRENT_BALANCE, "-100", Decimal(-100)),             # a ledger balance can be a credit
])
def test_override_values_accepted(field, raw, expected):
    assert parse_override_value(field, raw) == expected


@pytest.mark.parametrize("field, raw, message", [
    (F.DAYS_PAST_DUE, "4.5", "whole number"),
    (F.DAYS_PAST_DUE, "10000", "whole number"),
    (F.AMOUNT_OVERDUE, "-1", "cannot be negative"),
    (F.SETTLEMENT_AMOUNT, "1.234", "2 decimals"),
    (F.AMOUNT_OVERDUE, "abc", "not a number"),
    (F.AMOUNT_OVERDUE, "NaN", "not a number"),
])
def test_override_values_rejected(field, raw, message):
    with pytest.raises(AdjustmentError, match=message):
        parse_override_value(field, raw)


def test_duplicates_skipped_and_override_replaces_previous(adjustments, requests_repo):
    scope = draft(requests_repo)
    first = adjustments.add_exclusions(scope, K.EXCLUDE_ACCOUNT, ["A1", "A2"], " r ", "u")
    again = adjustments.add_exclusions(scope, K.EXCLUDE_ACCOUNT, ["A2", "A3"], None, "u")
    assert (first.added, again.added, again.already_present) == (["A1", "A2"], ["A3"], ["A2"])
    one, replaced = adjustments.add_override(scope, "A9", F.DAYS_PAST_DUE, Decimal(30), None, "u")
    _, replaced_two = adjustments.add_override(scope, "A9", F.DAYS_PAST_DUE, Decimal(60), None, "u")
    assert replaced is None and replaced_two == one
    active = adjustments.list(scope)
    assert [a.override_value for a in active if a.kind is K.OVERRIDE] == [Decimal(60)]
    assert {a.reason for a in active if a.key == "A1"} == {"r"}                    # trimmed
    assert {a.batch_id for a in active} == {scope.batch_id}                       # tagged with the request


def test_remove_is_soft_and_scoped_to_the_request(adjustments, requests_repo):
    scope, other = draft(requests_repo), draft(requests_repo, "nbfc_002")
    adjustments.add_exclusions(scope, K.EXCLUDE_ACCOUNT, ["A1"], None, "u")
    adjustments.add_exclusions(other, K.EXCLUDE_ACCOUNT, ["A1"], None, "u")
    (adj,) = adjustments.list(scope)
    adjustments.remove(adj.adjustment_id, "boss")
    assert adjustments.list(scope) == []
    (gone,) = adjustments.list(scope, include_removed=True)
    assert gone.removed_by == "boss" and len(adjustments.list(other)) == 1
    with pytest.raises(AdjustmentError, match="already removed"):
        adjustments.remove(adj.adjustment_id, "boss")


# ---------------------------------------------------------------- the request lifecycle
def test_draft_is_not_picked_up_until_submitted(worker, requests_repo):
    scope = draft(requests_repo)
    assert worker.run_once() == 0
    with pytest.raises(Exception, match="Already open"):           # the draft holds the duplicate guard
        requests_repo.create_batch("nbfc_001", AS_OF, M.VALIDATE, [T.CONSUMER])
    submit(worker, requests_repo, scope)
    with pytest.raises(InvalidTransitionError, match="not a draft"):
        requests_repo.submit_draft(scope.batch_id)


# ---------------------------------------------------------------- joined into the extract
def test_no_adjustments_changes_nothing(worker, requests_repo):
    row = submit(worker, requests_repo, draft(requests_repo))
    assert (row.total_records, row.accepted_records, row.rejected_records) == (56, 48, 8)
    assert (row.excluded_accounts, row.overridden_accounts) == (0, 0)


def test_excluding_a_joint_loan_removes_it_whole(worker, requests_repo, engine, adjustments):
    scope = draft(requests_repo)
    adjustments.add_exclusions(scope, K.EXCLUDE_ACCOUNT, [JOINT_LOAN], "disputed", "u")
    row = submit(worker, requests_repo, scope)
    assert rows(engine, "account", row.snapshot_id, account_number=JOINT_LOAN) == []
    assert rows(engine, "account_holder", row.snapshot_id, account_key="L00002") == []     # both holders
    assert row.total_records == 54 and row.excluded_accounts == 1
    assert [a.account_key for a in effects(engine, row.snapshot_id, "EXCLUDE")] == ["L00002"]


def test_excluding_a_customer_excludes_every_loan_they_hold(worker, requests_repo, engine, adjustments):
    scope = draft(requests_repo)
    adjustments.add_exclusions(scope, K.EXCLUDE_CUSTOMER, [MULTI_LOAN_CUSTOMER], None, "u")
    row = submit(worker, requests_repo, scope)
    snap = row.snapshot_id
    for number in ("B123HL54004637", "B107CC76729014"):
        assert rows(engine, "account", snap, account_number=number) == []
    assert rows(engine, "account_holder", snap, party_key=MULTI_LOAN_CUSTOMER) == []
    assert rows(engine, "party", snap, party_key=MULTI_LOAN_CUSTOMER) == []                # no loan left: data gone
    assert rows(engine, "party_address", snap, party_key=MULTI_LOAN_CUSTOMER) == []
    assert row.excluded_accounts == 2 and len(effects(engine, snap, "EXCLUDE")) == 2
    assert MULTI_LOAN_CUSTOMER in {a.party_key for a in effects(engine, snap, "EXCLUDE_PARTY")}


def test_override_feeds_validation(worker, requests_repo, engine, adjustments):
    scope = draft(requests_repo)
    adjustments.add_override(scope, CLEAN_LOAN, F.DAYS_PAST_DUE, Decimal(45), "late", "u")
    row = submit(worker, requests_repo, scope)
    (account,) = rows(engine, "account", row.snapshot_id, account_number=CLEAN_LOAN)
    assert account.days_past_due == 45
    flagged = {f.account_key for f in findings(engine, row.snapshot_id, "UCRF.TL.DPD_WITHOUT_OVERDUE")}
    assert account.account_key in flagged                              # validation saw the overridden value
    (applied,) = effects(engine, row.snapshot_id, "OVERRIDE")
    assert (applied.field, applied.old_value, applied.new_value) == (F.DAYS_PAST_DUE, Decimal(0), Decimal(45))
    assert row.overridden_accounts == 1

    second = draft(requests_repo)                                      # more exceptions: a new request
    adjustments.add_override(second, CLEAN_LOAN, F.DAYS_PAST_DUE, Decimal(45), None, "u")
    adjustments.add_override(second, CLEAN_LOAN, F.AMOUNT_OVERDUE, Decimal("5000"), None, "u")
    again = submit(worker, requests_repo, second)
    assert account.account_key not in {f.account_key for f in findings(engine, again.snapshot_id,
                                                                       "UCRF.TL.DPD_WITHOUT_OVERDUE")}
    (account,) = rows(engine, "account", again.snapshot_id, account_number=CLEAN_LOAN)
    assert (account.days_past_due, account.amount_overdue) == (45, Decimal("5000.00"))
    assert again.overridden_accounts == 1                              # one loan, two fields


def test_unmatched_adjustments_are_reported(worker, requests_repo, engine, adjustments):
    scope = draft(requests_repo)
    adjustments.add_exclusions(scope, K.EXCLUDE_ACCOUNT, ["NO-SUCH-LOAN"], None, "u")
    adjustments.add_override(scope, "NOPE", F.AMOUNT_OVERDUE, Decimal(1), None, "u")
    row = submit(worker, requests_repo, scope)
    values = sorted(f.value for f in findings(engine, row.snapshot_id, "UCRF.ADJ.NOT_FOUND"))
    assert values == ["EXCLUDE_ACCOUNT NO-SUCH-LOAN", "OVERRIDE NOPE amount_overdue"]
    assert (row.total_records, row.accepted_records) == (56, 48)       # WARN only
    assert applied_summary(engine, row.snapshot_id).not_found == 2


def test_only_this_requests_active_adjustments_apply(worker, requests_repo, adjustments):
    other = draft(requests_repo, "nbfc_002")
    adjustments.add_exclusions(other, K.EXCLUDE_ACCOUNT, [CLEAN_LOAN], None, "u")
    scope = draft(requests_repo)
    adjustments.add_exclusions(scope, K.EXCLUDE_ACCOUNT, [JOINT_LOAN], None, "u")
    (adj,) = adjustments.list(scope)
    adjustments.remove(adj.adjustment_id, "u")
    row = submit(worker, requests_repo, scope)
    assert (row.total_records, row.excluded_accounts) == (56, 0)


def test_generate_reuses_the_adjusted_snapshot(worker, requests_repo, adjustments):
    scope = draft(requests_repo)
    adjustments.add_exclusions(scope, K.EXCLUDE_ACCOUNT, [JOINT_LOAN], None, "u")
    v = submit(worker, requests_repo, scope)
    g = requests_repo.create_batch("nbfc_001", AS_OF, M.GENERATE, [T.CONSUMER], [B.CIBIL], snapshot_id=v.snapshot_id)
    worker.run_once()
    (row,) = requests_repo.list_batch(g)
    assert row.status is S.SUCCEEDED, row.error_message
    assert (row.total_records, row.excluded_accounts) == (54, 1)
    assert JOINT_LOAN.encode() not in Path(row.output_file_path).read_bytes()


def test_migration_view_matches_code():
    from submissions.adjustments.tables import ADJUSTMENT_VIEW_SQL
    sql = (MIGRATIONS_DIR / "006_adjustments.sql").read_text()
    assert ADJUSTMENT_VIEW_SQL + ";" in sql


def test_frozen_copy_is_the_audit_trail(worker, requests_repo, engine, adjustments):
    scope = draft(requests_repo)
    adjustments.add_exclusions(scope, K.EXCLUDE_CUSTOMER, [MULTI_LOAN_CUSTOMER], "deceased", "ops-user")
    adjustments.add_exclusions(scope, K.EXCLUDE_ACCOUNT, ["NO-SUCH-LOAN"], None, "u")
    row = submit(worker, requests_repo, scope)
    by_kind = {a.kind: a for a in adjustments.list(scope)}
    customer, missing = by_kind[K.EXCLUDE_CUSTOMER], by_kind[K.EXCLUDE_ACCOUNT]
    assert AdjustmentRepository(engine).frozen(row.snapshot_id) == {customer.adjustment_id: 2,
                                                                   missing.adjustment_id: 0}
    adjustments.remove(customer.adjustment_id, "u")                     # later edits don't rewrite history
    with engine.connect() as conn:
        frozen = conn.execute(select(snapshot_adjustment).where(
            snapshot_adjustment.c.snapshot_id == row.snapshot_id,
            snapshot_adjustment.c.adjustment_id == customer.adjustment_id)).one()
    assert (frozen.customer_id, frozen.reason, frozen.created_by) == (MULTI_LOAN_CUSTOMER, "deceased", "ops-user")


def test_adjustments_need_the_lms_on_the_app_server(engine, lms_engine, tenant_repo, requests_repo, adjustments):
    scope = draft(requests_repo)
    adjustments.add_exclusions(scope, K.EXCLUDE_ACCOUNT, [JOINT_LOAN], None, "u")
    tenant = tenant_repo.get("nbfc_001")                                 # stream from a separate server
    with pytest.raises(ExtractError, match="must be on the app's MySQL server"):
        run_extract(tenant=tenant, mapping=load_mapping(ROOT / tenant.mapping_path), submission_type="CONSUMER",
                    as_of_date=AS_OF, app_engine=engine, source_engine=lms_engine,
                    snapshots=SnapshotRepository(engine), owner="t", batch_id=scope.batch_id)


def test_without_adjustments_a_separate_lms_server_still_works(engine, lms_engine, tenant_repo):
    tenant = tenant_repo.get("nbfc_001")
    result = run_extract(tenant=tenant, mapping=load_mapping(ROOT / tenant.mapping_path), submission_type="CONSUMER",
                         as_of_date=AS_OF, app_engine=engine, source_engine=lms_engine,
                         snapshots=SnapshotRepository(engine), owner="t")
    assert result.row_counts["account_holder"] == 56
