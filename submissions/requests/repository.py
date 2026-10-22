"""All reads and writes of submission_request.

Used by the web app (create, list, read) and the worker (claim, record result).
Neither side touches the table directly.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import Engine, func, select, update
from sqlalchemy.exc import IntegrityError

from submissions.db import utcnow
from submissions.enums import BureauCode, ReportingCycle, RequestStatus as S, RunMode, SubmissionType
from submissions.requests.models import BatchSummary, RequestResult, SubmissionRequest
from submissions.requests.state import ACTIVE_STATUSES, InvalidTransitionError, check_transition, derive_batch_status
from submissions.requests.tables import submission_request as t


class RequestValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ActiveConflict:
    tenant_id: str
    as_of_date: date
    submission_type: SubmissionType
    run_mode: RunMode
    bureau_code: BureauCode | None

    def __str__(self) -> str:
        return (f"{self.tenant_id} {self.as_of_date:%d-%m-%Y} {self.submission_type} {self.run_mode}"
                + (f" {self.bureau_code}" if self.bureau_code else ""))


class DuplicateActiveRequestError(Exception):
    def __init__(self, conflicts: list[ActiveConflict]):
        self.conflicts = conflicts
        super().__init__(f"Already pending or running: {', '.join(map(str, conflicts)) or 'another request'}")


class RequestNotFoundError(LookupError):
    pass


def active_key(tenant_id: str, as_of_date: date, submission_type: SubmissionType, run_mode: RunMode,
               bureau_code: BureauCode | None) -> str:
    return f"{tenant_id}|{as_of_date:%Y%m%d}|{submission_type}|{run_mode}|{bureau_code or '-'}"


def new_batch_id(now: datetime) -> str:
    return f"{now:%Y%m%d%H%M%S}-{secrets.token_hex(3)}"


class RequestRepository:
    def __init__(self, engine: Engine, clock: Callable[[], datetime] = utcnow):
        self.engine = engine
        self.clock = clock

    # ------------------------------------------------------------------ create
    def create_batch(
        self,
        tenant_id: str,
        as_of_date: date,
        run_mode: RunMode | str,
        submission_types: Iterable[SubmissionType | str],
        bureau_codes: Iterable[BureauCode | str] = (),
        reporting_cycle: ReportingCycle | str | None = None,
        requested_by: str | None = None,
        snapshot_id: int | None = None,
    ) -> str:
        """Insert the PENDING rows for one click, all sharing a new batch_id.

        VALIDATE: one row per submission type (validation is bureau-independent; no bureaus allowed).
        GENERATE: one row per bureau, always from an already validated snapshot (`snapshot_id` required):
        "generate files from this validation". There are no direct GENERATE runs.
        Tenant existence/activity is the caller's check; the foreign key is the backstop.
        """
        now = self.clock()
        mode = RunMode(run_mode)
        types = list(dict.fromkeys(SubmissionType(x) for x in submission_types))
        bureaus = list(dict.fromkeys(BureauCode(x) for x in bureau_codes))
        cycle = ReportingCycle(reporting_cycle) if reporting_cycle else None
        if not tenant_id:
            raise RequestValidationError("Select an NBFC")
        if not types:
            raise RequestValidationError("Select at least one submission type")
        if mode is RunMode.GENERATE and snapshot_id is None:
            raise RequestValidationError("Files are generated only from a validated run: validate first")
        if mode is RunMode.GENERATE and not bureaus:
            raise RequestValidationError("Select at least one bureau to generate files")
        if mode is RunMode.VALIDATE and bureaus:
            raise RequestValidationError("Validation is bureau-independent; do not select bureaus")
        if mode is RunMode.VALIDATE and snapshot_id is not None:
            raise RequestValidationError("A snapshot can only be reused to generate files")
        if snapshot_id is not None and len(types) != 1:
            raise RequestValidationError("A reused snapshot belongs to exactly one submission type")
        if as_of_date > now.date():
            raise RequestValidationError("As-of date cannot be in the future")

        units = [(st, bc) for st in types for bc in (bureaus or [None])]
        keys = [active_key(tenant_id, as_of_date, st, mode, bc) for st, bc in units]
        conflicts = self._active_conflicts(keys)
        if conflicts:
            raise DuplicateActiveRequestError(conflicts)

        batch_id = new_batch_id(now)
        rows = [dict(batch_id=batch_id, tenant_id=tenant_id, run_mode=str(mode), as_of_date=as_of_date,
                     submission_type=str(st), bureau_code=str(bc) if bc else None, snapshot_id=snapshot_id,
                     reporting_cycle=str(cycle) if cycle else None, requested_by=requested_by,
                     requested_at=now, status=str(S.PENDING), active_key=key, attempt_count=0)
                for (st, bc), key in zip(units, keys)]
        try:
            with self.engine.begin() as conn:
                conn.execute(t.insert(), rows)
        except IntegrityError:
            conflicts = self._active_conflicts(keys)
            if conflicts:  # lost a race with a concurrent submit; the unique active_key caught it
                raise DuplicateActiveRequestError(conflicts) from None
            raise  # something else, e.g. unknown tenant (foreign key)
        return batch_id

    def _active_conflicts(self, keys: list[str]) -> list[ActiveConflict]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(t.c.tenant_id, t.c.as_of_date, t.c.submission_type, t.c.run_mode,
                                       t.c.bureau_code).where(t.c.active_key.in_(keys))).all()
        return [ActiveConflict(r.tenant_id, r.as_of_date, SubmissionType(r.submission_type), RunMode(r.run_mode),
                               BureauCode(r.bureau_code) if r.bureau_code else None) for r in rows]

    # ------------------------------------------------------------------ read
    def get(self, request_id: int) -> SubmissionRequest:
        with self.engine.connect() as conn:
            row = conn.execute(select(t).where(t.c.request_id == request_id)).first()
        if row is None:
            raise RequestNotFoundError(f"Request {request_id} not found")
        return SubmissionRequest.from_row(row)

    def list_batch(self, batch_id: str) -> list[SubmissionRequest]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(t).where(t.c.batch_id == batch_id)
                                .order_by(t.c.submission_type, t.c.bureau_code)).all()
        return [SubmissionRequest.from_row(r) for r in rows]

    def list_batches(self, limit: int = 50, tenant_id: str | None = None) -> list[BatchSummary]:
        """Most recent batches first, each with its derived status. Optionally one tenant only."""
        latest_q = select(t.c.batch_id, func.max(t.c.requested_at).label("at")).group_by(t.c.batch_id)
        if tenant_id is not None:
            latest_q = latest_q.where(t.c.tenant_id == tenant_id)
        with self.engine.connect() as conn:
            latest = latest_q.order_by(func.max(t.c.requested_at).desc()).limit(limit).subquery()
            rows = conn.execute(select(t).join(latest, latest.c.batch_id == t.c.batch_id)
                                .order_by(latest.c.at.desc(), t.c.request_id)).all()
        grouped: dict[str, list[SubmissionRequest]] = {}
        for r in rows:
            grouped.setdefault(r.batch_id, []).append(SubmissionRequest.from_row(r))
        return [self._summarise(batch_id, reqs) for batch_id, reqs in grouped.items()]

    @staticmethod
    def _summarise(batch_id: str, reqs: list[SubmissionRequest]) -> BatchSummary:
        counts: dict[S, int] = {}
        for r in reqs:
            counts[r.status] = counts.get(r.status, 0) + 1
        first = reqs[0]
        return BatchSummary(
            batch_id=batch_id, tenant_id=first.tenant_id, run_mode=first.run_mode, as_of_date=first.as_of_date,
            requested_by=first.requested_by, requested_at=first.requested_at,
            submission_types=tuple(dict.fromkeys(r.submission_type for r in reqs)),
            bureau_codes=tuple(dict.fromkeys(r.bureau_code for r in reqs if r.bureau_code)),
            status_counts=counts, status=derive_batch_status(r.status for r in reqs))

    def running_started_before(self, cutoff: datetime) -> list[SubmissionRequest]:
        """RUNNING rows whose worker started them before `cutoff` (candidates for stuck-row recovery)."""
        with self.engine.connect() as conn:
            rows = conn.execute(select(t).where(t.c.status == str(S.RUNNING), t.c.started_at < cutoff)
                                .order_by(t.c.started_at)).all()
        return [SubmissionRequest.from_row(r) for r in rows]

    def finished_with_files_before(self, cutoff: datetime) -> list[SubmissionRequest]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(t).where(t.c.finished_at < cutoff, (t.c.output_file_path.is_not(None))
                                                | (t.c.exceptions_file_path.is_not(None)))).all()
        return [SubmissionRequest.from_row(r) for r in rows]

    def clear_files(self, request_id: int) -> None:
        """Forget file paths after retention removed the files."""
        with self.engine.begin() as conn:
            conn.execute(update(t).where(t.c.request_id == request_id)
                         .values(output_file_path=None, exceptions_file_path=None))

    # ------------------------------------------------------------------ worker
    def claim_next(self, worker_id: str) -> SubmissionRequest | None:
        """Atomically move the oldest PENDING row to RUNNING for this worker."""
        while True:
            with self.engine.begin() as conn:
                candidate = conn.execute(select(t.c.request_id).where(t.c.status == str(S.PENDING))
                                         .order_by(t.c.requested_at, t.c.request_id).limit(1)).scalar()
                if candidate is None:
                    return None
                claimed = conn.execute(
                    update(t).where(t.c.request_id == candidate, t.c.status == str(S.PENDING))
                    .values(status=str(S.RUNNING), worker_id=worker_id, started_at=self.clock(),
                            finished_at=None, error_message=None, attempt_count=t.c.attempt_count + 1)
                ).rowcount
            if claimed == 1:
                return self.get(candidate)
            # another worker took it first; try the next one

    def assign_snapshot(self, request_id: int, snapshot_id: int) -> None:
        """Record which extract snapshot an active row is using (set by the worker)."""
        with self.engine.begin() as conn:
            changed = conn.execute(update(t).where(t.c.request_id == request_id,
                                                   t.c.status.in_([str(s) for s in ACTIVE_STATUSES]))
                                   .values(snapshot_id=snapshot_id)).rowcount
        if changed != 1:
            raise InvalidTransitionError(f"Request {request_id} is not active; cannot assign a snapshot")

    def release(self, request_id: int) -> None:
        """Hand a RUNNING row back to the queue (e.g. its snapshot is being built by another worker)."""
        self._transition(request_id, S.PENDING, worker_id=None, started_at=None)

    def mark_succeeded(self, request_id: int, result: RequestResult) -> None:
        self._transition(request_id, S.SUCCEEDED, finished_at=self.clock(), active_key=None,
                         **vars(result))

    def mark_failed(self, request_id: int, error_message: str) -> None:
        self._transition(request_id, S.FAILED, finished_at=self.clock(), active_key=None,
                         error_message=error_message[:65000])

    # ------------------------------------------------------------------ user actions
    def cancel(self, request_id: int) -> None:
        self._transition(request_id, S.CANCELLED, finished_at=self.clock(), active_key=None)

    def requeue(self, request_id: int) -> None:
        """Retry a FAILED or CANCELLED row. Fails if the same unit of work is already active."""
        req = self.get(request_id)
        key = active_key(req.tenant_id, req.as_of_date, req.submission_type, req.run_mode, req.bureau_code)
        try:
            self._transition(request_id, S.PENDING, active_key=key, worker_id=None, started_at=None,
                             finished_at=None, error_message=None)
        except IntegrityError:
            raise DuplicateActiveRequestError(self._active_conflicts([key])) from None

    # ------------------------------------------------------------------ internals
    def _transition(self, request_id: int, target: S, **values) -> None:
        current = self.get(request_id).status
        check_transition(current, target)
        with self.engine.begin() as conn:
            changed = conn.execute(update(t).where(t.c.request_id == request_id, t.c.status == str(current))
                                   .values(status=str(target), **values)).rowcount
        if changed != 1:
            raise InvalidTransitionError(f"Request {request_id} changed concurrently; expected {current}")
