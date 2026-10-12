"""Add immutable ink revision lineage and an active-page pointer.

The existing ``ink_bundles`` rows are already content-addressed and retained
for idempotent uploads.  This migration adds explicit revision numbers and a
page pointer so a conversion request can name the exact revision it consumed
without mutating or replacing an earlier bundle.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e4f5a6b7c8d9"
down_revision: Union[str, Sequence[str], None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _inspector():
    return sa.inspect(op.get_bind())


def _has_table(name: str) -> bool:
    return name in _inspector().get_table_names()


def _has_column(table: str, name: str) -> bool:
    return name in {column["name"] for column in _inspector().get_columns(table)}


def _has_index(name: str) -> bool:
    inspector = _inspector()
    for table in ("ink_bundles", "document_pages"):
        if table in inspector.get_table_names():
            if name in {index["name"] for index in inspector.get_indexes(table)}:
                return True
            if name in {constraint["name"] for constraint in inspector.get_unique_constraints(table)}:
                return True
    return False


def _add_column(table: str, column: sa.Column) -> None:
    """Add a column while keeping SQLite foreign-key additions portable."""
    if op.get_bind().dialect.name == "sqlite":
        # SQLite cannot add a column-level foreign-key constraint with a
        # plain ALTER TABLE. Batch mode recreates the table and preserves its
        # existing rows, indexes, and constraints.
        with op.batch_alter_table(table, recreate="always") as batch_op:
            batch_op.add_column(column)
        return
    op.add_column(table, column)


def _drop_column(table: str, column: str) -> None:
    """Drop a column without asking SQLite to ALTER a constrained table."""
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(table, recreate="always") as batch_op:
            batch_op.drop_column(column)
        return
    op.drop_column(table, column)


def upgrade() -> None:
    if not _has_table("ink_bundles"):
        return

    if not _has_column("ink_bundles", "revision_number"):
        _add_column(
            "ink_bundles",
            sa.Column("revision_number", sa.Integer(), nullable=False, server_default="1"),
        )
    if not _has_column("ink_bundles", "parent_bundle_id"):
        _add_column(
            "ink_bundles",
            sa.Column(
                "parent_bundle_id",
                sa.Uuid(),
                sa.ForeignKey(
                    "ink_bundles.bundle_id",
                    name="fk_ink_bundles_parent_bundle_id",
                    ondelete="SET NULL",
                ),
                nullable=True,
            ),
        )

    # Backfill deterministic revision numbers before adding the uniqueness
    # guard.  Existing uploads are ordered by their original creation time.
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT bundle_id, page_id FROM ink_bundles "
            "ORDER BY page_id, created_at, bundle_id"
        )
    ).mappings()
    next_by_page: dict[str, int] = {}
    for row in rows:
        page_key = str(row["page_id"])
        revision_number = next_by_page.get(page_key, 0) + 1
        next_by_page[page_key] = revision_number
        bind.execute(
            sa.text(
                "UPDATE ink_bundles SET revision_number = :revision_number "
                "WHERE bundle_id = :bundle_id"
            ),
            {"revision_number": revision_number, "bundle_id": row["bundle_id"]},
        )

    if not _has_index("uq_ink_bundle_page_revision"):
        op.create_index(
            "uq_ink_bundle_page_revision",
            "ink_bundles",
            ["page_id", "revision_number"],
            unique=True,
        )

    if _has_table("document_pages") and not _has_column(
        "document_pages", "active_ink_bundle_id"
    ):
        _add_column(
            "document_pages",
            sa.Column(
                "active_ink_bundle_id",
                sa.Uuid(),
                sa.ForeignKey(
                    "ink_bundles.bundle_id",
                    name="fk_document_pages_active_ink_bundle_id",
                    ondelete="SET NULL",
                ),
                nullable=True,
            ),
        )

        # Preserve the behavior of ``latest_for_page`` for existing rows.
        active_rows = bind.execute(
            sa.text(
                "SELECT page_id, bundle_id FROM ink_bundles "
                "ORDER BY page_id, revision_number"
            )
        ).mappings()
        latest_by_page: dict[str, object] = {}
        for row in active_rows:
            latest_by_page[str(row["page_id"])] = row["bundle_id"]
        for page_id, bundle_id in latest_by_page.items():
            bind.execute(
                sa.text(
                    "UPDATE document_pages SET active_ink_bundle_id = :bundle_id "
                    "WHERE page_id = :page_id"
                ),
                {"page_id": page_id, "bundle_id": bundle_id},
            )


def downgrade() -> None:
    if _has_table("document_pages") and _has_column(
        "document_pages", "active_ink_bundle_id"
    ):
        _drop_column("document_pages", "active_ink_bundle_id")
    if _has_table("ink_bundles"):
        if _has_index("uq_ink_bundle_page_revision"):
            op.drop_index("uq_ink_bundle_page_revision", table_name="ink_bundles")
        if _has_column("ink_bundles", "parent_bundle_id"):
            _drop_column("ink_bundles", "parent_bundle_id")
        if _has_column("ink_bundles", "revision_number"):
            _drop_column("ink_bundles", "revision_number")
