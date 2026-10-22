"""Build UCRF consumer records from a validated canonical snapshot.

Only ACCEPTED records (record_outcome) are written. Validation findings decide what is left out:
REJECT_FIELD drops that field, REJECT_SEGMENT drops that segment instance; the builder makes no rule decisions
of its own beyond the spec's mandatory reshaping (name/address fitting, DPD cap, rounding, caps per segment).
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import Connection, select

from submissions.bureaus import BureauProfile
from submissions.canonical.schema import ENTITY_TABLES
from submissions.enums import Outcome, RecordOutcome
from submissions.formats.ucrf_consumer.encoding import (
    END_OF_SUBJECT, FormatOptions, amount, ddmmyyyy, fit_words, printable, rate, segment_header, signed_amount, tlv,
)
from submissions.validation.checks.identifiers import normalise
from submissions.validation.checks.phones import valid_numbers
from submissions.validation.tables import record_outcome, validation_finding

MAX_SEGMENTS = {"party_identifier": 8, "party_phone": 10, "party_email": 10, "party_address": 5}
DPD_CAP = 900
FOREIGN = "77"
ASSET_TO_TH = {"01": "STD", "02": "SUB", "03": "DBT", "04": "LSS", "05": "SMA"}
CARD_TYPES = {"10", "16", "31", "35"}

# canonical account column -> TL field tag (§11)
TL_FIELDS = [
    ("account_number", "03"), ("account_type", "04"), ("date_opened", "08"), ("date_last_payment", "09"),
    ("date_closed", "10"), ("date_reported", "11"), ("high_credit_amount", "12"), ("current_balance", "13"),
    ("amount_overdue", "14"), ("days_past_due", "15"), ("old_member_code", "16"), ("old_member_short_name", "17"),
    ("old_account_number", "18"), ("old_account_type", "19"), ("old_ownership_indicator", "20"),
    ("suit_filed_status", "21"), ("credit_facility_status", "22"), ("asset_classification", "26"),
    ("collateral_value", "34"), ("collateral_type", "35"), ("credit_limit", "36"), ("cash_limit", "37"),
    ("rate_of_interest", "38"), ("repayment_tenure", "39"), ("emi_amount", "40"), ("written_off_total", "41"),
    ("written_off_principal", "42"), ("settlement_amount", "43"), ("payment_frequency", "44"),
    ("actual_payment_amount", "45"),
]
TL_PARTY_FIELDS = [("occupation", "46"), ("income", "47"), ("income_basis", "48"), ("income_period", "49")]
MAX_LEN = {"account_number": 25, "old_account_number": 25, "old_member_code": 10, "old_member_short_name": 16}


@dataclass
class Drops:
    """Field and segment drops from validation findings, with their account restriction (None = all records)."""
    fields: dict[tuple[str, int, str], list[str | None]] = field(default_factory=lambda: defaultdict(list))
    segments: dict[tuple[str, int], list[str | None]] = field(default_factory=lambda: defaultdict(list))

    def field_dropped(self, entity: str, row_id: int, column: str, account_key: str) -> bool:
        return any(a is None or a == account_key for a in self.fields.get((entity, row_id, column), []))

    def segment_dropped(self, entity: str, row_id: int, account_key: str) -> bool:
        return any(a is None or a == account_key for a in self.segments.get((entity, row_id), []))


@dataclass
class SnapshotData:
    parties: dict[str, object]
    accounts: dict[str, object]
    holders: dict[tuple[str, str], object]
    children: dict[str, dict[str, list]]          # entity -> key -> rows (party_key or account_key)
    accepted: list[tuple[str, str]]
    rejected: int
    drops: Drops


def load_snapshot(conn: Connection, snapshot_id: int) -> SnapshotData:
    def rows(entity):
        t = ENTITY_TABLES[entity]
        return conn.execute(select(t).where(t.c.snapshot_id == snapshot_id).order_by(t.c.row_id)).all()

    children = {}
    for entity, key in (("party_identifier", "party_key"), ("party_phone", "party_key"), ("party_email", "party_key"),
                        ("party_address", "party_key"), ("account_history", "account_key")):
        grouped = defaultdict(list)
        for r in rows(entity):
            grouped[getattr(r, key)].append(r)
        children[entity] = grouped

    drops = Drops()
    f = validation_finding.c
    for x in conn.execute(select(f.entity, f.row_id, f.field, f.account_key, f.outcome).where(
            f.snapshot_id == snapshot_id, f.outcome.in_([str(Outcome.REJECT_FIELD), str(Outcome.REJECT_SEGMENT)]),
            f.row_id.is_not(None))):
        if x.outcome == Outcome.REJECT_FIELD and x.field:
            drops.fields[(x.entity, x.row_id, x.field)].append(x.account_key)
        elif x.outcome == Outcome.REJECT_SEGMENT:
            drops.segments[(x.entity, x.row_id)].append(x.account_key)

    o = record_outcome.c
    outcomes = conn.execute(select(o.account_key, o.party_key, o.outcome).where(o.snapshot_id == snapshot_id)
                            .order_by(o.account_key, o.party_key)).all()
    return SnapshotData(
        parties={r.party_key: r for r in rows("party")},
        accounts={r.account_key: r for r in rows("account")},
        holders={(r.account_key, r.party_key): r for r in rows("account_holder")},
        children=children,
        accepted=[(x.account_key, x.party_key) for x in outcomes if x.outcome == RecordOutcome.ACCEPTED],
        rejected=sum(1 for x in outcomes if x.outcome == RecordOutcome.REJECTED),
        drops=drops,
    )


@dataclass
class BuildWarnings:
    truncated: list[str] = field(default_factory=list)   # e.g. name/address too long to fit without cutting


class RecordBuilder:
    def __init__(self, data: SnapshotData, bureau: BureauProfile, as_of_date: date, options: FormatOptions):
        self.data, self.bureau, self.as_of, self.options = data, bureau, as_of_date, options
        self.warnings = BuildWarnings()

    # ---------------------------------------------------------------- helpers
    def _keep(self, entity: str, row, column: str, account_key: str) -> bool:
        value = getattr(row, column)
        return value is not None and str(value).strip() != "" and not self.data.drops.field_dropped(
            entity, row.row_id, column, account_key)

    def _children(self, entity: str, key: str, account_key: str) -> list:
        rows = [r for r in self.data.children[entity].get(key, [])
                if not self.data.drops.segment_dropped(entity, r.row_id, account_key)]
        if "priority" in ENTITY_TABLES[entity].c:
            rows.sort(key=lambda r: (r.priority if r.priority is not None else 999, r.row_id))
        return rows[:MAX_SEGMENTS.get(entity, len(rows))]

    @staticmethod
    def _text(value, max_len: int) -> str:
        return printable(value)[:max_len]

    # ---------------------------------------------------------------- segments
    def _pn(self, party, account_key: str) -> str:
        parts, overflow = fit_words(party.full_name or "", 26, 5)
        if overflow:
            self.warnings.truncated.append(f"name of {party.party_key}")
        out = segment_header("PN", "N01", self.options)
        out += "".join(tlv(f"{i:02d}", p) for i, p in enumerate(parts, 1))
        if self._keep("party", party, "birth_date", account_key):
            out += tlv("07", ddmmyyyy(party.birth_date))
        if self._keep("party", party, "gender", account_key):
            out += tlv("08", party.gender)
        return out

    def _id(self, seq: int, row, account_key: str) -> str:
        raw = printable(row.id_number)
        number = raw.replace(" ", "") if row.id_type == "10" else (
            normalise(raw) if row.id_type in ("01", "02", "03", "06", "09") else raw)
        out = segment_header("ID", f"I{seq:02d}", self.options) + tlv("01", row.id_type) + tlv("02", number[:30])
        if self._keep("party_identifier", row, "issue_date", account_key):
            out += tlv("03", ddmmyyyy(row.issue_date))
        if self._keep("party_identifier", row, "expiry_date", account_key):
            out += tlv("04", ddmmyyyy(row.expiry_date))
        return out

    def _pt_fields(self, row, account_key: str) -> str | None:
        """PT fields without the segment header (numbered after unusable numbers are removed)."""
        numbers = valid_numbers(row.phone_number, row.phone_type)
        if not numbers:
            return None
        out = tlv("01", ",".join(numbers)[:20])
        if self._keep("party_phone", row, "extension", account_key):
            out += tlv("02", re.sub(r"[^A-Za-z0-9]", "", row.extension)[:10])
        if self._keep("party_phone", row, "phone_type", account_key):
            out += tlv("03", row.phone_type)
        return out

    def _ec(self, seq: int, row) -> str:
        return segment_header("EC", f"C{seq:02d}", self.options) + tlv("01", printable(row.email).lower()[:70])

    def _pa(self, seq: int, row, account_key: str) -> str:
        lines = [printable(getattr(row, f"address_line_{i}") or "") for i in range(1, 6)]
        lines = [ln for ln in lines if ln]
        if any(len(ln) > 40 for ln in lines):
            lines, overflow = fit_words(" ".join(lines), 40, 5)
            if overflow:
                self.warnings.truncated.append(f"address row {row.row_id} of {row.party_key}")
        out = segment_header("PA", f"A{seq:02d}", self.options)
        out += "".join(tlv(f"{i:02d}", ln) for i, ln in enumerate(lines, 1))
        if self._keep("party_address", row, "state_code", account_key):
            out += tlv("06", row.state_code)
        if self._keep("party_address", row, "pin_code", account_key):
            pin = printable(row.pin_code).replace(" ", "") if row.state_code == FOREIGN else re.sub(r"\D", "", row.pin_code)
            out += tlv("07", pin[:7])
        category = row.address_category if self._keep("party_address", row, "address_category", account_key) else "04"
        out += tlv("08", category or "04")
        if self._keep("party_address", row, "residence_code", account_key):
            out += tlv("09", row.residence_code)
        return out

    def _tl_value(self, column: str, value) -> str:
        if column == "current_balance":
            return signed_amount(value)
        if column == "rate_of_interest":
            return rate(value)
        if column == "days_past_due":
            return str(min(int(value), DPD_CAP))
        if column in ("income", "high_credit_amount", "amount_overdue", "collateral_value", "credit_limit",
                      "cash_limit", "emi_amount", "written_off_total", "written_off_principal", "settlement_amount",
                      "actual_payment_amount"):
            return amount(value)
        if isinstance(value, date):
            return ddmmyyyy(value)
        return self._text(value, MAX_LEN.get(column, 99))

    def _tl(self, account, holder, party) -> str:
        key = account.account_key
        out = segment_header("TL", "T001", self.options)
        out += tlv("01", self.bureau.member_id[:10])
        if self.bureau.member_short_name:
            out += tlv("02", self._text(self.bureau.member_short_name, 16))
        values = {}
        for column, tag in TL_FIELDS:
            if self._keep("account", account, column, key):
                values[tag] = self._tl_value(column, getattr(account, column))
        if "11" not in values:
            values["11"] = ddmmyyyy(self.as_of)                         # TL.11 defaults to the as-of date
        if self._keep("account_holder", holder, "ownership_indicator", key):
            values["05"] = holder.ownership_indicator
        for column, tag in TL_PARTY_FIELDS:
            if self._keep("party", party, column, key):
                values[tag] = self._tl_value(column, getattr(party, column))
        return out + "".join(tlv(tag, values[tag]) for tag in sorted(values))

    def _th(self, seq: int, row, account) -> str:
        out = segment_header("TH", f"H{seq:02d}", self.options)
        out += tlv("01", ddmmyyyy(row.history_month.replace(day=1)))
        if row.days_past_due is not None:
            out += tlv("02", f"{min(row.days_past_due, DPD_CAP):03d}")
        elif row.asset_classification in ASSET_TO_TH:
            out += tlv("02", ASSET_TO_TH[row.asset_classification])
        if row.amount_overdue is not None:
            out += tlv("03", amount(row.amount_overdue))
        if row.high_credit_amount is not None:
            out += tlv("04", amount(row.high_credit_amount))
        if account.account_type == "10":
            if row.credit_limit is not None:
                out += tlv("05", amount(row.credit_limit))
            if row.cash_limit is not None:
                out += tlv("06", amount(row.cash_limit))
        out += tlv("07", signed_amount(row.current_balance))
        if row.date_last_payment is not None:
            out += tlv("08", ddmmyyyy(row.date_last_payment))
        if row.actual_payment_amount is not None and row.actual_payment_amount > 0:
            out += tlv("09", amount(row.actual_payment_amount))
        return out

    # ---------------------------------------------------------------- record
    def record(self, account_key: str, party_key: str) -> str:
        account, party = self.data.accounts[account_key], self.data.parties[party_key]
        holder = self.data.holders[(account_key, party_key)]
        segs = [self._pn(party, account_key)]
        segs += [self._id(i, r, account_key)
                 for i, r in enumerate(self._children("party_identifier", party_key, account_key), 1)]
        phones = [p for p in (self._pt_fields(r, account_key)
                              for r in self._children("party_phone", party_key, account_key)) if p]
        segs += [segment_header("PT", f"T{i:02d}", self.options) + p for i, p in enumerate(phones, 1)]
        segs += [self._ec(i, r) for i, r in enumerate(self._children("party_email", party_key, account_key), 1)]
        segs += [self._pa(i, r, account_key)
                 for i, r in enumerate(self._children("party_address", party_key, account_key), 1)]
        segs.append(self._tl(account, holder, party))
        history = sorted(self.data.children["account_history"].get(account_key, []), key=lambda r: r.history_month,
                         reverse=True)[: self.options.max_history_segments]
        segs += [self._th(i, r, account) for i, r in enumerate(history, 1)]
        segs.append(END_OF_SUBJECT)
        return "".join(segs)
