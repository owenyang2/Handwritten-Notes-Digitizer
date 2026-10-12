"""Additive document, review, and math-notes schema.

Revision ID: c3a91e4d7b10
Revises: 564d261e008e
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3a91e4d7b10"
down_revision: Union[str, Sequence[str], None] = "564d261e008e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(name: str) -> bool:
    bind = op.get_bind()
    return name in sa.inspect(bind).get_table_names()


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    columns = {col["name"] for col in sa.inspect(bind).get_columns(table)}
    return column in columns


def upgrade() -> None:
    if _has_table("documents"):
        if not _has_column("documents", "source_kind"):
            op.add_column(
                "documents",
                sa.Column("source_kind", sa.String(length=32), nullable=False, server_default="images"),
            )
        if not _has_column("documents", "original_path"):
            op.add_column("documents", sa.Column("original_path", sa.String(length=1024), nullable=True))
        if not _has_column("documents", "active_revision_id"):
            op.add_column("documents", sa.Column("active_revision_id", sa.Uuid(), nullable=True))

    if not _has_table("image_line_reviews"):
        op.create_table(
            "image_line_reviews",
            sa.Column("review_id", sa.Uuid(), primary_key=True),
            sa.Column("image_id", sa.Uuid(), sa.ForeignKey("images.image_id", ondelete="CASCADE"), nullable=False),
            sa.Column("line_index", sa.Integer(), nullable=False),
            sa.Column("reviewed_text", sa.Text(), nullable=False),
            sa.Column("excluded", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("region_x", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("region_y", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("region_width", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("region_height", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("image_id", "line_index", name="uq_image_line_review"),
        )
        op.create_index("ix_image_line_reviews_image_id", "image_line_reviews", ["image_id"])

    if not _has_table("document_pages"):
        op.create_table(
            "document_pages",
            sa.Column("page_id", sa.Uuid(), primary_key=True),
            sa.Column("document_id", sa.Uuid(), sa.ForeignKey("documents.document_id", ondelete="CASCADE"), nullable=False),
            sa.Column("page_index", sa.Integer(), nullable=False),
            sa.Column("original_path", sa.String(length=1024), nullable=True),
            sa.Column("raster_path", sa.String(length=1024), nullable=False),
            sa.Column("image_id", sa.Uuid(), sa.ForeignKey("images.image_id", ondelete="SET NULL"), nullable=True),
            sa.Column("width", sa.Integer(), nullable=False),
            sa.Column("height", sa.Integer(), nullable=False),
            sa.Column("dpi", sa.Integer(), nullable=False, server_default="200"),
            sa.Column("rotation", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("coordinate_space", sa.String(length=64), nullable=False, server_default="page-raster-v1"),
            sa.Column("transforms", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("document_id", "page_index", name="uq_document_page_index"),
        )
        op.create_index("ix_document_pages_document_id", "document_pages", ["document_id"])

    if not _has_table("document_regions"):
        op.create_table(
            "document_regions",
            sa.Column("region_id", sa.Uuid(), primary_key=True),
            sa.Column("page_id", sa.Uuid(), sa.ForeignKey("document_pages.page_id", ondelete="CASCADE"), nullable=False),
            sa.Column("order_index", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("content_type", sa.String(length=32), nullable=False, server_default="math_display"),
            sa.Column("bbox_x", sa.Integer(), nullable=False),
            sa.Column("bbox_y", sa.Integer(), nullable=False),
            sa.Column("bbox_width", sa.Integer(), nullable=False),
            sa.Column("bbox_height", sa.Integer(), nullable=False),
            sa.Column("coordinate_space", sa.String(length=64), nullable=False, server_default="page-raster-v1"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_document_regions_page_id", "document_regions", ["page_id"])

    if not _has_table("region_predictions"):
        op.create_table(
            "region_predictions",
            sa.Column("prediction_id", sa.Uuid(), primary_key=True),
            sa.Column("region_id", sa.Uuid(), sa.ForeignKey("document_regions.region_id", ondelete="CASCADE"), nullable=False),
            sa.Column("engine_id", sa.String(length=64), nullable=False),
            sa.Column("engine_version", sa.String(length=128), nullable=False, server_default=""),
            sa.Column("raw_output", sa.Text(), nullable=False, server_default=""),
            sa.Column("score", sa.Float(), nullable=True),
            sa.Column("truncated", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="ok"),
            sa.Column("latency_ms", sa.Float(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_region_predictions_region_id", "region_predictions", ["region_id"])

    if not _has_table("region_reviews"):
        op.create_table(
            "region_reviews",
            sa.Column("review_id", sa.Uuid(), primary_key=True),
            sa.Column("region_id", sa.Uuid(), sa.ForeignKey("document_regions.region_id", ondelete="CASCADE"), nullable=False),
            sa.Column("prediction_id", sa.Uuid(), sa.ForeignKey("region_predictions.prediction_id", ondelete="SET NULL"), nullable=True),
            sa.Column("reviewed_text", sa.Text(), nullable=False),
            sa.Column("excluded", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("author_user_id", sa.Uuid(), sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_region_reviews_region_id", "region_reviews", ["region_id"])

    if not _has_table("model_artifacts"):
        op.create_table(
            "model_artifacts",
            sa.Column("artifact_id", sa.Uuid(), primary_key=True),
            sa.Column("backend_id", sa.String(length=64), nullable=False),
            sa.Column("source_url", sa.String(length=1024), nullable=True),
            sa.Column("revision", sa.String(length=128), nullable=True),
            sa.Column("license", sa.String(length=128), nullable=True),
            sa.Column("dtype", sa.String(length=32), nullable=True),
            sa.Column("tokenizer_hash", sa.String(length=128), nullable=True),
            sa.Column("processor_hash", sa.String(length=128), nullable=True),
            sa.Column("loaded", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("backend_id", name="uq_model_artifact_backend"),
        )


def downgrade() -> None:
    for table in (
        "model_artifacts",
        "region_reviews",
        "region_predictions",
        "document_regions",
        "document_pages",
        "image_line_reviews",
    ):
        if _has_table(table):
            op.drop_table(table)
    if _has_table("documents"):
        for column in ("active_revision_id", "original_path", "source_kind"):
            if _has_column("documents", column):
                op.drop_column("documents", column)
