"""Bureau profile: the only values that differ between bureau files."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from submissions.enums import BureauCode, SubmissionType

# Placeholders available in file_name_pattern
PATTERN_FIELDS = "{member_id} {bureau} {as_of:%d%m%Y} {created:%d%m%Y} {created:%H%M%S}"


class InvalidFileNamePatternError(ValueError):
    pass


@dataclass(frozen=True)
class BureauProfile:
    tenant_id: str
    bureau_code: BureauCode
    submission_type: SubmissionType
    display_name: str
    member_id: str
    member_short_name: str | None
    header_segment_tag: str
    file_name_pattern: str
    is_active: bool = True

    def file_name(self, created: datetime, as_of: date) -> str:
        try:
            return self.file_name_pattern.format(member_id=self.member_id, bureau=str(self.bureau_code),
                                                 created=created, as_of=as_of)
        except (KeyError, IndexError, ValueError) as exc:
            raise InvalidFileNamePatternError(
                f"Bad file_name_pattern for {self.tenant_id}/{self.bureau_code}/{self.submission_type}: "
                f"{self.file_name_pattern!r} ({exc}). Allowed: {PATTERN_FIELDS}") from exc

    @classmethod
    def from_row(cls, row) -> "BureauProfile":
        m = dict(row._mapping)
        m.pop("updated_at", None)
        m["bureau_code"] = BureauCode(m["bureau_code"])
        m["submission_type"] = SubmissionType(m["submission_type"])
        m["is_active"] = bool(m["is_active"])
        return cls(**m)
