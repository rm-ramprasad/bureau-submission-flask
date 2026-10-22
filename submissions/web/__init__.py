"""Flask UI: raise validation/generation runs and follow their results.

The web layer only writes request rows and reads results; the worker does the work.
Run:  flask --app submissions.web run      (or python -m submissions.web)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from flask import Flask
from sqlalchemy import Engine

from submissions.adjustments import AdjustmentRepository
from submissions.bureaus import BureauRepository
from submissions.config import Settings
from submissions.db import make_engine
from submissions.requests import RequestRepository
from submissions.snapshot import SnapshotRepository
from submissions.storage import FileStore, make_store
from submissions.tenants import TenantRepository


@dataclass(frozen=True)
class WebServices:
    engine: Engine
    store: FileStore                 # generated files (S3 in real use); downloads stream from here
    requests: RequestRepository
    tenants: TenantRepository
    bureaus: BureauRepository
    snapshots: SnapshotRepository
    adjustments: AdjustmentRepository


def create_app(settings: Settings | None = None, engine: Engine | None = None, store: FileStore | None = None) -> Flask:
    settings = settings or Settings.from_env()
    engine = engine or make_engine(settings.database_url)
    app = Flask(__name__)
    app.config.update(SECRET_KEY=settings.secret_key, MAX_CONTENT_LENGTH=1024 * 1024)
    app.extensions["submissions"] = WebServices(
        engine=engine, store=store or make_store(settings), requests=RequestRepository(engine),
        tenants=TenantRepository(engine), bureaus=BureauRepository(engine), snapshots=SnapshotRepository(engine),
        adjustments=AdjustmentRepository(engine))

    from submissions.ops.heartbeat import last_seen, worker_alive
    from submissions.web import auth, csrf, views
    if settings.https:                                   # behind the HTTPS proxy (Caddy): real scheme and host
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    csrf.init_app(app)
    auth.init_app(app, settings.auth, settings.https)
    app.register_blueprint(views.bp)

    css = Path(app.static_folder) / "app.css"
    asset_version = str(int(css.stat().st_mtime)) if css.exists() else "0"   # cache-buster for the stylesheet

    @app.context_processor
    def _asset_version():
        return {"asset_version": asset_version}

    @app.context_processor
    def _worker_status():
        try:
            return {"worker_alive": worker_alive(engine), "worker_last_seen": last_seen(engine)}
        except Exception:  # noqa: BLE001  never break a page over the banner
            return {"worker_alive": True, "worker_last_seen": None}

    return app
