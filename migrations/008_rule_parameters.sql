-- 008: per-NBFC overrides of rule parameters (cut-off dates, minimum lengths, ID patterns).
-- Defaults live in the rule pack (parameters:); a row here overrides one value for one NBFC and type.

CREATE TABLE validation_parameter (
    tenant_id         VARCHAR(30)   NOT NULL,
    submission_type   VARCHAR(20)   NOT NULL,
    name              VARCHAR(64)   NOT NULL,
    value             VARCHAR(500)  NOT NULL,     -- as text: YYYY-MM-DD, a whole number, or a regular expression
    updated_by        VARCHAR(100)  NOT NULL,
    updated_at        DATETIME      NOT NULL,     -- UTC
    PRIMARY KEY (tenant_id, submission_type, name),
    FOREIGN KEY (tenant_id) REFERENCES tenant (tenant_id)
);
