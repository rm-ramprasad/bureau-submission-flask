from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select

from mock_data.generate_mock_data import load_into, tenant_rows
from submissions.adapters.check import check_mapping
from submissions.adapters.compiler import compile_column, compile_mapping, write_generated
from submissions.adapters.extract import ExtractError, run_extract
from submissions.adapters.mapping import ColumnExpr, MappingError, deep_merge, load_mapping
from submissions.canonical.schema import ENTITY_TABLES
from submissions.enums import ExtractStrategy, SnapshotStatus
from submissions.snapshot import SnapshotExistsError, SnapshotRepository

ROOT = Path(__file__).resolve().parent.parent
CLIENTS = ROOT / "clients"
AS_OF = date(2026, 9, 30)


def mapping(tenant: str):
    return load_mapping(CLIENTS / tenant / "mapping.yaml")


def write_yaml(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------- mapping loader
def test_deep_merge_overrides_and_removes():
    base = {"a": {"x": 1, "y": 2}, "b": [1], "c": 3}
    assert deep_merge(base, {"a": {"y": 20, "z": 3}, "b": [9], "c": None}) == {"a": {"x": 1, "y": 20, "z": 3}, "b": [9]}


def test_tenant_mappings_load_and_inherit():
    base, variant = mapping("nbfc_001"), mapping("nbfc_002")
    assert set(base.entities) == set(variant.entities) - {"extension_attribute"}
    assert base.lookups["gender"] == {"M": "2", "F": "1", "T": "3"}
    assert variant.lookups["gender"]["MALE"] == "2" and variant.lookups["gender"]["M"] == "2"   # merged
    assert variant.entities["party"].selects[0].columns["birth_date"].expr == "b.dob"
    phone1 = next(s for s in variant.entities["party_phone"].selects if s.name == "phone1")
    assert phone1.columns["phone_number"].expr == "b.primary_mobile"
    assert phone1.columns["extension"].expr == "b.phone1_extension"   # untouched columns inherited


@pytest.mark.parametrize("body,message", [
    ('canonical_version: "0.9"\n', "does not match"),
    ('canonical_version: "1.0"\nentities: {borrower: {from: x, columns: {a: b}}}\n', "unknown canonical entity"),
    ('canonical_version: "1.0"\nentities: {party: {from: x, columns: {nickname: b}}}\n', "not a column"),
    ('canonical_version: "1.0"\nentities: {party: {from: x, columns: {party_key: b}}}\n', "required canonical columns"),
    ('canonical_version: "1.0"\nentities: {party: {from: x, columns: {party_key: k, full_name: {expr: n, lookup: nope}}}}\n',
     "unknown lookup"),
    ('canonical_version: "1.0"\nentities: {party: {frm: x}}\n', "unknown keys"),
    ('canonical_version: "1.0"\nlookups: {address_category: {OFF: "03"}}\n', "Quote it"),
    ('canonical_version: "1.0"\nlookups: {flag: {ACTIVE: yes}}\n', "Quote it"),
])
def test_invalid_mappings_rejected(tmp_path, body, message):
    with pytest.raises(MappingError, match=message):
        load_mapping(write_yaml(tmp_path, "m.yaml", body))


def test_required_entities_and_circular_extends(tmp_path):
    with pytest.raises(MappingError, match="required entities"):
        load_mapping(write_yaml(tmp_path, "m.yaml", 'canonical_version: "1.0"\n'))
    write_yaml(tmp_path, "a.yaml", "extends: b.yaml\n")
    write_yaml(tmp_path, "b.yaml", "extends: a.yaml\n")
    with pytest.raises(MappingError, match="Circular"):
        load_mapping(tmp_path / "a.yaml")


def test_override_can_drop_an_inherited_select(tmp_path):
    path = write_yaml(tmp_path, "m.yaml", f'canonical_version: "1.0"\nextends: {(CLIENTS / "_base" / "mapping.yaml").as_posix()}\n'
                                          "entities: {party_identifier: {selects: {ckyc: null}}}\n")
    names = [s.name for s in load_mapping(path).entities["party_identifier"].selects]
    assert "ckyc" not in names and "pan" in names


# ---------------------------------------------------------------- compiler
def test_compile_column_lookup_and_default():
    lookups = {"g": {"M": "2", "O'Neil": "9"}}
    assert compile_column(ColumnExpr("b.sex", "g"), lookups) == \
        "CASE (b.sex) WHEN 'M' THEN '2' WHEN 'O''Neil' THEN '9' ELSE (b.sex) END"
    assert compile_column(ColumnExpr("b.t", None, "04"), lookups) == "COALESCE((b.t), '04')"


def test_compiled_sql_is_deterministic_and_schema_qualified(tmp_path):
    one = compile_mapping(mapping("nbfc_002"), "nbfc_002")
    two = compile_mapping(mapping("nbfc_002"), "nbfc_002")
    assert one.version == two.version and one.sql == two.sql
    assert "{schema}" not in "".join(one.sql.values()) and "nbfc_002.loan_ext" in one.sql["account"]
    assert one.uses_as_of("account") and not one.uses_as_of("party")
    assert compile_mapping(mapping("nbfc_001"), "nbfc_001").version != one.version
    paths = write_generated(one, tmp_path / "generated")
    assert {p.stem for p in paths} == set(one.sql)


def test_compile_rejects_bad_schema_name():
    with pytest.raises(ValueError):
        compile_mapping(mapping("nbfc_001"), "nbfc_001; DROP TABLE x")


# ---------------------------------------------------------------- extract
def _counts(engine, snapshot_id):
    with engine.connect() as conn:
        return {name: conn.execute(select(func.count()).select_from(t).where(t.c.snapshot_id == snapshot_id)).scalar()
                for name, t in ENTITY_TABLES.items()}


def _rows(engine, entity, snapshot_id, **where):
    t = ENTITY_TABLES[entity]
    q = select(t).where(t.c.snapshot_id == snapshot_id, *(t.c[k] == v for k, v in where.items()))
    with engine.connect() as conn:
        return conn.execute(q).all()


def _expected_counts(tenant_no: int, variant: bool) -> dict[str, int]:
    rows = tenant_rows(tenant_no, variant)
    b, a = rows["borrower_demographics"], rows["account_details"]
    id_cols = ["pan_number", "passport_number", "voter_id_number", "driving_license_number", "ration_card_number",
               "aadhaar_number", "ckyc_number", "gram_g_id"]
    phone_cols = ["primary_mobile" if variant else "phone1_number", "phone2_number", "phone3_number", "phone4_number"]
    return {"party": len(b), "account": len({x["loan_id"] for x in a}), "account_holder": len(a),
            "party_identifier": sum(1 for r in b for c in id_cols if r.get(c)),
            "party_phone": sum(1 for r in b for c in phone_cols if r.get(c)),
            "party_email": sum(1 for r in b for i in (1, 2, 3) if r.get(f"email{i}")),
            "party_address": sum(1 for r in b for i in (1, 2, 3) if r.get(f"address{i}_line1")),
            "account_history": 0, "extension_attribute": len(rows.get("loan_ext", []))}


@pytest.mark.parametrize("tenant_id,tenant_no,variant", [("nbfc_001", 1, False), ("nbfc_002", 2, True)])
def test_stream_extract_matches_source(engine, lms_engine, tenant_repo, tenant_id, tenant_no, variant):
    snapshots = SnapshotRepository(engine)
    result = run_extract(tenant=tenant_repo.get(tenant_id), mapping=mapping(tenant_id), submission_type="CONSUMER",
                         as_of_date=AS_OF, app_engine=engine, source_engine=lms_engine, snapshots=snapshots,
                         owner="test", batch_id="b1")
    expected = _expected_counts(tenant_no, variant)
    assert result.row_counts == expected == _counts(engine, result.snapshot_id)
    snap = snapshots.get(result.snapshot_id)
    assert (snap.status, snap.mapping_version, snap.row_counts) == (SnapshotStatus.EXTRACTED, result.mapping_version, expected)


def test_canonical_values_after_translation(engine, lms_engine, tenant_repo):
    """Both layouts land as the same UCRF codes in the canonical model."""
    for tenant_id in ("nbfc_001", "nbfc_002"):
        sid = run_extract(tenant=tenant_repo.get(tenant_id), mapping=mapping(tenant_id), submission_type="CONSUMER",
                          as_of_date=AS_OF, app_engine=engine, source_engine=lms_engine,
                          snapshots=SnapshotRepository(engine), owner="t").snapshot_id
        home_loan = _rows(engine, "account", sid, account_key="L00001")[0]
        assert (home_loan.account_type, home_loan.date_opened) == ("02", date(2019, 4, 15))
        written_off = _rows(engine, "account", sid, account_key="L00025")[0]          # scenario 25
        assert (written_off.days_past_due, written_off.credit_facility_status) == (1100, "02")
        assert written_off.written_off_total == Decimal("265000.00")
        credit_balance = _rows(engine, "account", sid, account_key="L00009")[0]       # scenario 9
        assert credit_balance.current_balance == Decimal("-2450.00")
        joint = _rows(engine, "account_holder", sid, account_key="L00002")           # scenario 2: both are Joint
        assert sorted(h.ownership_indicator for h in joint) == ["4", "4"]
        assert {r.gender for r in _rows(engine, "party", sid)} <= {"1", "2", "3", None}
        assert {r.income_basis for r in _rows(engine, "party", sid)} == {"G", "N"}
        assert {r.address_category for r in _rows(engine, "party_address", sid)} <= {"01", "02", "03", "04", "05"}
        assert {r.state_code for r in _rows(engine, "party_address", sid)} <= \
            {f"{n:02d}" for n in range(1, 38)} | {"77", "99"}


def test_extension_attributes_only_for_variant(engine, lms_engine, tenant_repo):
    sid = run_extract(tenant=tenant_repo.get("nbfc_002"), mapping=mapping("nbfc_002"), submission_type="CONSUMER",
                      as_of_date=AS_OF, app_engine=engine, source_engine=lms_engine,
                      snapshots=SnapshotRepository(engine), owner="t").snapshot_id
    attrs = _rows(engine, "extension_attribute", sid, entity_key="L00001")
    assert [(a.entity, a.attr_name) for a in attrs] == [("account", "scheme_code")]


def test_as_of_date_filters_accounts(engine, lms_engine, tenant_repo):
    sid = run_extract(tenant=tenant_repo.get("nbfc_001"), mapping=mapping("nbfc_001"), submission_type="CONSUMER",
                      as_of_date=date(2016, 12, 31), app_engine=engine, source_engine=lms_engine,
                      snapshots=SnapshotRepository(engine), owner="t").snapshot_id
    opened = {r.date_opened for r in _rows(engine, "account", sid)}
    assert opened and max(opened) <= date(2016, 12, 31)


def test_insert_select_strategy_gives_same_rows(engine, tenant_repo):
    load_into(engine, ["nbfc_001"])          # source schema on the same server as the app tables
    tenant = replace(tenant_repo.get("nbfc_001"), extract_strategy=ExtractStrategy.INSERT_SELECT)
    result = run_extract(tenant=tenant, mapping=mapping("nbfc_001"), submission_type="CONSUMER", as_of_date=AS_OF,
                         app_engine=engine, source_engine=engine, snapshots=SnapshotRepository(engine), owner="t")
    assert result.row_counts == _expected_counts(1, False) == _counts(engine, result.snapshot_id)


def test_failed_extract_cleans_up(engine, lms_engine, tenant_repo, tmp_path):
    broken = write_yaml(tmp_path, "m.yaml", f'canonical_version: "1.0"\nextends: {(CLIENTS / "nbfc_001" / "mapping.yaml").as_posix()}\n'
                                            "entities: {account: {columns: {days_past_due: a.no_such_column}}}\n")
    snapshots = SnapshotRepository(engine)
    with pytest.raises(ExtractError, match="account"):
        run_extract(tenant=tenant_repo.get("nbfc_001"), mapping=load_mapping(broken), submission_type="CONSUMER",
                    as_of_date=AS_OF, app_engine=engine, source_engine=lms_engine, snapshots=snapshots, owner="t",
                    batch_id="b9")
    snap = snapshots.find_for_batch("b9", "nbfc_001", "CONSUMER")
    assert snap.status is SnapshotStatus.FAILED and "account" in snap.error_message
    assert sum(_counts(engine, snap.snapshot_id).values()) == 0


def test_one_snapshot_per_batch_tenant_type(engine, lms_engine, tenant_repo):
    snapshots = SnapshotRepository(engine)
    first = snapshots.create("nbfc_001", "CONSUMER", AS_OF, "b1", "v", "w1")
    with pytest.raises(SnapshotExistsError) as exc:
        snapshots.create("nbfc_001", "CONSUMER", AS_OF, "b1", "v", "w2")
    assert exc.value.snapshot_id == first
    snapshots.create("nbfc_002", "CONSUMER", AS_OF, "b1", "v", "w2")   # other tenant: fine


# ---------------------------------------------------------------- check-mapping
@pytest.mark.parametrize("tenant_id", ["nbfc_001", "nbfc_002"])
def test_check_mapping_clean_for_mock_tenants(lms_engine, tenant_id):
    report = check_mapping(mapping(tenant_id), tenant_id, lms_engine, AS_OF)
    assert report.ok, report.errors
    assert not [w for w in report.warnings if "lookup" in w], report.warnings
    assert report.rows["account"] == 52


def test_check_mapping_reports_unmapped_codes_and_bad_sql(lms_engine, tmp_path):
    # nbfc_002 data read with the base mapping: long product codes and MALE/FEMALE have no lookup entry,
    # and b.birth_date / a.days_past_due do not exist in that layout
    report = check_mapping(mapping("nbfc_001"), "nbfc_002", lms_engine, AS_OF)
    assert not report.ok
    assert any(e.startswith("party:") for e in report.errors)
    assert any(e.startswith("account:") for e in report.errors)
    lookup_warnings = " ".join(w for w in report.warnings if "lookup" in w)
    assert "HOME_LOAN" in lookup_warnings and "MALE" in lookup_warnings
