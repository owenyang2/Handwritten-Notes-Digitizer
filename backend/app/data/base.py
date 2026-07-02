"""Declarative base and shared column helpers for the relational data model.

All ORM models inherit from :class:`Base`. UUID primary keys use SQLAlchemy's
dialect-agnostic :class:`~sqlalchemy.Uuid` type so the same models work on
SQLite (used in tests/demo) and server-grade databases without change.
"""

from __future__ import annotations

import datetime as _dt
import uuid

from sqlalchemy import DateTime
from sqlalchemy.orm import DeclarativeBase, mapped_column
from sqlalchemy.orm import Mapped


def utcnow() -> _dt.datetime:
    """Timezone-aware UTC now, used as the default for ``created_at`` columns."""
    return _dt.datetime.now(_dt.timezone.utc)


def new_uuid() -> uuid.UUID:
    """Generate a random UUID4 for primary keys."""
    return uuid.uuid4()


class Base(DeclarativeBase):
    """Declarative base for all relational models."""


def created_at_column() -> Mapped[_dt.datetime]:
    """A non-null ``created_at`` timestamp defaulting to UTC now."""
    return mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
