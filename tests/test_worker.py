from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import func, select

from submissions.bureaus.seed import seed as seed_bureaus
from submissions.canonical.schema import extract_snapshot
from submissions.enums import BureauCode as B, RequestStatus as S, RunMode as M, SnapshotStatus, SubmissionType as T
from submissions.pipeline import Services, enabled_types
from submissions.snapshot import SnapshotRepository
from submissions.storage import LocalFileStore
from submissions.tenants import Tenant
from submissions.worker.runner import Worker

ROOT = Path(__file__).resolve().parent.parent
AS_OF = date(2026, 9, 30)


@pytest.fixture
def worker(engine, lms_engine, requests_repo, tenant_repo, bureau_repo, tmp_path):
    seed_bureaus(bureau_repo, ["nbfc_001", "nbfc_002"])
    services = Services(app_engine=engine, source_engine=lms_engine, snapshots=SnapshotRepository(engine),
                        base_dir=ROOT, store=LocalFileStore(tmp_path))
    return Worker(services, requests_repo, tenant_repo, bureau_repo, worker_id="w-test")


def snapshot_count(engine) -> int:
    with engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(extract_snapshot)).scalar()


def test_only_consumer_is_enabled():
    assert enabled_types() == [T.CONSUMER]


def test_validate_run_end_to_end(worker, requests_repo, engine):
    batch = requests_repo.create_batch("nbfc_001", AS_OF, M.VALIDATE, [T.CONSUMER])
    assert worker.run_once() == 1
    (row,) = requests_repo.list_batch(batch)
    assert row.status is S.SUCCEEDED, row.error_message
    assert (row.total_records, row.accepted_records, row.rejected_records) == (56, 48, 8)
    snap = SnapshotRepository(engine).get(row.snapshot_id)
    assert (snap.status, snap.tenant_id, snap.batch_id) == (SnapshotStatus.VALIDATED, "nbfc_001", batch)


def test_both_tenants_in_one_poll(worker, requests_repo):
    one = requests_repo.create_batch("nbfc_001", AS_OF, M.VALIDATE, [T.CONSUMER])
    two = requests_repo.create_batch("nbfc_002", AS_OF, M.VALIDATE, [T.CONSUMER])
    assert worker.run_once() == 2
    rows = requests_repo.list_batch(one) + requests_repo.list_batch(two)
    assert [r.status for r in rows] == [S.SUCCEEDED, S.SUCCEEDED]
    assert rows[0].snapshot_id != rows[1].snapshot_id


def test_generate_batch_shares_one_snapshot(worker, requests_repo, engine):
    v = requests_repo.create_batch("nbfc_001", AS_OF, M.VALIDATE, [T.CONSUMER])
    worker.run_once()
    sid = requests_repo.list_batch(v)[0].snapshot_id
    batch = requests_repo.create_batch("nbfc_001", AS_OF, M.GENERATE, [T.CONSUMER], [B.CIBIL, B.EXPERIAN, B.EQUIFAX],
                                       snapshot_id=sid)
    worker.run_once()
    rows = requests_repo.list_batch(batch)
    assert snapshot_count(engine) == 1                       # one extract + validation for three bureaus
    assert len({r.snapshot_id for r in rows}) == 1 and rows[0].snapshot_id is not None
    assert all(r.status is S.SUCCEEDED for r in rows), [r.error_message for r in rows]
    assert {Path(r.output_file_path).name[:3] for r in rows} == {"CIB", "EXP", "EQF"}   # one file per bureau
    assert all(Path(r.output_file_path).is_file() and Path(r.exceptions_file_path).is_file() for r in rows)
    assert all((r.total_records, r.accepted_records, r.rejected_records) == (56, 48, 8) for r in rows)


def test_generate_from_validated_snapshot_reuses_it(worker, requests_repo, engine):
    v = requests_repo.create_batch("nbfc_001", AS_OF, M.VALIDATE, [T.CONSUMER])
    worker.run_once()
    sid = requests_repo.list_batch(v)[0].snapshot_id
    g = requests_repo.create_batch("nbfc_001", AS_OF, M.GENERATE, [T.CONSUMER], [B.CIBIL], snapshot_id=sid)
    worker.run_once()
    (row,) = requests_repo.list_batch(g)
    assert row.snapshot_id == sid and snapshot_count(engine) == 1
    assert row.status is S.SUCCEEDED and row.output_file_name.startswith("CIB0000001-")


def test_no_file_when_every_record_is_rejected(worker, requests_repo):
    v = requests_repo.create_batch("nbfc_001", date(2026, 9, 16), M.VALIDATE, [T.CONSUMER])   # before reported dates
    worker.run_once()
    (validated,) = requests_repo.list_batch(v)
    assert (validated.accepted_records, validated.rejected_records) == (0, 56)
    g = requests_repo.create_batch("nbfc_001", date(2026, 9, 16), M.GENERATE, [T.CONSUMER], [B.CIBIL],
                                   snapshot_id=validated.snapshot_id)
    worker.run_once()
    (row,) = requests_repo.list_batch(g)
    assert row.status is S.FAILED and "No accepted records" in row.error_message and row.output_file_path is None


def test_reused_snapshot_must_match_the_request(worker, requests_repo):
    v = requests_repo.create_batch("nbfc_001", AS_OF, M.VALIDATE, [T.CONSUMER])
    worker.run_once()
    sid = requests_repo.list_batch(v)[0].snapshot_id
    g = requests_repo.create_batch("nbfc_002", AS_OF, M.GENERATE, [T.CONSUMER], [B.CIBIL], snapshot_id=sid)
    worker.run_once()
    (row,) = requests_repo.list_batch(g)
    assert row.status is S.FAILED and "belongs to nbfc_001" in row.error_message


def test_busy_snapshot_releases_the_row(worker, requests_repo, engine):
    batch = requests_repo.create_batch("nbfc_001", AS_OF, M.VALIDATE, [T.CONSUMER])
    SnapshotRepository(engine).create("nbfc_001", T.CONSUMER, AS_OF, batch, "v", "other-worker")   # still BUILDING
    assert worker.run_once() == 0
    (row,) = requests_repo.list_batch(batch)
    assert row.status is S.PENDING and row.worker_id is None


def test_inactive_tenant_fails_the_row(worker, requests_repo, tenant_repo):
    batch = requests_repo.create_batch("nbfc_002", AS_OF, M.VALIDATE, [T.CONSUMER])
    tenant_repo.set_active("nbfc_002", False)
    worker.run_once()
    (row,) = requests_repo.list_batch(batch)
    assert row.status is S.FAILED and "inactive" in row.error_message


def test_unsupported_type_fails_the_row(worker, requests_repo):
    batch = requests_repo.create_batch("nbfc_001", AS_OF, M.VALIDATE, [T.NESL])
    worker.run_once()
    (row,) = requests_repo.list_batch(batch)
    assert row.status is S.FAILED and "not supported" in row.error_message


def test_broken_mapping_fails_row_and_snapshot(worker, requests_repo, tenant_repo, engine, tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(f'canonical_version: "1.0"\nextends: {(ROOT / "clients" / "_base" / "mapping.yaml").as_posix()}\n'
                   "entities: {account: {columns: {days_past_due: a.no_such_column}}}\n", encoding="utf-8")
    tenant_repo.upsert(Tenant("nbfc_001", "Demo NBFC One", "nbfc_001", str(bad)))
    batch = requests_repo.create_batch("nbfc_001", AS_OF, M.VALIDATE, [T.CONSUMER])
    worker.run_once()
    (row,) = requests_repo.list_batch(batch)
    assert row.status is S.FAILED and "Snapshot build failed" in row.error_message and "account" in row.error_message
    snap = SnapshotRepository(engine).find_for_batch(batch, "nbfc_001", T.CONSUMER)
    assert snap.status is SnapshotStatus.FAILED


def test_missing_bureau_profile_fails_generate(worker, requests_repo, engine, bureau_repo):
    from submissions.bureaus.tables import bureau_profile
    v = requests_repo.create_batch("nbfc_002", AS_OF, M.VALIDATE, [T.CONSUMER])
    worker.run_once()
    with engine.begin() as conn:
        conn.execute(bureau_profile.delete().where(bureau_profile.c.tenant_id == "nbfc_002"))
    batch = requests_repo.create_batch("nbfc_002", AS_OF, M.GENERATE, [T.CONSUMER], [B.CIBIL],
                                       snapshot_id=requests_repo.list_batch(v)[0].snapshot_id)
    worker.run_once()
    (row,) = requests_repo.list_batch(batch)
    assert row.status is S.FAILED and "No bureau profile" in row.error_message
