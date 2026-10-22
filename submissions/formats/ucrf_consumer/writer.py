"""Assemble a complete UCRF consumer file: HD + records + TRLR (§1.2, §5, §13)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import Engine

from submissions.bureaus import BureauProfile
from submissions.enums import ReportingCycle
from submissions.formats.ucrf_consumer.builder import RecordBuilder, load_snapshot
from submissions.formats.ucrf_consumer.encoding import TRAILER, FormatOptions, ddmmyyyy, fixed

HEADER_LENGTH = 146
UCRF_VERSION = "12"


def header(bureau: BureauProfile, as_of_date: date, cycle: ReportingCycle | None) -> str:
    """HD segment, 146 bytes fixed (§5)."""
    hd = (fixed(bureau.header_segment_tag, 4)
          + fixed(UCRF_VERSION, 2, numeric=True)
          + fixed(bureau.member_id, 30)
          + fixed(bureau.member_short_name or "", 16)
          + fixed(str(cycle) if cycle else "", 2)
          + ddmmyyyy(as_of_date)
          + fixed("", 30) + fixed("", 1) + fixed("", 5) + fixed("", 48))
    assert len(hd) == HEADER_LENGTH, len(hd)
    return hd


@dataclass
class FileStats:
    total_records: int                  # accepted + rejected records in the snapshot
    written: int
    rejected: int
    duplicates: int
    truncated: list[str] = field(default_factory=list)


def build_file(engine: Engine, snapshot_id: int, as_of_date: date, bureau: BureauProfile,
               cycle: ReportingCycle | None = None, options: FormatOptions = FormatOptions()) -> tuple[bytes, FileStats]:
    with engine.connect() as conn:
        data = load_snapshot(conn, snapshot_id)
    builder = RecordBuilder(data, bureau, as_of_date, options)
    seen, records, duplicates = set(), [], 0
    for account_key, party_key in data.accepted:
        rec = builder.record(account_key, party_key)
        if rec in seen:                                   # byte-identical duplicate (§1.4): isolated, not written
            duplicates += 1
            continue
        seen.add(rec)
        records.append(rec)
    body = options.record_separator.join(records)
    sep = options.record_separator
    content = header(bureau, as_of_date, cycle) + sep + body + (sep if records else "") + TRAILER
    stats = FileStats(len(data.accepted) + data.rejected, len(records), data.rejected, duplicates,
                      builder.warnings.truncated)
    return content.encode("ascii"), stats
