"""In-app login: users, password rules, lockout, login required on every page, change password, CLI, audit."""

import re
from datetime import timedelta

import pytest
from sqlalchemy import select

from submissions.config import Settings
from submissions.ops.tables import audit_event
from submissions.users import MAX_FAILED, UserError, UserRepository, main as users_cli
from submissions.web import create_app

PASSWORD = "Bureau2026pass"


@pytest.fixture
def users(engine, clock):
    repo = UserRepository(engine, clock=clock)
    repo.add("asha.k", "Asha K", PASSWORD)
    return repo


@pytest.fixture
def client(engine, users, tmp_path):
    app = create_app(Settings("sqlite://", "sqlite://", tmp_path, secret_key="t", auth="login"), engine=engine)
    app.config.update(TESTING=True)
    return app.test_client()


def token(client, path="/login") -> str:
    return re.search(r'name="csrf_token" value="([^"]+)"', client.get(path).get_data(as_text=True)).group(1)


def login(client, username="asha.k", password=PASSWORD, next_url=""):
    return client.post("/login", data={"csrf_token": token(client), "username": username, "password": password,
                                       "next": next_url})


def actions(engine) -> list[str]:
    with engine.connect() as conn:
        return list(conn.execute(select(audit_event.c.action).order_by(audit_event.c.event_id)).scalars())


@pytest.mark.parametrize("password, message", [
    ("short1", "at least 10"), ("onlyletterspassword", "letters and digits"), ("xxasha.k123456", "username"),
])
def test_password_rules(users, password, message):
    with pytest.raises(UserError, match=message):
        users.set_password("asha.k", password)


def test_hash_only_and_duplicates(engine, users):
    from submissions.users import app_user
    with engine.connect() as conn:
        stored = conn.execute(select(app_user.c.password_hash)).scalar()
    assert PASSWORD not in stored and stored.startswith(("scrypt:", "pbkdf2:"))
    with pytest.raises(UserError, match="already exists"):
        users.add("Asha.K", "Again", PASSWORD)
    with pytest.raises(UserError, match="Username"):
        users.add("a b", "Bad", PASSWORD)


def test_every_page_needs_login(client):
    for path in ("/", "/batches", "/rules", "/files", "/runs/new"):
        resp = client.get(path)
        assert resp.status_code == 302 and "/login" in resp.headers["Location"]
    assert client.get("/login").status_code == 200
    assert client.get("/static/app.css").status_code == 200


def test_login_redirects_back_and_records_the_user(client, engine):
    resp = login(client, next_url="/files")
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/files")
    page = client.get("/").get_data(as_text=True)
    assert "Asha K" in page and "Sign out" in page
    assert login(client, next_url="https://evil.example/x").headers["Location"].endswith("/")   # no open redirect
    client.post("/logout", data={"csrf_token": token(client, "/")})
    assert client.get("/").status_code == 302
    assert actions(engine) == ["LOGIN", "LOGIN", "LOGOUT"]


def test_lock_expires_after_lock_for(users, clock):
    for _ in range(MAX_FAILED):
        with pytest.raises(UserError):
            users.authenticate("asha.k", "wrong-password-1")
    with pytest.raises(UserError, match="locked until"):
        users.authenticate("asha.k", PASSWORD)
    clock.advance(int(timedelta(minutes=16).total_seconds()))
    assert users.authenticate("asha.k", PASSWORD).username == "asha.k"


def test_wrong_password_and_lockout(client, engine, users):
    for _ in range(MAX_FAILED):
        resp = login(client, password="wrong-password-1")
        assert resp.status_code == 401 and "Wrong username or password" in resp.get_data(as_text=True)
    locked = login(client)                                               # right password, but locked
    assert locked.status_code == 401 and "locked until" in locked.get_data(as_text=True)
    users.set_password("asha.k", "Reset2026pass")                        # an admin reset clears the lock
    assert login(client, password="Reset2026pass").status_code == 302
    assert login(client, username="nobody").status_code == 401           # same message for unknown users
    assert actions(engine).count("LOGIN_FAILED") == MAX_FAILED + 2


def test_disabled_user_cannot_sign_in(client, users):
    users.set_active("asha.k", False)
    assert login(client).status_code == 401


def test_change_password(client, engine, users):
    login(client)
    data = {"csrf_token": token(client, "/account/password"), "current_password": "nope-wrong-1",
            "new_password": "NewPass2026x", "repeat_password": "NewPass2026x"}
    assert client.post("/account/password", data=data).status_code == 400
    assert client.post("/account/password", data=data | {"current_password": PASSWORD}).status_code == 302
    users.authenticate("asha.k", "NewPass2026x")
    assert "PASSWORD_CHANGED" in actions(engine)


def test_no_login_mode_is_unchanged(engine, tmp_path):
    app = create_app(Settings("sqlite://", "sqlite://", tmp_path, secret_key="t"), engine=engine)   # auth="none"
    c = app.test_client()
    assert c.get("/").status_code == 200 and c.get("/login").status_code == 302


def test_cli(engine, monkeypatch, capsys):
    monkeypatch.setenv("SUBMISSIONS_DATABASE_URL", "sqlite://")
    monkeypatch.setattr("submissions.db.make_engine", lambda url, **kw: engine)
    assert users_cli(["add", "ravi", "--name", "Ravi", "--generate"]) == 0
    out = capsys.readouterr().out
    password = out.strip().rsplit(" ", 1)[1]
    UserRepository(engine).authenticate("ravi", password)
    assert users_cli(["disable", "ravi"]) == 0 and not UserRepository(engine).find("ravi").is_active
    assert users_cli(["add", "ravi", "--name", "Ravi", "--generate"]) == 1
