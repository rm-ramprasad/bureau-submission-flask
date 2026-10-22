import os

import pytest
from sqlalchemy import inspect, text

import submissions.bureaus.tables  # noqa: F401
import submissions.canonical.schema  # noqa: F401
import submissions.requests.tables  # noqa: F401
from submissions.db import make_engine, metadata
from submissions.migrate import MIGRATIONS_DIR, apply_migrations, split_statements


def test_split_statements_ignores_comments_and_blank_tail():
    sql = "-- header; comment\nCREATE TABLE a (x INT);\n\n-- next\nCREATE TABLE b (\n  y INT\n);\n"
    assert split_statements(sql) == ["CREATE TABLE a (x INT)", "CREATE TABLE b (\n  y INT\n)"]


def test_runner_applies_each_file_once(tmp_path):
    (tmp_path / "001_a.sql").write_text("CREATE TABLE a (x INT);")
    (tmp_path / "002_b.sql").write_text("CREATE TABLE b (y INT);\nINSERT INTO b VALUES (1);")
    engine = make_engine(f"sqlite:///{tmp_path / 'm.db'}")
    assert apply_migrations(engine, tmp_path) == ["001_a", "002_b"]
    assert apply_migrations(engine, tmp_path) == []
    (tmp_path / "003_c.sql").write_text("CREATE TABLE c (z INT);")
    assert apply_migrations(engine, tmp_path) == ["003_c"]
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM b")).scalar() == 1


MYSQL_URL = os.environ.get("SUBMISSIONS_TEST_MYSQL_URL")


@pytest.mark.skipif(not MYSQL_URL, reason="set SUBMISSIONS_TEST_MYSQL_URL to an EMPTY scratch MySQL database")
def test_mysql_migration_matches_code_tables():
    """The .sql files and the Core table definitions must describe the same columns."""
    engine = make_engine(MYSQL_URL)
    apply_migrations(engine, MIGRATIONS_DIR)
    insp = inspect(engine)
    for table in metadata.sorted_tables:
        assert {c["name"] for c in insp.get_columns(table.name)} == {c.name for c in table.columns}, table.name
