-- 010: app users for the in-app login (SUBMISSIONS_AUTH=login). Passwords are salted hashes only.
-- Manage with: python -m submissions.users add|passwd|disable|enable|list

CREATE TABLE app_user (
    username             VARCHAR(50)   NOT NULL,
    display_name         VARCHAR(100)  NOT NULL,
    password_hash        VARCHAR(255)  NOT NULL,
    is_active            BOOLEAN       NOT NULL DEFAULT TRUE,
    is_admin             BOOLEAN       NOT NULL DEFAULT FALSE,
    failed_attempts      INT           NOT NULL DEFAULT 0,
    locked_until         DATETIME,                 -- UTC
    last_login_at        DATETIME,                 -- UTC
    password_changed_at  DATETIME      NOT NULL,   -- UTC
    created_at           DATETIME      NOT NULL,   -- UTC
    PRIMARY KEY (username)
);
