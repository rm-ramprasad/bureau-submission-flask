from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from submissions.adapters.extract import run_extract
from submissions.adapters.mapping import load_mapping
from submissions.bureaus.seed import seed as seed_bureaus
from submissions.formats.ucrf_consumer.encoding import (
    EncodingError, FormatOptions, amount, fit_words, fixed, parse_records, printable, rate, signed_amount, tlv,
)
from submissions.formats.ucrf_consumer.writer import HEADER_LENGTH, build_file, header
from submissions.snapshot import SnapshotRepository
from submissions.validation.engine import validate_snapshot
from submissions.validation.rules import load_rule_set

CLIENTS = Path(__file__).resolve().parent.parent / "clients"
AS_OF = date(2026, 9, 30)


# ---------------------------------------------------------------- encoding units
def test_tlv_and_segment_arithmetic():
    assert tlv("01", "HAREN PATEL") == "0111HAREN PATEL"                     # spec §2.2 example
    with pytest.raises(EncodingError):
        tlv("01", "x" * 100)


def test_fixed_width_padding():
    assert fixed("CONS", 6) == "CONS  " and fixed("12", 4, numeric=True) == "0012"
    with pytest.raises(EncodingError):
        fixed("toolong", 3)


@pytest.mark.parametrize("value,expected", [(Decimal("-2450.50"), "2451-"), (Decimal("-2450.49"), "2450-"),
                                            (Decimal("471875.18"), "471875"), (0, "0")])
def test_signed_amount(value, expected):
    assert signed_amount(value) == expected


def test_amount_and_rate():
    assert amount(Decimal("15000.60")) == "15001"
    with pytest.raises(EncodingError):
        amount(-1)
    assert rate(Decimal("14.0")) == "14.000" and rate(Decimal("9.15678")) == "9.156"


def test_printable_and_fitting():
    assert printable("José  Kumar\t") == "Jose Kumar"
    parts, overflow = fit_words("Ramesh Kumar S/O Suresh Kumar Venkataraman", 26, 5)
    assert parts == ["Ramesh Kumar S/O Suresh", "Kumar Venkataraman"] and not overflow
    assert all(len(p) <= 26 for p in parts)
    parts, overflow = fit_words("A" * 30, 26, 5)
    assert parts == ["A" * 26] and overflow


def test_header_is_146_bytes(bureau_repo):
    seed_bureaus(bureau_repo, ["nbfc_001"])
    hd = header(bureau_repo.get("nbfc_001", "CIBIL", "CONSUMER"), AS_OF, None)
    assert len(hd) == HEADER_LENGTH
    assert hd.startswith("TUDF12CIB0000001") and hd[52:54] == "  " and hd[54:62] == "30092026"


# ---------------------------------------------------------------- full file from the mock snapshot
@pytest.fixture
def validated(engine, lms_engine, tenant_repo, bureau_repo):
    seed_bureaus(bureau_repo, ["nbfc_001", "nbfc_002"])

    def make(tenant_id):
        snapshots = SnapshotRepository(engine)
        sid = run_extract(tenant=tenant_repo.get(tenant_id), mapping=load_mapping(CLIENTS / tenant_id / "mapping.yaml"),
                          submission_type="CONSUMER", as_of_date=AS_OF, app_engine=engine, source_engine=lms_engine,
                          snapshots=snapshots, owner="t").snapshot_id
        validate_snapshot(engine, snapshots, sid, load_rule_set(engine, tenant_id, "CONSUMER"))
        return sid
    return make


def parse(content: bytes, options=FormatOptions()):
    text = content.decode("ascii")
    assert text.endswith("TRLR") and text.count("TRLR") == 1
    return text[:HEADER_LENGTH], parse_records(text[HEADER_LENGTH:-4], options)


def tl(record):
    return next(s for s in record if s.tag == "TL").fields


def by_account(records):
    out = {}
    for r in records:
        out.setdefault(tl(r)["03"], []).append(r)
    return out


@pytest.mark.parametrize("tenant_id", ["nbfc_001", "nbfc_002"])
def test_file_structure_and_counts(engine, bureau_repo, validated, tenant_id):
    sid = validated(tenant_id)
    content, stats = build_file(engine, sid, AS_OF, bureau_repo.get(tenant_id, "CIBIL", "CONSUMER"))
    hd, records = parse(content)
    assert (stats.total_records, stats.written, stats.rejected, stats.duplicates) == (56, 48, 8, 0)
    assert len(records) == 48
    order = ["PN", "ID", "PT", "EC", "PA", "TL", "TH", "ES"]
    for rec in records:
        tags = [s.tag for s in rec]
        assert tags == sorted(tags, key=order.index) and tags[0] == "PN" and tags[-1] == "ES"
        assert tags.count("TL") == 1 and tags.count("PA") >= 1
        for seg_tag, prefix in (("ID", "I"), ("PT", "T"), ("EC", "C"), ("PA", "A")):
            seqs = [s.seq for s in rec if s.tag == seg_tag]
            assert seqs == [f"{prefix}{i:02d}" for i in range(1, len(seqs) + 1)]
        assert tl(rec)["01"] == "CIB0000001" if tenant_id == "nbfc_001" else "CIB0000002"
        assert all(len(v) <= 26 for k, v in next(s for s in rec if s.tag == "PN").fields.items() if k <= "05")


def test_spec_reshaping_and_validation_drops(engine, bureau_repo, validated):
    sid = validated("nbfc_001")
    content, _ = build_file(engine, sid, AS_OF, bureau_repo.get("nbfc_001", "CIBIL", "CONSUMER"))
    _, records = parse(content)
    assert len(by_account(records)) == 52 - 8            # 8 loans have their only record rejected

    tls = {tl(r)["03"]: tl(r) for r in records}
    written_off = next(t for t in tls.values() if t.get("22") == "02")            # scenario 25
    assert written_off["15"] == "900" and written_off["41"] == "265000" and written_off["21"] == "01"
    credit_balance = next(t for t in tls.values() if t["13"].endswith("-"))        # scenario 9
    assert credit_balance["13"] == "2450-" and credit_balance["04"] == "10"
    assert not any("26" in t and "15" in t for t in tls.values())                 # asset class dropped when DPD present
    assert any(t.get("26") == "05" and "15" not in t for t in tls.values())       # scenario 31 keeps SMA
    emi_on_card = [t for t in tls.values() if t["04"] == "10" and "40" in t]
    assert emi_on_card == []                                                       # scenario 50: TL.40 dropped
    joint = [r for r in records if tl(r).get("05") == "4"]
    assert len(joint) == 2 and tl(joint[0])["03"] == tl(joint[1])["03"]            # scenario 2: both holders Joint


def test_rejected_segments_are_left_out(engine, bureau_repo, validated):
    sid = validated("nbfc_001")
    content, _ = build_file(engine, sid, AS_OF, bureau_repo.get("nbfc_001", "CIBIL", "CONSUMER"))
    _, records = parse(content)
    ids = [s.fields for r in records for s in r if s.tag == "ID"]
    assert all(f["01"] != "06" or len(f["02"]) == 12 for f in ids)
    from submissions.validation.checks.identifiers import id_number_problem
    assert not [f for f in ids if id_number_problem(f["01"], f["02"])]           # scenario 43's bad Aadhaar is gone
    phones = [s.fields["01"] for r in records for s in r if s.tag == "PT"]
    assert "9876543210" not in phones and "1111111" not in phones                 # scenario 44
    emails = [s.fields["01"] for r in records for s in r if s.tag == "EC"]
    assert "ramesh@@gmail" not in emails                                           # scenario 45
    for r in records:                                                              # PIN digits only, 6 for India
        for s in r:
            if s.tag == "PA" and s.fields.get("06") != "77":
                assert len(s.fields["07"]) == 6 and s.fields["07"].isdigit()


def test_bureau_files_differ_only_in_identity(engine, bureau_repo, validated):
    sid = validated("nbfc_001")
    cibil, _ = build_file(engine, sid, AS_OF, bureau_repo.get("nbfc_001", "CIBIL", "CONSUMER"))
    experian, _ = build_file(engine, sid, AS_OF, bureau_repo.get("nbfc_001", "EXPERIAN", "CONSUMER"))
    normalise = lambda b, tag, member: b.decode().replace(tag, "????", 1).replace(member, "##########")  # noqa: E731
    assert normalise(cibil, "TUDF", "CIB0000001") == normalise(experian, "CONS", "EXP0000001")


def test_default_style_no_prefix_no_separator(engine, bureau_repo, validated):
    sid = validated("nbfc_001")
    content, stats = build_file(engine, sid, AS_OF, bureau_repo.get("nbfc_001", "CIBIL", "CONSUMER"))
    text = content.decode()
    assert text[HEADER_LENGTH:HEADER_LENGTH + 5] == "PNN01"
    assert "\n" not in text and "\r" not in text and "TLT001" in text and "PN03N01" not in text


def test_alternative_style_prefix_and_separator(engine, bureau_repo, validated):
    sid = validated("nbfc_001")
    options = FormatOptions(segment_tag_tlv=True, record_separator="\n")
    content, stats = build_file(engine, sid, AS_OF, bureau_repo.get("nbfc_001", "CIBIL", "CONSUMER"), options=options)
    text = content.decode()
    assert text.split("\n")[1].startswith("PN03N01") and len(text.split("\n")) == stats.written + 2
    _, records = parse(content, options)
    assert len(records) == stats.written
