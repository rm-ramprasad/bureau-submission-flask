"""ID number format rules per ID type (spec §7.1)."""

from __future__ import annotations

import re

_D = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5], [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
      [3, 4, 0, 1, 2, 8, 9, 5, 6, 7], [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
      [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3], [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
      [9, 8, 7, 6, 5, 4, 3, 2, 1, 0]]
_P = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4], [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
      [8, 9, 1, 6, 0, 4, 3, 5, 2, 7], [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
      [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8]]

PAN_RE = re.compile(r"^[A-Z]{3}[PH][A-Z][0-9]{4}[A-Z]$")
PASSPORT_RE = re.compile(r"^[A-Z]{1,2}[0-9]+$")
VOTER_RE = re.compile(r"^[A-Z]{2,3}[A-Z0-9]+$")
CKYC_RE = re.compile(r"^[A-Z0-9]{1,15}$")
GRAM_G_RE = re.compile(r"^[A-Z]{2}-[0-9]{2}-[0-9]{3}-[0-9]{3}-[0-9]{3}/[0-9]{3}[A-Za-z0-9]+$")


def verhoeff_valid(digits: str) -> bool:
    c = 0
    for i, ch in enumerate(reversed(digits)):
        c = _D[c][_P[i % 8][int(ch)]]
    return c == 0


def normalise(id_number: str) -> str:
    """Strip delimiters (spaces, hyphens, slashes...) and uppercase."""
    return re.sub(r"[^A-Za-z0-9]", "", id_number or "").upper()


def id_number_problem(id_type: str, id_number: str | None) -> str | None:
    """Reason the ID number is invalid for its type, or None. Types 04/05 are not validated."""
    raw = (id_number or "").strip()
    n = normalise(raw)
    if not n:
        return "empty ID number"
    if id_type == "01":
        return None if PAN_RE.match(n) else "PAN must be 5 letters, 4 digits, 1 letter with 4th letter P or H"
    if id_type == "02":
        return None if 7 <= len(n) <= 10 and PASSPORT_RE.match(n) else \
            "passport must be 7-10 chars, a letter in position 1 or 2, digits after"
    if id_type == "03":
        return None if len(n) >= 9 and VOTER_RE.match(n) else "voter ID must be >= 9 chars starting with 2-3 letters"
    if id_type == "06":
        if not (len(n) == 12 and n.isdigit()):
            return "Aadhaar must be 12 digits"
        return None if verhoeff_valid(n) else "Aadhaar check digit (Verhoeff) is wrong"
    if id_type == "09":
        return None if CKYC_RE.match(n) else "CKYC must be up to 15 letters/digits"
    if id_type == "10":
        return None if GRAM_G_RE.match(raw.replace(" ", "")) else "G RAM G must look like SS-DD-BBB-GGG-VVV/HHHSurname"
    return None
