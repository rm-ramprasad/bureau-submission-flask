# Submission Data Pipeline: Design

> How a submission file is produced: source data → canonical model → validation → file.
> The platform around it (tenants, requests, worker, UI) and the **single to-do list** are in [OVERALL_PLAN.md](OVERALL_PLAN.md).
> The consumer file spec is [UCRF_Consumer_v3.74_Spec.md](UCRF_Consumer_v3.74_Spec.md).

---

## 1. Context

- One LMS provider on AWS serves ~50 NBFCs. Every NBFC runs on the **same MySQL engine** but has **its own schema**, and column metadata can differ per NBFC.
- **Goal:** one product approach for all NBFCs. Per-NBFC effort is limited to a **mapping** plus configuration.
- **Validation is a first-class stage.** It can run on its own before any file exists, its results are stored in tables and tracked, and it is extensible with client rules beyond UCRF.

## 2. Architecture

```
 NBFC schema ──▶ ADAPTER ─────────▶ CANONICAL SNAPSHOT ──▶ VALIDATION ──────────▶ FORMATTER ──▶ bureau files
 (custom cols)   mapping file       (core model, per run)   SQL + Python rules     UCRF layout,
                 compiled to SQL                            → findings tables      bureau stamping
```

| Layer | Per NBFC? | Owns | Never does |
|---|---|---|---|
| **Adapter** | Yes: one mapping file + lookups | Joins, filtering, unpivoting, client code → UCRF code | Spec rules, rejecting records |
| **Canonical model** | No (core, versioned) | The data contract every later layer uses | — |
| **Snapshot** | No | Freezing the canonical data for one run | — |
| **Validation** | Core rules: no. Custom rules and policy: yes | Rules, outcomes, cascade, findings | Formatting |
| **Formatter** | No | Segments, encoding, header/trailer, bureau identity | Validation decisions |

**Dependency rule:** validation and the formatter depend only on the canonical model, never on each other or on adapters. Only the run orchestrator knows all layers.

**Change test:** changes when an NBFC's schema changes → adapter. Changes when the UCRF spec changes → validation/formatter. Changes when the product changes → canonical model.

## 3. Canonical model (core)

### 3.1 Single source of truth

`submissions/canonical/model.yaml` defines every entity, column, type, required flag and allowed code list. **Everything is derived from it** so nothing drifts:

| Derived artifact | How | Used by |
|---|---|---|
| SQLAlchemy tables for `c_*` | Built at import (`canonical/schema.py`) | Snapshot, validation, formatter |
| Record dataclasses (`AccountRecord`, …) | Built at import | Python rules, formatter |
| Reference data `ref_code`, `ref_state_pin` (Appendix C) | Built at import | Validation (enum, PIN/state) |
| MySQL migration | `python -m submissions.canonical.generate --out migrations/NNN_*.sql`; a test fails if the committed file is stale | Deployment |
| Mapping template + contract check | Phase D | Adapter onboarding |

Snapshot tables have a surrogate `row_id`, a `snapshot_id` foreign key with `ON DELETE CASCADE` (purging a snapshot purges its rows), indexes led by `snapshot_id`, and **no NOT NULL or unique constraints**. The snapshot holds what the source holds, and validation judges it. "Required" is a validation rule, not a DB constraint.

It is versioned (`canonical_version`). A change is a product release, not a client change.

### 3.2 Entities

Normalised and spec-aligned (UCRF codes), but not tied to the file layout. Every table carries `snapshot_id`.

| Table | Grain | Key fields |
|---|---|---|
| `c_party` | borrower | party_key, full_name, birth_date, gender, occupation, income, income_basis, income_period |
| `c_party_identifier` | party × ID | id_type (01–10), id_number, issue_date, expiry_date |
| `c_party_phone` | party × phone | number, extension, phone_type, priority |
| `c_party_email` | party × email | email, priority |
| `c_party_address` | party × address | line1–5, state_code, pin_code, address_category, residence_code, priority |
| `c_account` | account | account_key, account_number, account_type, dates (opened, last payment, closed, reported), amounts (sanctioned, balance, overdue, limits, EMI, write-off, settlement, collateral), DPD, asset class, facility status, suit status, ROI, tenure, payment frequency, previous-value fields (TL.16–20) |
| `c_account_holder` | account × party | ownership_indicator (1–5). **One UCRF record per row.** |
| `c_account_history` | account × month | TH fields (optional) |
| `c_extension_attribute` | entity × key | NBFC-specific values for custom rules (`entity, entity_key, attr_name, attr_value`) |

Amounts are `DECIMAL(20,2)` in the model. The formatter rounds to whole rupees per the spec.

## 4. Adapter (per-NBFC mapping)

### 4.1 Mapping file

One YAML per NBFC states **how its schema reaches the canonical model**. A **base mapping** covers the standard LMS schema, and each NBFC file only **overrides** what differs.

```yaml
tenant: nbfc_017
extends: base_lms_v1          # base mapping for the standard LMS schema
source_schema: nbfc_017
canonical_version: "1.0"
party:
  fields:
    birth_date: c.dob_dt                       # override: renamed column
account:
  from: "{s}.loan l LEFT JOIN {s}.loan_ext x ON x.loan_id = l.loan_id"
  fields:
    days_past_due: "COALESCE(l.dpd_days, x.overdue_days)"
party_phone:
  unpivot:                                     # wide source columns → canonical rows
    - { number: c.mobile_no,  phone_type: "01", priority: 1 }
    - { number: c.alt_mobile, phone_type: "01", priority: 2 }
extensions:
  account: { scheme_code: x.scheme_cd }        # → c_extension_attribute
lookups:                                       # client code → UCRF code (loaded into lookup tables)
  account_type: { HL: "02", PL: "05", GL: "07" }
  gender: { M: "2", F: "1", T: "3" }
```

### 4.2 Mapping compiler

- Compiles base + overrides into **plain MySQL**: one `SELECT` per canonical entity (several named SELECTs combine with `UNION ALL`, which is how wide columns become rows). Each statement is readable, reviewed and committed under `clients/<tenant>/generated/`. A hash of the compiled SQL is stored on each snapshot as `mapping_version`.
- **Lookups** compile inline to `CASE <expr> WHEN 'HL' THEN '02' … ELSE <expr> END`. There's no lookup table, so it works whichever server the source is on. An unmapped value passes through as the raw source value, so validation reports it (not a valid code) rather than dropping it.
- **Extract strategy** is configurable:
  - `insert_select`: our schema is on the same MySQL server/cluster, so run `INSERT INTO c_* SELECT …` directly (fastest).
  - `stream`: the source is a separate read replica, so stream the `SELECT` results in batches into our `c_*` tables.

### 4.3 Contract check

`check-mapping` verifies that every required canonical field is mapped, the SQL compiles against the schema (`EXPLAIN`), result types are compatible, and every distinct source code has a lookup entry.

## 5. Canonical snapshot

- Each extract creates an `extract_snapshot` (snapshot_id, tenant, submission_type, as_of_date, canonical_version, mapping_version, status, row counts) and fills the `c_*` tables with that `snapshot_id`.
- **One snapshot serves a whole batch for a tenant and type.** It is validated once, then stamped per bureau, so 4 bureau files cost one extract and one validation.
- **Why snapshot rather than validate live views:**
  - **Consistency:** the file always matches what was validated.
  - **Audit:** you can show exactly what was submitted.
  - **Tracking:** findings join to stable rows.
  - **Isolation:** one read from the LMS, then all work happens on our tables.
- **Retention:** snapshots are purged after N days (configurable, PII). Findings summaries can be kept longer.

## 6. Validation

### 6.1 Rule catalogue

**As built:**
- **Built-in rules ship with the code** as a versioned YAML pack, `submissions/validation/packs/ucrf_consumer_3_74.yaml`, like the canonical model.
- **REQUIRED and ENUM rules are generated** from `model.yaml` (one per required column and per code column). The pack sets their outcomes per entity or column.
- **`validation_rule` holds only per-tenant custom SQL rules.** Their IDs may not start with `UCRF.`.
- **`validation_policy`** overrides the enabled flag and/or outcome of any rule per tenant.
- **Rule kinds:**
  - `sql`: must use `:snapshot_id`, and returns `row_id, account_key, party_key, value`;
  - `python`: a registered function in `python_rules.py`;
  - `cascade`: a record decision.

Original column design, now split between the pack and the table:

| Column | Notes |
|---|---|
| `rule_id` | e.g. `UCRF.TL.CLOSED_WITH_BALANCE`, `NBFC017.HL_NEEDS_COLLATERAL` |
| `rule_pack` | `UCRF-3.74` (core) or `CUSTOM` |
| `tenant_id` | NULL = global; set = that NBFC only |
| `submission_type`, `scope` | CONSUMER…; FIELD / SEGMENT / RECORD / FILE |
| `engine` | `SQL` or `PYTHON` |
| `sql_text` / `python_ref` | The check |
| `segment`, `field` | What the finding points at |
| `default_outcome` | WARN / REJECT_FIELD / REJECT_SEGMENT / REJECT_RECORD / REJECT_FILE |
| `message`, `is_enabled`, `version` | |

`validation_policy (tenant_id, rule_id, is_enabled, outcome)` overrides per NBFC. UCRF's "configurable reject record" items are simply policy rows.

### 6.2 Rule engines

**SQL rules** (the majority, including all custom rules) run on the canonical tables:
```sql
-- UCRF.TL.CLOSED_WITH_BALANCE
SELECT a.account_key, h.party_key, a.closed_date AS value
FROM c_account a JOIN c_account_holder h ON h.snapshot_id = a.snapshot_id AND h.account_key = a.account_key
WHERE a.snapshot_id = :snapshot_id AND a.closed_date IS NOT NULL AND a.current_balance > 0
```
The runner wraps each rule as `INSERT INTO validation_finding … SELECT`. Rules are written once, against canonical tables, for all NBFCs.

**Python rules** cover the few that SQL can't express well: Aadhaar Verhoeff, name rules (tokens, initials, S/O descriptors, salutations), phone pre-edits, and the full email check set.

**Custom rules** use the same mechanism: SQL against canonical tables plus `c_extension_attribute`. A declarative form (required / enum / range / compare) compiles to SQL later.

### 6.3 Outcomes and cascade

After all rules run, the **cascade** (Python, ordered) computes the final status per record (`c_account_holder` row):
1. Field rejects drop the field, or apply its default.
2. Segment rejects drop the segment.
3. Re-check minimums: PN valid, ≥ 1 valid PA, PT or a qualifying ID (accounts opened on/after 01-06-2007), TL valid.
4. Any REJECT_RECORD, or a failed minimum → record rejected.
5. Any REJECT_FILE → the snapshot is not formattable.

The result is written to `record_outcome (snapshot_id, account_key, party_key, outcome, reason_rule_id)`. `WARN` findings never block anything.

### 6.4 Findings (tracked in tables)

- `validation_finding`: snapshot_id, account_key, party_key, rule_id, segment, field, outcome, value, message.
- `validation_summary`: snapshot_id, rule_id, outcome, count. Feeds the UI and trends across runs.
- CSV/XLSX is only an export of these tables.

## 7. Formatter

- **Input:** a validated snapshot plus a `BureauProfile`. It reads only records whose `record_outcome` is accepted, with field and segment drops applied.
- **Output:** the UCRF file. Segment order PN, ID, PT, EC, PA, TL, TH, ES; caps ID 8, PT 10, EC 10, PA 5; sequential tags; TLV / fixed-width encoding; padding; trailing signs; DDMMYYYY dates; spec reshaping (26-char name fields and 40-char address lines without splitting words, DPD capped at 900, ROI truncated to 3 dp).
- **Bureau stamping:** member ID, header segment tag and file name come from the bureau profile only.
- **Byte-identical duplicate records** are counted and reported (spec §1.4).
- **Golden tests:** fixed snapshot → expected file bytes. Verify the encoding (spec §14 #16) against a real CIC sample file.

## 8. Onboarding an NBFC

1. `introspect --schema nbfc_017`: diff against the base LMS schema and list the differences.
2. Write `clients/nbfc_017/mapping.yaml` (overrides only) and fill in the lookups.
3. `check-mapping`: coverage, compile, types, unmapped codes.
4. `extract --dry-run`: row counts, null rates, unmapped-value report.
5. Validate-only run: review the findings with the NBFC.
6. Tenant config: member IDs per bureau, policy overrides, custom rules → go live.

## 9. Code layout (pipeline part)

```
canonical/model.yaml            # source of truth
submissions/
  canonical/                    # model loader, generated dataclasses, DDL/ref generator
  adapters/                     # mapping loader (base + override), compiler, lookups, extract runner, check-mapping
  snapshot/                     # snapshot lifecycle, retention
  validation/                   # rule catalogue, SQL runner, python rules, ucrf pack, cascade, policy, summary
  formats/ucrf_consumer/        # segment builders, encoder, file writer
clients/
  _base/mapping.yaml            # standard LMS schema mapping
  <tenant>/mapping.yaml, generated/*.sql
```

## 10. Open questions (pipeline)

1. **Segment-tag TLV encoding** (`PN03N01` vs `PNN01`), spec §14 #16. Needs a real CIC sample file.
2. **Base LMS schema** and one example NBFC customisation, to write `_base/mapping.yaml` for real.
3. **Extract strategy:** can our tables live on the same MySQL cluster (`insert_select`), or only a read replica (`stream`)?
4. **TH history:** is a one-time historical load needed?
