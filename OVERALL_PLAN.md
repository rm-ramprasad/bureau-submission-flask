# Bureau Submission Platform: Overall Plan

> The platform: tenants, requests, worker, UI, plus the **decisions log** and the **single to-do list**.
> The data pipeline (canonical model, adapters, validation, formatter) is in [DESIGN_PLAN.md](DESIGN_PLAN.md).
> Original brief: [newdesign.md](newdesign.md). Consumer spec: [UCRF_Consumer_v3.74_Spec.md](UCRF_Consumer_v3.74_Spec.md).

---

## 1. Scope

1. A **Flask UI** where the internal team picks:
   - an **NBFC** (tenant)
   - an **as-of date**
   - **one or more submission types**: Consumer now; Commercial and NeSL later
   - **one or more bureaus**: Experian, CIBIL, CRIF High Mark, Equifax
   - a **run mode**: **Validate** (findings only) or **Generate** (validate + files)
2. Each click is stored as rows in one request table and processed by an **independent worker**.
3. **Validation results are stored in tables** and tracked across runs. **Files are downloaded** from the batch page.
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
| 15 | Adjustments (exclusions + overrides) | Users capture **loan account numbers and/or customer IDs to exclude**, and **overrides** for OD days, ledger balance, OD amount and settlement amount, each with an optional reason. They are applied **in the data layer through the SQL view `v_snapshot_adjustment`** while the snapshot is built, **before validation**, so validation checks the final data. Excluding a customer excludes every loan they hold, whole. Applied adjustments are frozen on the snapshot. Clicking Generate confirms and audits them. |
| 18 | File storage | Generated files and exception CSVs are written to **AWS S3** (`s3://<bucket>/<prefix>/<tenant>/<batch>/<file>`). The URI is stored on `submission_request`. UI downloads stream from S3 through the app (private bucket). Writes are conditional, so nothing is overwritten. Encryption is SSE-S3 or SSE-KMS. Credentials come from the AWS chain (IAM role). The local store is kept for development and tests. |
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

**Adjustments: exclusions and overrides (planned, K.4, migration 006)**

| Object | Kind | Purpose |
|---|---|---|
| `submission_adjustment` | table | What users capture, per **tenant + as-of date + submission type** (not per snapshot, so it carries over to the next validation). Columns: `adjustment_id`, `tenant_id`, `as_of_date`, `submission_type`, `kind`, `account_number` / `customer_id`, `field`, `override_value`, `reason` (optional, ≤ 500), `created_by/at`, `removed_by/at` (soft delete). |
| `v_snapshot_adjustment` | **view** | **The single data-layer definition** of how active adjustments apply to a snapshot. It matches `account_number` against `c_account` and `customer_id` against `c_account_holder.party_key`, expands customer exclusions to **every loan the customer holds**, and returns `snapshot_id, adjustment_id, account_key, action (EXCLUDE / OVERRIDE), field, new_value`. |
| `snapshot_adjustment` | table | **Frozen record of what was applied** to each snapshot: `snapshot_id, adjustment_id, account_key, action, field, old_value, new_value, applied_at`. Written from the view at build time, with old values taken from `c_account` first. |

- **`kind` values:** `EXCLUDE_ACCOUNT` (by `account_number`), `EXCLUDE_CUSTOMER` (by `customer_id`), and `OVERRIDE`.
- **`field` (OVERRIDE only):** `days_past_due` (OD days), `current_balance` (ledger balance), `amount_overdue` (OD amount), `settlement_amount`.

**Applying them (the new "adjust" step between extract and validate):** these are plain SQL statements driven by the view.

1. `INSERT INTO snapshot_adjustment … SELECT … FROM v_snapshot_adjustment WHERE snapshot_id = :s` (old values joined from `c_account`).
2. `DELETE` the excluded loans' rows from `c_account`, `c_account_holder`, `c_account_history` and `c_extension_attribute`. Whole loans, all holders.
3. `UPDATE c_account` with the override values.

Exclusions and overrides that match nothing in the snapshot become a WARN finding `ADJ.NOT_FOUND`, shown on the Exceptions page.

`submission_request` gains `excluded_accounts` and `overridden_accounts`, taken from `snapshot_adjustment`.

## 5. Run lifecycle

1. The user submits: tenant, date, types, run mode, and bureaus (Generate only). The web validates and inserts the rows in one transaction.
2. The worker claims a row.
3. **Snapshot stage:** get or create the snapshot for `(batch_id, tenant, type)`, or use the referenced `snapshot_id`.
   - If another worker is building it, release the row and retry later.
   - Building it means extract (adapter) → validate (rules → findings → cascade).
4. **VALIDATE row:** done. Record counts from `validation_summary`.
5. **GENERATE row:** if the snapshot has a REJECT_FILE outcome, fail with that reason. Otherwise the formatter writes this bureau's file from accepted records, and the row records the file path and counts.
6. The **batch page** shows each row's status, and for each snapshot the findings summary (by rule/outcome, drill-down, CSV export) and the Download buttons.

**Workflow (always validate first, decision #17):** Validate → review findings → capture exclusions/overrides → **Re-validate** (the new snapshot applies them, and validation checks the adjusted data) → "Generate files from this validation".

### 5.1 Adjustment lifecycle (K.4–K.5)

1. **Capture.** On the Exceptions page, for an NBFC + as-of date + type, the user enters:
   - **loan account numbers** and/or **customer IDs** to exclude (typed or pasted in bulk, or picked from a validated run's records);
   - **overrides** per loan account for OD days, ledger balance, OD amount and settlement amount.

   Each takes an optional reason. This writes `submission_adjustment` and audit `ADJUSTMENT_ADDED`.
2. **Remove.** Soft delete, with audit `ADJUSTMENT_REMOVED`. History stays visible.
3. **Apply.** A Validate run builds the snapshot with the adjust step (§4): extract → `v_snapshot_adjustment` → `snapshot_adjustment` (frozen, with old and new values) → delete/override → validate.
   - Findings and record counts reflect the adjusted data.
   - Each adjustment shows "applied to snapshot #…" or "not found".
4. **Generate.** "Generate files from this validation":
   - shows a confirmation: "N loans excluded, M overrides applied (validated in snapshot #…)";
   - is **blocked with a 'Re-validate' prompt** if adjustments were added or removed after that snapshot was built, so the file never carries unvalidated changes;
   - on confirm, writes audit `ADJUSTMENTS_CONFIRMED` (request IDs, snapshot, counts).

   The file is written from the snapshot as is. The exceptions CSV gains "Excluded loans" and "Overrides (old → new)" sections.
5. **Purged snapshots** keep their `snapshot_adjustment` rows only until retention removes the snapshot rows. The `audit_event` entries stay.

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
| **Dashboard** | **KPI cards:** submissions completed (GENERATE rows SUCCEEDED), records transmitted (sum written), accounts rejected by validation, accounts manually excluded. **Recent submissions** table: request, NBFC, bureau, type, as-of, records, excluded, status; links to the batch. **Bureaus panel:** files and records per bureau with the last date, as bar meters. **Highest-risk records:** rejected or excluded records with the highest DPD/overdue in the latest snapshots. Filtered by the NBFC selector. |
| **New request** | **Validate only:** NBFC, as-of date, types, cycle. The Generate option is removed. |
| **Exceptions** (adjustments) | For **NBFC + as-of + type**. **Exclude:** a box for loan account numbers and customer IDs (one per line, pasted in bulk), plus an optional reason; or pick rows from the latest validated run's records (search by account, customer, name, type, outcome, has findings). **Override:** loan account number + field (OD days / Ledger balance / OD amount / Settlement amount) + new value + optional reason, with the current value shown when a validated run exists. **Current adjustments:** kind, key, field, old → new, reason, by, when, status (*applied in snapshot #… / not found / pending re-validation*), Remove. **Actions:** "Re-validate with adjustments" (queues a Validate run) and "Generate files…" (enabled only when the latest validation includes every current adjustment). |
| **Submission tracker** | Today's batch list with more filters (status, mode, bureau, date range) and paging. |
| **Batch page** | Today's page plus an adjustments summary per snapshot (loans excluded, overrides old → new), with a link to Exceptions. |
| **Files** | Generated files in S3 (location shown): NBFC, bureau, file name, as-of, records written, loans excluded, overrides, rejected, size, created, Download file / exceptions. Purged files are shown as purged. |
| **Findings** | Today's page, with **"Exclude loan"** and **"Override…"** actions on records that pre-fill the Exceptions page. |
| **Audit** | Today's page, adding filters for action and user, and adjustment events with their reasons and old → new values. |
| **Later (K.7):** NBFCs & bureaus (read-only tenant and bureau profile view), Rules & policies (rule catalogue per NBFC, policy overrides editable with audit). |

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
- [ ] J.5 Login via `current_user()` hook (when needed)
- [x] J.6 Worker heartbeat (`worker_heartbeat`) + UI banner "No worker running" when the last poll is older than 30 s
- [x] J.8 EC2 deployment (decision 19): Dockerfile, compose, `deploy/` scripts, IAM (including the deny exceptions on the shared instance role), security groups EC2 ↔ RDS; RUNBOOK "Deploying on EC2"
- [ ] J.7 **User access to the EC2 app (deferred):**
  - Today it's reached only through an SSM port-forward tunnel (`localhost:8000`).
  - **To decide:** how the ~5 users connect. With an office network or VPN into the VPC, add inbound 8000 from that range on `sg-0945fe12331ab8cd0`, then use `http://<private-ip>:8000`. Without one, each user runs the SSM tunnel.
  - Do this together with J.5 login. Never open 8000 on the public IP while there's no login and only HTTP.

### Phase K: UI and extensions (order = build order)
- [x] K.1 **Layout:**
  - sidebar navigation (MAIN / REPORTS / ADMIN), top bar with breadcrumb, global NBFC selector (session) and "+ New request";
  - worker status and user in the sidebar footer; restyled components (cards, tables, badges, meters);
  - existing pages moved into the new shell.
- [ ] K.2 **Dashboard:** KPI cards, recent submissions, bureaus panel, highest-risk records; NBFC-filtered.
- [ ] K.3 **Submission tracker:** filters (status, mode, bureau, date range) and paging. **Files** page.
- [x] K.0 **Validate only:**
  - remove direct GENERATE from the New request form and the scheduler (`--mode`);
  - `create_batch(GENERATE)` requires a validated `snapshot_id`; update tests and RUNBOOK.
- [ ] K.4 **Migration 006:**
  - `submission_adjustment`, view `v_snapshot_adjustment`, `snapshot_adjustment`;
  - `submission_request.excluded_accounts / overridden_accounts`;
  - adjustment repository (add / remove / list, bulk add from pasted lists);
  - the **adjust step** in the snapshot build (view → frozen rows → delete/override) and the `ADJ.NOT_FOUND` warning;
  - tests incl. mock scenarios (exclude a joint loan via one customer, override DPD/overdue and see validation react).
- [ ] K.5 **Exceptions page:**
  - capture exclusions (bulk paste or pick from records) and overrides (current value shown), with optional reasons; list with status and remove;
  - "Re-validate with adjustments";
  - Generate blocked while re-validation is pending, with a confirmation showing counts;
  - exceptions CSV sections for excluded loans and overrides;
  - audit `ADJUSTMENT_ADDED` / `ADJUSTMENT_REMOVED` / `ADJUSTMENTS_CONFIRMED`.
- [ ] K.6 **"Exclude loan" / "Override…" actions** on the Findings page; adjustments summary on the batch page and Files page; audit filters.
- [ ] K.7 **Admin pages:** NBFCs & bureau profiles (read-only); Rules & policies (catalogue per NBFC, editable policy overrides with audit).
- [ ] K.8 Declarative custom rules (compile to SQL) and maintenance in the UI.
- [ ] K.9 Findings trends across runs (from the kept `validation_summary`).
- [ ] K.10 Commercial pipeline (needs spec).
- [ ] K.11 NeSL pipeline (own spec).
