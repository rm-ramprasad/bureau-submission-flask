# Bureau Submissions

Generates credit bureau submission files (UCRF consumer now; commercial and NeSL later).
Plans: [OVERALL_PLAN.md](OVERALL_PLAN.md) (platform) and [DESIGN_PLAN.md](DESIGN_PLAN.md) (consumer file engine).

## Setup

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
$env:SUBMISSIONS_DATABASE_URL = "mysql+pymysql://user:password@localhost:3306/submissions"
```

## Database

```powershell
.\.venv\Scripts\python -m submissions.migrate        # applies migrations/NNN_*.sql once each
.\.venv\Scripts\python -m submissions.tenants.seed                      # mock tenants nbfc_001, nbfc_002
.\.venv\Scripts\python -m submissions.bureaus.seed nbfc_001 nbfc_002   # PLACEHOLDER bureau profiles per tenant
```

Tenants must exist before their bureau profiles (foreign key).

Add a schema change as a new numbered file, e.g. `migrations/004_add_x.sql`. Never edit an applied file.
If you change a platform table, update the matching definition in `submissions/*/tables.py` too.

**Canonical model changes** are made only in `submissions/canonical/model.yaml`. Bump `canonical_version`, then generate a new migration (never edit 003):

```powershell
.\.venv\Scripts\python -m submissions.canonical.generate --out migrations/004_canonical_v1_1.sql
```

(For a change after 003 is applied, hand-write the ALTERs in the new file. The generator emits a full CREATE script, useful as the reference for what changed.)

## Mock LMS source data

`mock_data/mock_lms.sql` creates databases `nbfc_001` (base layout) and `nbfc_002` (customised layout) with the
same 52 test scenarios. Run it as a script in DBeaver (Alt+X), or load it directly:

```powershell
.\.venv\Scripts\python mock_data/generate_mock_data.py --load "mysql+pymysql://user:pass@host:3306/"
```

## Operations

Scheduling, retention, recovery, audit and troubleshooting: see [RUNBOOK.md](RUNBOOK.md).

## Onboarding a new NBFC

Step by step in [ONBOARDING.md](ONBOARDING.md): `new_tenant` → `introspect` → edit mapping → `check` → `dry_run` →
bureau profiles → go live.

## Tenant mappings

`clients/_base/mapping.yaml` maps the standard LMS layout. Each tenant's `clients/<tenant>/mapping.yaml` extends it
and lists only the differences. Format: see `submissions/adapters/mapping.py`.

```powershell
# check against the live source (SUBMISSIONS_SOURCE_DATABASE_URL, else SUBMISSIONS_DATABASE_URL)
.\.venv\Scripts\python -m submissions.adapters.check clients/nbfc_002/mapping.yaml --schema nbfc_002 --as-of 2026-09-30
# regenerate the reviewed SQL after changing a mapping
.\.venv\Scripts\python -m submissions.adapters.compiler clients/nbfc_002/mapping.yaml --schema nbfc_002 --out clients/nbfc_002/generated
```

## Web UI

Run the UI and the worker side by side (two terminals, both from the project root, same environment variables):

```powershell
$env:SUBMISSIONS_DATABASE_URL = "mysql+pymysql://user:pass@host:3306/my_demo_db"
$env:SUBMISSIONS_SECRET_KEY = "<long random string>"      # signs the session / form tokens
.\.venv\Scripts\python -m submissions.web                 # http://127.0.0.1:5000
.\.venv\Scripts\python -m submissions.worker              # second terminal
```

Layout: sidebar navigation (Main / Reports / Admin), top bar with a global **NBFC selector** and **New request**,
worker status in the sidebar footer.

**Request flow** (OVERALL_PLAN §5.1): **New request** creates a DRAFT → its **Exceptions** step (exclude loans or
customers, override OD days / ledger balance / OD amount / settlement amount; tagged with the request ID) → **Submit
for validation** (locks the exceptions; the extract leaves excluded records out, validation runs on the rest) →
**Generate files** from the validated request (confirm the adjustment counts, pick bureaus).

| Page | What it does |
|---|---|
| **Dashboard** | Request-stage KPI cards (raised, awaiting exceptions, in progress, validated – files pending, completed, failed, cancelled) by period, data KPIs, needs attention, NBFC status, bureaus, recent requests |
| **New request** / **Exceptions** | Create a draft; drafts waiting for exceptions; the request's Exceptions step |
| **Submission tracker** | All runs with status / mode / bureau / date filters and paging, or requests by stage |
| Batch page | Rows, submit, retry / cancel, snapshot and adjustments summary, generate files, downloads |
| Findings | Filters, CSV export, **Exclude loan** / **Override…** into a new draft request |
| **Files** | Generated bureau files (S3), filters, downloads |
| **Audit** | Every action, filterable by action, user and request |
| **Rules & policies** | Rule catalogue with its SQL / check; per NBFC: on/off and outcome, rule parameters, custom rules without SQL |

`python -m submissions.web` is Flask's development server. On EC2 the Docker image runs it under gunicorn
(RUNBOOK.md "Deploying on EC2").

## Worker

Runs pending `submission_request` rows: build (or reuse) the batch's snapshot → extract → validate → record the
result. GENERATE rows also write the bureau file (phase H).

```powershell
.\.venv\Scripts\python -m submissions.worker           # poll every 5 s
.\.venv\Scripts\python -m submissions.worker --once    # process what is pending and exit
```

Run it from the project root (tenant mapping paths are relative to it). To queue a validation without the UI:

```python
from datetime import date
from submissions.db import make_engine
from submissions.requests import RequestRepository
RequestRepository(make_engine(URL)).create_batch("nbfc_001", date(2026, 9, 30), "VALIDATE", ["CONSUMER"])
```

## Generated files

GENERATE rows write, per bureau, the UCRF file and `<same name>.exceptions.csv` to **AWS S3**:
`s3://<SUBMISSIONS_S3_BUCKET>/<SUBMISSIONS_S3_PREFIX>/<tenant>/<batch>/<file name from bureau_profile>`. The S3 URI
is stored on `submission_request`, and UI downloads stream from that location. Set `SUBMISSIONS_STORAGE=s3` plus the
bucket settings (RUNBOOK.md: environment, IAM policy). For development, `SUBMISSIONS_STORAGE=local` (the default) writes
to `SUBMISSIONS_OUTPUT_DIR` instead. Existing files are never overwritten.

Encoding options the spec leaves open (`submissions/formats/ucrf_consumer/encoding.py`, `FormatOptions`): decided as
segment tags **without** a length prefix (`PNN01`) and **no** separator or line break between records. This is still an
open review item (OVERALL_PLAN H.6): re-confirm against a CIC sample file before the first live submission.

## Validation

Built-in rules: `submissions/validation/packs/ucrf_consumer_3_74.yaml` (plus REQUIRED/ENUM rules generated from the
canonical model). The pack also declares **rule parameters** (cut-off dates, minimum address length, ID patterns) used
as `:name` in SQL and `{name}` in messages. Values fixed by the UCRF spec (DPD cap 900, dates after 01-01-1900, the
Aadhaar checksum) stay in code.

Everything per NBFC is managed on the **Rules & policies** page and audited:

| What | Stored in | Code |
|---|---|---|
| Rule on/off and outcome | `validation_policy` | `rules.set_policy`, `clear_policy` |
| Rule parameter overrides | `validation_parameter` (migration 008) | `parameters.set_parameter`, `load_parameters` |
| Custom rules built from a form (required / one of / between / length / pattern / compare, optional "only when") | `validation_rule` with the JSON definition (migration 009) | `custom.save`, compiled by `custom.compile_sql` |
| Hand-written custom SQL rules | `validation_rule` (no definition) | `rules.save_custom_rule` |

Exclusions and overrides (`submissions/adjustments/`) are joined into the extract in SQL before validation
(`adjustments/sql.py`); each snapshot keeps a frozen copy and their effects for the audit trail.

Results: `validation_finding` (every finding), `validation_summary` (counts per rule/outcome), `record_outcome`
(ACCEPTED/REJECTED per account × holder, with the first rejecting rule).

## Tests

```powershell
.\.venv\Scripts\python -m pytest
```

Optional check that the SQL migrations match the code's table definitions (needs an **empty** scratch MySQL DB):

```powershell
$env:SUBMISSIONS_TEST_MYSQL_URL = "mysql+pymysql://user:password@localhost:3306/submissions_test"
.\.venv\Scripts\python -m pytest tests/test_migrate.py
```

## Layout

| Path | Purpose |
|---|---|
| `submissions/requests/` | `submission_request` table: create batch, claim row, record result, derived batch status |
| `submissions/bureaus/` | `bureau_profile` table: member ID, header tag, file-name pattern per tenant × bureau × type |
| `submissions/tenants/` | `tenant` table: one per NBFC and its source schema |
| `submissions/canonical/` | Canonical model (`model.yaml`), tables/records built from it, migration generator |
| `submissions/migrate.py` | Plain SQL migration runner |
| `submissions/config.py` | Settings from environment variables |
| `migrations/` | Versioned SQL files (MySQL) |
| `submissions/adapters/` | Mapping loader, SQL compiler, extract runner, `check` tool |
| `submissions/snapshot/` | Extract snapshot lifecycle |
| `submissions/onboarding/` | `new_tenant`, `introspect`, `dry_run` |
| `submissions/ops/` | Heartbeat, audit, recovery, retention, schedule |
| `clients/` | Base and per-tenant mappings, generated SQL |
| `mock_data/` | Mock LMS source (two tenant schemas) |
