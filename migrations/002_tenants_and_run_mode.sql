-- 002: tenants (NBFCs), run mode and snapshot link on requests, tenant-scoped bureau profiles.
-- Pre-production migration: submission_request must be empty (the tenant foreign key fails otherwise).
-- bureau_profile only holds placeholder seed data, so it is cleared here; re-run the seed afterwards.

CREATE TABLE tenant (
    tenant_id             VARCHAR(30)   NOT NULL,   -- e.g. nbfc_017
    display_name          VARCHAR(100)  NOT NULL,
    source_schema         VARCHAR(64)   NOT NULL,   -- the NBFC's schema in the LMS MySQL
    mapping_path          VARCHAR(255),             -- clients/<tenant>/mapping.yaml
    extract_strategy      VARCHAR(20)   NOT NULL DEFAULT 'stream',  -- stream / insert_select
    is_active             BOOLEAN       NOT NULL DEFAULT TRUE,
    created_at            DATETIME      NOT NULL,   -- UTC
    updated_at            DATETIME      NOT NULL,   -- UTC
    PRIMARY KEY (tenant_id),
    UNIQUE KEY uq_tenant_source_schema (source_schema)
);

ALTER TABLE submission_request
    ADD COLUMN tenant_id   VARCHAR(30) NOT NULL AFTER batch_id,
    ADD COLUMN run_mode    VARCHAR(10) NOT NULL DEFAULT 'GENERATE' AFTER tenant_id,   -- VALIDATE / GENERATE
    ADD COLUMN snapshot_id BIGINT      NULL AFTER bureau_code,                        -- extract snapshot used
    MODIFY COLUMN bureau_code VARCHAR(20)  NULL,                                       -- NULL for VALIDATE rows
    MODIFY COLUMN active_key  VARCHAR(150) NULL,
    ADD KEY ix_submission_request_tenant (tenant_id, requested_at),
    ADD CONSTRAINT fk_submission_request_tenant FOREIGN KEY (tenant_id) REFERENCES tenant (tenant_id);

DELETE FROM bureau_profile;

ALTER TABLE bureau_profile
    ADD COLUMN tenant_id VARCHAR(30) NOT NULL FIRST,
    DROP PRIMARY KEY,
    ADD PRIMARY KEY (tenant_id, bureau_code, submission_type),
    ADD CONSTRAINT fk_bureau_profile_tenant FOREIGN KEY (tenant_id) REFERENCES tenant (tenant_id);
