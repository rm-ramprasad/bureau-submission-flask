"""K.3 tracker filters and Files page; K.6 Exclude / Override from findings into a draft request; audit filters."""

import re
from datetime import date
from pathlib import Path

import pytest

from submissions.adjustments import AdjustmentRepository, AdjustmentScope
from submissions.bureaus.seed import seed as seed_bureaus
from submissions.config import Settings
from submissions.enums import BureauCode as B, RequestStatus as S, RunMode as M, SubmissionType as T
from submissions.pipeline import Services
from submissions.snapshot import SnapshotRepository
from submissions.storage import LocalFileStore
from submissions.web import create_app
from submissions.worker.runner import Worker

ROOT = Path(__file__).resolve().parent.parent
AS_OF = date(2026, 9, 30)


@pytest.fixture
def worker(engine, lms_engine, requests_repo, tenant_repo, bureau_repo, tmp_path):
    seed_bureaus(bureau_repo, ["nbfc_001", "nbfc_002"])
    services = Services(app_engine=engine, source_engine=lms_engine, snapshots=SnapshotRepository(engine),
                        base_dir=ROOT, store=LocalFileStore(tmp_path))
    return Worker(services, requests_repo, tenant_repo, bureau_repo, worker_id="w-k3")


@pytest.fixture
def client(engine, tmp_path):
    app = create_app(Settings("sqlite://", "sqlite://", tmp_path, secret_key="t"), engine=engine,
                     store=LocalFileStore(tmp_path))
    app.config.update(TESTING=True)
    return app.test_client()


def token(client, path="/runs/new") -> str:
    return re.search(r'name="csrf_token" value="([^"]+)"', client.get(path).get_data(as_text=True)).group(1)


@pytest.fixture
def validated(worker, requests_repo):
    """A validated nbfc_001 request with CIBIL and Experian files."""
    v = requests_repo.create_batch("nbfc_001", AS_OF, M.VALIDATE, [T.CONSUMER])
    worker.run_once()
    row = requests_repo.list_batch(v)[0]
    g = requests_repo.create_batch("nbfc_001", AS_OF, M.GENERATE, [T.CONSUMER], [B.CIBIL, B.EXPERIAN],
                                   snapshot_id=row.snapshot_id)
    worker.run_once()
    return row, g


def test_files_page_lists_generated_files(client, validated):
    _, g = validated
    html = client.get("/files").get_data(as_text=True)
    assert "2 file(s)" in html and html.count("CIB0000001-") + html.count("EXP") >= 2 and g in html
    cibil = client.get("/files?bureau=CIBIL").get_data(as_text=True)
    assert "1 file(s)" in cibil and "/requests/" in cibil
    assert client.get("/files?bureau=NOPE").status_code == 400


def test_tracker_filters_and_paging(client, validated, requests_repo):
    row, g = validated
    assert row.batch_id in client.get("/batches?mode=VALIDATE").get_data(as_text=True)
    assert row.batch_id not in client.get("/batches?mode=GENERATE").get_data(as_text=True)
    assert g in client.get("/batches?bureau=EXPERIAN&status=SUCCEEDED").get_data(as_text=True)
    assert "No runs match" in client.get("/batches?status=FAILED").get_data(as_text=True)
    assert "No runs match" in client.get("/batches?from=2030-01-01").get_data(as_text=True)
    assert client.get("/batches?from=not-a-date").status_code == 400


def test_exclude_from_findings_goes_into_one_draft(client, validated, engine, requests_repo):
    row, _ = validated
    findings = f"/snapshots/{row.snapshot_id}/findings"
    html = client.get(findings).get_data(as_text=True)
    keys = re.findall(r'name="account_key" value="([^"]+)"', html)
    assert keys and "Exclude loan" in html
    for key in keys[:2] + keys[:1]:                                      # the third is a duplicate
        client.post(f"/snapshots/{row.snapshot_id}/exclude",
                    data={"csrf_token": token(client, findings), "account_key": key, "next": findings})
    drafts = [b for b in requests_repo.list_batches() if b.status.value == "DRAFT"]
    assert len(drafts) == 1                                              # one draft collects them
    (draft_row,) = requests_repo.list_batch(drafts[0].batch_id)
    adjustments = AdjustmentRepository(engine).list(AdjustmentScope.of(draft_row))
    assert len(adjustments) == len(set(keys[:2]))
    assert all(a.reason.startswith("From findings") for a in adjustments)


def test_override_from_findings_prefills_the_draft(client, validated, requests_repo):
    row, _ = validated
    findings = f"/snapshots/{row.snapshot_id}/findings"
    key = re.search(r'name="account_key" value="([^"]+)"', client.get(findings).get_data(as_text=True)).group(1)
    resp = client.post(f"/snapshots/{row.snapshot_id}/override",
                       data={"csrf_token": token(client, findings), "account_key": key, "field": "amount_overdue"})
    assert re.search(r"/batches/[^/]+/exceptions\?type=CONSUMER&account=\w+&field=amount_overdue", resp.headers["Location"])
    page = client.get(resp.headers["Location"]).get_data(as_text=True)
    assert re.search(r'id="account_number"[^>]*value="\w+"', page)
    assert '<option value="amount_overdue" selected>' in page


def test_findings_actions_blocked_while_a_run_is_open(client, validated, requests_repo):
    row, _ = validated
    requests_repo.create_batch("nbfc_001", AS_OF, M.VALIDATE, [T.CONSUMER])          # pending, not a draft
    findings = f"/snapshots/{row.snapshot_id}/findings"
    key = re.search(r'name="account_key" value="([^"]+)"', client.get(findings).get_data(as_text=True)).group(1)
    resp = client.post(f"/snapshots/{row.snapshot_id}/exclude",
                       data={"csrf_token": token(client, findings), "account_key": key, "next": findings})
    assert "Cannot open a new request" in client.get(resp.headers["Location"]).get_data(as_text=True)
    assert client.post(f"/snapshots/{row.snapshot_id}/exclude", data={"csrf_token": token(client, findings),
                                                                      "account_key": "NOPE"}).status_code == 404


def test_audit_filters(client, validated, engine):
    from submissions.ops import audit
    audit.record(engine, "alice", "ADJUSTMENT_ADDED", tenant_id="nbfc_001", target="b-1")
    audit.record(engine, "bob", "FILE_DOWNLOADED", tenant_id="nbfc_001", target="7")
    html = client.get("/audit?actor=alice").get_data(as_text=True)
    assert "ADJUSTMENT_ADDED" in html and "<td>bob</td>" not in html
    assert "<td>alice</td>" not in client.get("/audit?action=FILE_DOWNLOADED").get_data(as_text=True)
    assert "b-1" in client.get("/audit?target=b-1").get_data(as_text=True)
