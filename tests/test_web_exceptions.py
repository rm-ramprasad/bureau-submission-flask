"""Request flow: create (draft) -> exceptions tagged with the request -> submit -> validate -> confirm -> files."""

import re
from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy import select

from mock_data.generate_mock_data import load_into
from submissions.bureaus.seed import seed as seed_bureaus
from submissions.config import Settings
from submissions.enums import ExtractStrategy, RequestStatus as S
from submissions.ops.tables import audit_event
from submissions.pipeline import Services
from submissions.snapshot import SnapshotRepository
from submissions.storage import LocalFileStore
from submissions.web import create_app
from submissions.worker.runner import Worker

ROOT = Path(__file__).resolve().parent.parent
JOINT_LOAN, CLEAN_LOAN = "B122HL10788234", "B107PL79230310"


@pytest.fixture
def app(engine, bureau_repo, tenant_repo, tmp_path):
    load_into(engine, ["nbfc_001"])                      # LMS schema on the app server (adjustments join in SQL)
    tenant_repo.upsert(replace(tenant_repo.get("nbfc_001"), extract_strategy=ExtractStrategy.INSERT_SELECT))
    seed_bureaus(bureau_repo, ["nbfc_001"])
    app = create_app(Settings("sqlite://", "sqlite://", tmp_path, secret_key="t"), engine=engine,
                     store=LocalFileStore(tmp_path))
    app.config.update(TESTING=True)
    return app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def worker(engine, requests_repo, tenant_repo, bureau_repo, tmp_path):
    services = Services(app_engine=engine, source_engine=engine, snapshots=SnapshotRepository(engine),
                        base_dir=ROOT, store=LocalFileStore(tmp_path))
    return Worker(services, requests_repo, tenant_repo, bureau_repo, worker_id="w-exc")


def token(client, path="/runs/new") -> str:
    return re.search(r'name="csrf_token" value="([^"]+)"', client.get(path).get_data(as_text=True)).group(1)


def post(client, path, **fields):
    return client.post(path, data={"csrf_token": token(client)} | fields)


def new_request(client) -> str:
    resp = post(client, "/runs/new", tenant_id="nbfc_001", as_of_date="2026-09-30", submission_types=["CONSUMER"],
                reporting_cycle="ME")
    assert re.search(r"/batches/[^/]+/exceptions$", resp.headers["Location"])     # straight to step 2
    return resp.headers["Location"].split("/")[2]


def page(client, batch_id) -> str:
    return client.get(f"/batches/{batch_id}/exceptions").get_data(as_text=True)


def actions(engine) -> list[str]:
    with engine.connect() as conn:
        return list(conn.execute(select(audit_event.c.action).order_by(audit_event.c.event_id)).scalars())


def test_new_request_is_a_draft_with_an_exceptions_step(client, requests_repo, worker):
    batch_id = new_request(client)
    (row,) = requests_repo.list_batch(batch_id)
    assert row.status is S.DRAFT
    assert worker.run_once() == 0                                          # not validated until submitted
    html = page(client, batch_id)
    assert "Submit for validation" in html and "Add exclusions" in html
    assert batch_id in client.get("/exceptions").get_data(as_text=True)    # listed as waiting for exceptions
    assert "Step 2 of 4" in client.get(f"/batches/{batch_id}").get_data(as_text=True)


def test_capture_and_remove(client, engine):
    batch_id = new_request(client)
    base = f"/batches/{batch_id}/exceptions"
    post(client, f"{base}/exclude", kind="account", keys=f"{JOINT_LOAN}\nX-1, X-2", reason="disputed")
    resp = post(client, f"{base}/exclude", kind="account", keys="X-1")
    assert "Already excluded: X-1" in client.get(resp.headers["Location"]).get_data(as_text=True)
    post(client, f"{base}/exclude", kind="customer", keys="C00001")
    bad = post(client, f"{base}/override", account_number=CLEAN_LOAN, field="days_past_due", value="4.5")
    assert "whole number" in client.get(bad.headers["Location"]).get_data(as_text=True)
    post(client, f"{base}/override", account_number=CLEAN_LOAN, field="days_past_due", value="45", reason="late")
    html = page(client, batch_id)
    assert html.count("Will apply") == 5 and "Exclude customer" in html and "disputed" in html
    adj_id = re.search(r'/exceptions/(\d+)/remove', html).group(1)
    post(client, f"{base}/{adj_id}/remove")
    assert page(client, batch_id).count("Will apply") == 4
    assert "Removed" in client.get(f"{base}?history=1").get_data(as_text=True)
    assert actions(engine) == ["RUN_CREATED"] + ["ADJUSTMENT_ADDED"] * 3 + ["ADJUSTMENT_REMOVED"]


def test_submit_validate_confirm_generate(client, engine, worker, requests_repo):
    batch_id = new_request(client)
    base = f"/batches/{batch_id}/exceptions"
    post(client, f"{base}/exclude", kind="account", keys=f"{JOINT_LOAN}\nNO-SUCH-LOAN")
    post(client, f"{base}/override", account_number=CLEAN_LOAN, field="amount_overdue", value="1500")
    post(client, f"/batches/{batch_id}/submit")
    worker.run_once()
    (row,) = requests_repo.list_batch(batch_id)
    assert row.status is S.SUCCEEDED, row.error_message
    assert (row.total_records, row.excluded_accounts, row.overridden_accounts) == (54, 1, 1)   # validated the rest

    html = page(client, batch_id)
    assert "Locked." in html and html.count(">Applied<") == 2 and ">Not found<" in html
    assert "0.00 → <strong>1500.00</strong>" in html                    # old (source) -> new
    locked = post(client, f"{base}/exclude", kind="account", keys="X-9")
    assert "locked" in client.get(locked.headers["Location"]).get_data(as_text=True)

    batch_html = client.get(f"/batches/{batch_id}").get_data(as_text=True)
    assert "1 loan(s) excluded" in batch_html and "confirm_adjustments" in batch_html
    gen = {"csrf_token": token(client, f"/batches/{batch_id}"), "request_id": row.request_id, "bureau_codes": ["CIBIL"]}
    refused = client.post(f"/batches/{batch_id}/generate", data=gen)
    assert "Confirm the excluded loans" in client.get(refused.headers["Location"]).get_data(as_text=True)
    ok = client.post(f"/batches/{batch_id}/generate", data=gen | {"confirm_adjustments": "1"})
    file_batch = ok.headers["Location"].rsplit("/", 1)[1]
    worker.run_once()
    (file_row,) = requests_repo.list_batch(file_batch)
    assert file_row.status is S.SUCCEEDED and file_row.excluded_accounts == 1
    csv_text = Path(file_row.exceptions_file_path).read_text()
    assert "# User adjustments applied" in csv_text and "NO-SUCH-LOAN" in csv_text and "1500.00" in csv_text
    assert {"RUN_SUBMITTED", "ADJUSTMENTS_CONFIRMED"} <= set(actions(engine))


def test_submit_without_exceptions_and_discard(client, requests_repo, worker):
    batch_id = new_request(client)
    client.post(f"/batches/{batch_id}/submit", data={"csrf_token": token(client)})
    worker.run_once()
    (row,) = requests_repo.list_batch(batch_id)
    assert row.status is S.SUCCEEDED and (row.total_records, row.excluded_accounts) == (56, 0)

    other = new_request(client)
    post(client, f"/batches/{other}/discard")
    (gone,) = requests_repo.list_batch(other)
    assert gone.status is S.CANCELLED
    assert new_request(client)                                           # the date is free again
