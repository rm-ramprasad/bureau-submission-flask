"""Seed bureau profiles for tenants.

PLACEHOLDER VALUES: member IDs, header tags and file-name formats must be replaced with the
values each bureau issued to each NBFC (OVERALL_PLAN.md to-do A.4). Re-running the seed overwrites
them, so once real values are loaded, maintain them in the table instead of here.

Usage:  python -m submissions.bureaus.seed nbfc_001 nbfc_002
"""

from __future__ import annotations

import logging
import sys

from submissions.bureaus.models import BureauProfile
from submissions.bureaus.repository import BureauRepository
from submissions.config import Settings
from submissions.db import make_engine
from submissions.enums import BureauCode as B, SubmissionType as T
from submissions.logging_config import configure_logging

log = logging.getLogger(__name__)

UCRF_FILE_NAME = "{member_id}-{created:%d%m%Y}-{created:%H%M%S}.txt"

# bureau -> (display name, member-id prefix, header segment tag)
PLACEHOLDERS = {
    B.EXPERIAN: ("Experian", "EXP", "CONS"),
    B.CIBIL: ("TransUnion CIBIL", "CIB", "TUDF"),
    B.CRIF_HIGHMARK: ("CRIF High Mark", "CRF", "CONS"),
    B.EQUIFAX: ("Equifax", "EQF", "CONS"),
}


def placeholder_profiles(tenant_id: str, tenant_no: int, short_name: str) -> list[BureauProfile]:
    """Consumer profiles for one tenant; member IDs like CIB0000017 (10 chars, TL.01 length)."""
    return [BureauProfile(tenant_id, bureau, T.CONSUMER, name, f"{prefix}{tenant_no:07d}", short_name[:16], tag,
                          UCRF_FILE_NAME)
            for bureau, (name, prefix, tag) in PLACEHOLDERS.items()]


def seed(repo: BureauRepository, tenant_ids: list[str]) -> int:
    profiles = [p for no, tid in enumerate(tenant_ids, 1)
                for p in placeholder_profiles(tid, no, tid.upper().replace("_", " "))]
    for p in profiles:
        repo.upsert(p)
    return len(profiles)


def main() -> None:
    tenant_ids = sys.argv[1:]
    if not tenant_ids:
        sys.exit("usage: python -m submissions.bureaus.seed <tenant_id> [<tenant_id> ...]")
    settings = Settings.from_env()
    configure_logging(settings.log_level)
    n = seed(BureauRepository(make_engine(settings.database_url)), tenant_ids)
    log.warning("Seeded %d bureau profiles with PLACEHOLDER member IDs - replace before real use", n)


if __name__ == "__main__":
    main()
