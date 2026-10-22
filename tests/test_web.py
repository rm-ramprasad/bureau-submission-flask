import re
from datetime import date
from pathlib import Path

import pytest

from submissions.bureaus.seed import seed as seed_bureaus
from submissions.config import Settings
from submissions.enums import RequestStatus as S, RunMode as M
from submissions.pipeline import Services
from submissions.storage import LocalFileStore
from submissions.snapshot import SnapshotRepository
from submissions.web import create_app
from submissions.worker.runner import Worker

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def app(engine, bureau_repo, tmp_path):
    seed_bureaus(bureau_repo, ["nbfc_001", "nbfc_002"])
    settings = Settings(database_url="sqlite://", source_database_url="sqlite://", output_dir=tmp_path,
                        secret_key="test-secret")
    app = create_app(settings, engine=engine)
    app.config.update(TESTING=True)
    return app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def worker(engine, lms_engine, requests_repo, tenant_repo, bureau_repo, tmp_path):
    services = Services(app_engine=engine, source_engine=lms_engine, snapshots=SnapshotRepository(engine),
                        base_dir=ROOT, store=LocalFileStore(tmp_path))
    return Worker(services, requests_repo, tenant_repo, bureau_repo, worker_id="w-web")


def token(client, path="/runs/new") -> str:
    html = client.get(path).get_data(as_text=True)
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def post_run(client, **fields):
    data = {"csrf_token": token(client), "tenant_id": "nbfc_001", "as_of_date": "2026-09-30",
            "submission_types": ["CONSUMER"], "reporting_cycle": "ME"} | fields
    return client.post("/runs/new", data=data)


def batch_id_from(resp) -> str:
    return resp.headers["Location"].rsplit("/", 1)[1]


# ---------------------------------------------------------------- pages
def test_no_worker_banner_until_a_worker_polls(client, engine):
    from submissions.ops.heartbeat import Heartbeat
    assert "No worker running" in client.get("/batches").get_data(as_text=True)
    Heartbeat(engine, "w1").beat()
    assert "No worker running" not in client.get("/batches").get_data(as_text=True)


def test_ui_actions_are_audited(client):
    post_run(client)
    page = client.get("/audit").get_data(as_text=True)
    assert "RUN_CREATED" in page and "internal-user" in page and "nbfc_001" in page


def test_index_redirects_and_empty_batches(client):
    assert client.get("/").status_code == 302
    page = client.get("/batches").get_data(as_text=True)
    assert "No runs yet" in page


def test_shell_navigation_and_worker_status(client, engine):
    page = client.get("/batches").get_data(as_text=True)
    for label in ("Dashboard", "New request", "Exceptions", "Submission tracker", "Files", "Audit"):
        assert label in page
    assert re.search(r'class="nav-item active"\s+href="/batches"', page)
    assert "No worker running" in page and "Bureau reporting</a> <span>/</span> Submission tracker" in page


def test_global_nbfc_selector_filters_pages(client, requests_repo):
    post_run(client)                                                   # nbfc_001
    post_run(client, tenant_id="nbfc_002")
    resp = client.post("/context/tenant", data={"csrf_token": token(client), "tenant": "nbfc_002",
                                                "next": "/batches"})
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/batches")
    page = client.get("/batches").get_data(as_text=True)
    assert ">nbfc_002<" in page and ">nbfc_001<" not in page
    assert 'value="nbfc_002" selected' in client.get("/runs/new").get_data(as_text=True)   # form preselects it
    client.post("/context/tenant", data={"csrf_token": token(client), "tenant": ""})       # back to all
    page = client.get("/batches").get_data(as_text=True)
    assert ">nbfc_001<" in page and ">nbfc_002<" in page


def test_nbfc_selector_rejects_unknown_tenant_and_external_redirect(client):
    assert client.post("/context/tenant", data={"csrf_token": token(client), "tenant": "nbfc_999"}).status_code == 400
    resp = client.post("/context/tenant", data={"csrf_token": token(client), "tenant": "nbfc_001",
                                                "next": "https://evil.example/x"})
    assert resp.headers["Location"] in ("/", "http://localhost/")


def test_new_run_form_lists_tenants_and_disables_unbuilt_types(client):
    page = client.get("/runs/new").get_data(as_text=True)
    assert "nbfc_001" in page and "nbfc_002" in page
    assert re.search(r'value="NESL"[^>]*disabled', page)
    assert "2026-09-30" in page or 'type="date"' in page


# ---------------------------------------------------------------- creating runs
def test_validate_run_is_queued(client, requests_repo):
    resp = post_run(client)
    assert resp.status_code == 302
    rows = requests_repo.list_batch(batch_id_from(resp))
    assert [(r.run_mode, r.bureau_code, r.requested_by, r.status) for r in rows] == [
        (M.VALIDATE, None, "internal-user", S.PENDING)]
    assert "refreshing every 5 s" in client.get(resp.headers["Location"]).get_data(as_text=True)


def test_new_request_is_always_a_validation(client, requests_repo):
    page = client.get("/runs/new").get_data(as_text=True)
    assert 'name="run_mode"' not in page and 'name="bureau_codes"' not in page
    resp = post_run(client, run_mode="GENERATE", bureau_codes=["CIBIL"])        # ignored if posted anyway
    rows = requests_repo.list_batch(batch_id_from(resp))
    assert [(r.run_mode, r.bureau_code) for r in rows] == [(M.VALIDATE, None)]


def test_user_header_is_recorded(client, requests_repo):
    data = {"csrf_token": token(client), "tenant_id": "nbfc_002", "as_of_date": "2026-09-30",
            "submission_types": ["CONSUMER"]}
    resp = client.post("/runs/new", data=data, headers={"X-Forwarded-User": "ops.priya"})
    assert requests_repo.list_batch(batch_id_from(resp))[0].requested_by == "ops.priya"


@pytest.mark.parametrize("fields,message", [
    (dict(tenant_id="nbfc_999"), "Select an active NBFC"),
    (dict(as_of_date="2099-01-01"), "future"),
    (dict(as_of_date="not-a-date"), "Check the date"),
    (dict(submission_types=["NESL"]), "Not available yet"),
])
def test_invalid_runs_show_errors(client, fields, message):
    resp = post_run(client, **fields)
    assert resp.status_code == 400 and message in resp.get_data(as_text=True)


def test_duplicate_run_rejected(client):
    post_run(client)
    resp = post_run(client)
    assert resp.status_code == 400 and "Already pending or running" in resp.get_data(as_text=True)


def test_generate_needs_bureau_profiles(client, engine, worker, requests_repo):
    from submissions.bureaus.tables import bureau_profile
    batch_id = batch_id_from(post_run(client))
    worker.run_once()
    (validated,) = requests_repo.list_batch(batch_id)
    with engine.begin() as conn:
        conn.execute(bureau_profile.delete().where(bureau_profile.c.bureau_code == "EXPERIAN"))
    resp = client.post(f"/batches/{batch_id}/generate", data={"csrf_token": token(client),
                       "request_id": validated.request_id, "bureau_codes": ["EXPERIAN"]}, follow_redirects=True)
    assert "No active bureau profile" in resp.get_data(as_text=True)


def test_post_without_csrf_token_rejected(client):
    resp = client.post("/runs/new", data={"tenant_id": "nbfc_001"})
    assert resp.status_code == 400


# ---------------------------------------------------------------- after the worker ran
def test_batch_page_findings_and_csv_after_validation(client, worker):
    batch_id = batch_id_from(post_run(client))
    worker.run_once()
    page = client.get(f"/batches/{batch_id}").get_data(as_text=True)
    assert "SUCCEEDED" in page and "48 accepted" in page and "8 rejected" in page
    assert "refreshing" not in page
    assert "Generate files from this validation" in page
    sid = int(re.search(r"/snapshots/(\d+)/findings", page).group(1))

    findings = client.get(f"/snapshots/{sid}/findings?rule_id=UCRF.TL.CLOSED_WITH_BALANCE").get_data(as_text=True)
    assert "L00047" in findings and "Findings (1)" in findings
    csv_resp = client.get(f"/snapshots/{sid}/findings.csv?outcome=REJECT_RECORD")
    lines = csv_resp.get_data(as_text=True).strip().splitlines()
    assert csv_resp.mimetype == "text/csv" and lines[0].startswith("rule_id,outcome")
    assert len(lines) - 1 == 9 and all(",REJECT_RECORD," in line for line in lines[1:])


def test_generate_from_validation_reuses_snapshot(client, worker, requests_repo):
    batch_id = batch_id_from(post_run(client))
    worker.run_once()
    (validated,) = requests_repo.list_batch(batch_id)
    resp = client.post(f"/batches/{batch_id}/generate",
                       data={"csrf_token": token(client), "request_id": validated.request_id,
                             "bureau_codes": ["CIBIL", "EXPERIAN"]})
    rows = requests_repo.list_batch(batch_id_from(resp))
    assert len(rows) == 2 and all(r.run_mode is M.GENERATE and r.snapshot_id == validated.snapshot_id for r in rows)


def test_cancel_and_retry(client, requests_repo):
    batch_id = batch_id_from(post_run(client))
    pending = requests_repo.list_batch(batch_id)[0]
    client.post(f"/requests/{pending.request_id}/cancel", data={"csrf_token": token(client)})
    assert requests_repo.get(pending.request_id).status is S.CANCELLED
    client.post(f"/requests/{pending.request_id}/retry", data={"csrf_token": token(client)})
    assert requests_repo.get(pending.request_id).status is S.PENDING


def test_unknown_pages_404(client):
    assert client.get("/batches/nope").status_code == 404
    assert client.get("/snapshots/999/findings").status_code == 404
    assert client.get("/requests/999/file").status_code == 404


def test_download_only_serves_files_inside_output_dir(client, requests_repo, tmp_path):
    from submissions.requests import RequestResult
    requests_repo.create_batch("nbfc_001", date(2026, 9, 30), M.GENERATE, ["CONSUMER"], ["CIBIL", "EQUIFAX"],
                               snapshot_id=1)
    outside, inside = tmp_path.parent / "secret.txt", tmp_path / "CIB0000001-01102026-090000.txt"
    outside.write_text("nope")
    inside.write_text("HDR")

    bad = requests_repo.claim_next("w")
    requests_repo.mark_succeeded(bad.request_id, RequestResult(output_file_path=str(outside)))
    assert client.get(f"/requests/{bad.request_id}/file").status_code == 404

    good = requests_repo.claim_next("w")
    requests_repo.mark_succeeded(good.request_id, RequestResult(output_file_name=inside.name,
                                                                output_file_path=str(inside)))
    resp = client.get(f"/requests/{good.request_id}/file")
    assert resp.status_code == 200 and resp.get_data() == b"HDR"
    assert inside.name in resp.headers["Content-Disposition"]
