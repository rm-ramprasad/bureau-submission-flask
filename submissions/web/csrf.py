"""Minimal CSRF protection: one random token per session, required on every POST."""

from __future__ import annotations

import hmac
import secrets

from flask import Flask, abort, request, session

FIELD = "csrf_token"


def token() -> str:
    if FIELD not in session:
        session[FIELD] = secrets.token_urlsafe(32)
    return session[FIELD]


def init_app(app: Flask) -> None:
    @app.before_request
    def _check():
        if request.method == "POST":
            sent = request.form.get(FIELD, "")
            if not sent or not hmac.compare_digest(sent, session.get(FIELD, "")):
                abort(400, "Missing or invalid form token. Reload the page and try again.")

    app.jinja_env.globals["csrf_token"] = token
