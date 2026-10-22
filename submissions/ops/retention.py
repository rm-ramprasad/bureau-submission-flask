"""Retention: remove old PII (canonical rows, findings, record outcomes, generated files).

The snapshot header and its per-rule validation_summary are kept for trends; the snapshot becomes PURGED.

Usage:  python -m submissions.ops.retention --days 90 [--dry-run]
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import Engine, delete

from submissions.adapters.extract import purge_rows
from submissions.db import utcnow
from submissions.enums import SnapshotStatus
from submissions.ops import audit
from submissions.requests import RequestRepository
from submissions.snapshot import SnapshotRepository
from submissions.storage import FileStore, make_store
from submissions.validation.tables import record_outcome, validation_finding

PURGEABLE = [SnapshotStatus.EXTRACTED, SnapshotStatus.VALIDATED, SnapshotStatus.FAILED]


@dataclass
class PurgeResult:
    snapshots: list[int] = field(default_factory=list)
    files: list[str] = field(default_factory=list)


def purge(engine: Engine, older_than: timedelta, store: FileStore, *, dry_run: bool = False, actor: str = "purge",
          clock: Callable[[], datetime] = utcnow) -> PurgeResult:
    cutoff, result = clock() - older_than, PurgeResult()
    snapshots, requests = SnapshotRepository(engine), RequestRepository(engine)
    for snap in snapshots.list_where(PURGEABLE, created_before=cutoff):
        result.snapshots.append(snap.snapshot_id)
        if dry_run:
            continue
        purge_rows(engine, snap.snapshot_id)
        with engine.begin() as conn:
            for t in (validation_finding, record_outcome):
                conn.execute(delete(t).where(t.c.snapshot_id == snap.snapshot_id))
        snapshots.mark_purged(snap.snapshot_id)
    for req in requests.finished_with_files_before(cutoff):
        uris = [u for u in (req.output_file_path, req.exceptions_file_path) if u and store.owns(u)]  # never elsewhere
        result.files += uris
        if dry_run:
            continue
        for uri in uris:
            store.delete(uri)
        requests.clear_files(req.request_id)
    if not dry_run and (result.snapshots or result.files):
        audit.record(engine, actor, "RETENTION_PURGE", target=f"before {cutoff:%Y-%m-%d}",
                     snapshots=result.snapshots, files=len(result.files))
    return result


def main(argv: list[str] | None = None) -> int:
    from submissions.config import Settings
    from submissions.db import make_engine
    from submissions.logging_config import configure_logging

    ap = argparse.ArgumentParser(description="Purge snapshot data and files older than N days")
    ap.add_argument("--days", type=int, required=True)
    ap.add_argument("--dry-run", action="store_true", help="list what would be purged")
    args = ap.parse_args(argv)
    if args.days < 1:
        ap.error("--days must be at least 1")
    settings = Settings.from_env()
    configure_logging(settings.log_level)
    result = purge(make_engine(settings.database_url), timedelta(days=args.days), make_store(settings),
                   dry_run=args.dry_run)
    verb = "Would purge" if args.dry_run else "Purged"
    print(f"{verb} {len(result.snapshots)} snapshot(s) and {len(result.files)} file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
