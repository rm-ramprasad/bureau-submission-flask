from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

from submissions.bureaus.seed import seed as seed_bureaus
from submissions.canonical.schema import ENTITY_TABLES
from submissions.enums import ReportingCycle as C, RequestStatus as S, RunMode as M, SnapshotStatus
from submissions.ops import audit
from submissions.ops.heartbeat import Heartbeat, last_seen, worker_alive
from submissions.ops.recovery import RecoverySettings, recover
from submissions.ops.retention import purge
from submissions.ops.schedule import default_as_of, schedule_runs
from submissions.pipeline import Services
from submissions.storage import LocalFileStore
from submissions.snapshot import SnapshotRepository
from submissions.tenants import Tenant
from submissions.validation.tables import record_outcome, validation_finding, validation_summary
from submissions.worker.runner import Worker

ROOT = Path(__file__).resolve().parent.parent
AS_OF = date(2026, 9, 30)
T0 = datetime(2026, 10, 21, 9, 0, 0)          # FakeClock start used by the repositories


def count(engine, table, **where) -> int:
    with engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(table)
                            .where(*(table.c[k] == v for k, v in where.items()))).scalar()


@pytest.fixture
def worker(engine, lms_engine, requests_repo, tenant_repo, bureau_repo, tmp_path):
    seed_bureaus(bureau_repo, ["nbfc_001", "nbfc_002"])
    services = Services(app_engine=engine, source_engine=lms_engine, snapshots=SnapshotRepository(engine),
                        base_dir=ROOT, store=LocalFileStore(tmp_path))
    return Worker(services, requests_repo, tenant_repo, bureau_repo, worker_id="w-ops",
                  recovery=RecoverySettings(run_timeout=timedelta(days=3650), build_timeout=timedelta(days=3650)))


# ---------------------------------------------------------------- heartbeat and audit
def test_heartbeat_marks_worker_alive(engine, clock):
    assert last_seen(engine) is None and not worker_alive(engine, now=T0)
    hb = Heartbeat(engine, "w1", clock=clock)
    hb.beat()
    hb.beat(processed=2)
    assert last_seen(engine) == T0 and worker_alive(engine, now=T0 + timedelta(seconds=20))
    assert not worker_alive(engine, now=T0 + timedelta(seconds=31))


def test_audit_records_and_lists(engine):
    audit.record(engine, "ops.priya", "RUN_CREATED", tenant_id="nbfc_001", target="b1", bureaus=["CIBIL"])
    audit.record(engine, "scheduler", "RUN_SCHEDULED", tenant_id="nbfc_002", target="b2")
    (latest, first) = audit.recent(engine)
    assert (latest.actor, latest.action) == ("scheduler", "RUN_SCHEDULED")
    assert first.details == '{"bureaus": ["CIBIL"]}'
    assert [e.tenant_id for e in audit.recent(engine, tenant_id="nbfc_001")] == ["nbfc_001"]


# ---------------------------------------------------------------- recovery
def test_stuck_running_rows_are_released_then_failed(engine, requests_repo):
    requests_repo.create_batch("nbfc_001", AS_OF, M.VALIDATE, ["CONSUMER"])
    req = requests_repo.claim_next("dead-worker")                       # started at T0, attempt 1
    later = lambda: T0 + timedelta(hours=3)                             # noqa: E731
    settings = RecoverySettings(run_timeout=timedelta(hours=2), max_attempts=2)
    result = recover(engine, requests_repo, SnapshotRepository(engine), settings, clock=later)
    assert result.released == [req.request_id] and requests_repo.get(req.request_id).status is S.PENDING

    requests_repo.claim_next("dead-worker-2")                           # attempt 2, also dies
    result = recover(engine, requests_repo, SnapshotRepository(engine), settings, clock=later)
    failed = requests_repo.get(req.request_id)
    assert result.failed == [req.request_id] and failed.status is S.FAILED and "Timed out" in failed.error_message
    assert [e.action for e in audit.recent(engine)] == ["REQUEST_RECOVERED", "REQUEST_RECOVERED"]


def test_recent_running_rows_are_left_alone(engine, requests_repo):
    requests_repo.create_batch("nbfc_001", AS_OF, M.VALIDATE, ["CONSUMER"])
    req = requests_repo.claim_next("busy-worker")
    result = recover(engine, requests_repo, SnapshotRepository(engine), clock=lambda: T0 + timedelta(minutes=30))
    assert result.released == [] and requests_repo.get(req.request_id).status is S.RUNNING


def test_abandoned_snapshot_build_is_failed_and_cleaned(engine, clock, requests_repo):
    snapshots = SnapshotRepository(engine, clock=clock)
    sid = snapshots.create("nbfc_001", "CONSUMER", AS_OF, "b1", "v", "dead-worker")
    with engine.begin() as conn:
        conn.execute(ENTITY_TABLES["party"].insert(), [dict(snapshot_id=sid, party_key="C1")])
    result = recover(engine, requests_repo, snapshots, clock=lambda: T0 + timedelta(hours=3))
    assert result.snapshots_failed == [sid]
    snap = snapshots.get(sid)
    assert snap.status is SnapshotStatus.FAILED and "abandoned" in snap.error_message
    assert count(engine, ENTITY_TABLES["party"], snapshot_id=sid) == 0


def test_retry_rebuilds_a_failed_batch_snapshot(worker, requests_repo, tenant_repo, tmp_path):
    good = tenant_repo.get("nbfc_001")
    bad = tmp_path / "bad.yaml"
    bad.write_text(f'canonical_version: "1.0"\nextends: {(ROOT / "clients" / "_base" / "mapping.yaml").as_posix()}\n'
                   "entities: {account: {columns: {days_past_due: a.no_such_column}}}\n", encoding="utf-8")
    tenant_repo.upsert(Tenant("nbfc_001", good.display_name, "nbfc_001", str(bad)))
    batch = requests_repo.create_batch("nbfc_001", AS_OF, M.VALIDATE, ["CONSUMER"])
    worker.run_once()
    (row,) = requests_repo.list_batch(batch)
    assert row.status is S.FAILED

    tenant_repo.upsert(good)                                            # mapping fixed
    requests_repo.requeue(row.request_id)
    worker.run_once()
    retried = requests_repo.get(row.request_id)
    assert retried.status is S.SUCCEEDED and retried.attempt_count == 2, retried.error_message
    assert SnapshotRepository(worker.services.app_engine).get(retried.snapshot_id).status is SnapshotStatus.VALIDATED


def test_worker_beats_while_polling(worker, engine, requests_repo):
    worker.heartbeat = Heartbeat(engine, "w-ops")
    requests_repo.create_batch("nbfc_001", AS_OF, M.VALIDATE, ["CONSUMER"])
    worker.run_once()
    assert worker_alive(engine)


# ---------------------------------------------------------------- retention
def test_purge_removes_pii_keeps_summary(worker, engine, requests_repo, tmp_path):
    v = requests_repo.create_batch("nbfc_001", AS_OF, M.VALIDATE, ["CONSUMER"])
    worker.run_once()
    batch = requests_repo.create_batch("nbfc_001", AS_OF, M.GENERATE, ["CONSUMER"], ["CIBIL"],
                                       snapshot_id=requests_repo.list_batch(v)[0].snapshot_id)
    worker.run_once()
    (row,) = requests_repo.list_batch(batch)
    sid, files = row.snapshot_id, [Path(row.output_file_path), Path(row.exceptions_file_path)]
    assert row.status is S.SUCCEEDED and all(f.exists() for f in files)
    summary_rows = count(engine, validation_summary, snapshot_id=sid)

    future = lambda: datetime.now() + timedelta(days=200)               # noqa: E731
    preview = purge(engine, timedelta(days=90), LocalFileStore(tmp_path), dry_run=True, clock=future)
    assert preview.snapshots == [sid] and len(preview.files) == 2 and all(f.exists() for f in files)

    purge(engine, timedelta(days=90), LocalFileStore(tmp_path), clock=future)
    assert SnapshotRepository(engine).get(sid).status is SnapshotStatus.PURGED
    assert count(engine, ENTITY_TABLES["party"], snapshot_id=sid) == 0
    assert count(engine, validation_finding, snapshot_id=sid) == 0 and count(engine, record_outcome, snapshot_id=sid) == 0
    assert count(engine, validation_summary, snapshot_id=sid) == summary_rows > 0
    assert not any(f.exists() for f in files) and requests_repo.get(row.request_id).output_file_path is None
    assert audit.recent(engine)[0].action == "RETENTION_PURGE"


def test_purge_keeps_recent_data(worker, engine, requests_repo, tmp_path):
    requests_repo.create_batch("nbfc_001", AS_OF, M.VALIDATE, ["CONSUMER"])
    worker.run_once()
    assert purge(engine, timedelta(days=90), LocalFileStore(tmp_path)).snapshots == []


def test_generate_from_purged_snapshot_is_refused(worker, engine, requests_repo, tmp_path):
    v = requests_repo.create_batch("nbfc_001", AS_OF, M.VALIDATE, ["CONSUMER"])
    worker.run_once()
    sid = requests_repo.list_batch(v)[0].snapshot_id
    purge(engine, timedelta(days=90), LocalFileStore(tmp_path), clock=lambda: datetime.now() + timedelta(days=200))
    g = requests_repo.create_batch("nbfc_001", AS_OF, M.GENERATE, ["CONSUMER"], ["CIBIL"], snapshot_id=sid)
    worker.run_once()
    (row,) = requests_repo.list_batch(g)
    assert row.status is S.FAILED and "PURGED" in row.error_message


# ---------------------------------------------------------------- scheduling
@pytest.mark.parametrize("cycle,today,expected", [
    (C.MONTH_END, date(2026, 10, 21), date(2026, 9, 30)),
    (C.MONTH_END, date(2026, 3, 1), date(2026, 2, 28)),
    (C.WEEKLY_1, date(2026, 10, 21), date(2026, 10, 9)),
    (C.WEEKLY_3, date(2026, 10, 21), date(2026, 9, 23)),       # 23rd not reached yet this month
    (C.WEEKLY_2, date(2026, 10, 16), date(2026, 10, 16)),
    (C.DAILY, date(2026, 10, 21), date(2026, 10, 20)),
])
def test_default_as_of(cycle, today, expected):
    assert default_as_of(cycle, today) == expected


def test_default_as_of_needs_explicit_date_for_adhoc():
    with pytest.raises(ValueError):
        default_as_of(C.AD_HOC, date(2026, 10, 21))


def test_schedule_validate_for_all_active_tenants(engine, requests_repo, tenant_repo):
    result = schedule_runs(engine, "ME", today=date(2026, 10, 21))
    assert set(result.created) == {"nbfc_001", "nbfc_002"} and result.skipped == {}
    rows = requests_repo.list_batch(result.created["nbfc_001"])
    assert [(r.as_of_date, r.reporting_cycle.value, r.requested_by, r.run_mode) for r in rows] == [
        (AS_OF, "ME", "scheduler", M.VALIDATE)]
    again = schedule_runs(engine, "ME", today=date(2026, 10, 21))     # still pending -> skipped, not duplicated
    assert again.created == {} and "Already open" in again.skipped["nbfc_001"]
    tenant_repo.set_active("nbfc_002", False)
    assert "nbfc_002" not in schedule_runs(engine, "W1", today=date(2026, 10, 21)).created


def test_scheduler_only_validates():
    import inspect as pyinspect
    from submissions.ops import schedule
    assert "mode" not in pyinspect.signature(schedule.schedule_runs).parameters
    with pytest.raises(SystemExit):                                     # --mode no longer exists
        schedule.main(["--cycle", "ME", "--mode", "GENERATE"])
