from dataclasses import replace

import pytest

from submissions.enums import ExtractStrategy
from submissions.tenants import (
    InactiveTenantError, InvalidTenantError, Tenant, TenantNotFoundError,
)
from submissions.tenants.seed import MOCK_TENANTS, seed


def test_mock_tenants_seeded_by_fixture(tenant_repo):
    assert [t.tenant_id for t in tenant_repo.list()] == ["nbfc_001", "nbfc_002"]
    one = tenant_repo.get("nbfc_001")
    assert (one.source_schema, one.extract_strategy, one.is_active) == ("nbfc_001", ExtractStrategy.STREAM, True)


def test_seed_is_rerunnable(tenant_repo):
    assert seed(tenant_repo) == 2
    assert len(tenant_repo.list(active_only=False)) == 2


def test_upsert_inserts_and_updates(tenant_repo, clock):
    tenant_repo.upsert(Tenant("nbfc_017", "Acme Finance", "acme_prod", extract_strategy="insert_select"))
    clock.advance()
    tenant_repo.upsert(replace(tenant_repo.get("nbfc_017"), display_name="Acme Finance Ltd"))
    acme = tenant_repo.get("nbfc_017")
    assert (acme.display_name, acme.extract_strategy) == ("Acme Finance Ltd", ExtractStrategy.INSERT_SELECT)


def test_deactivated_tenant_hidden_and_blocked(tenant_repo):
    tenant_repo.set_active("nbfc_002", False)
    assert [t.tenant_id for t in tenant_repo.list()] == ["nbfc_001"]
    assert len(tenant_repo.list(active_only=False)) == 2
    with pytest.raises(InactiveTenantError):
        tenant_repo.get_active("nbfc_002")
    assert tenant_repo.get_active("nbfc_001").tenant_id == "nbfc_001"


def test_missing_tenant(tenant_repo):
    with pytest.raises(TenantNotFoundError):
        tenant_repo.get("nbfc_999")
    with pytest.raises(TenantNotFoundError):
        tenant_repo.set_active("nbfc_999", False)


@pytest.mark.parametrize("tenant_id,schema", [
    ("NBFC_001", "nbfc_001"),           # uppercase id
    ("1nbfc", "nbfc_001"),              # starts with digit
    ("nbfc-001", "nbfc_001"),           # hyphen
    ("n" * 31, "nbfc_001"),             # too long
    ("nbfc_001", "nbfc;DROP TABLE x"),  # schema injection attempt
    ("nbfc_001", "nbfc 001"),           # space in schema
    ("nbfc_001", "s" * 65),             # schema too long
])
def test_identifiers_are_whitelisted(tenant_id, schema):
    with pytest.raises(InvalidTenantError):
        Tenant(tenant_id, "X", schema)


def test_display_name_and_strategy_validated():
    with pytest.raises(InvalidTenantError):
        Tenant("nbfc_003", "  ", "nbfc_003")
    with pytest.raises(ValueError):
        Tenant("nbfc_003", "X", "nbfc_003", extract_strategy="copy")


def test_mock_tenant_definitions_are_valid():
    assert {t.tenant_id for t in MOCK_TENANTS} == {"nbfc_001", "nbfc_002"}
