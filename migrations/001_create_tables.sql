-- 001: application tables (MySQL 8).
-- One row in submission_request = one file = one (submission type x bureau).
-- Rows created by one click share a batch_id.

CREATE TABLE submission_request (
    request_id            BIGINT        NOT NULL AUTO_INCREMENT,
    batch_id              VARCHAR(40)   NOT NULL,
    as_of_date            DATE          NOT NULL,
    submission_type       VARCHAR(20)   NOT NULL,   -- CONSUMER / COMMERCIAL / NESL
    bureau_code           VARCHAR(20)   NOT NULL,   -- EXPERIAN / CIBIL / CRIF_HIGHMARK / EQUIFAX
    reporting_cycle       VARCHAR(2),               -- DL / W1 / W2 / W3 / ME / DC / AH / RR
    requested_by          VARCHAR(100),
    requested_at          DATETIME      NOT NULL,   -- UTC
    status                VARCHAR(12)   NOT NULL,   -- PENDING / RUNNING / SUCCEEDED / FAILED / CANCELLED
    active_key            VARCHAR(80),              -- set while PENDING/RUNNING, NULL otherwise; enforces duplicate guard
    worker_id             VARCHAR(100),
    attempt_count         INT           NOT NULL DEFAULT 0,
    started_at            DATETIME,
    finished_at           DATETIME,
    output_file_name      VARCHAR(255),
    output_file_path      VARCHAR(500),
    exceptions_file_path  VARCHAR(500),
    total_records         INT,
    accepted_records      INT,
    rejected_records      INT,
    duplicate_records     INT,
    error_message         TEXT,
    PRIMARY KEY (request_id),
    UNIQUE KEY uq_submission_request_active (active_key),
    KEY ix_submission_request_batch (batch_id),
    KEY ix_submission_request_status (status, requested_at)
);

CREATE TABLE bureau_profile (
    bureau_code           VARCHAR(20)   NOT NULL,
    submission_type       VARCHAR(20)   NOT NULL,
    display_name          VARCHAR(50)   NOT NULL,
    member_id             VARCHAR(30)   NOT NULL,   -- HD member/user ID and TL.01
    member_short_name     VARCHAR(16),              -- HD short name and TL.02
    header_segment_tag    VARCHAR(4)    NOT NULL,   -- e.g. TUDF / CONS
    file_name_pattern     VARCHAR(200)  NOT NULL,   -- e.g. {member_id}-{created:%d%m%Y}-{created:%H%M%S}.txt
    is_active             BOOLEAN       NOT NULL DEFAULT TRUE,
    updated_at            DATETIME      NOT NULL,   -- UTC
    PRIMARY KEY (bureau_code, submission_type)
);
