"""Add assets, ink bundles, consent events, vocabulary, and password-reset tokens.

Revision ID: d9e4b5c6a7f8
Revises: c8f3a1b2d4e5
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d9e4b5c6a7f8"
down_revision: Union[str, Sequence[str], None] = "c8f3a1b2d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _inspector():
    return sa.inspect(op.get_bind())


def _has_table(name: str) -> bool:
    return name in _inspector().get_table_names()


def upgrade() -> None:
    if not _has_table("assets"):
        op.create_table(
            "assets",
            sa.Column("asset_id", sa.Uuid(), nullable=False),
            sa.Column("user_id", sa.Uuid(), nullable=False),
            sa.Column("content_hash", sa.String(length=64), nullable=False),
            sa.Column("media_type", sa.String(length=128), nullable=False),
            sa.Column("byte_size", sa.Integer(), nullable=False),
            sa.Column("object_ref", sa.String(length=1024), nullable=False),
            sa.Column("encryption_key_ref", sa.String(length=128), nullable=True),
            sa.Column("retention_class", sa.String(length=32), nullable=False),
            sa.Column("scan_status", sa.String(length=32), nullable=False),
            sa.Column("source_kind", sa.String(length=32), nullable=False),
            sa.Column("source_asset_id", sa.Uuid(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["source_asset_id"], ["assets.asset_id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("asset_id"),
            sa.UniqueConstraint("user_id", "content_hash", "media_type", name="uq_asset_user_hash_type"),
        )
        op.create_index("ix_assets_user_id", "assets", ["user_id"])
        op.create_index("ix_assets_user_created", "assets", ["user_id", "created_at"])

    if not _has_table("ink_bundles"):
        op.create_table(
            "ink_bundles",
            sa.Column("bundle_id", sa.Uuid(), nullable=False),
            sa.Column("user_id", sa.Uuid(), nullable=False),
            sa.Column("page_id", sa.Uuid(), nullable=False),
            sa.Column("asset_id", sa.Uuid(), nullable=False),
            sa.Column("schema_name", sa.String(length=32), nullable=False),
            sa.Column("schema_version", sa.Integer(), nullable=False),
            sa.Column("content_hash", sa.String(length=64), nullable=False),
            sa.Column("stroke_count", sa.Integer(), nullable=False),
            sa.Column("point_count", sa.Integer(), nullable=False),
            sa.Column("coordinate_space", sa.JSON(), nullable=True),
            sa.Column("capture", sa.JSON(), nullable=True),
            sa.Column("native_asset_id", sa.Uuid(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["asset_id"], ["assets.asset_id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["native_asset_id"], ["assets.asset_id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["page_id"], ["document_pages.page_id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("bundle_id"),
            sa.UniqueConstraint("page_id", "content_hash", name="uq_ink_bundle_page_hash"),
        )
        op.create_index("ix_ink_bundles_user_id", "ink_bundles", ["user_id"])
        op.create_index("ix_ink_bundles_page_id", "ink_bundles", ["page_id"])
        op.create_index("ix_ink_bundles_page_created", "ink_bundles", ["page_id", "created_at"])

    if not _has_table("consent_events"):
        op.create_table(
            "consent_events",
            sa.Column("event_id", sa.Uuid(), nullable=False),
            sa.Column("user_id", sa.Uuid(), nullable=False),
            sa.Column("policy_version", sa.String(length=32), nullable=False),
            sa.Column("scope", sa.String(length=64), nullable=False),
            sa.Column("action", sa.String(length=32), nullable=False),
            sa.Column("document_id", sa.Uuid(), nullable=True),
            sa.Column("sample_id", sa.String(length=128), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["document_id"], ["documents.document_id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("event_id"),
        )
        op.create_index("ix_consent_events_user_id", "consent_events", ["user_id"])
        op.create_index("ix_consent_events_user_created", "consent_events", ["user_id", "created_at"])

    if not _has_table("user_vocabulary_entries"):
        op.create_table(
            "user_vocabulary_entries",
            sa.Column("entry_id", sa.Uuid(), nullable=False),
            sa.Column("user_id", sa.Uuid(), nullable=False),
            sa.Column("token", sa.String(length=128), nullable=False),
            sa.Column("preferred_form", sa.String(length=256), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("entry_id"),
            sa.UniqueConstraint("user_id", "token", name="uq_user_vocabulary_token"),
        )
        op.create_index("ix_user_vocabulary_entries_user_id", "user_vocabulary_entries", ["user_id"])

    if not _has_table("password_reset_tokens"):
        op.create_table(
            "password_reset_tokens",
            sa.Column("token_hash", sa.String(length=64), nullable=False),
            sa.Column("user_id", sa.Uuid(), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("token_hash"),
        )
        op.create_index("ix_password_reset_tokens_user_id", "password_reset_tokens", ["user_id"])


def downgrade() -> None:
    for table in (
        "password_reset_tokens",
        "user_vocabulary_entries",
        "consent_events",
        "ink_bundles",
        "assets",
    ):
        if _has_table(table):
            op.drop_table(table)
