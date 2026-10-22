"""Minimal migration runner for plain SQL files.

Applies migrations/NNN_*.sql in order, once each, recording them in `schema_migration`.
Statements are split on ';' at line end, so keep semicolons out of string literals.

Usage:  python -m submissions.migrate
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import Engine, text

from submissions.config import Settings
from submissions.db import make_engine, utcnow
from submissions.logging_config import configure_logging

log = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def split_statements(sql: str) -> list[str]:
    lines = [ln for ln in sql.splitlines() if not ln.strip().startswith("--")]
    statements, current = [], []
    for line in lines:
        current.append(line)
        if line.rstrip().endswith(";"):
            statements.append("\n".join(current).strip().rstrip(";"))
            current = []
    tail = "\n".join(current).strip()
    if tail:
        statements.append(tail)
    return [s for s in statements if s]


def pending_migrations(engine: Engine, migrations_dir: Path = MIGRATIONS_DIR) -> list[Path]:
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS schema_migration ("
            " version VARCHAR(100) NOT NULL PRIMARY KEY,"
            " applied_at DATETIME NOT NULL)"
        ))
        applied = {r[0] for r in conn.execute(text("SELECT version FROM schema_migration"))}
    return [p for p in sorted(migrations_dir.glob("*.sql")) if p.stem not in applied]


def apply_migrations(engine: Engine, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    applied = []
    for path in pending_migrations(engine, migrations_dir):
        log.info("Applying migration %s", path.name)
        # MySQL commits DDL implicitly; the version row is written only after all statements succeed.
        with engine.begin() as conn:
            for stmt in split_statements(path.read_text(encoding="utf-8")):
                conn.execute(text(stmt))
            conn.execute(text("INSERT INTO schema_migration (version, applied_at) VALUES (:v, :t)"),
                         {"v": path.stem, "t": utcnow()})
        applied.append(path.stem)
    return applied


def main() -> None:
    settings = Settings.from_env()
    configure_logging(settings.log_level)
    applied = apply_migrations(make_engine(settings.database_url))
    log.info("Applied %d migration(s): %s", len(applied), ", ".join(applied) or "none")


if __name__ == "__main__":
    main()
