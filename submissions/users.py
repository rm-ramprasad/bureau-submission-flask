"""App users for the in-app login (username + password).

Passwords are stored only as salted hashes (werkzeug: scrypt). After MAX_FAILED wrong passwords an account is locked
for LOCK_FOR. Users are managed with this module's command line (and change their own password in the UI):

  python -m submissions.users add <username> --name "Full Name" [--admin]   (prompts for the password)
  python -m submissions.users add <username> --name "Full Name" --generate  (prints a random first password)
  python -m submissions.users passwd <username> [--generate]
  python -m submissions.users disable <username> | enable <username> | list
"""

from __future__ import annotations

import argparse
import getpass
import re
import secrets
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import Boolean, Column, DateTime, Engine, Integer, String, Table, select, update
from werkzeug.security import check_password_hash, generate_password_hash

from submissions.db import metadata, utcnow

MIN_PASSWORD = 10
MAX_FAILED = 5
LOCK_FOR = timedelta(minutes=15)
USERNAME_RE = re.compile(r"^[a-z0-9._-]{3,50}$")

app_user = Table(                                       # migration 010
    "app_user", metadata,
    Column("username", String(50), primary_key=True),
    Column("display_name", String(100), nullable=False),
    Column("password_hash", String(255), nullable=False),
    Column("is_active", Boolean, nullable=False, default=True),
    Column("is_admin", Boolean, nullable=False, default=False),
    Column("failed_attempts", Integer, nullable=False, default=0),
    Column("locked_until", DateTime),
    Column("last_login_at", DateTime),
    Column("password_changed_at", DateTime, nullable=False),
    Column("created_at", DateTime, nullable=False),
)


class UserError(ValueError):
    """Shown to the user."""


@dataclass(frozen=True)
class User:
    username: str
    display_name: str
    is_active: bool
    is_admin: bool
    failed_attempts: int
    locked_until: datetime | None
    last_login_at: datetime | None
    password_changed_at: datetime
    created_at: datetime

    @classmethod
    def from_row(cls, row) -> "User":
        m = dict(row._mapping)
        m.pop("password_hash", None)
        return cls(**m)


def check_password_rules(password: str, username: str = "") -> None:
    if len(password or "") < MIN_PASSWORD:
        raise UserError(f"Password: at least {MIN_PASSWORD} characters")
    if username and username.lower() in password.lower():
        raise UserError("Password must not contain the username")
    if not (re.search(r"[A-Za-z]", password) and re.search(r"[0-9]", password)):
        raise UserError("Password: use letters and digits")


def generate_password() -> str:
    return secrets.token_urlsafe(12) + "7a"             # ~16 chars, always letters and digits


class UserRepository:
    def __init__(self, engine: Engine, clock: Callable[[], datetime] = utcnow):
        self.engine = engine
        self.clock = clock

    def add(self, username: str, display_name: str, password: str, is_admin: bool = False) -> User:
        username = (username or "").strip().lower()
        if not USERNAME_RE.match(username):
            raise UserError("Username: 3-50 characters, a-z, 0-9, dot, dash or underscore")
        if not (display_name or "").strip():
            raise UserError("Enter the user's name")
        check_password_rules(password, username)
        if self.find(username):
            raise UserError(f"User {username} already exists")
        now = self.clock()
        with self.engine.begin() as conn:
            conn.execute(app_user.insert().values(
                username=username, display_name=display_name.strip()[:100], password_hash=generate_password_hash(password),
                is_active=True, is_admin=is_admin, failed_attempts=0, password_changed_at=now, created_at=now))
        return self.find(username)

    def find(self, username: str) -> User | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(app_user).where(app_user.c.username == (username or "").strip().lower())).first()
        return User.from_row(row) if row else None

    def list(self) -> list[User]:
        with self.engine.connect() as conn:
            return [User.from_row(r) for r in conn.execute(select(app_user).order_by(app_user.c.username))]

    def authenticate(self, username: str, password: str) -> User:
        """The user if the password is right; raises UserError otherwise (same message for unknown users)."""
        username = (username or "").strip().lower()
        with self.engine.connect() as conn:
            row = conn.execute(select(app_user).where(app_user.c.username == username)).first()
        now = self.clock()
        if row is None or not row.is_active:
            check_password_hash(generate_password_hash("x"), password or "")      # same timing as a real check
            raise UserError("Wrong username or password")
        if row.locked_until and row.locked_until > now:
            raise UserError(f"Too many wrong passwords: locked until {row.locked_until:%H:%M} UTC. "
                            "Try again later or ask an administrator to reset it.")
        if not check_password_hash(row.password_hash, password or ""):
            failed = row.failed_attempts + 1
            locked = now + LOCK_FOR if failed >= MAX_FAILED else None
            with self.engine.begin() as conn:
                conn.execute(update(app_user).where(app_user.c.username == username).values(
                    failed_attempts=0 if locked else failed, locked_until=locked))
            raise UserError("Wrong username or password")
        with self.engine.begin() as conn:
            conn.execute(update(app_user).where(app_user.c.username == username).values(
                failed_attempts=0, locked_until=None, last_login_at=now))
        return self.find(username)

    def set_password(self, username: str, new_password: str, current_password: str | None = None) -> None:
        """Change a password. With `current_password` (self-service) the old one must match."""
        user = self.find(username)
        if user is None:
            raise UserError(f"No user {username}")
        if current_password is not None:
            with self.engine.connect() as conn:
                stored = conn.execute(select(app_user.c.password_hash)
                                      .where(app_user.c.username == user.username)).scalar()
            if not check_password_hash(stored, current_password):
                raise UserError("The current password is wrong")
        check_password_rules(new_password, user.username)
        with self.engine.begin() as conn:
            conn.execute(update(app_user).where(app_user.c.username == user.username).values(
                password_hash=generate_password_hash(new_password), password_changed_at=self.clock(),
                failed_attempts=0, locked_until=None))

    def set_active(self, username: str, active: bool) -> None:
        with self.engine.begin() as conn:
            if conn.execute(update(app_user).where(app_user.c.username == (username or "").lower())
                            .values(is_active=active, failed_attempts=0, locked_until=None)).rowcount == 0:
                raise UserError(f"No user {username}")


# ---------------------------------------------------------------- command line
def _password(args) -> str:
    if args.generate:
        return generate_password()
    first = getpass.getpass("New password: ")
    if first != getpass.getpass("Repeat it: "):
        raise UserError("The passwords differ")
    return first


def main(argv: list[str] | None = None) -> int:
    from submissions.config import Settings
    from submissions.db import make_engine
    from submissions.ops import audit

    ap = argparse.ArgumentParser(description="Manage app users (in-app login)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    add = sub.add_parser("add")
    add.add_argument("username")
    add.add_argument("--name", required=True)
    add.add_argument("--admin", action="store_true")
    add.add_argument("--generate", action="store_true", help="generate and print a first password")
    pw = sub.add_parser("passwd")
    pw.add_argument("username")
    pw.add_argument("--generate", action="store_true")
    for name in ("disable", "enable"):
        sub.add_parser(name).add_argument("username")
    sub.add_parser("list")
    args = ap.parse_args(argv)
    engine = make_engine(Settings.from_env().database_url)
    repo = UserRepository(engine)
    try:
        if args.cmd == "list":
            for u in repo.list():
                state = "active" if u.is_active else "disabled"
                print(f"{u.username:20} {u.display_name:30} {state:8} {'admin' if u.is_admin else '':5} "
                      f"last login {u.last_login_at or '-'}")
            return 0
        if args.cmd == "add":
            password = _password(args)
            repo.add(args.username, args.name, password, args.admin)
            audit.record(engine, "admin-cli", "USER_ADDED", target=args.username.lower(), admin=args.admin)
        elif args.cmd == "passwd":
            password = _password(args)
            repo.set_password(args.username, password)
            audit.record(engine, "admin-cli", "PASSWORD_RESET", target=args.username.lower())
        else:
            repo.set_active(args.username, args.cmd == "enable")
            audit.record(engine, "admin-cli", f"USER_{args.cmd.upper()}D", target=args.username.lower())
        print(f"{args.cmd}: {args.username.lower()} done")
        if getattr(args, "generate", False):
            print(f"Password (share it privately; the user can change it under 'Change password'): {password}")
        return 0
    except UserError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
