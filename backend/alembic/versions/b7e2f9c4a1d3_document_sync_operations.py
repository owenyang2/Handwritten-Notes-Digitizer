"""Add the server-sequenced document sync operation log.

The migration is additive.  Existing document rows and revision snapshots are
left untouched; the operation log starts recording client mutations through the
new sync API from this revision onward.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b7e2f9c4a1d3"
down_revision: Union[str, Sequence[str], None] = "9c8d7e6f5a4b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if not _has_table("document_operations"):
        op.create_table(
            "document_operations",
            sa.Column(
                "server_sequence",
                sa.Integer(),
                primary_key=True,
                autoincrement=True,
            ),
            sa.Column("operation_id", sa.Uuid(), nullable=False, unique=True),
            sa.Column(
                "user_id",
                sa.Uuid(),
                sa.ForeignKey("users.user_id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "document_id",
                sa.Uuid(),
                sa.ForeignKey("documents.document_id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("client_id", sa.String(length=128), nullable=False),
            sa.Column("client_operation_id", sa.Uuid(), nullable=False),
            sa.Column("base_sync_version", sa.Integer(), nullable=False),
            sa.Column("document_sync_version", sa.Integer(), nullable=False),
            sa.Column("entity_type", sa.String(length=64), nullable=False),
            sa.Column("entity_id", sa.String(length=255), nullable=True),
            sa.Column("operation_type", sa.String(length=64), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "user_id",
                "client_id",
                "client_operation_id",
                name="uq_document_operation_client_key",
            ),
        )
        op.create_index(
            "ix_document_operations_user_id", "document_operations", ["user_id"]
        )
        op.create_index(
            "ix_document_operations_document_id",
            "document_operations",
            ["document_id"],
        )
        op.create_index(
            "ix_document_operations_document_sequence",
            "document_operations",
            ["document_id", "server_sequence"],
        )

    if not _has_table("document_conflicts"):
        op.create_table(
            "document_conflicts",
            sa.Column("conflict_id", sa.Uuid(), primary_key=True),
            sa.Column(
                "user_id",
                sa.Uuid(),
                sa.ForeignKey("users.user_id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "document_id",
                sa.Uuid(),
                sa.ForeignKey("documents.document_id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("client_id", sa.String(length=128), nullable=False),
            sa.Column("client_operation_id", sa.Uuid(), nullable=False),
            sa.Column("base_sync_version", sa.Integer(), nullable=False),
            sa.Column("current_sync_version", sa.Integer(), nullable=False),
            sa.Column("entity_type", sa.String(length=64), nullable=False),
            sa.Column("entity_id", sa.String(length=255), nullable=True),
            sa.Column("operation_type", sa.String(length=64), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column(
                "status",
                sa.String(length=16),
                nullable=False,
                server_default="pending",
            ),
            sa.Column("resolution", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint(
                "user_id",
                "client_id",
                "client_operation_id",
                name="uq_document_conflict_client_key",
            ),
        )
        op.create_index(
            "ix_document_conflicts_user_id", "document_conflicts", ["user_id"]
        )
        op.create_index(
            "ix_document_conflicts_document_id",
            "document_conflicts",
            ["document_id"],
        )
        op.create_index(
            "ix_document_conflicts_document_status",
            "document_conflicts",
            ["document_id", "status"],
        )


def downgrade() -> None:
    if _has_table("document_conflicts"):
        op.drop_table("document_conflicts")
    if _has_table("document_operations"):
        op.drop_table("document_operations")
