"""Pages: batches, new run, batch detail (actions, generate-from-validation, downloads), findings."""

from __future__ import annotations

import itertools
import logging
import math
from datetime import date, timedelta

from flask import (
    Blueprint, Response, abort, current_app, flash, redirect, render_template, request, session, url_for,
)

from submissions.adjustments import (
    AdjustmentError, AdjustmentNotFoundError, applied_summary, parse_keys, parse_override_value,
)
from submissions.enums import (
    AdjustmentKind, OverrideField, ReportingCycle, RequestStatus, RunMode, SnapshotStatus, SubmissionType,
)
from submissions.ops import audit
from submissions.pipeline import enabled_types
from submissions.requests import (
    DuplicateActiveRequestError, InvalidTransitionError, RequestNotFoundError, RequestValidationError,
)
from submissions.snapshot import SnapshotNotFoundError
from submissions.storage import StoredFileNotFound
from submissions.tenants import InactiveTenantError, TenantNotFoundError
from submissions.validation import results
from submissions.web import WebServices
from submissions.web.auth import current_user

bp = Blueprint("ui", __name__)
log = logging.getLogger(__name__)
PAGE_SIZE = 200
ACTIVE = {RequestStatus.PENDING, RequestStatus.RUNNING}


def svc() -> WebServices:
    return current_app.extensions["submissions"]


def last_month_end(today: date | None = None) -> date:
    return (today or date.today()).replace(day=1) - timedelta(days=1)


@bp.app_template_filter("dt")
def _dt(value) -> str:
    return value.strftime("%d-%m-%Y %H:%M") if value else ""


@bp.app_template_filter("d")
def _d(value) -> str:
    return value.strftime("%d-%m-%Y") if value else ""


# ---------------------------------------------------------------- global context: NBFC selector, user
def selected_tenant() -> str | None:
    """NBFC chosen in the top bar (session). None = all NBFCs."""
    return session.get("tenant") or None


@bp.app_context_processor
def _shell_context():
    try:
        tenants = svc().tenants.list(active_only=False)
    except Exception:  # noqa: BLE001  never break a page over the selector
        tenants = []
    return {"nav_tenants": tenants, "selected_tenant": selected_tenant(), "current_user_name": current_user()}


@bp.post("/context/tenant")
def select_tenant():
    tenant_id = request.form.get("tenant", "")
    if tenant_id and tenant_id not in {t.tenant_id for t in svc().tenants.list(active_only=False)}:
        abort(400)
    session["tenant"] = tenant_id
    target = request.form.get("next", "")
    if not target.startswith("/") or target.startswith("//"):      # only local redirects
        target = url_for("ui.dashboard")
    return redirect(target)


# ---------------------------------------------------------------- dashboard / tracker
@bp.get("/")
def dashboard():
    return redirect(url_for("ui.batches"))          # dashboard page arrives in K.2


@bp.get("/batches")
def batches():
    s = svc()
    return render_template("batches.html", batches=s.requests.list_batches(limit=100, tenant_id=selected_tenant()))


# ---------------------------------------------------------------- new request (always Validate)
def _form_defaults() -> dict:
    return dict(tenant_id=selected_tenant() or "", as_of_date=last_month_end().isoformat(),
                submission_types=[SubmissionType.CONSUMER.value], reporting_cycle="ME")


def _render_new(form: dict, status: int = 200):
    s = svc()
    return render_template("new_run.html", form=form, tenants=s.tenants.list(), types=enabled_types(),
                           all_types=list(SubmissionType), cycles=list(ReportingCycle)), status


def _check_bureau_profiles(tenant_id: str, types: list[str], bureaus: list[str]) -> list[str]:
    s = svc()
    missing = []
    for t in types:
        active = {p.bureau_code.value for p in s.bureaus.list(tenant_id, t)}
        missing += [f"{b} ({t})" for b in bureaus if b not in active]
    return missing


@bp.get("/runs/new")
def new_run():
    return _render_new(_form_defaults())


@bp.post("/runs/new")
def create_run():
    s = svc()
    form = dict(tenant_id=request.form.get("tenant_id", ""), as_of_date=request.form.get("as_of_date", ""),
                submission_types=request.form.getlist("submission_types"),
                reporting_cycle=request.form.get("reporting_cycle", ""))
    try:
        tenant = s.tenants.get_active(form["tenant_id"])
        as_of = date.fromisoformat(form["as_of_date"])
        not_enabled = [t for t in form["submission_types"] if SubmissionType(t) not in enabled_types()]
        if not_enabled:
            raise RequestValidationError(f"Not available yet: {', '.join(not_enabled)}")
        batch_id = s.requests.create_batch(tenant.tenant_id, as_of, RunMode.VALIDATE, form["submission_types"], (),
                                           form["reporting_cycle"] or None, current_user())
    except (TenantNotFoundError, InactiveTenantError):
        flash("Select an active NBFC.", "error")
        return _render_new(form, 400)
    except (RequestValidationError, DuplicateActiveRequestError) as exc:
        flash(str(exc), "error")
        return _render_new(form, 400)
    except ValueError:
        flash("Check the date and selections.", "error")
        return _render_new(form, 400)
    audit.record(s.engine, current_user(), "RUN_CREATED", tenant_id=tenant.tenant_id, target=batch_id,
                 as_of=as_of, mode=str(RunMode.VALIDATE), types=form["submission_types"])
    flash("Validation queued. This page refreshes until it finishes.", "ok")
    return redirect(url_for("ui.batch", batch_id=batch_id))


# ---------------------------------------------------------------- batch detail
@bp.get("/batches/<batch_id>")
def batch(batch_id: str):
    s = svc()
    rows = s.requests.list_batch(batch_id)
    if not rows:
        abort(404)
    snapshots = {}
    for snapshot_id in sorted({r.snapshot_id for r in rows if r.snapshot_id}):
        try:
            snap = s.snapshots.get(snapshot_id)
        except SnapshotNotFoundError:
            continue
        snapshots[snapshot_id] = dict(snapshot=snap, records=results.record_counts(s.engine, snapshot_id),
                                      summary=results.summary(s.engine, snapshot_id)[:10])
    generate_from = [r for r in rows if r.run_mode is RunMode.VALIDATE and r.status is RequestStatus.SUCCEEDED
                     and r.snapshot_id in snapshots and snapshots[r.snapshot_id]["snapshot"].status is SnapshotStatus.VALIDATED]
    bureau_options = {r.request_id: [p.bureau_code for p in s.bureaus.list(r.tenant_id, r.submission_type)]
                      for r in generate_from}
    for snapshot_id, info in snapshots.items():
        snap = info["snapshot"]
        info["adjusted"] = applied_summary(s.engine, snapshot_id)
        info["stale"] = s.adjustments.is_stale(snapshot_id, snap.tenant_id, snap.as_of_date, snap.submission_type)
    tenant = s.tenants.get(rows[0].tenant_id)
    return render_template("batch.html", batch_id=batch_id, rows=rows, tenant=tenant, snapshots=snapshots,
                           generate_from=generate_from, bureau_options=bureau_options,
                           refreshing=any(r.status in ACTIVE for r in rows))


@bp.post("/batches/<batch_id>/generate")
def generate_from_validation(batch_id: str):
    s = svc()
    try:
        source = s.requests.get(int(request.form["request_id"]))
    except (KeyError, ValueError, RequestNotFoundError):
        abort(400)
    if source.batch_id != batch_id or source.run_mode is not RunMode.VALIDATE or source.snapshot_id is None:
        abort(400)
    bureaus = request.form.getlist("bureau_codes")
    adjusted = applied_summary(s.engine, source.snapshot_id)
    try:
        if s.adjustments.is_stale(source.snapshot_id, source.tenant_id, source.as_of_date, source.submission_type):
            raise RequestValidationError("Exclusions or overrides changed after this validation. Re-validate from "
                                         "the Exceptions page, then generate from the new validation.")
        if (adjusted.excluded_accounts or adjusted.overridden_accounts) and not request.form.get("confirm_adjustments"):
            raise RequestValidationError("Confirm the excluded loans and overrides before generating.")
        if not bureaus:
            raise RequestValidationError("Select at least one bureau")
        missing = _check_bureau_profiles(source.tenant_id, [source.submission_type.value], bureaus)
        if missing:
            raise RequestValidationError(f"No active bureau profile: {', '.join(missing)}")
        new_batch = s.requests.create_batch(source.tenant_id, source.as_of_date, RunMode.GENERATE,
                                            [source.submission_type], bureaus, source.reporting_cycle, current_user(),
                                            snapshot_id=source.snapshot_id)
    except (RequestValidationError, DuplicateActiveRequestError, ValueError) as exc:
        flash(str(exc), "error")
        return redirect(url_for("ui.batch", batch_id=batch_id))
    audit.record(s.engine, current_user(), "GENERATE_FROM_VALIDATION", tenant_id=source.tenant_id, target=new_batch,
                 snapshot_id=source.snapshot_id, bureaus=bureaus)
    if adjusted.excluded_accounts or adjusted.overridden_accounts:
        audit.record(s.engine, current_user(), "ADJUSTMENTS_CONFIRMED", tenant_id=source.tenant_id, target=new_batch,
                     snapshot_id=source.snapshot_id, excluded_loans=adjusted.excluded_accounts,
                     overridden_loans=adjusted.overridden_accounts,
                     request_ids=[r.request_id for r in s.requests.list_batch(new_batch)])
    flash("File generation queued from the validated snapshot.", "ok")
    return redirect(url_for("ui.batch", batch_id=new_batch))


def _row_action(request_id: int, action: str):
    s = svc()
    try:
        req = s.requests.get(request_id)
        getattr(s.requests, action)(request_id)
        audit.record(s.engine, current_user(), "REQUEST_RETRIED" if action == "requeue" else "REQUEST_CANCELLED",
                     tenant_id=req.tenant_id, target=request_id, batch_id=req.batch_id)
        flash(f"Request {request_id}: {'re-queued' if action == 'requeue' else 'cancelled'}.", "ok")
    except RequestNotFoundError:
        abort(404)
    except (InvalidTransitionError, DuplicateActiveRequestError) as exc:
        flash(str(exc), "error")
    return redirect(url_for("ui.batch", batch_id=req.batch_id))


@bp.post("/requests/<int:request_id>/retry")
def retry(request_id: int):
    return _row_action(request_id, "requeue")


@bp.post("/requests/<int:request_id>/cancel")
def cancel(request_id: int):
    return _row_action(request_id, "cancel")


@bp.get("/requests/<int:request_id>/<kind>")
def download(request_id: int, kind: str):
    """Stream a generated file from the file store (S3). Only locations owned by the configured store are served."""
    s = svc()
    try:
        req = s.requests.get(request_id)
    except RequestNotFoundError:
        abort(404)
    if kind not in ("file", "exceptions"):
        abort(404)
    uri = req.output_file_path if kind == "file" else req.exceptions_file_path
    if req.status is not RequestStatus.SUCCEEDED or not uri:
        abort(404, f"Request {request_id} has no {kind} to download (status {req.status.value}). "
                   "It may not have finished, or retention has removed the file.")
    if not s.store.owns(uri):
        log.warning("Download refused for request %s: %s is outside the configured store", request_id, uri)
        abort(404, f"The file for request {request_id} was saved at {uri}, which is not in the storage this web app "
                   f"is configured for ({s.store.describe()}). Check that the web app and the worker use the same "
                   "SUBMISSIONS_STORAGE / bucket / prefix settings, or regenerate the file.")
    chunks = s.store.read_chunks(uri)
    try:
        first = next(chunks, b"")                       # fail with 404 before any bytes are sent
    except StoredFileNotFound:
        abort(404, f"The file {uri} no longer exists in storage (deleted or purged). Regenerate it from the validation.")
    name = req.output_file_name if kind == "file" and req.output_file_name else uri.replace("\\", "/").rsplit("/", 1)[-1]
    audit.record(s.engine, current_user(), "FILE_DOWNLOADED", tenant_id=req.tenant_id, target=request_id,
                 kind=kind, file=name, location=uri)
    mimetype = "text/csv" if name.endswith(".csv") else "text/plain"
    return Response(itertools.chain([first], chunks), mimetype=mimetype,
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ---------------------------------------------------------------- exceptions (exclusions + overrides)
def _scope_from(values) -> tuple[date, SubmissionType]:
    """As-of date and type from a form or query string (defaults: last month end, consumer)."""
    try:
        as_of = date.fromisoformat(values.get("as_of") or last_month_end().isoformat())
        sub_type = SubmissionType(values.get("type") or SubmissionType.CONSUMER.value)
    except ValueError:
        abort(400)
    return as_of, sub_type


def _back_to_exceptions(as_of: date, sub_type: SubmissionType):
    return redirect(url_for("ui.exceptions", as_of=as_of.isoformat(), type=sub_type.value))


def _exceptions_tenant():
    tenant_id = selected_tenant()
    if not tenant_id:
        flash("Select an NBFC in the top bar first.", "error")
        return None
    try:
        return svc().tenants.get_active(tenant_id)
    except (TenantNotFoundError, InactiveTenantError):
        flash("Select an active NBFC in the top bar.", "error")
        return None


@bp.get("/exceptions")
def exceptions():
    s = svc()
    as_of, sub_type = _scope_from(request.args)
    tenant_id = selected_tenant()
    ctx = dict(as_of=as_of, sub_type=sub_type, types=enabled_types(), fields=list(OverrideField), tenant=None,
               adjustments=[], latest=None, frozen={}, stale=False, effects={}, history=bool(request.args.get("history")))
    if tenant_id:
        ctx["tenant"] = s.tenants.get(tenant_id)
        ctx["adjustments"] = s.adjustments.list(tenant_id, as_of, sub_type, include_removed=ctx["history"])
        latest = s.snapshots.latest(tenant_id, sub_type, as_of)
        if latest:
            ctx.update(latest=latest, frozen=s.adjustments.frozen(latest.snapshot_id),
                       stale=s.adjustments.is_stale(latest.snapshot_id, tenant_id, as_of, sub_type))
            for e in s.adjustments.applied(latest.snapshot_id):
                ctx["effects"].setdefault(e.adjustment_id, []).append(e)
    return render_template("exceptions.html", **ctx)


@bp.post("/exceptions/exclude")
def add_exclusions():
    s = svc()
    as_of, sub_type = _scope_from(request.form)
    tenant = _exceptions_tenant()
    if tenant is None:
        return _back_to_exceptions(as_of, sub_type)
    kind = AdjustmentKind.EXCLUDE_CUSTOMER if request.form.get("kind") == "customer" else AdjustmentKind.EXCLUDE_ACCOUNT
    try:
        result = s.adjustments.add_exclusions(tenant.tenant_id, as_of, sub_type, kind,
                                              parse_keys(request.form.get("keys", "")),
                                              request.form.get("reason"), current_user())
    except AdjustmentError as exc:
        flash(str(exc), "error")
        return _back_to_exceptions(as_of, sub_type)
    if result.added:
        audit.record(s.engine, current_user(), "ADJUSTMENT_ADDED", tenant_id=tenant.tenant_id,
                     target=f"{as_of:%Y-%m-%d} {sub_type}", kind=str(kind), keys=result.added[:200],
                     count=len(result.added), reason=request.form.get("reason") or None)
    what = "customer(s)" if kind is AdjustmentKind.EXCLUDE_CUSTOMER else "loan(s)"
    msg = f"{len(result.added)} {what} added to the exclusions."
    if result.already_present:
        msg += f" Already excluded: {', '.join(result.already_present[:10])}."
    flash(msg + " Re-validate to apply.", "ok")
    return _back_to_exceptions(as_of, sub_type)


@bp.post("/exceptions/override")
def add_override():
    s = svc()
    as_of, sub_type = _scope_from(request.form)
    tenant = _exceptions_tenant()
    if tenant is None:
        return _back_to_exceptions(as_of, sub_type)
    try:
        field = OverrideField(request.form.get("field", ""))
    except ValueError:
        flash("Choose the field to override.", "error")
        return _back_to_exceptions(as_of, sub_type)
    account = request.form.get("account_number", "")
    try:
        value = parse_override_value(field, request.form.get("value", ""))
        new_id, replaced = s.adjustments.add_override(tenant.tenant_id, as_of, sub_type, account, field, value,
                                                      request.form.get("reason"), current_user())
    except AdjustmentError as exc:
        flash(str(exc), "error")
        return _back_to_exceptions(as_of, sub_type)
    audit.record(s.engine, current_user(), "ADJUSTMENT_ADDED", tenant_id=tenant.tenant_id,
                 target=f"{as_of:%Y-%m-%d} {sub_type}", kind=str(AdjustmentKind.OVERRIDE), adjustment_id=new_id,
                 account_number=account.strip(), field=field.value, value=str(value), replaced=replaced,
                 reason=request.form.get("reason") or None)
    flash(f"Override saved: {account.strip()} {field.label} = {value}"
          f"{' (replaces the previous one)' if replaced else ''}. Re-validate to apply.", "ok")
    return _back_to_exceptions(as_of, sub_type)


@bp.post("/exceptions/<int:adjustment_id>/remove")
def remove_adjustment(adjustment_id: int):
    s = svc()
    try:
        adj = s.adjustments.get(adjustment_id)
    except AdjustmentNotFoundError:
        abort(404)
    if adj.tenant_id != selected_tenant():
        abort(403)
    try:
        s.adjustments.remove(adjustment_id, current_user())
        audit.record(s.engine, current_user(), "ADJUSTMENT_REMOVED", tenant_id=adj.tenant_id,
                     target=f"{adj.as_of_date:%Y-%m-%d} {adj.submission_type}", adjustment_id=adjustment_id,
                     kind=str(adj.kind), key=adj.key, field=adj.field.value if adj.field else None)
        flash(f"Removed: {adj.kind.value.replace('_', ' ').lower()} {adj.key}. Re-validate to apply.", "ok")
    except AdjustmentError as exc:
        flash(str(exc), "error")
    return _back_to_exceptions(adj.as_of_date, adj.submission_type)


@bp.post("/exceptions/revalidate")
def revalidate():
    s = svc()
    as_of, sub_type = _scope_from(request.form)
    tenant = _exceptions_tenant()
    if tenant is None:
        return _back_to_exceptions(as_of, sub_type)
    try:
        batch_id = s.requests.create_batch(tenant.tenant_id, as_of, RunMode.VALIDATE, [sub_type], (),
                                           request.form.get("reporting_cycle") or None, current_user())
    except (RequestValidationError, DuplicateActiveRequestError) as exc:
        flash(str(exc), "error")
        return _back_to_exceptions(as_of, sub_type)
    audit.record(s.engine, current_user(), "RUN_CREATED", tenant_id=tenant.tenant_id, target=batch_id, as_of=as_of,
                 mode=str(RunMode.VALIDATE), types=[sub_type.value], source="exceptions")
    flash("Re-validation queued with the current exclusions and overrides.", "ok")
    return redirect(url_for("ui.batch", batch_id=batch_id))


@bp.get("/audit")
def audit_log():
    return render_template("audit.html", events=audit.recent(svc().engine, 200, selected_tenant()))


# ---------------------------------------------------------------- findings
def _filter() -> results.FindingFilter:
    return results.FindingFilter(**{k: request.args.get(k) or None for k in ("rule_id", "outcome", "account_key",
                                                                              "party_key")})


@bp.get("/snapshots/<int:snapshot_id>/findings")
def findings(snapshot_id: int):
    s = svc()
    try:
        snap = s.snapshots.get(snapshot_id)
    except SnapshotNotFoundError:
        abort(404)
    flt = _filter()
    page = max(1, request.args.get("page", 1, type=int))
    rows, total = results.findings(s.engine, snapshot_id, flt, PAGE_SIZE, (page - 1) * PAGE_SIZE)
    return render_template("findings.html", snap=snap, summary=results.summary(s.engine, snapshot_id),
                           records=results.record_counts(s.engine, snapshot_id), rows=rows, total=total, flt=flt,
                           page=page, pages=max(1, math.ceil(total / PAGE_SIZE)), outcomes=results.OUTCOME_ORDER)


@bp.get("/snapshots/<int:snapshot_id>/findings.csv")
def findings_csv(snapshot_id: int):
    s = svc()
    try:
        snap = s.snapshots.get(snapshot_id)
    except SnapshotNotFoundError:
        abort(404)
    name = f"findings-{snap.tenant_id}-{snap.as_of_date:%Y%m%d}-{snapshot_id}.csv"
    return Response(results.findings_csv(s.engine, snapshot_id, _filter()), mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})
