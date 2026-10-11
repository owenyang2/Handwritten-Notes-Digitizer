"""Add optional user-facing titles to document pages.

Page titles are additive so older notebooks and older clients continue to
decode successfully. The sync layer trims and bounds values before writing.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, Sequence[str], None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def _has_column(table: str, name: str) -> bool:
    return name in {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)
    }


def upgrade() -> None:
    if _has_table("document_pages") and not _has_column("document_pages", "title"):
        op.add_column(
            "document_pages",
            sa.Column("title", sa.String(length=255), nullable=True),
        )


def downgrade() -> None:
    if _has_table("document_pages") and _has_column("document_pages", "title"):
        op.drop_column("document_pages", "title")
