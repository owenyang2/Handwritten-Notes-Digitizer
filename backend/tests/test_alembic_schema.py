"""Regression tests for the repair migration and its clean-install path."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect

from app.data.base import Base
from app.data import migrations
from app.data.migrations import upgrade_database
import app.data.models  # Register every ORM table in Base.metadata.


BACKEND_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_REPAIR_REVISION = "f07c6f8f2ba6"


def _run_alembic(database: Path, *args: str) -> None:
    env = os.environ.copy()
    env["HND_DATABASE_URL"] = f"sqlite:///{database}"
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_alembic_upgrade_repairs_the_empty_initial_revision(tmp_path: Path) -> None:
    """A clean database reaches the ORM schema without autogenerate drift."""
    database = tmp_path / "schema.db"

    # The repair runs before sessions so a fresh PostgreSQL database has the
    # referenced users table before the session foreign key is created.
    _run_alembic(database, "upgrade", SCHEMA_REPAIR_REVISION)
    _run_alembic(database, "upgrade", "head")
    _run_alembic(database, "check")

    engine = create_engine(f"sqlite:///{database}")
    inspector = inspect(engine)
    assert set(Base.metadata.tables).issubset(inspector.get_table_names())
    assert "document_id" in {
        column["name"] for column in inspector.get_columns("images")
    }


def test_application_migration_runner_uses_the_explicit_database_url(tmp_path: Path) -> None:
    database = tmp_path / "runtime-schema.db"
    database_url = f"sqlite:///{database}"

    upgrade_database(database_url)

    inspector = inspect(create_engine(database_url))
    assert set(Base.metadata.tables).issubset(inspector.get_table_names())


def test_reordered_session_revision_upgrades_an_old_repair_database(tmp_path: Path) -> None:
    database = tmp_path / "old-repair.db"
    database_url = f"sqlite:///{database}"

    _run_alembic(database, "upgrade", SCHEMA_REPAIR_REVISION)
    # Simulate a deployment created under the former migration ordering: the
    # database is stamped at the repair revision but already has sessions.
    Base.metadata.create_all(create_engine(database_url))

    _run_alembic(database, "upgrade", "head")
    _run_alembic(database, "check")

    inspector = inspect(create_engine(database_url))
    assert "user_sessions" in inspector.get_table_names()


def test_application_migration_runner_accepts_percent_encoded_database_urls(monkeypatch) -> None:
    captured: dict[str, str] = {}

    def capture_upgrade(config, target: str) -> None:
        captured["url"] = config.get_main_option("sqlalchemy.url")
        captured["target"] = target

    monkeypatch.setattr(migrations.command, "upgrade", capture_upgrade)
    database_url = "postgresql+psycopg://user:p%40ss%25word@db.example/app"

    upgrade_database(database_url)

    assert captured == {"url": database_url, "target": "head"}
