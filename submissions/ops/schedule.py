"""Create the scheduled Validate runs for every active NBFC. Trigger from Windows Task Scheduler / cron (RUNBOOK.md).

Always validate first: the team reviews findings, captures exclusions/overrides, then generates files from the
validated run in the UI. The scheduler never generates files directly.

Usage:  python -m submissions.ops.schedule --cycle ME                       # as of last month-end
        python -m submissions.ops.schedule --cycle ME --tenant nbfc_001 --as-of 2026-09-30
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import Engine

from submissions.enums import ReportingCycle, RunMode
from submissions.ops import audit
from submissions.pipeline import enabled_types
from submissions.requests import DuplicateActiveRequestError, RequestRepository, RequestValidationError
from submissions.tenants import TenantRepository

ACTOR = "scheduler"
WEEKLY_DAY = {ReportingCycle.WEEKLY_1: 9, ReportingCycle.WEEKLY_2: 16, ReportingCycle.WEEKLY_3: 23}   # UCRF HD.53


def default_as_of(cycle: ReportingCycle, today: date) -> date:
    """DL: yesterday. W1/W2/W3: the 9th/16th/23rd (this month, or last month if not reached yet). ME: last month-end."""
    if cycle is ReportingCycle.DAILY:
        return today - timedelta(days=1)
    if cycle in WEEKLY_DAY:
        day = WEEKLY_DAY[cycle]
        this_month = today.replace(day=day)
        if this_month <= today:
            return this_month
        return (today.replace(day=1) - timedelta(days=1)).replace(day=day)
    if cycle is ReportingCycle.MONTH_END:
        return today.replace(day=1) - timedelta(days=1)
    raise ValueError(f"No default as-of date for cycle {cycle}; pass --as-of")


@dataclass
class ScheduleResult:
    created: dict[str, str] = field(default_factory=dict)      # tenant -> batch id
    skipped: dict[str, str] = field(default_factory=dict)      # tenant -> reason


def schedule_runs(engine: Engine, cycle: ReportingCycle | str, *, today: date | None = None, as_of: date | None = None,
                  tenant_ids: list[str] | None = None) -> ScheduleResult:
    cycle = ReportingCycle(cycle)
    as_of = as_of or default_as_of(cycle, today or date.today())
    requests, result = RequestRepository(engine), ScheduleResult()
    types = enabled_types()                          # every submission type that has a pipeline
    for tenant in TenantRepository(engine).list(active_only=True):
        if tenant_ids and tenant.tenant_id not in tenant_ids:
            continue
        try:
            batch = requests.create_batch(tenant.tenant_id, as_of, RunMode.VALIDATE, types, (), cycle,
                                          requested_by=ACTOR)
        except (DuplicateActiveRequestError, RequestValidationError) as exc:
            result.skipped[tenant.tenant_id] = str(exc)
            continue
        result.created[tenant.tenant_id] = batch
        audit.record(engine, ACTOR, "RUN_SCHEDULED", tenant_id=tenant.tenant_id, target=batch, cycle=str(cycle),
                     as_of=as_of, types=[str(t) for t in types])
    return result


def main(argv: list[str] | None = None) -> int:
    from submissions.config import Settings
    from submissions.db import make_engine
    from submissions.logging_config import configure_logging

    ap = argparse.ArgumentParser(description="Create scheduled Validate runs for all active NBFCs")
    ap.add_argument("--cycle", required=True, choices=[c.value for c in ReportingCycle])
    ap.add_argument("--as-of", type=date.fromisoformat)
    ap.add_argument("--tenant", action="append", help="limit to these tenants (repeatable)")
    args = ap.parse_args(argv)
    settings = Settings.from_env()
    configure_logging(settings.log_level)
    result = schedule_runs(make_engine(settings.database_url), args.cycle, as_of=args.as_of, tenant_ids=args.tenant)
    for tenant, batch in result.created.items():
        print(f"created  {tenant:20} {batch}")
    for tenant, reason in result.skipped.items():
        print(f"skipped  {tenant:20} {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
