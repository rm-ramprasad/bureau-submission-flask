"""Rule parameters: tunable values the rules use (cut-off dates, minimum lengths, ID patterns), as data.

Defaults are declared in the rule pack (`parameters:`); an NBFC can override them (table validation_parameter,
Rules & policies page). SQL rules read them as bind parameters (:name), Python checks via the rule context, and
rule messages as {name}. Values fixed by the UCRF spec are not parameters.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from functools import cache

import yaml
from sqlalchemy import Engine, delete, select, update

from submissions.db import utcnow
from submissions.enums import SubmissionType
from submissions.validation.tables import validation_parameter as t

MAX_REGEX_LENGTH = 300


class ParameterError(ValueError):
    """Invalid value; the message is shown to the user."""


@dataclass(frozen=True)
class Parameter:
    name: str
    type: str                     # date / int / regex
    default: object
    label: str
    description: str
    used_by: tuple[str, ...]
    min: int | None = None
    max: int | None = None

    def parse(self, raw) -> object:
        """Typed value from text (form or table), validated."""
        text = str(raw).strip() if raw is not None else ""
        if not text:
            raise ParameterError(f"{self.label}: enter a value")
        if self.type == "date":
            try:
                value = date.fromisoformat(text)
            except ValueError:
                raise ParameterError(f"{self.label}: enter a date as YYYY-MM-DD") from None
            if not date(1900, 1, 1) < value <= date(2100, 1, 1):
                raise ParameterError(f"{self.label}: date out of range")
            return value
        if self.type == "int":
            try:
                value = int(text)
            except ValueError:
                raise ParameterError(f"{self.label}: enter a whole number") from None
            if (self.min is not None and value < self.min) or (self.max is not None and value > self.max):
                raise ParameterError(f"{self.label}: must be between {self.min} and {self.max}")
            return value
        if self.type == "regex":
            if len(text) > MAX_REGEX_LENGTH:
                raise ParameterError(f"{self.label}: at most {MAX_REGEX_LENGTH} characters")
            try:
                re.compile(text)
            except re.error as exc:
                raise ParameterError(f"{self.label}: not a valid regular expression ({exc})") from None
            return text
        raise ParameterError(f"{self.label}: unknown parameter type {self.type}")

    def text(self, value) -> str:
        return value.isoformat() if isinstance(value, date) else str(value)


@cache
def builtin_parameters(submission_type: SubmissionType | str) -> dict[str, Parameter]:
    from submissions.validation.rules import PACK_FILES, PACKS_DIR
    raw = yaml.safe_load((PACKS_DIR / PACK_FILES[SubmissionType(submission_type)]).read_text(encoding="utf-8"))
    out = {}
    for spec in raw.get("parameters", []):
        p = Parameter(name=spec["name"], type=spec["type"], default=None, label=spec["label"],
                      description=spec["description"], used_by=tuple(spec.get("used_by", [])),
                      min=spec.get("min"), max=spec.get("max"))
        out[p.name] = Parameter(**{**p.__dict__, "default": p.parse(spec["default"])})
    return out


def defaults(submission_type: SubmissionType | str) -> dict[str, object]:
    return {name: p.default for name, p in builtin_parameters(submission_type).items()}


def overrides(engine: Engine, tenant_id: str, submission_type: SubmissionType | str) -> dict[str, dict]:
    """name -> {value (typed), updated_by, updated_at} for one tenant; unknown names are ignored."""
    params = builtin_parameters(submission_type)
    with engine.connect() as conn:
        rows = conn.execute(select(t).where(t.c.tenant_id == tenant_id,
                                            t.c.submission_type == str(SubmissionType(submission_type)))).all()
    return {r.name: dict(value=params[r.name].parse(r.value), updated_by=r.updated_by, updated_at=r.updated_at)
            for r in rows if r.name in params}


def load_parameters(engine: Engine, tenant_id: str, submission_type: SubmissionType | str) -> dict[str, object]:
    """Effective values for one tenant: defaults with its overrides applied."""
    values = defaults(submission_type)
    values.update({k: v["value"] for k, v in overrides(engine, tenant_id, submission_type).items()})
    return values


def set_parameter(engine: Engine, tenant_id: str, submission_type: SubmissionType | str, name: str, raw,
                  user: str, clock: Callable[[], datetime] = utcnow) -> object:
    """Override one parameter for one tenant. Setting the default removes the override. Returns the typed value."""
    param = builtin_parameters(submission_type).get(name)
    if param is None:
        raise ParameterError(f"Unknown parameter {name!r}")
    value = param.parse(raw)
    sub = str(SubmissionType(submission_type))
    key = (t.c.tenant_id == tenant_id, t.c.submission_type == sub, t.c.name == name)
    with engine.begin() as conn:
        if value == param.default:
            conn.execute(delete(t).where(*key))
        elif conn.execute(update(t).where(*key).values(value=param.text(value), updated_by=user,
                                                      updated_at=clock())).rowcount == 0:
            conn.execute(t.insert().values(tenant_id=tenant_id, submission_type=sub, name=name,
                                           value=param.text(value), updated_by=user, updated_at=clock()))
    return value


def clear_parameter(engine: Engine, tenant_id: str, submission_type: SubmissionType | str, name: str) -> bool:
    with engine.begin() as conn:
        return conn.execute(delete(t).where(t.c.tenant_id == tenant_id, t.c.name == name,
                                            t.c.submission_type == str(SubmissionType(submission_type)))).rowcount > 0


def render_message(message: str, values: dict[str, object]) -> str:
    """A rule message with its {parameter} placeholders filled in (unknown placeholders are left as they are)."""
    try:
        return message.format(**values) if "{" in message else message
    except (KeyError, IndexError, ValueError):
        return message
