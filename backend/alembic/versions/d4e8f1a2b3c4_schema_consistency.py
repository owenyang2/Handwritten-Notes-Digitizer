"""Backfill and enforce non-null recognition-region update timestamps."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4e8f1a2b3c4"
down_revision: Union[str, Sequence[str], None] = "7b1f2d9c4e6a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if "document_regions" not in sa.inspect(bind).get_table_names():
        return
    op.execute(
        sa.text(
            "UPDATE document_regions "
            "SET updated_at = CURRENT_TIMESTAMP "
            "WHERE updated_at IS NULL"
        )
    )
    with op.batch_alter_table("document_regions") as batch_op:
        batch_op.alter_column(
            "updated_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    if "document_regions" not in sa.inspect(bind).get_table_names():
        return
    with op.batch_alter_table("document_regions") as batch_op:
        batch_op.alter_column(
            "updated_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=True,
        )
