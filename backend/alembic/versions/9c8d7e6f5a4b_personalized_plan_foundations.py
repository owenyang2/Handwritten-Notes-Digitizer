"""Add document lifecycle metadata and model-assignment provenance.

This migration is additive.  Existing documents, predictions, and personal
training data remain valid; new columns are backfilled with conservative
defaults and old predictions may keep a null model assignment until they are
re-run.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "9c8d7e6f5a4b"
down_revision: Union[str, Sequence[str], None] = "d4e8f1a2b3c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _inspector():
    return sa.inspect(op.get_bind())


def _has_table(name: str) -> bool:
    return name in _inspector().get_table_names()


def _has_column(table: str, name: str) -> bool:
    return name in {column["name"] for column in _inspector().get_columns(table)}


def _has_index(table: str, name: str) -> bool:
    return name in {index["name"] for index in _inspector().get_indexes(table)}


def _add_column(table: str, column: sa.Column) -> None:
    if _has_table(table) and not _has_column(table, column.name):
        op.add_column(table, column)


def upgrade() -> None:
    # Document fields support the first cross-device library contract.  The
    # server defaults make upgrades safe for existing rows on SQLite and
    # PostgreSQL; application code also supplies explicit values for new rows.
    _add_column(
        "documents",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
    )
    _add_column(
        "documents",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    _add_column(
        "documents",
        sa.Column("trashed_at", sa.DateTime(timezone=True), nullable=True),
    )
    _add_column(
        "documents",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    _add_column(
        "documents",
        sa.Column("sync_version", sa.Integer(), nullable=False, server_default="0"),
    )
    _add_column(
        "documents",
        sa.Column(
            "default_language",
            sa.String(length=32),
            nullable=False,
            server_default="en",
        ),
    )
    _add_column("documents", sa.Column("course_context", sa.Text(), nullable=True))
    _add_column("documents", sa.Column("source_metadata", sa.JSON(), nullable=True))

    if _has_table("documents"):
        op.execute(
            sa.text(
                "UPDATE documents SET updated_at = CURRENT_TIMESTAMP "
                "WHERE updated_at IS NULL"
            )
        )

    if not _has_table("model_assignments"):
        op.create_table(
            "model_assignments",
            sa.Column("assignment_id", sa.Uuid(), primary_key=True),
            sa.Column(
                "user_id",
                sa.Uuid(),
                sa.ForeignKey("users.user_id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("task", sa.String(length=64), nullable=False),
            sa.Column("assignment_key", sa.String(length=512), nullable=False),
            sa.Column("base_model_version", sa.String(length=255), nullable=False),
            sa.Column(
                "adapter_id",
                sa.Uuid(),
                sa.ForeignKey("adapters.adapter_id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "adapter_version",
                sa.String(length=255),
                nullable=False,
                server_default="base",
            ),
            sa.Column(
                "activation_reason",
                sa.String(length=64),
                nullable=False,
                server_default="base_default",
            ),
            sa.Column("metrics", sa.JSON(), nullable=True),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint(
                "user_id", "assignment_key", name="uq_model_assignment_user_key"
            ),
        )
        op.create_index(
            "ix_model_assignments_user_id", "model_assignments", ["user_id"]
        )
        op.create_index("ix_model_assignments_task", "model_assignments", ["task"])
        op.create_index(
            "uq_model_assignment_active_task",
            "model_assignments",
            ["user_id", "task"],
            unique=True,
            sqlite_where=sa.text("active = 1"),
            postgresql_where=sa.text("active = true"),
        )

    if _has_table("region_predictions"):
        if not _has_column("region_predictions", "model_assignment_id"):
            # SQLite cannot add a foreign-key constraint with a plain ALTER
            # TABLE.  Batch mode recreates the table safely while preserving
            # the existing prediction rows and constraints.
            with op.batch_alter_table(
                "region_predictions", recreate="always"
            ) as batch_op:
                batch_op.add_column(
                    sa.Column(
                        "model_assignment_id",
                        sa.Uuid(),
                        sa.ForeignKey(
                            "model_assignments.assignment_id",
                            name="fk_region_predictions_model_assignment_id",
                            ondelete="SET NULL",
                        ),
                        nullable=True,
                    )
                )
        if not _has_index("region_predictions", "ix_region_predictions_model_assignment_id"):
            op.create_index(
                "ix_region_predictions_model_assignment_id",
                "region_predictions",
                ["model_assignment_id"],
            )


def downgrade() -> None:
    if _has_table("region_predictions") and _has_index(
        "region_predictions", "ix_region_predictions_model_assignment_id"
    ):
        op.drop_index(
            "ix_region_predictions_model_assignment_id", table_name="region_predictions"
        )
    if _has_table("region_predictions") and _has_column(
        "region_predictions", "model_assignment_id"
    ):
        op.drop_column("region_predictions", "model_assignment_id")

    if _has_table("model_assignments"):
        if _has_index("model_assignments", "uq_model_assignment_active_task"):
            op.drop_index(
                "uq_model_assignment_active_task", table_name="model_assignments"
            )
        op.drop_table("model_assignments")

    for name in (
        "source_metadata",
        "course_context",
        "default_language",
        "sync_version",
        "deleted_at",
        "trashed_at",
        "archived_at",
        "updated_at",
    ):
        if _has_table("documents") and _has_column("documents", name):
            op.drop_column("documents", name)
