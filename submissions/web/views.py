"""Pages: dashboard, tracker, files, new request, exceptions step, batch detail (submit, generate, downloads),
findings (with exclude / override into a draft request), audit."""

from __future__ import annotations

import itertools
import logging
import math
import re
from datetime import date, datetime, time, timedelta

from flask import (
    Blueprint, Response, abort, current_app, flash, redirect, render_template, request, session, url_for,
)
from sqlalchemy import select

from submissions.adjustments import (
    AdjustmentError, AdjustmentNotFoundError, AdjustmentScope, applied_summary, parse_keys, parse_override_value,
)
from submissions.canonical.schema import ENTITY_TABLES
from submissions.db import utcnow
from submissions.enums import (
    AdjustmentKind, BatchStatus, BureauCode, Outcome, OverrideField, ReportingCycle, RequestStatus, RunMode, SnapshotStatus,
    SubmissionType,
)
from submissions.ops import audit
from submissions.pipeline import enabled_types
from submissions.requests import (
    DuplicateActiveRequestError, InvalidTransitionError, RequestNotFoundError, RequestValidationError, stages,
)
from submissions.requests.stages import Period, Stage
from submissions.snapshot import SnapshotNotFoundError
from submissions.storage import StoredFileNotFound
from submissions.tenants import InactiveTenantError, TenantNotFoundError
from submissions.validation import custom as custom_rules
from submissions.validation import parameters as rule_parameters
from submissions.validation import results, rules
from submissions.web import WebServices
from submissions.web.auth import current_user

bp = Blueprint("ui", __name__)
log = logging.getLogger(__name__)
PAGE_SIZE = 200
TRACKER_PAGE = 50
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


@bp.app_template_filter("age")
def _age(value) -> str:
    """timedelta -> '3d 4h' / '5h' / '12m'."""
    if value is None:
        return ""
    minutes = int(value.total_seconds() // 60)
    days, hours = minutes // 1440, minutes % 1440 // 60
    return f"{days}d {hours}h" if days else (f"{hours}h" if hours else f"{minutes}m")


@bp.app_template_filter("pct")
def _pct(value) -> str:
    return "—" if value is None else f"{value:.0%}"


@bp.app_template_filter("num")
def _num(value) -> str:
    return f"{value:,}" if value is not None else ""


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
def _period() -> Period:
    try:
        return Period(request.args.get("period") or Period.THIS_MONTH.value)
    except ValueError:
        return Period.THIS_MONTH


@bp.get("/")
def dashboard():
    s = svc()
    period, tenant_id = _period(), selected_tenant()
    board = stages.build_dashboard(s.engine, tenant_id, period, utcnow())
    nbfc_status = None
    if not tenant_id:                                    # all NBFCs: who has (not) submitted
        active = s.tenants.list()
        latest = stages.latest_per_tenant(s.engine, [x.tenant_id for x in active])
        nbfc_status = [(x, latest.get(x.tenant_id)) for x in active]
    return render_template("dashboard.html", board=board, period=period, periods=list(Period), Stage=Stage,
                           nbfc_status=nbfc_status)


@bp.get("/batches")
def batches():
    s = svc()
    stage_arg, period_arg = request.args.get("stage"), request.args.get("period")
    if stage_arg or period_arg:                          # request view (dashboard click-through)
        try:
            stage = Stage(stage_arg) if stage_arg else None
        except ValueError:
            abort(400)
        period = _period()
        reqs = stages.load_requests(s.engine, selected_tenant(), period.start(utcnow()))
        if stage:
            reqs = [r for r in reqs if r.stage is stage]
        return render_template("requests.html", reqs=reqs, stage=stage, period=period, periods=list(Period),
                               stages_all=list(Stage))
    flt = {k: request.args.get(k) or "" for k in ("status", "mode", "bureau", "from", "to")}
    try:
        mode = RunMode(flt["mode"]) if flt["mode"] else None
        bureau = BureauCode(flt["bureau"]) if flt["bureau"] else None
        status = BatchStatus(flt["status"]) if flt["status"] else None
        start = datetime.combine(date.fromisoformat(flt["from"]), time()) if flt["from"] else None
        end = datetime.combine(date.fromisoformat(flt["to"]) + timedelta(days=1), time()) if flt["to"] else None
    except ValueError:
        abort(400)
    found = s.requests.list_batches(limit=2000, tenant_id=selected_tenant(), run_mode=mode, bureau_code=bureau,
                                    requested_from=start, requested_to=end)
    if status:
        found = [b for b in found if b.status is status]
    page = max(1, request.args.get("page", 1, type=int))
    pages = max(1, math.ceil(len(found) / TRACKER_PAGE))
    return render_template("batches.html", batches=found[(page - 1) * TRACKER_PAGE:page * TRACKER_PAGE],
                           total=len(found), page=page, pages=pages, flt=flt, filtered=any(flt.values()),
                           periods=list(Period), stages_all=list(Stage), statuses=list(BatchStatus),
                           modes=list(RunMode), bureaus=list(BureauCode))


@bp.get("/files")
def files():
    s = svc()
    try:
        bureau = BureauCode(request.args["bureau"]) if request.args.get("bureau") else None
    except ValueError:
        abort(400)
    period = _period() if request.args.get("period") else Period.ALL
    page = max(1, request.args.get("page", 1, type=int))
    rows, total = s.requests.list_files(selected_tenant(), bureau, period.start(utcnow()), TRACKER_PAGE,
                                        (page - 1) * TRACKER_PAGE)
    return render_template("files.html", rows=rows, total=total, page=page,
                           pages=max(1, math.ceil(total / TRACKER_PAGE)), bureau=bureau, bureaus=list(BureauCode),
                           period=period, periods=list(Period), store=s.store.describe())


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
                                           form["reporting_cycle"] or None, current_user(), draft=True)
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
                 as_of=as_of, mode=str(RunMode.VALIDATE), types=form["submission_types"], status="DRAFT")
    session["tenant"] = tenant.tenant_id                  # follow the request's NBFC in the top bar
    flash(f"Request {batch_id} created. Add any exclusions or overrides, then submit it for validation.", "ok")
    return redirect(url_for("ui.request_exceptions", batch_id=batch_id))


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
        info["adjusted"] = applied_summary(s.engine, snapshot_id)
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


# ---------------------------------------------------------------- exceptions: step 2 of a request
def _request_row(batch_id: str):
    """The request's row for the chosen submission type (?type= / form type; default the first)."""
    rows = svc().requests.list_batch(batch_id)
    if not rows or rows[0].run_mode is not RunMode.VALIDATE:
        abort(404)
    wanted = request.values.get("type")
    row = next((r for r in rows if r.submission_type.value == wanted), rows[0])
    return rows, row


def _back(batch_id: str, row):
    return redirect(url_for("ui.request_exceptions", batch_id=batch_id, type=row.submission_type.value))


def _draft_or_flash(row) -> bool:
    if row.status is RequestStatus.DRAFT:
        return True
    flash("This request was already submitted, so its exceptions are locked. For more exceptions, create a new "
          "request for the same date.", "error")
    return False


@bp.get("/exceptions")
def exceptions():
    """Requests waiting for their exceptions (drafts), for the NBFC chosen in the top bar."""
    s = svc()
    drafts = [b for b in s.requests.list_batches(limit=100, tenant_id=selected_tenant())
              if b.status is BatchStatus.DRAFT]
    return render_template("exceptions_list.html", drafts=drafts)


@bp.get("/batches/<batch_id>/exceptions")
def request_exceptions(batch_id: str):
    s = svc()
    rows, row = _request_row(batch_id)
    scope = AdjustmentScope.of(row)
    history = bool(request.args.get("history"))
    frozen, effects = {}, {}
    if row.snapshot_id:
        frozen = s.adjustments.frozen(row.snapshot_id)
        for e in s.adjustments.applied(row.snapshot_id):
            effects.setdefault(e.adjustment_id, []).append(e)
    return render_template("exceptions.html", batch_id=batch_id, rows=rows, row=row,
                           tenant=s.tenants.get(row.tenant_id), fields=list(OverrideField), history=history,
                           adjustments=s.adjustments.list(scope, include_removed=history), frozen=frozen,
                           effects=effects, is_draft=row.status is RequestStatus.DRAFT)


@bp.post("/batches/<batch_id>/exceptions/exclude")
def add_exclusions(batch_id: str):
    s = svc()
    _, row = _request_row(batch_id)
    if not _draft_or_flash(row):
        return _back(batch_id, row)
    kind = AdjustmentKind.EXCLUDE_CUSTOMER if request.form.get("kind") == "customer" else AdjustmentKind.EXCLUDE_ACCOUNT
    try:
        result = s.adjustments.add_exclusions(AdjustmentScope.of(row), kind, parse_keys(request.form.get("keys", "")),
                                              request.form.get("reason"), current_user())
    except AdjustmentError as exc:
        flash(str(exc), "error")
        return _back(batch_id, row)
    if result.added:
        audit.record(s.engine, current_user(), "ADJUSTMENT_ADDED", tenant_id=row.tenant_id, target=batch_id,
                     kind=str(kind), keys=result.added[:200], count=len(result.added),
                     reason=request.form.get("reason") or None)
    what = "customer(s)" if kind is AdjustmentKind.EXCLUDE_CUSTOMER else "loan(s)"
    msg = f"{len(result.added)} {what} added to the exclusions."
    if result.already_present:
        msg += f" Already excluded: {', '.join(result.already_present[:10])}."
    flash(msg, "ok")
    return _back(batch_id, row)


@bp.post("/batches/<batch_id>/exceptions/override")
def add_override(batch_id: str):
    s = svc()
    _, row = _request_row(batch_id)
    if not _draft_or_flash(row):
        return _back(batch_id, row)
    try:
        field = OverrideField(request.form.get("field", ""))
    except ValueError:
        flash("Choose the field to override.", "error")
        return _back(batch_id, row)
    account = request.form.get("account_number", "")
    try:
        value = parse_override_value(field, request.form.get("value", ""))
        new_id, replaced = s.adjustments.add_override(AdjustmentScope.of(row), account, field, value,
                                                      request.form.get("reason"), current_user())
    except AdjustmentError as exc:
        flash(str(exc), "error")
        return _back(batch_id, row)
    audit.record(s.engine, current_user(), "ADJUSTMENT_ADDED", tenant_id=row.tenant_id, target=batch_id,
                 kind=str(AdjustmentKind.OVERRIDE), adjustment_id=new_id, account_number=account.strip(),
                 field=field.value, value=str(value), replaced=replaced, reason=request.form.get("reason") or None)
    flash(f"Override saved: {account.strip()} {field.label} = {value}"
          f"{' (replaces the previous one)' if replaced else ''}.", "ok")
    return _back(batch_id, row)


@bp.post("/batches/<batch_id>/exceptions/<int:adjustment_id>/remove")
def remove_adjustment(batch_id: str, adjustment_id: int):
    s = svc()
    _, row = _request_row(batch_id)
    try:
        adj = s.adjustments.get(adjustment_id)
    except AdjustmentNotFoundError:
        abort(404)
    if adj.batch_id != batch_id:
        abort(404)
    if not _draft_or_flash(row):
        return _back(batch_id, row)
    try:
        s.adjustments.remove(adjustment_id, current_user())
        audit.record(s.engine, current_user(), "ADJUSTMENT_REMOVED", tenant_id=adj.tenant_id, target=batch_id,
                     adjustment_id=adjustment_id, kind=str(adj.kind), key=adj.key,
                     field=adj.field.value if adj.field else None)
        flash(f"Removed: {adj.kind.value.replace('_', ' ').lower()} {adj.key}.", "ok")
    except AdjustmentError as exc:
        flash(str(exc), "error")
    return _back(batch_id, row)


@bp.post("/batches/<batch_id>/submit")
def submit_request(batch_id: str):
    """Step 3: lock the exceptions and queue the validation (DRAFT -> PENDING)."""
    s = svc()
    rows, _ = _request_row(batch_id)
    try:
        s.requests.submit_draft(batch_id)
    except InvalidTransitionError as exc:
        flash(str(exc), "error")
        return redirect(url_for("ui.batch", batch_id=batch_id))
    counts = {r.submission_type.value: len(s.adjustments.list(AdjustmentScope.of(r))) for r in rows}
    audit.record(s.engine, current_user(), "RUN_SUBMITTED", tenant_id=rows[0].tenant_id, target=batch_id,
                 adjustments=counts)
    flash("Submitted for validation. Excluded records are left out of the extract; validation runs on the rest. "
          "This page refreshes until it finishes.", "ok")
    return redirect(url_for("ui.batch", batch_id=batch_id))


@bp.post("/batches/<batch_id>/discard")
def discard_request(batch_id: str):
    """Cancel a draft request (its exceptions stay in the history)."""
    s = svc()
    rows, _ = _request_row(batch_id)
    drafts = [r for r in rows if r.status is RequestStatus.DRAFT]
    if not drafts:
        flash("Only a draft request can be discarded.", "error")
        return redirect(url_for("ui.batch", batch_id=batch_id))
    for r in drafts:
        s.requests.cancel(r.request_id)
    audit.record(s.engine, current_user(), "REQUEST_CANCELLED", tenant_id=rows[0].tenant_id, target=batch_id,
                 batch_id=batch_id, draft=True)
    flash(f"Draft request {batch_id} discarded.", "ok")
    return redirect(url_for("ui.exceptions"))


# ---------------------------------------------------------------- admin: rules & policies (per NBFC)
def _rule_kind(rule, custom_defs=None) -> str:
    if rule.source == "CUSTOM":
        return "Custom (form)" if custom_defs and rule.rule_id in custom_defs else "Custom SQL"
    if rule.rule_id.startswith(("UCRF.REQUIRED.", "UCRF.ENUM.")):
        return "Generated"
    return {"sql": "SQL", "python": "Python", "cascade": "Record decision"}[rule.kind]


def _rules_type(values) -> SubmissionType:
    try:
        sub_type = SubmissionType(values.get("type") or SubmissionType.CONSUMER.value)
    except ValueError:
        abort(400)
    if sub_type not in enabled_types():
        abort(400)
    return sub_type


@bp.get("/rules")
def rules_page():
    return _render_rules()


def _render_rules(custom_form: dict | None = None, preview: dict | None = None, status: int = 200):
    s = svc()
    tenant_id = selected_tenant()
    sub_type = _rules_type(request.values)
    custom_defs = custom_rules.definitions(s.engine, tenant_id, sub_type) if tenant_id else {}
    editing = request.args.get("edit")
    if custom_form is None and editing in custom_defs:            # Edit: the form pre-filled with the definition
        d = custom_defs[editing]
        custom_form = {**d.__dict__, "values": "\n".join(d.values), "when_values": "\n".join(d.when_values),
                       "field": f"{d.entity}.{d.field}", "editing": editing,
                       "other_field": f"{d.entity}.{d.other_field}" if d.other_field else "",
                       "when_field": f"{d.entity}.{d.when_field}" if d.when_field else ""}
    q = (request.args.get("q") or "").strip().lower()
    only_changed = bool(request.args.get("changed"))
    entries = all_entries = rules.catalogue(s.engine, tenant_id or "", sub_type)
    if q:
        entries = [e for e in entries if q in e.default.rule_id.lower() or q in e.default.message.lower()
                   or q in (e.default.field or "").lower()]
    if only_changed:
        entries = [e for e in entries if e.changed]
    definitions = rule_parameters.builtin_parameters(sub_type)
    overridden = rule_parameters.overrides(s.engine, tenant_id, sub_type) if tenant_id else {}
    values = {n: overridden[n]["value"] if n in overridden else p.default for n, p in definitions.items()}
    return render_template("rules.html", entries=entries, sub_type=sub_type, types=enabled_types(), q=q,
                           parameters=list(definitions.values()), overridden=overridden, values=values,
                           render=lambda m: rule_parameters.render_message(m, values),
                           only_changed=only_changed, outcomes=list(Outcome),
                           kind=lambda r: _rule_kind(r, custom_defs), custom_defs=custom_defs,
                           custom_entries=[e for e in all_entries if e.default.source == "CUSTOM"],
                           custom_form=custom_form or {}, preview=preview, checks=custom_rules.CHECKS,
                           operators=custom_rules.OPERATORS, field_options=custom_rules.fields(),
                           tenant=s.tenants.get(tenant_id) if tenant_id else None,
                           total=len(all_entries), changed_count=sum(e.changed for e in all_entries)), status


def _definition_from_form(form) -> custom_rules.Definition:
    entity, _, field_name = (form.get("field") or "").partition(".")
    other_entity, _, other = (form.get("other_field") or "").partition(".")
    when_entity, _, when = (form.get("when_field") or "").partition(".")
    if other and other_entity != entity:
        raise custom_rules.CustomRuleError("Compare: pick a field of the same record type")
    if when and when_entity != entity:
        raise custom_rules.CustomRuleError("Condition: pick a field of the same record type")
    return custom_rules.Definition(
        name=(form.get("name") or "").strip().upper(), entity=entity, field=field_name,
        check=form.get("check") or "", outcome=form.get("outcome") or "", message=(form.get("message") or "").strip(),
        values=parse_keys_lines(form.get("values")), min=(form.get("min") or "").strip() or None,
        max=(form.get("max") or "").strip() or None, pattern=(form.get("pattern") or "").strip() or None,
        operator=form.get("operator") or None, other_field=other or None, when_field=when or None,
        when_values=parse_keys_lines(form.get("when_values")))


def parse_keys_lines(raw: str | None) -> list[str]:
    """Values typed one per line or comma-separated (spaces inside a value are kept)."""
    return [v.strip() for v in re.split(r"[\n,]", raw or "") if v.strip()]


@bp.post("/rules/custom")
def save_custom_rule():
    """Preview (count on the latest validation; nothing saved) or save a form-built custom rule."""
    s = svc()
    tenant_id = selected_tenant()
    if not tenant_id:
        flash("Select an NBFC in the top bar: custom rules are per NBFC.", "error")
        return _rules_back()
    sub_type = _rules_type(request.form)
    form = request.form.to_dict()
    editing = form.get("editing") or None
    try:
        d = _definition_from_form(request.form)
        if editing and d.rule_id(tenant_id) != editing:
            raise custom_rules.CustomRuleError("The name of an existing rule cannot change; add a new rule instead")
        rule = custom_rules.build(tenant_id, d)
        if request.form.get("action") == "preview":
            snap = s.snapshots.latest(tenant_id, sub_type)
            if snap is None:
                return _render_rules(form, {"none": True})
            count, examples = custom_rules.preview(s.engine, rule, snap.snapshot_id,
                                                   rule_parameters.load_parameters(s.engine, tenant_id, sub_type))
            return _render_rules(form, {"count": count, "examples": examples, "snapshot": snap, "sql": rule.sql})
        reason = (request.form.get("reason") or "").strip()
        if not reason:
            raise custom_rules.CustomRuleError("Give a reason for the rule (it is kept in the audit trail)")
        custom_rules.save(s.engine, tenant_id, sub_type, d, current_user(), replace_existing=bool(editing))
    except custom_rules.CustomRuleError as exc:
        flash(str(exc), "error")
        return _render_rules(form, status=400)
    audit.record(s.engine, current_user(), "CUSTOM_RULE_CHANGED" if editing else "CUSTOM_RULE_ADDED",
                 tenant_id=tenant_id, target=rule.rule_id, reason=reason[:500], rule=d.describe(),
                 outcome=d.outcome, message=d.message)
    flash(f"{rule.rule_id} {'updated' if editing else 'added'}: {d.describe()}. "
          "Applies to requests validated from now on.", "ok")
    return redirect(url_for("ui.rules_page", q=rule.rule_id))


@bp.post("/rules/custom/<rule_id>/delete")
def delete_custom_rule(rule_id: str):
    s = svc()
    tenant_id = selected_tenant()
    if not tenant_id:
        abort(400)
    d = custom_rules.definitions(s.engine, tenant_id, _rules_type(request.form)).get(rule_id)
    if custom_rules.delete_rule(s.engine, tenant_id, rule_id):
        rules.clear_policy(s.engine, tenant_id, rule_id)
        audit.record(s.engine, current_user(), "CUSTOM_RULE_DELETED", tenant_id=tenant_id, target=rule_id,
                     rule=d.describe() if d else None, reason=(request.form.get("reason") or "").strip() or None)
        flash(f"{rule_id} deleted. Earlier findings keep its id.", "ok")
    else:
        abort(404)
    return redirect(url_for("ui.rules_page"))


def _rules_back():
    target = request.form.get("next", "")
    return redirect(target if target.startswith("/") and not target.startswith("//") else url_for("ui.rules_page"))


@bp.post("/rules/<rule_id>")
def save_rule_policy(rule_id: str):
    s = svc()
    tenant_id = selected_tenant()
    if not tenant_id:
        flash("Select an NBFC in the top bar: rule settings are per NBFC.", "error")
        return _rules_back()
    try:
        sub_type = SubmissionType(request.form.get("type") or SubmissionType.CONSUMER.value)
    except ValueError:
        abort(400)
    entry = next((e for e in rules.catalogue(s.engine, tenant_id, sub_type) if e.default.rule_id == rule_id), None)
    if entry is None:
        abort(404)
    enabled = request.form.get("enabled") == "1"
    try:
        outcome = Outcome(request.form.get("outcome") or entry.default.outcome.value)
    except ValueError:
        abort(400)
    reason = (request.form.get("reason") or "").strip()
    if (enabled, outcome) == (entry.effective.enabled, entry.effective.outcome):
        flash(f"{rule_id}: nothing changed.", "ok")
        return _rules_back()
    if not reason:
        flash(f"{rule_id}: give a reason for the change (it is kept in the audit trail).", "error")
        return _rules_back()
    if (enabled, outcome) == (entry.default.enabled, entry.default.outcome):
        rules.clear_policy(s.engine, tenant_id, rule_id)             # back to the default: no override row
    else:
        rules.set_policy(s.engine, tenant_id, rule_id,
                         is_enabled=None if enabled == entry.default.enabled else enabled,
                         outcome=None if outcome == entry.default.outcome else outcome)
    audit.record(s.engine, current_user(), "RULE_POLICY_CHANGED", tenant_id=tenant_id, target=rule_id,
                 reason=reason[:500], before={"enabled": entry.effective.enabled, "outcome": str(entry.effective.outcome)},
                 after={"enabled": enabled, "outcome": str(outcome)},
                 default={"enabled": entry.default.enabled, "outcome": str(entry.default.outcome)})
    flash(f"{rule_id}: {'on' if enabled else 'off'}, outcome {outcome.value} for {tenant_id}. "
          "Applies to requests validated from now on.", "ok")
    return _rules_back()


@bp.post("/rules/parameters/<name>")
def save_rule_parameter(name: str):
    s = svc()
    tenant_id = selected_tenant()
    if not tenant_id:
        flash("Select an NBFC in the top bar: parameters are per NBFC.", "error")
        return _rules_back()
    try:
        sub_type = SubmissionType(request.form.get("type") or SubmissionType.CONSUMER.value)
    except ValueError:
        abort(400)
    param = rule_parameters.builtin_parameters(sub_type).get(name)
    if param is None:
        abort(404)
    before = rule_parameters.load_parameters(s.engine, tenant_id, sub_type)[name]
    reason = (request.form.get("reason") or "").strip()
    try:
        value = param.parse(request.form.get("value"))
        if value == before:
            flash(f"{param.label}: nothing changed.", "ok")
            return _rules_back()
        if not reason:
            raise rule_parameters.ParameterError(f"{param.label}: give a reason for the change (kept in the audit trail)")
        rule_parameters.set_parameter(s.engine, tenant_id, sub_type, name, request.form.get("value"), current_user())
    except rule_parameters.ParameterError as exc:
        flash(str(exc), "error")
        return _rules_back()
    audit.record(s.engine, current_user(), "RULE_PARAMETER_CHANGED", tenant_id=tenant_id, target=name,
                 reason=reason[:500], before=param.text(before), after=param.text(value),
                 default=param.text(param.default), rules=list(param.used_by))
    flash(f"{param.label} = {param.text(value)} for {tenant_id}. Applies to requests validated from now on.", "ok")
    return _rules_back()


@bp.post("/rules/parameters/<name>/reset")
def reset_rule_parameter(name: str):
    s = svc()
    tenant_id = selected_tenant()
    if not tenant_id:
        abort(400)
    sub_type = SubmissionType(request.form.get("type") or SubmissionType.CONSUMER.value)
    param = rule_parameters.builtin_parameters(sub_type).get(name)
    if param is None:
        abort(404)
    before = rule_parameters.load_parameters(s.engine, tenant_id, sub_type)[name]
    if rule_parameters.clear_parameter(s.engine, tenant_id, sub_type, name):
        audit.record(s.engine, current_user(), "RULE_PARAMETER_RESET", tenant_id=tenant_id, target=name,
                     before=param.text(before), after=param.text(param.default))
        flash(f"{param.label}: back to the default ({param.text(param.default)}) for {tenant_id}.", "ok")
    return _rules_back()


@bp.post("/rules/<rule_id>/reset")
def reset_rule_policy(rule_id: str):
    s = svc()
    tenant_id = selected_tenant()
    if not tenant_id:
        abort(400)
    before = rules.policies(s.engine, tenant_id).get(rule_id)
    if rules.clear_policy(s.engine, tenant_id, rule_id):
        audit.record(s.engine, current_user(), "RULE_POLICY_RESET", tenant_id=tenant_id, target=rule_id,
                     before={"enabled": before["is_enabled"], "outcome": before["outcome"]},
                     reason=(request.form.get("reason") or "").strip()[:500] or None)
        flash(f"{rule_id}: back to the default for {tenant_id}.", "ok")
    return _rules_back()


@bp.get("/audit")
def audit_log():
    s = svc()
    flt = {k: request.args.get(k) or "" for k in ("action", "actor", "target")}
    actions, actors = audit.choices(s.engine, selected_tenant())
    return render_template("audit.html", events=audit.recent(s.engine, 200, selected_tenant(), flt["action"] or None,
                                                             flt["actor"] or None, flt["target"] or None),
                           flt=flt, actions=actions, actors=actors)


# ---------------------------------------------------------------- findings -> exceptions of a new draft request
def _draft_for(snap) -> str:
    """The open draft request for the snapshot's NBFC, date and type; created if there is none."""
    s = svc()
    draft = s.requests.open_draft(snap.tenant_id, snap.as_of_date, snap.submission_type)
    if draft:
        return draft
    source = s.requests.list_batch(snap.batch_id) if snap.batch_id else []
    cycle = source[0].reporting_cycle if source else None
    draft = s.requests.create_batch(snap.tenant_id, snap.as_of_date, RunMode.VALIDATE, [snap.submission_type], (),
                                    cycle, current_user(), draft=True)
    audit.record(s.engine, current_user(), "RUN_CREATED", tenant_id=snap.tenant_id, target=draft,
                 as_of=snap.as_of_date, mode=str(RunMode.VALIDATE), types=[snap.submission_type.value],
                 status="DRAFT", source=f"findings of snapshot {snap.snapshot_id}")
    return draft


def _finding_account(snapshot_id: int):
    """(snapshot, account_number) for the account_key posted from the Findings page."""
    s = svc()
    try:
        snap = s.snapshots.get(snapshot_id)
    except SnapshotNotFoundError:
        abort(404)
    account = ENTITY_TABLES["account"]
    with s.engine.connect() as conn:
        number = conn.execute(select(account.c.account_number).where(
            account.c.snapshot_id == snapshot_id, account.c.account_key == request.form.get("account_key", ""))
        ).scalar()
    if not number:
        abort(404, "That loan is not in this snapshot (it may have been purged).")
    return snap, number


@bp.post("/snapshots/<int:snapshot_id>/exclude")
def exclude_from_findings(snapshot_id: int):
    s = svc()
    snap, number = _finding_account(snapshot_id)
    back = request.form.get("next", "")
    back = back if back.startswith("/") and not back.startswith("//") else url_for("ui.findings", snapshot_id=snapshot_id)
    try:
        draft = _draft_for(snap)
    except (RequestValidationError, DuplicateActiveRequestError) as exc:
        flash(f"Cannot open a new request for this date: {exc}", "error")
        return redirect(back)
    (row,) = [r for r in s.requests.list_batch(draft) if r.submission_type is snap.submission_type]
    result = s.adjustments.add_exclusions(AdjustmentScope.of(row), AdjustmentKind.EXCLUDE_ACCOUNT, [number],
                                          f"From findings of snapshot #{snapshot_id}", current_user())
    if result.added:
        audit.record(s.engine, current_user(), "ADJUSTMENT_ADDED", tenant_id=snap.tenant_id, target=draft,
                     kind=str(AdjustmentKind.EXCLUDE_ACCOUNT), keys=result.added, count=1,
                     reason=f"From findings of snapshot #{snapshot_id}")
    flash(f"Loan {number} {'added to' if result.added else 'is already in'} the exclusions of draft request "
          f"{draft}. Submit that request to validate without it.", "ok")
    return redirect(back)


@bp.post("/snapshots/<int:snapshot_id>/override")
def override_from_findings(snapshot_id: int):
    snap, number = _finding_account(snapshot_id)
    try:
        draft = _draft_for(snap)
    except (RequestValidationError, DuplicateActiveRequestError) as exc:
        flash(f"Cannot open a new request for this date: {exc}", "error")
        return redirect(url_for("ui.findings", snapshot_id=snapshot_id))
    return redirect(url_for("ui.request_exceptions", batch_id=draft, type=snap.submission_type.value,
                            account=number, field=request.form.get("field") or None))


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
                           page=page, pages=max(1, math.ceil(total / PAGE_SIZE)), outcomes=results.OUTCOME_ORDER,
                           overridable={f.value for f in OverrideField})


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
