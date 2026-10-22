"""Exceptions page: capture, remove, re-validate, and the generate gate (stale block + confirmation)."""

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
SCOPE = {"as_of": "2026-09-30", "type": "CONSUMER"}
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


def token(client, path="/exceptions") -> str:
    return re.search(r'name="csrf_token" value="([^"]+)"', client.get(path).get_data(as_text=True)).group(1)


def post(client, path, **fields):
    return client.post(path, data={"csrf_token": token(client)} | SCOPE | fields)


def select_nbfc(client, tenant="nbfc_001"):
    client.post("/context/tenant", data={"csrf_token": token(client), "tenant": tenant, "next": "/exceptions"})


def page(client) -> str:
    return client.get("/exceptions?as_of=2026-09-30&type=CONSUMER").get_data(as_text=True)


def actions(engine) -> list[str]:
    with engine.connect() as conn:
        return list(conn.execute(select(audit_event.c.action).order_by(audit_event.c.event_id)).scalars())


def revalidate(client, worker, requests_repo):
    resp = post(client, "/exceptions/revalidate")
    batch_id = resp.headers["Location"].rsplit("/", 1)[1]
    worker.run_once()
    (row,) = requests_repo.list_batch(batch_id)
    assert row.status is S.SUCCEEDED, row.error_message
    return row


def test_needs_an_nbfc(client):
    assert "Select an NBFC in the top bar" in page(client)
    resp = post(client, "/exceptions/exclude", keys=JOINT_LOAN)
    assert resp.status_code == 302 and client.get("/exceptions").status_code == 200


def test_capture_and_remove(client, engine):
    select_nbfc(client)
    post(client, "/exceptions/exclude", kind="account", keys=f"{JOINT_LOAN}\nX-1, X-2", reason="disputed")
    resp = post(client, "/exceptions/exclude", kind="account", keys="X-1")
    assert "Already excluded: X-1" in client.get(resp.headers["Location"]).get_data(as_text=True)
    post(client, "/exceptions/exclude", kind="customer", keys="C00001")
    bad = post(client, "/exceptions/override", account_number=CLEAN_LOAN, field="days_past_due", value="4.5")
    assert "whole number" in client.get(bad.headers["Location"]).get_data(as_text=True)
    post(client, "/exceptions/override", account_number=CLEAN_LOAN, field="days_past_due", value="45", reason="late")
    html = page(client)
    assert html.count("Pending re-validation") == 5 and "Exclude customer" in html and "disputed" in html
    assert "No validation yet" in html

    adj_id = re.search(r'/exceptions/(\d+)/remove', html).group(1)
    post(client, f"/exceptions/{adj_id}/remove")
    assert page(client).count("Pending re-validation") == 4
    assert "Removed" in client.get("/exceptions?as_of=2026-09-30&type=CONSUMER&history=1").get_data(as_text=True)
    assert actions(engine) == ["ADJUSTMENT_ADDED"] * 3 + ["ADJUSTMENT_REMOVED"]     # the duplicate added nothing


def test_other_nbfc_cannot_remove(client, engine):
    select_nbfc(client)
    post(client, "/exceptions/exclude", kind="account", keys="X-1")
    adj_id = re.search(r'/exceptions/(\d+)/remove', page(client)).group(1)
    select_nbfc(client, "nbfc_002")
    assert post(client, f"/exceptions/{adj_id}/remove").status_code == 403


def test_revalidate_apply_confirm_generate(client, engine, worker, requests_repo):
    select_nbfc(client)
    post(client, "/exceptions/exclude", kind="account", keys=f"{JOINT_LOAN}\nNO-SUCH-LOAN")
    post(client, "/exceptions/override", account_number=CLEAN_LOAN, field="amount_overdue", value="1500")
    row = revalidate(client, worker, requests_repo)
    assert (row.total_records, row.excluded_accounts, row.overridden_accounts) == (54, 1, 1)
    html = page(client)
    assert "Up to date." in html and html.count(">Applied<") == 2 and ">Not found<" in html
    assert "0.00 → <strong>1500.00</strong>" in html                    # old (source) -> new

    batch_html = client.get(f"/batches/{row.batch_id}").get_data(as_text=True)
    assert "1 loan(s) excluded" in batch_html and "confirm_adjustments" in batch_html
    gen = {"csrf_token": token(client, f"/batches/{row.batch_id}"), "request_id": row.request_id,
           "bureau_codes": ["CIBIL"]}
    refused = client.post(f"/batches/{row.batch_id}/generate", data=gen)
    assert refused.headers["Location"].endswith(row.batch_id)            # back to the batch page, nothing queued
    assert "Confirm the excluded loans" in client.get(refused.headers["Location"]).get_data(as_text=True)
    ok = client.post(f"/batches/{row.batch_id}/generate", data=gen | {"confirm_adjustments": "1"})
    new_batch = ok.headers["Location"].rsplit("/", 1)[1]
    worker.run_once()
    (file_row,) = requests_repo.list_batch(new_batch)
    assert file_row.status is S.SUCCEEDED and file_row.excluded_accounts == 1
    csv_text = Path(file_row.exceptions_file_path).read_text()
    assert "# User adjustments applied" in csv_text and "NO-SUCH-LOAN" in csv_text and "1500.00" in csv_text
    assert "ADJUSTMENTS_CONFIRMED" in actions(engine)


def test_generate_blocked_after_adjustments_change(client, engine, worker, requests_repo):
    select_nbfc(client)
    row = revalidate(client, worker, requests_repo)                     # validated without adjustments
    post(client, "/exceptions/exclude", kind="account", keys=JOINT_LOAN)
    assert "Re-validation needed." in page(client)
    batch_html = client.get(f"/batches/{row.batch_id}").get_data(as_text=True)
    assert "changed after this validation" in batch_html and "Generate files</button>" not in batch_html
    resp = client.post(f"/batches/{row.batch_id}/generate",
                       data={"csrf_token": token(client, f"/batches/{row.batch_id}"), "request_id": row.request_id,
                             "bureau_codes": ["CIBIL"], "confirm_adjustments": "1"})
    assert resp.headers["Location"].endswith(row.batch_id)
    assert len(requests_repo.list_batches(limit=10)) == 1                # nothing queued
