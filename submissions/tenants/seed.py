"""Seed the mock tenants used for development.

nbfc_001 uses the base LMS schema; nbfc_002 will be the variant with column differences
(OVERALL_PLAN.md to-do D.1). Real NBFCs are added with TenantRepository.upsert during onboarding.

Usage:  python -m submissions.tenants.seed
"""

from __future__ import annotations

import logging

from submissions.config import Settings
from submissions.db import make_engine
from submissions.logging_config import configure_logging
from submissions.tenants.models import Tenant
from submissions.tenants.repository import TenantRepository

log = logging.getLogger(__name__)

MOCK_TENANTS = [
    Tenant("nbfc_001", "Demo NBFC One (base schema)", "nbfc_001", "clients/nbfc_001/mapping.yaml"),
    Tenant("nbfc_002", "Demo NBFC Two (custom schema)", "nbfc_002", "clients/nbfc_002/mapping.yaml"),
]


def seed(repo: TenantRepository, tenants: list[Tenant] = MOCK_TENANTS) -> int:
    for tenant in tenants:
        repo.upsert(tenant)
    return len(tenants)


def main() -> None:
    settings = Settings.from_env()
    configure_logging(settings.log_level)
    n = seed(TenantRepository(make_engine(settings.database_url)))
    log.info("Seeded %d mock tenants", n)


if __name__ == "__main__":
    main()
