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
worker status in the sidebar footer. Pages: **New request** (always a validation) · **Submission tracker** · batch page
(status per row, retry/cancel, snapshot summary, "Generate files from this validation", downloads) · findings (filters,
CSV export) · **Audit**. Dashboard, Exceptions, Files and Admin arrive in phase K.
`python -m submissions.web` is Flask's development server. Put a production WSGI server (e.g. waitress or gunicorn)
behind the internal proxy for real use.

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
canonical model). Per tenant, add custom SQL rules and change any rule's outcome or on/off state:

```python
from submissions.validation.rules import Rule, save_custom_rule, set_policy
set_policy(engine, "nbfc_001", "UCRF.TL.WRITTEN_OFF_AMOUNTS_MISSING", outcome="REJECT_RECORD")
save_custom_rule(engine, "nbfc_002", "CONSUMER", Rule("NBFC002.MY_RULE", "CUSTOM", "account", "WARN", "message",
                 sql="SELECT a.row_id, a.account_key, NULL AS party_key, NULL AS value FROM c_account a "
                     "WHERE a.snapshot_id = :snapshot_id AND ..."))
```

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
