"""Runtime settings, read from environment variables only (no secrets in code)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_url: str            # app tables (requests, snapshots, findings)
    source_database_url: str     # LMS server holding the tenant schemas (defaults to database_url)
    output_dir: Path                         # local file store root (storage = "local")
    log_level: str = "INFO"
    secret_key: str = "dev-only-change-me"   # Flask session/CSRF signing key
    storage: str = "local"                   # "s3" in real use, "local" for development/tests
    s3_bucket: str | None = None
    s3_prefix: str = "bureau-submissions"
    s3_region: str | None = None             # None = from the AWS config/environment
    s3_kms_key_id: str | None = None         # SSE-KMS key; None = SSE-S3 (AES256)
    auth: str = "none"                       # "login" (username + password, the server default) or "none" (dev/tests)
    https: bool = False                      # behind the HTTPS proxy: trust X-Forwarded-Proto, secure cookies

    @classmethod
    def from_env(cls) -> "Settings":
        url = os.environ.get("SUBMISSIONS_DATABASE_URL")
        if not url:
            raise RuntimeError(
                "SUBMISSIONS_DATABASE_URL is not set, "
                "e.g. mysql+pymysql://user:password@localhost:3306/submissions"
            )
        return cls(
            database_url=url,
            source_database_url=os.environ.get("SUBMISSIONS_SOURCE_DATABASE_URL") or url,
            output_dir=Path(os.environ.get("SUBMISSIONS_OUTPUT_DIR", "output")),
            log_level=os.environ.get("SUBMISSIONS_LOG_LEVEL", "INFO"),
            secret_key=os.environ.get("SUBMISSIONS_SECRET_KEY", "dev-only-change-me"),
            storage=os.environ.get("SUBMISSIONS_STORAGE", "local").lower(),
            s3_bucket=os.environ.get("SUBMISSIONS_S3_BUCKET") or None,
            s3_prefix=os.environ.get("SUBMISSIONS_S3_PREFIX", "bureau-submissions"),
            s3_region=os.environ.get("SUBMISSIONS_S3_REGION") or None,
            s3_kms_key_id=os.environ.get("SUBMISSIONS_S3_KMS_KEY_ID") or None,
            auth=os.environ.get("SUBMISSIONS_AUTH", "login").lower(),
            https=os.environ.get("SUBMISSIONS_HTTPS", "").lower() in ("1", "true", "yes"),
        )
