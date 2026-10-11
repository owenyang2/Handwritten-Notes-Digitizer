"""Add purpose and page/result bindings to resumable upload sessions.

Generic upload sessions remain compatible with existing source uploads.  Ink
sessions additionally identify the page they will publish and retain the
resulting immutable bundle id after completion, allowing a client to resume a
partially uploaded canonical revision without falling back to an unbounded
single-request body.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f5a6b7c8d9e0"
down_revision: Union[str, Sequence[str], None] = "e4f5a6b7c8d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _inspector():
    return sa.inspect(op.get_bind())


def _has_table(name: str) -> bool:
    return name in _inspector().get_table_names()


def _has_column(table: str, name: str) -> bool:
    return name in {column["name"] for column in _inspector().get_columns(table)}


def _add_column(table: str, column: sa.Column) -> None:
    if not _has_table(table) or _has_column(table, column.name):
        return
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(table, recreate="always") as batch_op:
            batch_op.add_column(column)
        return
    op.add_column(table, column)


def _drop_column(table: str, column: str) -> None:
    if not _has_table(table) or not _has_column(table, column):
        return
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(table, recreate="always") as batch_op:
            batch_op.drop_column(column)
        return
    op.drop_column(table, column)


def upgrade() -> None:
    if not _has_table("upload_sessions"):
        return
    _add_column(
        "upload_sessions",
        sa.Column(
            "purpose",
            sa.String(length=32),
            nullable=False,
            server_default="asset",
        ),
    )
    _add_column(
        "upload_sessions",
        sa.Column(
            "page_id",
            sa.Uuid(),
            sa.ForeignKey(
                "document_pages.page_id",
                name="fk_upload_sessions_page_id",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
    )
    _add_column(
        "upload_sessions",
        sa.Column(
            "ink_bundle_id",
            sa.Uuid(),
            sa.ForeignKey(
                "ink_bundles.bundle_id",
                name="fk_upload_sessions_ink_bundle_id",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
    )


def downgrade() -> None:
    _drop_column("upload_sessions", "ink_bundle_id")
    _drop_column("upload_sessions", "page_id")
    _drop_column("upload_sessions", "purpose")
