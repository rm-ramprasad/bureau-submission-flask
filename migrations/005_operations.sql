-- 005: operations (MySQL 8): worker heartbeat and audit trail.

CREATE TABLE worker_heartbeat (
    worker_id         VARCHAR(100)  NOT NULL,
    host              VARCHAR(100),
    started_at        DATETIME      NOT NULL,   -- UTC, when this worker process started
    last_seen_at      DATETIME      NOT NULL,   -- UTC, last poll
    processed_count   INT           NOT NULL DEFAULT 0,
    PRIMARY KEY (worker_id),
    KEY ix_worker_heartbeat_seen (last_seen_at)
);

CREATE TABLE audit_event (
    event_id          BIGINT        NOT NULL AUTO_INCREMENT,
    occurred_at       DATETIME      NOT NULL,   -- UTC
    actor             VARCHAR(100)  NOT NULL,   -- user, "scheduler", "recovery", "purge", worker id
    action            VARCHAR(50)   NOT NULL,   -- e.g. RUN_CREATED, GENERATE_FROM_VALIDATION, REQUEST_RETRIED
    tenant_id         VARCHAR(30),
    target            VARCHAR(100),             -- batch id, request id, snapshot id
    details           TEXT,                     -- JSON
    PRIMARY KEY (event_id),
    KEY ix_audit_event_time (occurred_at),
    KEY ix_audit_event_tenant (tenant_id, occurred_at)
);
