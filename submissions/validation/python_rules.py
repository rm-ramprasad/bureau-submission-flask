"""Rules that SQL cannot express well. Each reads canonical rows for one snapshot and yields findings.

Register a function in PYTHON_RULES and reference it by name from a rule pack (`python: <name>`).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import Connection, select

from submissions.canonical.schema import ENTITY_TABLES
from submissions.validation.checks.addresses import pin_format_problem, pin_ranges, pin_state_problem
from submissions.validation.checks.emails import email_problem
from submissions.validation.checks.identifiers import id_number_problem
from submissions.validation.checks.names import check_name
from submissions.validation.checks.phones import valid_numbers


@dataclass(frozen=True)
class PyFinding:
    row_id: int
    party_key: str | None
    account_key: str | None = None
    value: object = None
    detail: str | None = None          # appended to the rule message


@dataclass
class RuleContext:
    conn: Connection
    snapshot_id: int
    as_of_date: date
    params: dict = field(default_factory=dict)        # rule parameters (pack defaults + tenant overrides)
    _cache: dict = field(default_factory=dict)

    def rows(self, entity: str) -> list:
        if entity not in self._cache:
            t = ENTITY_TABLES[entity]
            self._cache[entity] = self.conn.execute(select(t).where(t.c.snapshot_id == self.snapshot_id)).all()
        return self._cache[entity]


RuleFunc = Callable[[RuleContext], Iterator[PyFinding]]
PYTHON_RULES: dict[str, RuleFunc] = {}


def register(name: str):
    def wrap(fn: RuleFunc) -> RuleFunc:
        PYTHON_RULES[name] = fn
        return fn
    return wrap


def _names(ctx: RuleContext, predicate) -> Iterator[PyFinding]:
    for p in ctx.rows("party"):
        if p.full_name and predicate(check_name(p.full_name)):
            yield PyFinding(p.row_id, p.party_key, value=p.full_name)


@register("name_digits")
def name_digits(ctx):
    yield from _names(ctx, lambda c: c.has_digits)


@register("name_disallowed_chars")
def name_disallowed_chars(ctx):
    yield from _names(ctx, lambda c: bool(c.disallowed_chars))


@register("name_no_valid_token")
def name_no_valid_token(ctx):
    yield from _names(ctx, lambda c: not c.has_valid_token)


@register("name_starts_with_descriptor")
def name_starts_with_descriptor(ctx):
    yield from _names(ctx, lambda c: c.starts_with_descriptor)


@register("name_single_word")
def name_single_word(ctx):
    yield from _names(ctx, lambda c: c.word_count == 1)


@register("id_number_format")
def id_number_format(ctx):
    for i in ctx.rows("party_identifier"):
        problem = id_number_problem(i.id_type, i.id_number, ctx.params) if i.id_number else None
        if problem:
            yield PyFinding(i.row_id, i.party_key, value=i.id_number, detail=problem)


@register("phone_number_invalid")
def phone_number_invalid(ctx):
    for ph in ctx.rows("party_phone"):
        if ph.phone_number and not valid_numbers(ph.phone_number, ph.phone_type):
            yield PyFinding(ph.row_id, ph.party_key, value=ph.phone_number)


@register("email_invalid")
def email_invalid(ctx):
    for e in ctx.rows("party_email"):
        problem = email_problem(e.email) if e.email else None
        if problem:
            yield PyFinding(e.row_id, e.party_key, value=e.email, detail=problem)


@register("pin_format")
def pin_format(ctx):
    for a in ctx.rows("party_address"):
        problem = pin_format_problem(a.pin_code, a.state_code) if a.pin_code else None
        if problem:
            yield PyFinding(a.row_id, a.party_key, value=a.pin_code, detail=problem)


@register("pin_state_mismatch")
def pin_state_mismatch(ctx):
    ranges = pin_ranges()
    for a in ctx.rows("party_address"):
        problem = pin_state_problem(a.pin_code, a.state_code, ranges) if a.pin_code else None
        if problem:
            yield PyFinding(a.row_id, a.party_key, value=a.pin_code, detail=problem)
