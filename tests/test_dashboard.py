"""Request stages (derived) and the dashboard / tracker views built on them."""

import re
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from submissions.bureaus.seed import seed as seed_bureaus
from submissions.config import Settings
from submissions.enums import BureauCode as B, RequestStatus as S, RunMode as M, SubmissionType as T
from submissions.pipeline import Services
from submissions.requests import RequestResult
from submissions.requests.models import SubmissionRequest
from submissions.requests.stages import Period, Request, Stage, build_dashboard, load_requests
from submissions.snapshot import SnapshotRepository
from submissions.storage import LocalFileStore
from submissions.web import create_app
from submissions.worker.runner import Worker

ROOT = Path(__file__).resolve().parent.parent
AS_OF = date(2026, 9, 30)
NOW = datetime(2026, 10, 21, 9, 0)


def row(status, mode=M.VALIDATE, **kw) -> SubmissionRequest:
    base = dict(request_id=1, batch_id="b", tenant_id="nbfc_001", run_mode=mode, as_of_date=AS_OF,
                submission_type=T.CONSUMER, bureau_code=None, snapshot_id=1, reporting_cycle=None, requested_by="u",
                requested_at=NOW, status=status, worker_id=None, attempt_count=0, started_at=None, finished_at=None,
                output_file_name=None, output_file_path=None, exceptions_file_path=None, total_records=None,
                accepted_records=None, rejected_records=None, duplicate_records=None, excluded_accounts=None,
                overridden_accounts=None, error_message=None)
    return SubmissionRequest(**base | kw)


@pytest.mark.parametrize("validation, files, stage", [
    ([S.DRAFT], [], Stage.AWAITING_EXCEPTIONS),
    ([S.CANCELLED], [], Stage.CANCELLED),
    ([S.PENDING], [], Stage.IN_PROGRESS),
    ([S.RUNNING], [], Stage.IN_PROGRESS),
    ([S.SUCCEEDED], [], Stage.VALIDATED),
    ([S.SUCCEEDED], [S.RUNNING], Stage.IN_PROGRESS),
    ([S.SUCCEEDED], [S.FAILED, S.SUCCEEDED], Stage.COMPLETED),
    ([S.SUCCEEDED], [S.FAILED], Stage.FAILED),
    ([S.SUCCEEDED], [S.CANCELLED], Stage.VALIDATED),
    ([S.FAILED], [], Stage.FAILED),
])
def test_stage_rules(validation, files, stage):
    req = Request("b", [row(s) for s in validation], [row(s, M.GENERATE, bureau_code=B.CIBIL) for s in files])
    assert req.stage is stage


def test_period_start():
    assert Period.THIS_MONTH.start(NOW) == datetime(2026, 10, 1)
    assert Period.LAST_30.start(NOW) == NOW - timedelta(days=30) and Period.ALL.start(NOW) is None


# ---------------------------------------------------------------- built from real rows
@pytest.fixture
def worker(engine, lms_engine, requests_repo, tenant_repo, bureau_repo, tmp_path):
    seed_bureaus(bureau_repo, ["nbfc_001", "nbfc_002"])
    services = Services(app_engine=engine, source_engine=lms_engine, snapshots=SnapshotRepository(engine),
                        base_dir=ROOT, store=LocalFileStore(tmp_path))
    return Worker(services, requests_repo, tenant_repo, bureau_repo, worker_id="w-dash")


def test_dashboard_counts_requests_not_rows(engine, worker, requests_repo, clock):
    done = requests_repo.create_batch("nbfc_001", AS_OF, M.VALIDATE, [T.CONSUMER])
    worker.run_once()
    snap = requests_repo.list_batch(done)[0].snapshot_id
    requests_repo.create_batch("nbfc_001", AS_OF, M.GENERATE, [T.CONSUMER], [B.CIBIL, B.EXPERIAN], snapshot_id=snap)
    worker.run_once()                                                   # two files: still one request
    requests_repo.create_batch("nbfc_002", AS_OF, M.VALIDATE, [T.CONSUMER], draft=True)
    clock.advance(3 * 86400)
    board = build_dashboard(engine, None, Period.ALL, clock())
    assert board.raised == 2
    assert (board.count(Stage.COMPLETED), board.count(Stage.AWAITING_EXCEPTIONS)) == (1, 1)
    assert board.files_generated == 2 and board.records_submitted == 96 and board.total("records") == 56
    assert board.completion_rate == 0.5
    assert [a.reason for a in board.attention] == ["Draft for 3 days: submit or discard it"]
    assert {(b["bureau"], b["files"], b["records"]) for b in board.bureaus} == {(B.CIBIL, 1, 48), (B.EXPERIAN, 1, 48)}
    assert [r.tenant_id for r in load_requests(engine, "nbfc_002")] == ["nbfc_002"]


@pytest.fixture
def client(engine, bureau_repo, tmp_path):
    app = create_app(Settings("sqlite://", "sqlite://", tmp_path, secret_key="t"), engine=engine)
    app.config.update(TESTING=True)
    return app.test_client()


def test_dashboard_page_and_click_through(client, worker, requests_repo):
    v = requests_repo.create_batch("nbfc_001", AS_OF, M.VALIDATE, [T.CONSUMER])
    worker.run_once()
    requests_repo.create_batch("nbfc_002", AS_OF, M.VALIDATE, [T.CONSUMER], draft=True)
    html = client.get("/?period=all").get_data(as_text=True)
    assert "Requests raised" in html and "Validated, files pending" in html and "NBFC status" in html
    assert re.search(r'Awaiting exceptions</span>\s*<span class="value">1<', html)
    assert "/batches?stage=VALIDATED&amp;period=all" in html
    tracker = client.get("/batches?stage=VALIDATED&period=all").get_data(as_text=True)
    assert v in tracker and "Validated, files pending" in tracker
    assert v not in client.get("/batches?stage=COMPLETED&period=all").get_data(as_text=True)
    assert client.get("/batches?stage=NOPE").status_code == 400
    assert client.get("/?period=bogus").status_code == 200                # falls back to this month
