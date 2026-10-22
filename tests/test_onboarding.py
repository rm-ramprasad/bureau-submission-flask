import shutil
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import func, select

from submissions.adapters.mapping import load_mapping
from submissions.bureaus import BureauRepository
from submissions.canonical.schema import extract_snapshot
from submissions.onboarding.dry_run import dry_run, format_report as format_dry_run
from submissions.onboarding.introspect import format_report, introspect, mapping_references
from submissions.onboarding.new_tenant import register_tenant
from submissions.tenants import InvalidTenantError, Tenant

ROOT = Path(__file__).resolve().parent.parent
BASE = load_mapping(ROOT / "clients" / "_base" / "mapping.yaml")
AS_OF = date(2026, 9, 30)


# ---------------------------------------------------------------- introspect
def test_mapping_references_resolve_aliases_including_subselects():
    refs = mapping_references(BASE)
    assert ("party.main.birth_date", "borrower_demographics", "birth_date") in refs
    assert any(t == "account_details" and c == "party_role" and w.startswith("account_holder") for w, t, c in refs)
    assert ("account_holder.main.ownership_indicator", "account_details", "loan_id") in refs   # j.loan_id sub-select


def test_introspect_finds_differences_and_needed_overrides(lms_engine):
    report = introspect(lms_engine, "nbfc_002", "nbfc_001", BASE)
    assert report.extra_tables == ["loan_ext"] and report.missing_tables == []
    tables = {t.table: t for t in report.tables}
    assert tables["borrower_demographics"].missing_columns == ["birth_date", "phone1_number"]
    assert tables["borrower_demographics"].extra_columns == ["dob", "primary_mobile"]
    assert tables["account_details"].missing_columns == ["days_past_due"]
    assert report.needs_overrides
    assert "party.main.birth_date: borrower_demographics.birth_date" in report.unresolved
    assert "account.main.days_past_due: account_details.days_past_due" in report.unresolved
    assert any(u.startswith("party_phone.phone1.") for u in report.unresolved)
    text = format_report(report)
    assert "Override these in the tenant mapping" in text and "loan_ext" in text


def test_introspect_clean_with_the_tenant_mapping(lms_engine):
    tenant_mapping = load_mapping(ROOT / "clients" / "nbfc_002" / "mapping.yaml")
    report = introspect(lms_engine, "nbfc_002", "nbfc_001", tenant_mapping)
    assert report.unresolved == [] and not report.needs_overrides
    assert "resolves fully" in format_report(report)


def test_introspect_reference_against_itself_is_identical(lms_engine):
    report = introspect(lms_engine, "nbfc_001", "nbfc_001", BASE)
    assert (report.tables, report.unresolved, report.extra_tables) == ([], [], [])


# ---------------------------------------------------------------- new tenant
@pytest.fixture
def base_dir(tmp_path):
    (tmp_path / "clients").mkdir()
    shutil.copytree(ROOT / "clients" / "_template", tmp_path / "clients" / "_template")
    shutil.copytree(ROOT / "clients" / "_base", tmp_path / "clients" / "_base")
    return tmp_path


def test_register_tenant_creates_mapping_from_template(engine, tenant_repo, base_dir):
    tenant, path, created = register_tenant(engine, base_dir, "nbfc_017", "Acme Finance", "acme_lms",
                                            placeholder_bureaus=True)
    assert created and path.is_file() and "nbfc_017" in path.read_text(encoding="utf-8")
    stored = tenant_repo.get("nbfc_017")
    assert (stored.source_schema, stored.mapping_path) == ("acme_lms", "clients/nbfc_017/mapping.yaml")
    assert set(load_mapping(path).entities) >= {"party", "account", "account_holder"}   # template is a valid mapping
    assert len(BureauRepository(engine).list("nbfc_017")) == 4


def test_register_tenant_never_overwrites_an_edited_mapping(engine, base_dir):
    _, path, _ = register_tenant(engine, base_dir, "nbfc_017", "Acme", "acme_lms")
    path.write_text(path.read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8")
    _, _, created = register_tenant(engine, base_dir, "nbfc_017", "Acme Finance Ltd", "acme_lms")
    assert not created and path.read_text(encoding="utf-8").endswith("# edited\n")


def test_register_tenant_rejects_bad_ids(engine, base_dir):
    with pytest.raises(InvalidTenantError):
        register_tenant(engine, base_dir, "NBFC-17", "Acme", "acme_lms")
    assert not (base_dir / "clients" / "NBFC-17").exists()


# ---------------------------------------------------------------- dry run
def snapshot_count(engine) -> int:
    with engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(extract_snapshot)).scalar()


def test_dry_run_reports_and_cleans_up(engine, lms_engine):
    report = dry_run(app_engine=engine, source_engine=lms_engine, tenant_id="nbfc_002", as_of_date=AS_OF,
                     base_dir=ROOT)
    assert report.rows["party"] == 55 and report.rows["account"] == 52
    assert report.records == {"ACCEPTED": 48, "REJECTED": 8}
    assert report.unmapped_codes == {}
    assert "account" in report.empty_columns and report.snapshot_id is None
    assert snapshot_count(engine) == 0                          # trial snapshot and its results removed
    assert "Trial snapshot deleted" in format_dry_run(report)


def test_dry_run_keep_leaves_snapshot(engine, lms_engine):
    report = dry_run(app_engine=engine, source_engine=lms_engine, tenant_id="nbfc_001", as_of_date=AS_OF,
                     base_dir=ROOT, keep=True)
    assert report.snapshot_id is not None and snapshot_count(engine) == 1


def test_dry_run_lists_untranslated_codes(engine, lms_engine, tenant_repo, tmp_path):
    mapping = tmp_path / "mapping.yaml"
    mapping.write_text(f'canonical_version: "1.0"\nextends: {(ROOT / "clients" / "nbfc_002" / "mapping.yaml").as_posix()}\n'
                       "lookups: {gender: {MALE: null}, account_type: {PERSONAL: null}}\n", encoding="utf-8")
    tenant_repo.upsert(Tenant("nbfc_002", "Demo NBFC Two", "nbfc_002", str(mapping)))
    report = dry_run(app_engine=engine, source_engine=lms_engine, tenant_id="nbfc_002", as_of_date=AS_OF,
                     base_dir=ROOT)
    assert report.unmapped_codes["party.gender"] == ["MALE"]
    assert report.unmapped_codes["account.account_type"] == ["PERSONAL"]
    assert "add them to the mapping lookups" in format_dry_run(report)
