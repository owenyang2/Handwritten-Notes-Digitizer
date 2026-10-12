"""Add owner-scoped favorite state to documents.

The column is additive and defaults to false so existing notebooks retain the
same library behavior after the migration. Favorite changes are also recorded
through the document sync operation log by the application layer.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def _has_column(table: str, name: str) -> bool:
    return name in {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)
    }


def upgrade() -> None:
    if not _has_table("documents") or _has_column("documents", "is_favorite"):
        return
    op.add_column(
        "documents",
        sa.Column(
            "is_favorite",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_index(
        "ix_documents_is_favorite",
        "documents",
        ["is_favorite"],
    )


def downgrade() -> None:
    if not _has_table("documents") or not _has_column("documents", "is_favorite"):
        return
    inspector = sa.inspect(op.get_bind())
    if "ix_documents_is_favorite" in {
        index["name"] for index in inspector.get_indexes("documents")
    }:
        op.drop_index("ix_documents_is_favorite", table_name="documents")
    op.drop_column("documents", "is_favorite")
