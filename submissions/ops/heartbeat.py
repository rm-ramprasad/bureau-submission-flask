"""Worker heartbeat: each poll updates the worker's row, so the UI can tell whether anything is processing the queue."""

from __future__ import annotations

import socket
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import Engine, func, select, update

from submissions.db import utcnow
from submissions.ops.tables import worker_heartbeat as t

ALIVE_WITHIN = timedelta(seconds=30)


class Heartbeat:
    def __init__(self, engine: Engine, worker_id: str, clock: Callable[[], datetime] = utcnow):
        self.engine, self.worker_id, self.clock = engine, worker_id, clock
        self.started_at = clock()

    def beat(self, processed: int = 0) -> None:
        now = self.clock()
        with self.engine.begin() as conn:
            changed = conn.execute(update(t).where(t.c.worker_id == self.worker_id).values(
                last_seen_at=now, processed_count=t.c.processed_count + processed)).rowcount
            if changed == 0:
                conn.execute(t.insert().values(worker_id=self.worker_id, host=socket.gethostname()[:100],
                                               started_at=self.started_at, last_seen_at=now, processed_count=processed))


def last_seen(engine: Engine) -> datetime | None:
    """Most recent poll by any worker (UTC), or None if no worker has ever run."""
    with engine.connect() as conn:
        return conn.execute(select(func.max(t.c.last_seen_at))).scalar()


def worker_alive(engine: Engine, now: datetime | None = None, within: timedelta = ALIVE_WITHIN) -> bool:
    seen = last_seen(engine)
    return seen is not None and (now or utcnow()) - seen <= within
