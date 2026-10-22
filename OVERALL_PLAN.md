# Bureau Submission Platform: Overall Plan

> The platform: tenants, requests, worker, UI, plus the **decisions log** and the **single to-do list**.
> The data pipeline (canonical model, adapters, validation, formatter) is in [DESIGN_PLAN.md](DESIGN_PLAN.md).
> Original brief: [newdesign.md](newdesign.md). Consumer spec: [UCRF_Consumer_v3.74_Spec.md](UCRF_Consumer_v3.74_Spec.md).

---

## Status (22-10-2026)

**Built and tested:** 396 tests passed, 1 skipped (it needs an empty scratch MySQL); the new SQL was also checked on RDS MySQL.
- **Live on EC2** (`i-080783c59045ca2ca`, Docker; decision 19), with S3 files and RDS MySQL.
- **Request flow:** request (draft) → exceptions → validation → files (§5.1). Exclusions and overrides are joined into the extract in SQL, with an audit copy per snapshot (§4).
- **Validation:** 105 UCRF consumer rules. Per NBFC: on/off and outcome, rule parameters, custom rules without SQL (Rules & policies).
- **UI:** dashboard with request-stage KPIs, tracker, Files, Findings actions, Audit. Every change is audited.
- **Access:** https://52-66-49-134.sslip.io with in-app login (J.5, J.7).
- **Migrations applied on RDS:** 001–010.

**Open:**
- Users to be added on the server; later a company subdomain instead of sslip.io.
- H.6 encoding review against a CIC sample file.
- A.2 / A.4 real LMS schema and bureau member IDs.
- K.7b NBFC & bureau page, K.9 trends, K.10 / K.11 Commercial and NeSL.

---

## 1. Scope

1. A **Flask UI** where the internal team picks:
   - an **NBFC** (tenant)
   - an **as-of date**
   - **one or more submission types**: Consumer now; Commercial and NeSL later
   - **one or more bureaus**: Experian, CIBIL, CRIF High Mark, Equifax
   - then optional **exceptions** (exclusions, overrides), **validation**, and **files** per bureau (§5.1 request flow)
2. Each request is stored as rows in one request table and processed by an **independent worker**.
3. **Validation results are stored in tables** and tracked across runs. **Files are downloaded** from the batch and Files pages.
4. **Context:** one LMS provider on AWS, ~50 NBFCs, one MySQL engine, **one schema per NBFC** with possible column differences.

## 2. Decisions log

| # | Topic | Decision |
|---|---|---|
| 1 | Bureau differences | Only member ID, header segment tag and file name. All other content is identical. |
| 2 | Multiple bureaus / types | Allowed in one click. One row per unit of work in a single `submission_request` table, grouped by `batch_id`. |
| 3 | Access | Internal team only. Login deferred behind a `current_user()` hook. |
| 4 | Delivery | Download only. |
| 5 | NeSL / Commercial | Own specs. Built later as separate pipeline plugins. |
| 6 | UI vs orchestration | Independent. Flask writes and reads rows; a separate polling worker runs the jobs. |
| 7 | Stack | Python 3.14, Flask, SQLAlchemy Core (no ORM) + PyMySQL, plain SQL migrations (no Alembic yet), pytest. |
| 8 | Database | **MySQL** for the source (LMS) and the app. |
| 9 | Data model | A **core canonical model** (single YAML definition) that every later stage uses. |
| 10 | Onboarding | **Per-NBFC mapping file** (base LMS mapping + overrides + lookups), compiled to MySQL. |
| 11 | Snapshot | Each run extracts to **canonical snapshot tables**. Validation and formatting read the same frozen data. |
| 12 | Validation | Separate stage, runnable alone. **SQL-first rules** over canonical tables, Python only for algorithmic rules. Findings go to tables. Custom per-NBFC rules and policy overrides. |
| 13 | Tenancy | Isolation by **schema** at extract. Everything after extract carries `tenant_id` / `snapshot_id`. |
| 14 | UI layout | Sidebar navigation + top bar (breadcrumb, NBFC selector, New request), modelled on the reference "BureauPulse" mock. Still server-rendered Flask with no JS framework or CDN. |
| 15 | Adjustments (exclusions + overrides) | Users capture **loan account numbers and/or customer IDs to exclude**, and **overrides** for OD days, ledger balance, OD amount and settlement amount, each with an optional reason. They are **joined into the extract in SQL** (exclusions as `NOT EXISTS`, overrides as `COALESCE(override, source value)`), so the snapshot holds the final data and validation checks it. No row edits in Python. Excluding a customer excludes every loan they hold, whole. Applied adjustments are frozen on the snapshot. Clicking Generate confirms and audits them. |
| 18 | File storage | Generated files and exception CSVs are written to **AWS S3** (`s3://<bucket>/<prefix>/<tenant>/<batch>/<file>`). The URI is stored on `submission_request`. UI downloads stream from S3 through the app (private bucket). Writes are conditional, so nothing is overwritten. Encryption is SSE-S3 or SSE-KMS. Credentials come from the AWS chain (IAM role). The local store is kept for development and tests. |
| 20 | Request flow (22-10-2026) | **Request → Exceptions → Validation → Files.** The New request form creates a **DRAFT** request. Its exclusions and overrides are captured on its Exceptions step and **tagged with its request (batch) ID**. "Submit for validation" locks them and queues the run. The extract leaves the excluded records out (SQL join), so validation runs on the remaining records only. Generate needs a confirmation of the counts. More exceptions after submitting means a **new request**: there is no reopening and no carry-over. Scheduled runs skip the Exceptions step. |
| 17 | Validate first, always | No direct GENERATE runs. The New request form and scheduler create **Validate** runs only. Files come only from "Generate files from this validation". |
| 16 | Deferred | J.5 login, J.7 user access to the EC2 app, H.6 encoding review, A.2/A.4 real bureau IDs and LMS schema: later. |
| 19 | Hosting | **One EC2 (Amazon Linux 2023), Docker Compose** (web + worker from one image; Python and libraries pinned in the image), host cron for scheduled tasks, releases shipped through S3 (`deploy/package.ps1` → `deploy/update.sh`). Sized for about 5 internal users: no load balancer or scaling. Live on `i-080783c59045ca2ca` since 22-10-2026, verified end to end (validate, generate to S3, download). |

## 3. Architecture

```
 ┌──────────────┐ insert ┌──────────────────────┐ claim  ┌──────────────────────────────────────────────┐
 │ Flask Web    │──────▶ │ submission_request   │◀─────▶ │ Worker                                       │
 │ form, batches│ read   │ 1 row = 1 unit of    │ update │  VALIDATE row: snapshot → validation         │
 │ findings,    │◀────── │ work, by batch_id    │        │  GENERATE row: snapshot → validation →       │
 │ downloads    │        └──────────────────────┘        │                format (this bureau)          │
 └──────┬───────┘                                       └───────────────┬──────────────────────────────┘
        │ reads findings / summaries                                    │ uses (DESIGN_PLAN.md)
        ▼                                                               ▼
 validation_finding / validation_summary ◀──── adapters → c_* snapshot → validation → formatter
```

| Module | Responsibility | Depends on |
|---|---|---|
| `web` | Forms, batch list/page, findings view, downloads | `requests`, `tenants`, `bureaus`, `validation` (read-only) |
| `requests` | `submission_request` repository, state machine, atomic claim | DB |
| `tenants` | Tenant registry (schema, mapping, active) | DB |
| `bureaus` | Bureau profile per tenant × bureau × type | DB |
| `worker` | Claims rows, runs the pipeline stages, records results | `requests`, pipeline modules |
| pipeline: `canonical`, `adapters`, `snapshot`, `validation`, `formats` | See DESIGN_PLAN.md | each other only through the canonical model |

## 4. Platform data model

**`tenant`**: `tenant_id` (e.g. `nbfc_017`), `display_name`, `source_schema`, `mapping_path`, `extract_strategy` (insert_select / stream), `is_active`.

**`submission_request`**: one row = one unit of work.

| Column | Notes |
|---|---|
| `request_id`, `batch_id` | PK; rows from one click share `batch_id` |
| `tenant_id` | NBFC |
| `run_mode` | `VALIDATE` or `GENERATE` |
| `as_of_date`, `submission_type`, `reporting_cycle` | |
| `bureau_code` | Required for GENERATE; **NULL for VALIDATE** (validation is bureau-independent) |
| `snapshot_id` | The snapshot used. A GENERATE can **reuse a validated snapshot** ("generate files from this validation") |
| `status`, `active_key`, `worker_id`, `attempt_count`, `started_at`, `finished_at` | As built in Phase B |
| `output_file_name`, `output_file_path`, counts, `error_message` | Results |

Rows per click:
- **Validate:** one per type.
- **Generate:** one per type × bureau.

Duplicate guard: one active row per `(tenant, date, type, run_mode, bureau)`.

**`bureau_profile`**: keyed by **tenant × bureau × submission type** (member IDs are per NBFC).

Pipeline tables (`extract_snapshot`, `c_*`, `validation_rule`, `validation_policy`, `validation_finding`, `validation_summary`, `record_outcome`) are specified in DESIGN_PLAN.md. Operations tables: `worker_heartbeat`, `audit_event` (migration 005).

**Adjustments: exclusions and overrides (K.4, migration 006, built)**

| Object | Kind | Purpose |
|---|---|---|
| `submission_adjustment` | table | What users capture, per **tenant + as-of date + submission type** (not per snapshot, so it carries over to the next validation). Columns: `adjustment_id`, `tenant_id`, `as_of_date`, `submission_type`, `kind`, `account_number` / `customer_id`, `field`, `override_value`, `reason` (optional, ≤ 500), `created_by/at`, `removed_by/at` (soft delete). |
| `snapshot_adjustment` | table | **Frozen copy** of the adjustments in force when a snapshot was extracted (kind, keys, value, reason, who, when). The audit trail: later edits or removals don't change it. |
| `snapshot_adjustment_effect` | table | What each frozen adjustment hit in the source: `EXCLUDE` per loan (customer exclusions expand to **every loan the customer holds**), `EXCLUDE_PARTY` per borrower left with no loan, `OVERRIDE` per loan field with **old (source) → new** value. |
| `v_snapshot_adjustment` | **view** | Each frozen adjustment with `loans_affected` (0 = not found). Used by the UI and rule `UCRF.ADJ.NOT_FOUND`. |

- **`kind` values:** `EXCLUDE_ACCOUNT` (by `account_number`), `EXCLUDE_CUSTOMER` (by `customer_id`), and `OVERRIDE`.
- **`field` (OVERRIDE only):** `days_past_due` (OD days), `current_balance` (ledger balance), `amount_overdue` (OD amount), `settlement_amount`.

**In the extract** ([submissions/adjustments/sql.py](submissions/adjustments/sql.py)): set-based SQL, no Python row edits.
1. **Freeze:** `INSERT INTO snapshot_adjustment SELECT … FROM submission_adjustment` (active, in scope).
2. **Resolve:** `INSERT INTO snapshot_adjustment_effect SELECT …`, joining the frozen rows to the tenant's own account and account-holder SELECTs.
3. **Extract each entity** with its mapping SELECT wrapped in a join to the effects:
   - excluded loans and borrowers are filtered with `NOT EXISTS`;
   - overridden fields come from `COALESCE(override, source value)`.

   The `c_*` tables hold the final data; validation and the formatter are unchanged.

**Requirements and results:**
- **Same server:** this needs the LMS schema on the app's MySQL server, as on RDS. Without any adjustments, a separate LMS server still works. With adjustments, the extract fails with a clear message.
- **Not found:** adjustments that hit nothing become the WARN finding `UCRF.ADJ.NOT_FOUND`.
- **Counts:** `submission_request` gains `excluded_accounts` and `overridden_accounts`.
- **Retention:** purges the frozen copy and effects with the snapshot's rows.

## 5. Run lifecycle

1. The user submits: tenant, date, types, run mode, and bureaus (Generate only). The web validates and inserts the rows in one transaction.
2. The worker claims a row.
3. **Snapshot stage:** get or create the snapshot for `(batch_id, tenant, type)`, or use the referenced `snapshot_id`.
   - If another worker is building it, release the row and retry later.
   - Building it means extract (adapter) → validate (rules → findings → cascade).
4. **VALIDATE row:** done. Record counts from `validation_summary`.
5. **GENERATE row:** if the snapshot has a REJECT_FILE outcome, fail with that reason. Otherwise the formatter writes this bureau's file from accepted records, and the row records the file path and counts.
6. The **batch page** shows each row's status, and for each snapshot the findings summary (by rule/outcome, drill-down, CSV export) and the Download buttons.

### 5.1 Request flow (decision 20; requirement, built 22-10-2026)

**Request → Exceptions → Validation → Files.** One request ID (`batch_id`) ties the steps together.

| Step | Requirement |
|---|---|
| **1. Request** | The user picks NBFC, as-of date, submission type(s) and cycle, then clicks **Create request**. The request is created as **DRAFT**: the worker does not pick it up. The Exceptions step opens straight away. |
| **2. Exceptions** | Optional. The user excludes **loan account numbers** and/or **customer IDs** (pasted in bulk; a customer exclusion takes all their loans, whole) and **overrides** OD days, ledger balance, OD amount or settlement amount per loan, each with an optional reason. Every exception is **tagged with the request ID**. Remove is a soft delete. Audit: `ADJUSTMENT_ADDED` / `ADJUSTMENT_REMOVED`. |
| **3. Validation** | **Submit for validation** locks the exceptions (DRAFT → PENDING, audit `RUN_SUBMITTED`). The extract **leaves the excluded records out** and applies the overrides in SQL (§4), so **validation runs on the remaining records only**. Each exception then shows *Applied (n loans)* or *Not found* (WARN `UCRF.ADJ.NOT_FOUND`), with old → new values for overrides. |
| **4. Files** | From the validated request: the user confirms "N loans excluded, M loans overridden" (required when there are any; audit `ADJUSTMENTS_CONFIRMED`), picks bureaus, and one file per bureau is generated from the validated snapshot. Each file's exceptions CSV ends with the applied adjustments. |

**Rules:**
- **Locked after submit:** exceptions cannot change once a request is submitted. More exceptions means a **new request** for the same date; there is no reopening and no carry-over.
- **One open request:** a draft, pending or running request blocks a second one for the same NBFC, date and type until it is submitted, finishes or is **discarded** (draft → CANCELLED).
- **Scheduled runs** (`ops.schedule`) skip step 2 and are created PENDING.
- **Audit trail:** each snapshot keeps a frozen copy of the request's exceptions and their effects (`snapshot_adjustment`, `snapshot_adjustment_effect`). Retention purges them with the snapshot rows; the `audit_event` entries stay.

**Request stage** (derived, used by the dashboard and tracker). A request is the validation batch plus the file generations made from its snapshot:

| Stage | Rule |
|---|---|
| Awaiting exceptions | Validation rows are DRAFT. |
| In progress | Validation rows are PENDING or RUNNING, or a file generation from it is PENDING or RUNNING. |
| Validated, files pending | Validation SUCCEEDED, and no file has been generated from it yet. |
| Completed | At least one file was generated successfully from its snapshot. |
| Failed | Validation FAILED, or every file generation from it FAILED. |
| Cancelled | Discarded draft or cancelled request. |

## 6. UI (target design, K.1–K.6)

Modelled on the reference mock ("BureauPulse"). Server-rendered Flask, plain CSS and inline SVG icons, no CDN.

### 6.1 Layout

- **Left sidebar.** Brand at the top, then three groups:
  - **MAIN:** Dashboard · New request · Exceptions
  - **REPORTS:** Submission tracker · Files · Findings · Audit
  - **ADMIN** (later): NBFCs & bureaus · Rules & policies
- **Sidebar footer:** a worker status dot ("Worker online" or "No worker running", from the heartbeat) and the current user.
- **Top bar:** breadcrumb (e.g. Bureau reporting / Dashboard), a **global NBFC selector** (All, or one NBFC, kept in the session and applied to every page), and a **+ New request** button.
- **Page header:** title, subtitle, and page actions (e.g. "Review exceptions").
- **Small screens:** the sidebar collapses to icons, and tables scroll horizontally.

### 6.2 Pages

| Page | Content |
|---|---|
| **Dashboard** (K.2) | See §6.3. |
| **New request** | NBFC, as-of date, types, cycle → **Create request** (DRAFT) → opens the request's Exceptions step (§5.1). |
| **Exceptions** | **Menu page:** the requests awaiting exceptions (drafts) for the selected NBFC. **Request step** (`/batches/<id>/exceptions`): a 4-step indicator; Exclude (loan numbers or customer IDs, bulk paste, reason) and Override (loan, field, value, reason) forms while DRAFT; the request's exceptions with status (*Will apply / Applied (n loans) / Not found / Removed*) and old → new values; **Submit for validation** and **Discard request**. Read-only once submitted. |
| **Submission tracker** | Today's batch list with more filters (stage, status, mode, bureau, NBFC, date range) and paging. Reached pre-filtered from the dashboard KPI cards. |
| **Batch page** | Rows and statuses; a "Step 2 of 4" panel while DRAFT (Exceptions, Submit); per snapshot the adjustments summary (loans excluded, overridden, not found); the generate form with the adjustments confirmation. |
| **Files** | Generated files in S3 (location shown): NBFC, bureau, file name, as-of, records written, loans excluded, overrides, rejected, size, created, Download file / exceptions. Purged files are shown as purged. |
| **Findings** | Today's page, with **"Exclude loan"** and **"Override…"** actions on records. Because a validated request is locked, they add the exception to a **new draft request** for the same NBFC, date and type (created on first use, or the open draft if one exists). |
| **Audit** | Today's page, adding filters for action and user, and adjustment events with their reasons and old → new values. |
| **Later (K.7):** NBFCs & bureaus (read-only tenant and bureau profile view), Rules & policies (rule catalogue per NBFC, policy overrides editable with audit). |

### 6.3 Dashboard (K.2 requirement, 22-10-2026)

Answers "where does every request stand, and what needs attention?" It counts **requests** (§5.1 request stage), not raw rows.

- **Filters:** the global NBFC selector (All or one NBFC) and a **period**: *This month* (default), *Last 30 days*, *Last 90 days*, *All time*. A request belongs to the period by its creation date.
- **Request KPI cards** (one row, each clickable to the tracker filtered by that stage):

  | Card | Value | Sub-text |
  |---|---|---|
  | Requests raised | all requests in the period | of which scheduled |
  | Awaiting exceptions | stage *Awaiting exceptions* | oldest draft's age |
  | In progress | stage *In progress* | oldest one's age (flags stuck work) |
  | Validated, files pending | stage *Validated, files pending* | oldest one's age |
  | Completed | stage *Completed* | completion rate = completed ÷ raised (excluding cancelled) |
  | Failed | stage *Failed* | needs a retry or a new request |
  | Cancelled | stage *Cancelled* | |

- **Data KPI cards** (second row, same filters): records validated, acceptance rate (accepted ÷ validated), loans excluded, loans overridden, files generated, records submitted to bureaus.
- **Needs attention** panel: failed requests; drafts older than 2 days; validated requests with no files after 2 days; in-progress work older than 2 hours; "No worker running". Each line links to its request.
- **NBFC status** table (when *All NBFCs* is selected): one row per active NBFC with its latest request (as-of, stage, records, files by bureau, last activity), so missing or late month-end submissions stand out.
- **Bureaus** panel: files and records per bureau in the period, with the last file date, as bar meters.
- **Recent requests:** the last 10 requests with NBFC, as-of, type, stage, records, excluded, requested by and when; linked to the request.
- **Later:** highest-risk records (rejected or excluded loans with the highest DPD / overdue).

## 7. Code layout

```
submissions/
  web/ requests/ tenants/ bureaus/ worker/           # platform
  canonical/ adapters/ snapshot/ validation/ formats/ # pipeline (DESIGN_PLAN.md)
  config.py db.py enums.py migrate.py
canonical/model.yaml
migrations/NNN_*.sql
clients/_base/  clients/<tenant>/
mock_data/      # mock LMS source; will become tenant schemas nbfc_001 / nbfc_002
tests/
```

## 8. Non-functional

- **Tenant safety:**
  - extract reads only the tenant's schema;
  - member IDs come only from that tenant's bureau profile;
  - tests assert every request/snapshot query is tenant-scoped.
- **Source load:** extract from a read replica where possible. Extract is the only read of the LMS.
- **Scale:** ~50 tenants × 4 bureaus × weekly/month-end cycles. Run multiple workers (claiming is atomic), and add scheduled batch creation later.
- **PII:** internal network only; snapshot/file retention with a purge job; restricted download routes.
- **Observability:** logs keyed by `request_id` / `snapshot_id`. The batch page is the user-facing log.

## 9. Open questions

1. **Base LMS schema** + one NBFC customisation sample (for the base mapping and realistic mock data).
2. **DB access:**
   - Can our app schema live on the same MySQL cluster (`insert_select`)?
   - Is there a read replica?
   - Where does our app run (same AWS account/VPC)?
3. **Bureau values per tenant:** member IDs, header tags, file-name formats.
4. **Re-runs:** keep every version of a generated file, or only the latest?
5. **Retention period** for snapshots and files.
6. **CIC sample file** to re-confirm the encoding decision (no segment-tag length prefix, no record separator). See H.6.
7. **TH history** load needed?

---

## 10. To-Do List (single list for the whole project)

### Phase A: Inputs
- [x] A.1 Core design decisions (§2)
- [ ] A.2 Base LMS schema + one customisation sample
- [ ] A.3 DB access model (same cluster vs replica). Hosting: done, see decision 19
- [ ] A.4 Bureau values per tenant; CIC sample file
- [ ] A.5 Re-run, retention and TH decisions

### Phase B: Foundations
- [x] B.1 Project scaffold (package, config, logging, pytest)
- [x] B.2 Plain SQL migrations + runner; `submission_request`, `bureau_profile`
- [x] B.3 `requests` module: batch create, atomic claim, state machine, derived batch status
- [x] B.4 `bureaus` module: profile repository, file-name rendering, placeholder seed
- [x] B.5 Migration 002: `tenant` table; `tenant_id`, `run_mode`, `snapshot_id`, nullable `bureau_code` on requests; tenant key on `bureau_profile`; update repositories and tests
- [x] B.6 `tenants` module + seed (`nbfc_001`, `nbfc_002` mock tenants)

### Phase C: Canonical model
- [x] C.1 `submissions/canonical/model.yaml` v1.0 (9 entities, 17 code lists from spec Appendices)
- [x] C.2 Tables, reference data and record types built from the model at runtime; MySQL migration generator with `--check`
- [x] C.3 Migration 003 generated (`extract_snapshot`, `ref_code`, `ref_state_pin`, `c_*`); test fails if it drifts from the model

### Phase D: Adapter
- [x] D.1 Mock LMS: two tenant schemas (`nbfc_001` base, `nbfc_002` renamed columns / long codes / `loan_ext`), `mock_data/mock_lms.sql`
- [x] D.2 Mapping file format + loader (extends + deep merge, named UNION ALL selects, validation incl. YAML boolean trap)
- [x] D.3 Compiler: mapping → MySQL SELECTs; `generated/*.sql` committed per tenant; mapping version hash
- [x] D.4 Lookups compiled inline as `CASE … ELSE <raw value>` (no `map_lookup` table); unmapped values pass through for validation to report
- [x] D.5 Extract runner (`stream` and `insert_select`) + snapshot repository (create/build lock, extracted, failed + cleanup)
- [x] D.6 `clients/_base/mapping.yaml`; `nbfc_001` (no overrides) and `nbfc_002` (overrides only)
- [x] D.7 `check` command: compile on source, row counts, unmapped code values per lookup

### Phase E: Validation
- [x] E.1 Migration 004: `validation_rule` (tenant custom rules), `validation_policy`, `validation_finding`, `validation_summary`, `record_outcome`
- [x] E.2 SQL rule runner (INSERT…SELECT findings per snapshot; every SQL rule must use `:snapshot_id`)
- [x] E.3 Python rules + pure checks: names, ID formats/Verhoeff, phones, e-mail, PIN/state
- [x] E.4 UCRF-3.74 pack (`submissions/validation/packs/`): 104 rules (45 generated REQUIRED/ENUM, 46 hand-written SQL, 10 Python, 3 cascade), unit tests per check
- [x] E.5 Cascade → `record_outcome` (address, phone-or-ID, single-word name); tenant policy (enable/disable, outcome override); custom SQL rules
- [x] E.6 Mock oracle end to end for both tenants: all 52 scenarios × holders match expected outcomes
- [ ] E.7 Per-rule timing and a findings export (CSV) — with the UI (G.4)

### Phase F: Worker
- [x] F.1 Pipeline contract (`build_snapshot`, `generate_file`) + registry by submission type (`submissions/pipeline`)
- [x] F.2 Polling worker (`python -m submissions.worker`): claim row, reuse/build the batch snapshot (busy → release and retry), record result; `run_once()` for tests
- [x] F.3 VALIDATE runs end to end on both mock tenants; GENERATE rows share one snapshot and stop at file generation (phase H)

### Phase G: Web
- [x] G.1 Flask app factory, layout, session CSRF, `current_user()` hook (X-Forwarded-User or placeholder)
- [x] G.2 New run form (tenant, date, types, mode, bureaus, cycle) + validation (active tenant, enabled types, bureau profiles, duplicates)
- [x] G.3 Batch list (tenant filter) and batch page (rows, statuses, errors, retry/cancel, auto-refresh while active)
- [x] G.4 Findings: summary per rule, filters, paging, CSV export (streamed)
- [x] G.5 "Generate files from this validation" (reuses the snapshot) + downloads restricted to the output directory

### Phase H: Formatter (UCRF consumer)
- [x] H.1 TLV + fixed-width encoder; printable ASCII; whole-rupee amounts with trailing sign; DDMMYYYY dates
- [x] H.2 Spec reshaping: 26-char name fields and 40-char address lines without splitting words, DPD cap 900, ROI 3 dp, segment caps and sequential tags
- [x] H.3 Record builder from snapshot + record_outcome + field/segment drops from findings; HD (146 bytes) / ES / TRLR; bureau stamping; file + exceptions CSV per bureau under `output/<tenant>/<batch>/`
- [x] H.4 Byte-identical duplicate records isolated and counted; counts back to the request
- [x] H.5 Structure tests via a parser of the generated file; GENERATE end to end for 3 bureaus; bureau files differ only in header tag + member ID
- [ ] H.6 **Open for review**: decided 21-10-2026 segment tags without length prefix (`PNN01`) and no separator / line break between records. Re-confirm against a real CIC sample file; move `FormatOptions` onto `bureau_profile` if bureaus differ

### Phase I: Onboarding tools
- [x] I.1 `introspect`: schema diff vs reference + base-mapping columns the schema lacks (= overrides needed)
- [x] I.2 `dry_run`: extract + validate without a request; rows, empty-column rates, untranslated codes, records, top findings; trial snapshot removed unless `--keep`
- [x] I.3 `new_tenant` (tenant row + mapping from `clients/_template/`, optional placeholder bureaus) + [ONBOARDING.md](ONBOARDING.md)

### Phase J: Hardening
- [x] J.1 Recovery on every worker poll: stuck RUNNING rows released / failed after max attempts; abandoned BUILDING snapshots failed and cleaned; a retried row rebuilds a FAILED batch snapshot
- [x] J.2 Retention (`python -m submissions.ops.retention --days N [--dry-run]`): canonical rows, findings, record outcomes and files removed; summary kept; snapshot PURGED
- [x] J.3 Scheduled runs (`python -m submissions.ops.schedule --cycle ME|W1|W2|W3|DL [--mode GENERATE]`) for all active tenants, default as-of per cycle; triggered by Task Scheduler / cron
- [x] J.4 Audit trail (`audit_event`, UI Audit page) for UI actions, downloads, scheduler, recovery, purge; [RUNBOOK.md](RUNBOOK.md)
- [x] J.5 **In-app login** (22-10-2026, migration 010 `app_user`): username + password (salted hashes), 8-hour sessions, lockout after 5 wrong passwords, change password, user CLI `python -m submissions.users`, audited; `SUBMISSIONS_AUTH=login` on the server, `none` for local development
- [x] J.6 Worker heartbeat (`worker_heartbeat`) + UI banner "No worker running" when the last poll is older than 30 s
- [x] J.8 EC2 deployment (decision 19): Dockerfile, compose, `deploy/` scripts, IAM (including the deny exceptions on the shared instance role), security groups EC2 ↔ RDS; RUNBOOK "Deploying on EC2"
- [x] J.7 **User access (Option C, 22-10-2026):** https://52-66-49-134.sslip.io. Caddy on the EC2 (Let's Encrypt), Elastic IP 52.66.49.134, inbound 80/443; app port 8000 only on the server. Later: a company subdomain (DNS A record + SITE_ADDRESS), or Option A (load balancer + company sign-in)
- [x] K.1 **Layout:**
  - sidebar navigation (MAIN / REPORTS / ADMIN), top bar with breadcrumb, global NBFC selector (session) and "+ New request";
  - worker status and user in the sidebar footer; restyled components (cards, tables, badges, meters);
  - existing pages moved into the new shell.
- [x] K.2 **Dashboard** (§6.3; stages in `submissions/requests/stages.py`, tracker stage + period filter from K.3 built with it): request-stage KPI cards (raised, awaiting exceptions, in progress, validated-files pending, completed, failed, cancelled) with period filter and click-through; data KPIs; Needs attention; NBFC status; bureaus; recent requests. NBFC-filtered.
- [x] K.3 **Submission tracker:** filters (status, mode, bureau, requested from/to), paging (50), and the request-by-stage view. **Files** page (bureau and period filters, excluded/overridden counts, purged shown).
- [x] K.0 **Validate only:**
  - remove direct GENERATE from the New request form and the scheduler (`--mode`);
  - `create_batch(GENERATE)` requires a validated `snapshot_id`; update tests and RUNBOOK.
- [x] K.4 **Migration 006:**
  - `submission_adjustment`, frozen `snapshot_adjustment`, `snapshot_adjustment_effect`, view `v_snapshot_adjustment`;
  - `submission_request.excluded_accounts / overridden_accounts`;
  - adjustment repository (add / remove / list, bulk keys, override replaces the previous one for the same loan and field);
  - **joined into the extract SQL** (freeze → resolve → wrapped entity SELECTs) and the `UCRF.ADJ.NOT_FOUND` warning;
  - tests: joint loan, customer with two loans, DPD/overdue overrides changing validation, audit copy, the same-server requirement.
- [x] K.5 **Exceptions step of a request** (decision 20, migration 007: `submission_adjustment.batch_id`, request status DRAFT). As first built, then reworked to the request flow:
  - capture exclusions (bulk paste or pick from records) and overrides (current value shown), with optional reasons; list with status and remove;
  - "Re-validate with adjustments";
  - Generate blocked while re-validation is pending, with a confirmation showing counts;
  - exceptions CSV sections for excluded loans and overrides;
  - audit `ADJUSTMENT_ADDED` / `ADJUSTMENT_REMOVED` / `ADJUSTMENTS_CONFIRMED`.
- [x] K.6 **"Exclude loan" / "Override…" actions** on the Findings page (into the open draft request for the same NBFC, date and type, created on first use; Override opens it pre-filled); adjustments summary on the batch page and Files page; audit filters.
- [x] K.7a **Rules & policies page** (22-10-2026): the rule catalogue with what each checks and its SQL / Python check; per NBFC on/off and outcome with a required reason; reset to default; audit `RULE_POLICY_CHANGED` / `RULE_POLICY_RESET`; applies from the next validation. **Rule parameters as data** (migration 008 `validation_parameter`): declared in the pack's `parameters:` (DOB/gender, state/PIN and phone-or-ID cut-off dates, minimum address length, ID patterns for PAN/passport/voter/CKYC/G RAM G), used as `:name` in SQL, via the rule context in Python checks and as `{name}` in messages; per-NBFC overrides on the same page with a reason, audited `RULE_PARAMETER_CHANGED` / `RESET`. Spec-fixed values (DPD cap 900, dates after 01-01-1900, the Aadhaar checksum) stay in code. 
- [ ] K.7b **NBFCs & bureaus page** (read-only tenant and bureau profile view).
- [x] K.8 **Custom rules without SQL** (`submissions/validation/custom.py`, migration 009 `validation_rule.definition / created_by`):
  - **Building blocks:** a per-NBFC form on Rules & policies: record type + field; check *required / one of / not one of / between / length / pattern / compare with another field*; optional "only when <field> is one of …"; outcome; message.
  - **Compiled to SQL:** the same shape as the pack rules, stored with its definition. Fields come from the canonical model only and values are validated and quoted (no SQL injection).
  - **Preview** counts the findings on the latest validation and saves nothing. Edit, delete, a reason is required, audited `CUSTOM_RULE_ADDED / CHANGED / DELETED`.
  - **Policy:** custom rules take the same on/off and outcome switches as the UCRF rules.
  - **MySQL:** uses `REGEXP_LIKE` and `CHAR_LENGTH` (MySQL 8).
- [ ] K.9 Findings trends across runs (from the kept `validation_summary`).
- [ ] K.10 Commercial pipeline (needs spec).
- [ ] K.11 NeSL pipeline (own spec).
