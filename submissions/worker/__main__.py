"""python -m submissions.worker [--once] [--poll 5] [--id NAME]"""

from __future__ import annotations

import argparse
import logging
import socket
from pathlib import Path

from submissions.bureaus import BureauRepository
from submissions.config import Settings
from submissions.db import make_engine
from submissions.logging_config import configure_logging
from submissions.ops.heartbeat import Heartbeat
from submissions.pipeline import Services
from submissions.requests import RequestRepository
from submissions.snapshot import SnapshotRepository
from submissions.storage import make_store
from submissions.tenants import TenantRepository
from submissions.worker.runner import Worker


def build_worker(settings: Settings, worker_id: str) -> Worker:
    app = make_engine(settings.database_url)
    source = app if settings.source_database_url == settings.database_url else make_engine(settings.source_database_url)
    services = Services(app_engine=app, source_engine=source, snapshots=SnapshotRepository(app),
                        base_dir=Path.cwd(), store=make_store(settings))
    return Worker(services, RequestRepository(app), TenantRepository(app), BureauRepository(app), worker_id,
                  heartbeat=Heartbeat(app, worker_id))


def main() -> None:
    ap = argparse.ArgumentParser(description="Submission worker")
    ap.add_argument("--once", action="store_true", help="process what is pending, then exit")
    ap.add_argument("--poll", type=float, default=5.0, help="seconds between polls")
    ap.add_argument("--id", default=f"{socket.gethostname()}-worker", help="worker id recorded on claimed rows")
    args = ap.parse_args()
    settings = Settings.from_env()
    configure_logging(settings.log_level)
    worker = build_worker(settings, args.id)
    logging.getLogger("submissions.worker").info("Generated files are written to %s",
                                                 worker.services.store.describe().split(" or ")[0])
    if args.once:
        print(f"processed {worker.run_once()} request(s)")
    else:
        worker.run_forever(args.poll)


if __name__ == "__main__":
    main()
