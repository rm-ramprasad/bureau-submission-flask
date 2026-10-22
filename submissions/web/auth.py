"""Who is using the UI. Login is deferred: this is the single hook to replace with SSO/login later."""

from __future__ import annotations

from flask import request


def current_user() -> str:
    """Internal network only for now. A fronting proxy may pass the user in X-Forwarded-User."""
    return (request.headers.get("X-Forwarded-User") or "internal-user")[:100]
