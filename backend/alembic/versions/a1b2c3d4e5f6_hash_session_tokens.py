"""Replace raw session-token storage with one-way hashes."""

from __future__ import annotations

import hashlib
import datetime as dt
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "d9e4b5c6a7f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(bind) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns("user_sessions")}


def _as_uuid(value: object) -> uuid.UUID:
    """Normalize SQLite text and PostgreSQL UUID values for the new table."""
    if isinstance(value, uuid.UUID):
        return value
    if value is None:
        raise RuntimeError("user_sessions contains a row without a UUID value")
    return uuid.UUID(str(value))


def _as_datetime(value: object) -> dt.datetime | None:
    """Normalize typed database timestamps and SQLite text timestamps."""
    if value is None or isinstance(value, dt.datetime):
        return value
    if isinstance(value, str):
        try:
            return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise RuntimeError(f"Invalid user_sessions timestamp: {value!r}") from exc
    raise RuntimeError(f"Invalid user_sessions timestamp type: {type(value)!r}")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "user_sessions" not in inspector.get_table_names():
        return

    old_columns = _columns(bind)
    # A database created directly from the current ORM model is already
    # hardened. This guard also keeps the reordered-session migration path
    # idempotent for old repair databases used by the test suite.
    if "token" not in old_columns:
        return

    rows = list(
        bind.execute(
            sa.text(
                "SELECT token, token_hash, session_id, user_id, expires_at, "
                "created_at, last_seen_at, revoked_at, device_name, client_id, "
                "user_agent FROM user_sessions"
            )
        ).mappings()
    )

    hardened = op.create_table(
        "user_sessions_hardened",
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("device_name", sa.String(length=128), nullable=True),
        sa.Column("client_id", sa.String(length=128), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("session_id"),
    )

    values: list[dict[str, object]] = []
    for row in rows:
        raw_token = row["token"]
        if raw_token is None:
            raise RuntimeError("user_sessions contains a row without a raw token")
        raw_token_text = str(raw_token)
        raw_session_id = row["session_id"]
        session_id = uuid.UUID(str(raw_session_id)) if raw_session_id else uuid.uuid4()
        token_hash = row["token_hash"] or hashlib.sha256(
            raw_token_text.encode("utf-8")
        ).hexdigest()
        values.append(
            {
                "session_id": session_id,
                "token_hash": str(token_hash),
                "user_id": _as_uuid(row["user_id"]),
                "expires_at": _as_datetime(row["expires_at"]),
                "created_at": _as_datetime(row["created_at"]),
                "last_seen_at": _as_datetime(row["last_seen_at"]),
                "revoked_at": _as_datetime(row["revoked_at"]),
                "device_name": row["device_name"],
                "client_id": row["client_id"],
                "user_agent": row["user_agent"],
            }
        )
    if values:
        op.bulk_insert(hardened, values)

    op.drop_table("user_sessions")
    op.rename_table("user_sessions_hardened", "user_sessions")
    op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])
    op.create_index(
        "ix_user_sessions_token_hash",
        "user_sessions",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        "ix_user_sessions_session_id",
        "user_sessions",
        ["session_id"],
        unique=True,
    )


def downgrade() -> None:
    raise RuntimeError(
        "Raw session tokens cannot be restored from one-way hashes; "
        "restore a database backup instead."
    )
