"""Who is using the UI: the in-app login (SUBMISSIONS_AUTH=login, the server default) or none (development).

With login, every page except /login needs a signed-in user; the session lasts SESSION_HOURS. `current_user()` is
the single hook the rest of the app uses (requests, exceptions and the audit trail record it).
"""

from __future__ import annotations

from datetime import timedelta
from urllib.parse import urlparse

from flask import Blueprint, Flask, current_app, flash, redirect, render_template, request, session, url_for

from submissions.ops import audit
from submissions.users import UserError, UserRepository

SESSION_HOURS = 8
OPEN_ENDPOINTS = {"auth.login", "static"}
bp = Blueprint("auth", __name__)


def current_user() -> str:
    """The signed-in username; without login, a fronting proxy may pass X-Forwarded-User."""
    if session.get("user"):
        return session["user"]
    return (request.headers.get("X-Forwarded-User") or "internal-user")[:100]


def _login_required() -> bool:
    return current_app.config.get("SUBMISSIONS_AUTH") == "login"


def _users() -> UserRepository:
    return UserRepository(current_app.extensions["submissions"].engine)


def _safe_next(target: str | None) -> str:
    target = target or ""
    parsed = urlparse(target)
    if not target.startswith("/") or target.startswith("//") or parsed.netloc:
        return url_for("ui.dashboard")
    return target


def init_app(app: Flask, auth_mode: str, https: bool) -> None:
    app.config.update(SUBMISSIONS_AUTH=auth_mode, PERMANENT_SESSION_LIFETIME=timedelta(hours=SESSION_HOURS),
                      SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_SECURE=https)
    app.register_blueprint(bp)

    @app.before_request
    def _require_login():
        if not _login_required() or request.endpoint in OPEN_ENDPOINTS or session.get("user"):
            return None
        return redirect(url_for("auth.login", next=request.full_path if request.method == "GET" else None))

    @app.context_processor
    def _auth_context():
        return {"auth_enabled": _login_required(), "display_name": session.get("display_name")}


@bp.route("/login", methods=["GET", "POST"])
def login():
    if not _login_required():
        return redirect(url_for("ui.dashboard"))
    if request.method == "GET":
        return render_template("login.html", next=request.args.get("next", ""))
    username = (request.form.get("username") or "").strip().lower()
    engine = current_app.extensions["submissions"].engine
    try:
        user = _users().authenticate(username, request.form.get("password") or "")
    except UserError as exc:
        audit.record(engine, username[:100] or "-", "LOGIN_FAILED", target=username or None,
                     ip=request.remote_addr, reason=str(exc)[:200])
        flash(str(exc), "error")
        return render_template("login.html", next=request.form.get("next", ""), username=username), 401
    tenant = session.get("tenant")
    session.clear()                                        # new session on login (no fixation)
    session.permanent = True
    session.update(user=user.username, display_name=user.display_name, is_admin=user.is_admin)
    if tenant:
        session["tenant"] = tenant
    audit.record(engine, user.username, "LOGIN", target=user.username, ip=request.remote_addr)
    return redirect(_safe_next(request.form.get("next")))


@bp.post("/logout")
def logout():
    user = session.get("user")
    if user:
        audit.record(current_app.extensions["submissions"].engine, user, "LOGOUT", target=user)
    session.clear()
    flash("Signed out.", "ok")
    return redirect(url_for("auth.login"))


@bp.route("/account/password", methods=["GET", "POST"])
def change_password():
    if not session.get("user"):
        return redirect(url_for("ui.dashboard"))
    if request.method == "POST":
        new = request.form.get("new_password") or ""
        try:
            if new != request.form.get("repeat_password"):
                raise UserError("The new passwords differ")
            _users().set_password(session["user"], new, current_password=request.form.get("current_password") or "")
        except UserError as exc:
            flash(str(exc), "error")
            return render_template("password.html"), 400
        audit.record(current_app.extensions["submissions"].engine, session["user"], "PASSWORD_CHANGED",
                     target=session["user"])
        flash("Password changed.", "ok")
        return redirect(url_for("ui.dashboard"))
    return render_template("password.html")
