-- 006: adjustments (MySQL 8): user exclusions and overrides.
-- Captured in submission_adjustment. When a snapshot is extracted, the active ones are frozen into
-- snapshot_adjustment (audit trail), resolved against the source into snapshot_adjustment_effect, and every
-- entity's extract SELECT is joined to the effects (NOT EXISTS for exclusions, COALESCE for overrides), so the
-- c_* tables hold the final data. See submissions/adjustments/sql.py.
-- The view text must match ADJUSTMENT_VIEW_SQL in submissions/adjustments/tables.py (a test checks it).

CREATE TABLE submission_adjustment (
    adjustment_id     BIGINT        NOT NULL AUTO_INCREMENT,
    tenant_id         VARCHAR(30)   NOT NULL,
    as_of_date        DATE          NOT NULL,
    submission_type   VARCHAR(20)   NOT NULL,
    kind              VARCHAR(20)   NOT NULL,   -- EXCLUDE_ACCOUNT / EXCLUDE_CUSTOMER / OVERRIDE
    account_number    VARCHAR(50),              -- EXCLUDE_ACCOUNT and OVERRIDE (matches the loan account number)
    customer_id       VARCHAR(50),              -- EXCLUDE_CUSTOMER (matches the borrower key)
    field             VARCHAR(30),              -- OVERRIDE: days_past_due / current_balance / amount_overdue / settlement_amount
    override_value    DECIMAL(20,2),
    reason            VARCHAR(500),
    created_by        VARCHAR(100)  NOT NULL,
    created_at        DATETIME      NOT NULL,   -- UTC
    removed_by        VARCHAR(100),
    removed_at        DATETIME,                 -- UTC; soft delete, history stays
    PRIMARY KEY (adjustment_id),
    KEY ix_submission_adjustment_scope (tenant_id, as_of_date, submission_type),
    FOREIGN KEY (tenant_id) REFERENCES tenant (tenant_id)
);

-- Frozen copy of the adjustments in force when the snapshot was extracted (audit trail)
CREATE TABLE snapshot_adjustment (
    snapshot_id       BIGINT        NOT NULL,
    adjustment_id     BIGINT        NOT NULL,
    kind              VARCHAR(20)   NOT NULL,
    account_number    VARCHAR(50),
    customer_id       VARCHAR(50),
    field             VARCHAR(30),
    override_value    DECIMAL(20,2),
    reason            VARCHAR(500),
    created_by        VARCHAR(100)  NOT NULL,
    created_at        DATETIME      NOT NULL,
    PRIMARY KEY (snapshot_id, adjustment_id),
    FOREIGN KEY (snapshot_id) REFERENCES extract_snapshot (snapshot_id) ON DELETE CASCADE,
    FOREIGN KEY (adjustment_id) REFERENCES submission_adjustment (adjustment_id)
);

-- What each frozen adjustment hit in the source: EXCLUDE (a loan), EXCLUDE_PARTY (a borrower left with no
-- loan), OVERRIDE (a loan field, with the source value as old_value)
CREATE TABLE snapshot_adjustment_effect (
    effect_id         BIGINT        NOT NULL AUTO_INCREMENT,
    snapshot_id       BIGINT        NOT NULL,
    adjustment_id     BIGINT        NOT NULL,
    action            VARCHAR(15)   NOT NULL,
    account_key       VARCHAR(50),
    party_key         VARCHAR(50),
    field             VARCHAR(30),
    old_value         DECIMAL(20,2),
    new_value         DECIMAL(20,2),
    PRIMARY KEY (effect_id),
    KEY ix_snapshot_adjustment_effect_account (snapshot_id, action, account_key),
    KEY ix_snapshot_adjustment_effect_party (snapshot_id, action, party_key),
    FOREIGN KEY (snapshot_id) REFERENCES extract_snapshot (snapshot_id) ON DELETE CASCADE
);

CREATE VIEW v_snapshot_adjustment AS
SELECT x.snapshot_id, x.adjustment_id, x.kind, x.account_number, x.customer_id, x.field, x.override_value,
       x.reason, x.created_by, x.created_at,
       (SELECT COUNT(DISTINCT e.account_key) FROM snapshot_adjustment_effect e
        WHERE e.snapshot_id = x.snapshot_id AND e.adjustment_id = x.adjustment_id
          AND e.action IN ('EXCLUDE', 'OVERRIDE')) AS loans_affected
FROM snapshot_adjustment x;

ALTER TABLE submission_request
    ADD COLUMN excluded_accounts INT,
    ADD COLUMN overridden_accounts INT;
