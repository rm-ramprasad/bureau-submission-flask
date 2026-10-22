"""PIN code rules (spec §10.2, Appendix C)."""

from __future__ import annotations

import re

from submissions.canonical.definition import canonical_model

FOREIGN = "77"


def pin_ranges() -> dict[str, tuple[int, int]]:
    return {c.code: (c.pin_min, c.pin_max) for c in canonical_model().code_lists["state_code"].codes
            if c.pin_min is not None}


def pin_digits(pin: str | None) -> str:
    return re.sub(r"\D", "", pin or "")


def pin_format_problem(pin: str | None, state_code: str | None) -> str | None:
    if state_code == FOREIGN:
        return None                                   # foreign PINs: not validated beyond length/characters
    d = pin_digits(pin)
    if len(d) != 6:
        return "PIN must have exactly 6 digits"
    if d.endswith("000"):
        return "last three PIN digits are 000"
    return None


def pin_state_problem(pin: str | None, state_code: str | None,
                      ranges: dict[str, tuple[int, int]] | None = None) -> str | None:
    """PIN prefix outside the state's Appendix C range. Only checked when the PIN format is valid."""
    ranges = ranges if ranges is not None else pin_ranges()
    if state_code not in ranges or pin_format_problem(pin, state_code):
        return None
    lo, hi = ranges[state_code]
    prefix = int(pin_digits(pin)[:2])
    return None if lo <= prefix <= hi else f"PIN prefix {prefix:02d} outside {lo}-{hi} for state {state_code}"
