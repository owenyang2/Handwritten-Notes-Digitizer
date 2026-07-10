"""Engine and session factory for the relational data model.

Reads the database URL from the central settings module. ``create_all`` remains
a convenience for isolated test databases; application startup uses Alembic via
``app.data.migrations``. This module only *reads* configuration.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config.settings import get_settings

from .base import Base


def create_db_engine(database_url: str | None = None, **kwargs) -> Engine:
    """Create a SQLAlchemy engine from the configured (or supplied) URL."""
    if database_url is None:
        database_url = get_settings().database_url
    connect_args = {}
    if database_url.startswith("sqlite"):
        # Allow usage across threads in the demo/test context.
        connect_args["check_same_thread"] = False
    return create_engine(database_url, connect_args=connect_args, **kwargs)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create a configured session factory bound to ``engine``."""
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def create_all(engine: Engine) -> None:
    """Create all tables for the relational model (SQLite/demo convenience)."""
    Base.metadata.create_all(engine)
