"""Low-level UCRF encoding: printable ASCII, TLV fields, fixed-width fields, amounts, dates, text fitting.

Spec references are to UCRF_Consumer_v3.74_Spec.md.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

SEGMENT_TAGS = ("PN", "ID", "PT", "EC", "PA", "TL", "TH")
END_OF_SUBJECT = "ES02**"
TRAILER = "TRLR"
_SPACES_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class FormatOptions:
    """Encoding choices the spec leaves open (§14 #16) or that a CIC may set.

    Decided 21-10-2026: segment tags WITHOUT a length prefix ("PNN01"), no separator / line break between records.
    Still open for one more review against a CIC sample file (OVERALL_PLAN H.6).
    """
    segment_tag_tlv: bool = False    # False: "PNN01" (decided). True: "PN03N01" (spec byte arithmetic)
    record_separator: str = ""       # decided: none (no line breaks)
    max_history_segments: int = 47  # §14 #3: 47 (segment text) vs 35 (overview)


class EncodingError(ValueError):
    pass


# ---------------------------------------------------------------- text
def printable(value) -> str:
    """Printable ASCII 32-126 only (§3.1 P): transliterate accents, drop anything else, collapse whitespace."""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(ch for ch in text if 32 <= ord(ch) <= 126 or ch.isspace())
    return _SPACES_RE.sub(" ", text).strip()


def fit_words(text: str, width: int, max_parts: int) -> tuple[list[str], bool]:
    """Pack words into at most `max_parts` chunks of `width` chars without splitting words (§6.1 #4, §10.1).
    Returns (parts, overflowed). A single word longer than `width` is cut (it cannot be moved)."""
    parts, current, overflowed = [], "", False
    for word in printable(text).split(" "):
        if not word:
            continue
        if len(word) > width:
            word, overflowed = word[:width], True
        candidate = f"{current} {word}" if current else word
        if len(candidate) <= width:
            current = candidate
            continue
        parts.append(current)
        current = word
    if current:
        parts.append(current)
    if len(parts) > max_parts:
        parts, overflowed = parts[:max_parts], True
    return parts, overflowed


# ---------------------------------------------------------------- values
def ddmmyyyy(d: date) -> str:
    return d.strftime("%d%m%Y")


def whole_rupees(amount) -> int:
    return int(Decimal(str(amount)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def amount(value) -> str:
    """Unsigned whole-rupee amount (§3.5). Negative values are an error (validation should have caught them)."""
    n = whole_rupees(value)
    if n < 0:
        raise EncodingError(f"negative amount {value} for an unsigned field")
    return str(n)


def signed_amount(value) -> str:
    """Whole rupees with a trailing '-' for credit balances (§2.3, TL.13): -2450.5 -> '2451-'."""
    n = whole_rupees(value)
    return f"{-n}-" if n < 0 else str(n)


def rate(value) -> str:
    """Rate of interest: digits '.' digits, first 3 decimals kept, rest truncated (TL.38)."""
    return str(Decimal(str(value)).quantize(Decimal("0.001"), rounding=ROUND_DOWN))


# ---------------------------------------------------------------- fields and segments
def tlv(tag: str, value: str) -> str:
    """Variable-length field: 2-char tag + 2-digit length + value (§2.2)."""
    if len(value) > 99:
        raise EncodingError(f"field {tag} longer than 99 bytes")
    return f"{tag}{len(value):02d}{value}"


def segment_header(tag: str, value: str, options: FormatOptions) -> str:
    return tlv(tag, value) if options.segment_tag_tlv else f"{tag}{value}"


def fixed(value: str, width: int, numeric: bool = False) -> str:
    """Fixed-width field (§2.3): text left-justified space-filled, numbers right-justified zero-filled."""
    value = printable(value) if not numeric else str(value)
    if len(value) > width:
        raise EncodingError(f"value {value!r} longer than {width}")
    return value.rjust(width, "0") if numeric else value.ljust(width)


# ---------------------------------------------------------------- parsing (for checks and tests)
@dataclass(frozen=True)
class ParsedSegment:
    tag: str                       # PN, ID, ..., ES
    seq: str                       # N01, I01, T001, ...
    fields: dict[str, str]


def parse_records(body: str, options: FormatOptions = FormatOptions()) -> list[list[ParsedSegment]]:
    """Parse everything between HD and TRLR into records of segments."""
    records, current, i = [], [], 0
    while i < len(body):
        if options.record_separator and body.startswith(options.record_separator, i):
            i += len(options.record_separator)
            continue
        tag = body[i:i + 2]
        if body.startswith(END_OF_SUBJECT, i):
            current.append(ParsedSegment("ES", "", {}))
            records.append(current)
            current, i = [], i + len(END_OF_SUBJECT)
            continue
        if tag not in SEGMENT_TAGS:
            raise EncodingError(f"unexpected bytes at {i}: {body[i:i + 10]!r}")
        i += 2
        seq_len = (4 if tag == "TL" else 3)
        if options.segment_tag_tlv:
            seq_len = int(body[i:i + 2])
            i += 2
        seq, i = body[i:i + seq_len], i + seq_len
        fields = {}
        while body[i:i + 2].isdigit():
            ftag, flen = body[i:i + 2], int(body[i + 2:i + 4])
            fields[ftag] = body[i + 4:i + 4 + flen]
            i += 4 + flen
        current.append(ParsedSegment(tag, seq, fields))
    if current:
        raise EncodingError("file ends inside a record (no ES segment)")
    return records
