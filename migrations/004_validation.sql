-- 004: validation (MySQL 8).
-- Built-in rule packs ship with the code (submissions/validation/packs). These tables hold per-tenant
-- custom rules, per-tenant policy overrides, and the results of every validation run.

CREATE TABLE validation_rule (
    rule_id           VARCHAR(80)   NOT NULL,   -- must not start with UCRF. (reserved for built-in packs)
    tenant_id         VARCHAR(30)   NOT NULL,
    submission_type   VARCHAR(20)   NOT NULL,
    entity            VARCHAR(30)   NOT NULL,   -- canonical entity the finding points at
    segment           VARCHAR(4),
    field             VARCHAR(64),
    outcome           VARCHAR(16)   NOT NULL,   -- WARN / REJECT_FIELD / REJECT_SEGMENT / REJECT_RECORD / REJECT_FILE
    message           VARCHAR(500)  NOT NULL,
    sql_text          TEXT          NOT NULL,   -- returns row_id, account_key, party_key, value; uses :snapshot_id
    is_enabled        BOOLEAN       NOT NULL DEFAULT TRUE,
    updated_at        DATETIME      NOT NULL,
    PRIMARY KEY (rule_id),
    CONSTRAINT fk_validation_rule_tenant FOREIGN KEY (tenant_id) REFERENCES tenant (tenant_id)
);

CREATE TABLE validation_policy (
    tenant_id         VARCHAR(30)   NOT NULL,
    rule_id           VARCHAR(80)   NOT NULL,   -- built-in or custom
    is_enabled        BOOLEAN,                  -- NULL = keep the rule's own setting
    outcome           VARCHAR(16),              -- NULL = keep the rule's own outcome
    updated_at        DATETIME      NOT NULL,
    PRIMARY KEY (tenant_id, rule_id),
    CONSTRAINT fk_validation_policy_tenant FOREIGN KEY (tenant_id) REFERENCES tenant (tenant_id)
);

CREATE TABLE validation_finding (
    finding_id        BIGINT        NOT NULL AUTO_INCREMENT,
    snapshot_id       BIGINT        NOT NULL,
    rule_id           VARCHAR(80)   NOT NULL,
    entity            VARCHAR(30)   NOT NULL,
    row_id            BIGINT,                   -- row in the entity's c_* table
    account_key       VARCHAR(50),
    party_key         VARCHAR(50),
    segment           VARCHAR(4),
    field             VARCHAR(64),
    outcome           VARCHAR(16)   NOT NULL,
    value             VARCHAR(500),
    message           VARCHAR(500)  NOT NULL,
    PRIMARY KEY (finding_id),
    KEY ix_validation_finding_rule (snapshot_id, rule_id),
    KEY ix_validation_finding_record (snapshot_id, account_key, party_key),
    CONSTRAINT fk_validation_finding_snapshot FOREIGN KEY (snapshot_id) REFERENCES extract_snapshot (snapshot_id) ON DELETE CASCADE
);

CREATE TABLE validation_summary (
    snapshot_id       BIGINT        NOT NULL,
    rule_id           VARCHAR(80)   NOT NULL,
    outcome           VARCHAR(16)   NOT NULL,
    finding_count     INT           NOT NULL,
    PRIMARY KEY (snapshot_id, rule_id, outcome),
    CONSTRAINT fk_validation_summary_snapshot FOREIGN KEY (snapshot_id) REFERENCES extract_snapshot (snapshot_id) ON DELETE CASCADE
);

CREATE TABLE record_outcome (
    snapshot_id       BIGINT        NOT NULL,
    account_key       VARCHAR(50)   NOT NULL,
    party_key         VARCHAR(50)   NOT NULL,
    outcome           VARCHAR(10)   NOT NULL,   -- ACCEPTED / REJECTED
    reason_rule_id    VARCHAR(80),              -- first rule that rejected the record
    PRIMARY KEY (snapshot_id, account_key, party_key),
    CONSTRAINT fk_record_outcome_snapshot FOREIGN KEY (snapshot_id) REFERENCES extract_snapshot (snapshot_id) ON DELETE CASCADE
);
