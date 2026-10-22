"""Rules & policies page: catalogue, per-NBFC on/off and outcome with a reason, reset, audit, effect on validation."""

import re

import pytest
from sqlalchemy import select

from submissions.config import Settings
from submissions.enums import Outcome
from submissions.ops.tables import audit_event
from submissions.validation import rules
from submissions.web import create_app

RULE = "UCRF.ID.NUMBER_FORMAT"                 # default: on, REJECT_SEGMENT


@pytest.fixture
def client(engine, tmp_path):
    app = create_app(Settings("sqlite://", "sqlite://", tmp_path, secret_key="t"), engine=engine)
    app.config.update(TESTING=True)
    return app.test_client()


def token(client) -> str:
    return re.search(r'name="csrf_token" value="([^"]+)"', client.get("/rules").get_data(as_text=True)).group(1)


def select_nbfc(client, tenant="nbfc_001"):
    client.post("/context/tenant", data={"csrf_token": token(client), "tenant": tenant, "next": "/rules"})


def save(client, rule_id=RULE, **fields):
    return client.post(f"/rules/{rule_id}", data={"csrf_token": token(client), "type": "CONSUMER"} | fields)


def effective(engine, tenant="nbfc_001", rule_id=RULE):
    return next(r for r in rules.load_rule_set(engine, tenant, "CONSUMER") if r.rule_id == rule_id)


def test_catalogue_without_an_nbfc_is_read_only(client):
    html = client.get("/rules").get_data(as_text=True)
    assert "Showing the defaults" in html and RULE in html and "ID number is not valid" in html
    assert 'action="/rules/' not in html
    assert "Python check" in html and "<pre class=\"sql\">" in html            # the logic is visible
    assert "1 rule(s)" in client.get("/rules?q=number_format").get_data(as_text=True)


def test_change_needs_a_reason_and_is_per_nbfc(client, engine):
    select_nbfc(client)
    resp = save(client, enabled="1", outcome="WARN")
    assert "give a reason" in client.get(resp.headers["Location"]).get_data(as_text=True)
    assert effective(engine).outcome is Outcome.REJECT_SEGMENT

    save(client, enabled="1", outcome="WARN", reason="NBFC holds legacy IDs")
    assert effective(engine).outcome is Outcome.WARN and effective(engine).enabled
    assert effective(engine, "nbfc_002").outcome is Outcome.REJECT_SEGMENT       # other NBFC untouched
    save(client, outcome="WARN", reason="not used by this NBFC")                  # unticked = off
    assert not effective(engine).enabled
    html = client.get("/rules?changed=1").get_data(as_text=True)
    assert "1 rule(s)" in html and "reset to default" in html

    with engine.connect() as conn:
        events = conn.execute(select(audit_event).where(audit_event.c.action == "RULE_POLICY_CHANGED")
                              .order_by(audit_event.c.event_id)).all()
    assert [e.target for e in events] == [RULE, RULE] and "legacy IDs" in events[0].details


def test_back_to_default_removes_the_override(client, engine):
    select_nbfc(client)
    save(client, enabled="1", outcome="WARN", reason="r")
    save(client, enabled="1", outcome="REJECT_SEGMENT", reason="back")           # same as default
    assert rules.policies(engine, "nbfc_001") == {}
    save(client, outcome="REJECT_SEGMENT", reason="off")
    client.post(f"/rules/{RULE}/reset", data={"csrf_token": token(client)})
    assert rules.policies(engine, "nbfc_001") == {} and effective(engine).enabled


def test_unknown_rule_and_no_nbfc(client):
    assert save(client, enabled="1", reason="r").status_code == 302              # no NBFC: refused with a message
    select_nbfc(client)
    assert save(client, rule_id="NOPE", enabled="1", reason="r").status_code == 404
    assert save(client, enabled="1", outcome="BOGUS", reason="r").status_code == 400
