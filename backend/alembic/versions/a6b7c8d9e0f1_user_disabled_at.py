"""Add users.disabled_at so a deleted account cannot log in before purge.

Account deletion sets this timestamp and commits it before removing the user's
rows. A purge that stops halfway still leaves authentication rejected.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a6b7c8d9e0f1"
down_revision: Union[str, Sequence[str], None] = "f5a6b7c8d9e0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _inspector():
    return sa.inspect(op.get_bind())


def _has_table(name: str) -> bool:
    return name in _inspector().get_table_names()


def _has_column(table: str, name: str) -> bool:
    return name in {column["name"] for column in _inspector().get_columns(table)}


def upgrade() -> None:
    if _has_table("users") and not _has_column("users", "disabled_at"):
        op.add_column(
            "users",
            sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    if _has_table("users") and _has_column("users", "disabled_at"):
        op.drop_column("users", "disabled_at")
