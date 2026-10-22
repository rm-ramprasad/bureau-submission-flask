"""Domain codes shared across modules. Stored in the DB as their string values."""

from __future__ import annotations

from enum import StrEnum


class SubmissionType(StrEnum):
    CONSUMER = "CONSUMER"
    COMMERCIAL = "COMMERCIAL"
    NESL = "NESL"


class RunMode(StrEnum):
    VALIDATE = "VALIDATE"    # extract + validate, findings only (bureau-independent)
    GENERATE = "GENERATE"    # extract + validate + file for one bureau


class ExtractStrategy(StrEnum):
    """How a tenant's source schema is copied into the canonical snapshot (DESIGN_PLAN.md §4.2)."""
    STREAM = "stream"                  # source on a separate server/replica: stream rows across
    INSERT_SELECT = "insert_select"    # source on the same MySQL cluster: INSERT ... SELECT


class Outcome(StrEnum):
    """Outcome of a validation finding, lightest first (spec §0.1, plus WARN)."""
    WARN = "WARN"                      # reported only
    REJECT_FIELD = "REJECT_FIELD"      # field dropped (or defaulted)
    REJECT_SEGMENT = "REJECT_SEGMENT"  # segment instance dropped
    REJECT_RECORD = "REJECT_RECORD"    # whole record (account x holder) excluded
    REJECT_FILE = "REJECT_FILE"        # nothing from the snapshot may be submitted


class RecordOutcome(StrEnum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


class SnapshotStatus(StrEnum):
    """Lifecycle of one canonical extract snapshot."""
    BUILDING = "BUILDING"      # extract (and validation) in progress, owned by one worker
    EXTRACTED = "EXTRACTED"    # canonical rows loaded, not yet validated
    VALIDATED = "VALIDATED"    # findings and record outcomes written; ready to format
    FAILED = "FAILED"
    PURGED = "PURGED"          # rows removed by retention; metadata kept


class AdjustmentKind(StrEnum):
    """What a user-captured adjustment does (applied while the snapshot is built, before validation)."""
    EXCLUDE_ACCOUNT = "EXCLUDE_ACCOUNT"      # one loan, all its holders
    EXCLUDE_CUSTOMER = "EXCLUDE_CUSTOMER"    # every loan the customer holds, whole
    OVERRIDE = "OVERRIDE"                    # replace one c_account value of one loan


class OverrideField(StrEnum):
    """c_account columns users may override, with their UI labels."""
    DAYS_PAST_DUE = "days_past_due"
    CURRENT_BALANCE = "current_balance"
    AMOUNT_OVERDUE = "amount_overdue"
    SETTLEMENT_AMOUNT = "settlement_amount"

    @property
    def label(self) -> str:
        return {"days_past_due": "OD days", "current_balance": "Ledger balance", "amount_overdue": "OD amount",
                "settlement_amount": "Settlement amount"}[self.value]


class BureauCode(StrEnum):
    EXPERIAN = "EXPERIAN"
    CIBIL = "CIBIL"
    CRIF_HIGHMARK = "CRIF_HIGHMARK"
    EQUIFAX = "EQUIFAX"


class ReportingCycle(StrEnum):
    """UCRF header HD.53."""
    DAILY = "DL"
    WEEKLY_1 = "W1"
    WEEKLY_2 = "W2"
    WEEKLY_3 = "W3"
    MONTH_END = "ME"
    DATA_CORRECTION = "DC"
    AD_HOC = "AH"
    REJECTION_RESUBMISSION = "RR"


class RequestStatus(StrEnum):
    """Status of one row (one type x bureau file)."""
    DRAFT = "DRAFT"            # created by the New request form; exceptions are captured, then it is submitted
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class BatchStatus(StrEnum):
    """Derived status of all rows created by one click. Never stored."""
    DRAFT = "DRAFT"
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
