"""Add durable recognition jobs, document snapshots, and reviewed examples.

This migration is additive.  The legacy image/word/review tables remain in
place so existing installations can upgrade without rewriting user content.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7b1f2d9c4e6a"
down_revision: Union[str, Sequence[str], None] = "c3a91e4d7b10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _inspector():
    return sa.inspect(op.get_bind())


def _has_table(name: str) -> bool:
    return name in _inspector().get_table_names()


def _has_column(table: str, name: str) -> bool:
    return name in {column["name"] for column in _inspector().get_columns(table)}


def _add_column(table: str, column: sa.Column) -> None:
    if _has_table(table) and not _has_column(table, column.name):
        op.add_column(table, column)


def upgrade() -> None:
    # Existing deployments need a non-null backfill value for these columns.
    _add_column(
        "document_regions",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.current_timestamp()),
    )
    _add_column(
        "document_regions",
        sa.Column("page_revision", sa.Integer(), nullable=False, server_default="1"),
    )
    _add_column("document_regions", sa.Column("source_crop_path", sa.String(length=1024), nullable=True))
    _add_column("document_regions", sa.Column("crop_sha256", sa.String(length=64), nullable=True))
    _add_column("region_predictions", sa.Column("alternatives", sa.JSON(), nullable=True))
    _add_column("region_predictions", sa.Column("provenance", sa.JSON(), nullable=True))
    _add_column("region_predictions", sa.Column("settings", sa.JSON(), nullable=True))
    _add_column("region_reviews", sa.Column("crop_revision", sa.Integer(), nullable=False, server_default="1"))
    _add_column("region_reviews", sa.Column("crop_sha256", sa.String(length=64), nullable=True))
    _add_column("region_reviews", sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
    for name, length in (
        ("model_id", 255),
        ("upstream_commit", 128),
        ("weight_sha256", 64),
        ("benchmark_version", 128),
    ):
        _add_column("model_artifacts", sa.Column(name, sa.String(length=length), nullable=True))
    _add_column("model_artifacts", sa.Column("processor_config", sa.JSON(), nullable=True))
    _add_column("model_artifacts", sa.Column("supported_inputs", sa.JSON(), nullable=True))

    if not _has_table("document_revisions"):
        op.create_table(
            "document_revisions",
            sa.Column("revision_id", sa.Uuid(), primary_key=True),
            sa.Column("document_id", sa.Uuid(), sa.ForeignKey("documents.document_id", ondelete="CASCADE"), nullable=False),
            sa.Column("revision_number", sa.Integer(), nullable=False),
            sa.Column("author_user_id", sa.Uuid(), sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
            sa.Column("parent_revision_id", sa.Uuid(), nullable=True),
            sa.Column("block_snapshot", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("document_id", "revision_number", name="uq_document_revision_number"),
        )
        op.create_index("ix_document_revisions_document_id", "document_revisions", ["document_id"])
        op.create_index("ix_document_revisions_author_user_id", "document_revisions", ["author_user_id"])

    if not _has_table("review_revisions"):
        op.create_table(
            "review_revisions",
            sa.Column("review_revision_id", sa.Uuid(), primary_key=True),
            sa.Column("region_id", sa.Uuid(), sa.ForeignKey("document_regions.region_id", ondelete="CASCADE"), nullable=False),
            sa.Column("prediction_id", sa.Uuid(), sa.ForeignKey("region_predictions.prediction_id", ondelete="SET NULL"), nullable=True),
            sa.Column("document_revision_id", sa.Uuid(), sa.ForeignKey("document_revisions.revision_id", ondelete="SET NULL"), nullable=True),
            sa.Column("reviewed_text", sa.Text(), nullable=False),
            sa.Column("excluded", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("crop_revision", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("crop_sha256", sa.String(length=64), nullable=True),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("author_user_id", sa.Uuid(), sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_review_revisions_region_id", "review_revisions", ["region_id"])
        op.create_index("ix_review_revisions_document_revision_id", "review_revisions", ["document_revision_id"])
        op.create_index("ix_review_revisions_author_user_id", "review_revisions", ["author_user_id"])

    if not _has_table("training_examples"):
        op.create_table(
            "training_examples",
            sa.Column("example_id", sa.Uuid(), primary_key=True),
            sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
            sa.Column("region_id", sa.Uuid(), sa.ForeignKey("document_regions.region_id", ondelete="CASCADE"), nullable=False),
            sa.Column("review_revision_id", sa.Uuid(), sa.ForeignKey("review_revisions.review_revision_id", ondelete="CASCADE"), nullable=False),
            sa.Column("crop_sha256", sa.String(length=64), nullable=False),
            sa.Column("split", sa.Enum("TRAIN", "DEV", "TEST", "UNASSIGNED", name="training_example_split", native_enum=False), nullable=False, server_default="UNASSIGNED"),
            sa.Column("consent", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("eligible", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("user_id", "review_revision_id", name="uq_training_example_review"),
        )
        op.create_index("ix_training_examples_user_id", "training_examples", ["user_id"])
        op.create_index("ix_training_examples_region_id", "training_examples", ["region_id"])

    if not _has_table("recognition_jobs"):
        op.create_table(
            "recognition_jobs",
            sa.Column("job_id", sa.Uuid(), primary_key=True),
            sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
            sa.Column("document_id", sa.Uuid(), sa.ForeignKey("documents.document_id", ondelete="CASCADE"), nullable=True),
            sa.Column("page_id", sa.Uuid(), sa.ForeignKey("document_pages.page_id", ondelete="CASCADE"), nullable=True),
            sa.Column("image_id", sa.Uuid(), sa.ForeignKey("images.image_id", ondelete="CASCADE"), nullable=True),
            sa.Column("mode", sa.String(length=32), nullable=False, server_default="formula"),
            sa.Column("region_ids", sa.JSON(), nullable=False),
            sa.Column("state", sa.Enum("QUEUED", "RUNNING", "SUCCEEDED", "NO_TEXT", "FAILED", "TIMEOUT", "CANCELLED", name="recognition_job_state", native_enum=False), nullable=False, server_default="QUEUED"),
            sa.Column("progress_current", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("progress_total", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="2"),
            sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("result_ids", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        for name, column in (
            ("ix_recognition_jobs_user_id", "user_id"),
            ("ix_recognition_jobs_document_id", "document_id"),
            ("ix_recognition_jobs_page_id", "page_id"),
            ("ix_recognition_jobs_image_id", "image_id"),
            ("ix_recognition_jobs_state", "state"),
        ):
            op.create_index(name, "recognition_jobs", [column])


def downgrade() -> None:
    for table in ("recognition_jobs", "training_examples", "review_revisions", "document_revisions"):
        if _has_table(table):
            op.drop_table(table)
    for table, columns in (
        ("model_artifacts", ("supported_inputs", "processor_config", "benchmark_version", "weight_sha256", "upstream_commit", "model_id")),
        ("region_reviews", ("version", "crop_sha256", "crop_revision")),
        ("region_predictions", ("settings", "provenance", "alternatives")),
        ("document_regions", ("crop_sha256", "source_crop_path", "page_revision", "updated_at")),
    ):
        for column in columns:
            if _has_column(table, column):
                op.drop_column(table, column)
