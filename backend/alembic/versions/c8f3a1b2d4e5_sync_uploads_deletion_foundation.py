"""Add sync clients, resumable uploads, and deletion-job foundations.

This migration is additive and idempotent. Existing sessions, pages, sync
operations, and conflicts remain valid; new columns use conservative defaults
and new tables only appear when missing.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c8f3a1b2d4e5"
down_revision: Union[str, Sequence[str], None] = "b7e2f9c4a1d3"
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
    # --- user_sessions: opaque-token migration + device metadata -------------
    _add_column(
        "user_sessions",
        sa.Column("token_hash", sa.String(length=64), nullable=True),
    )
    _add_column(
        "user_sessions",
        sa.Column("session_id", sa.Uuid(), nullable=True),
    )
    _add_column(
        "user_sessions",
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
    )
    _add_column(
        "user_sessions",
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    _add_column(
        "user_sessions",
        sa.Column("device_name", sa.String(length=128), nullable=True),
    )
    _add_column(
        "user_sessions",
        sa.Column("client_id", sa.String(length=128), nullable=True),
    )
    _add_column(
        "user_sessions",
        sa.Column("user_agent", sa.String(length=512), nullable=True),
    )
    if _has_table("user_sessions"):
        if not _has_index("user_sessions", "ix_user_sessions_token_hash"):
            op.create_index(
                "ix_user_sessions_token_hash",
                "user_sessions",
                ["token_hash"],
                unique=True,
            )
        if not _has_index("user_sessions", "ix_user_sessions_session_id"):
            op.create_index(
                "ix_user_sessions_session_id",
                "user_sessions",
                ["session_id"],
                unique=True,
            )

    # --- document_pages: local-first page metadata --------------------------
    _add_column(
        "document_pages",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
    )
    _add_column(
        "document_pages",
        sa.Column(
            "order_key",
            sa.String(length=128),
            nullable=False,
            server_default="00000000000000000000",
        ),
    )
    _add_column(
        "document_pages",
        sa.Column("paper_template", sa.String(length=64), nullable=True),
    )
    _add_column(
        "document_pages",
        sa.Column("ink_asset_ref", sa.String(length=1024), nullable=True),
    )
    _add_column(
        "document_pages",
        sa.Column("preview_asset_ref", sa.String(length=1024), nullable=True),
    )
    _add_column(
        "document_pages",
        sa.Column(
            "page_revision",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )
    if _has_table("document_pages") and _has_column("document_pages", "updated_at"):
        op.execute(
            sa.text(
                "UPDATE document_pages SET updated_at = CURRENT_TIMESTAMP "
                "WHERE updated_at IS NULL"
            )
        )

    # --- region_predictions: immutable inference provenance -----------------
    _add_column(
        "region_predictions",
        sa.Column("source_asset_ref", sa.String(length=1024), nullable=True),
    )
    _add_column(
        "region_predictions",
        sa.Column("source_crop_sha256", sa.String(length=64), nullable=True),
    )
    _add_column(
        "region_predictions",
        sa.Column("source_bbox", sa.JSON(), nullable=True),
    )
    _add_column(
        "region_predictions",
        sa.Column("source_page_revision", sa.Integer(), nullable=True),
    )

    # --- document_operations / conflicts: contract + compaction -------------
    _add_column(
        "document_operations",
        sa.Column(
            "contract_version",
            sa.String(length=16),
            nullable=False,
            server_default="v1",
        ),
    )
    _add_column(
        "document_operations",
        sa.Column(
            "is_tombstone",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    _add_column(
        "document_operations",
        sa.Column("compacted_at", sa.DateTime(timezone=True), nullable=True),
    )

    _add_column(
        "document_conflicts",
        sa.Column(
            "contract_version",
            sa.String(length=16),
            nullable=False,
            server_default="v1",
        ),
    )
    _add_column(
        "document_conflicts",
        sa.Column(
            "is_tombstone",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    # --- sync_clients -------------------------------------------------------
    if not _has_table("sync_clients"):
        op.create_table(
            "sync_clients",
            sa.Column("client_record_id", sa.Uuid(), primary_key=True),
            sa.Column(
                "user_id",
                sa.Uuid(),
                sa.ForeignKey("users.user_id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("client_id", sa.String(length=128), nullable=False),
            sa.Column("platform", sa.String(length=32), nullable=True),
            sa.Column("device_name", sa.String(length=128), nullable=True),
            sa.Column(
                "cursor",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "user_id", "client_id", name="uq_sync_client_user_client"
            ),
        )
        op.create_index("ix_sync_clients_user_id", "sync_clients", ["user_id"])
        op.create_index(
            "ix_sync_clients_user_last_seen",
            "sync_clients",
            ["user_id", "last_seen_at"],
        )
    else:
        if not _has_index("sync_clients", "ix_sync_clients_user_id"):
            op.create_index("ix_sync_clients_user_id", "sync_clients", ["user_id"])
        if not _has_index("sync_clients", "ix_sync_clients_user_last_seen"):
            op.create_index(
                "ix_sync_clients_user_last_seen",
                "sync_clients",
                ["user_id", "last_seen_at"],
            )

    # --- sync_cursor_states -------------------------------------------------
    if not _has_table("sync_cursor_states"):
        op.create_table(
            "sync_cursor_states",
            sa.Column(
                "user_id",
                sa.Uuid(),
                sa.ForeignKey("users.user_id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column(
                "cursor_floor",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )

    # --- document_sync_snapshots --------------------------------------------
    if not _has_table("document_sync_snapshots"):
        op.create_table(
            "document_sync_snapshots",
            sa.Column("snapshot_id", sa.Uuid(), primary_key=True),
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
            sa.Column("server_sequence", sa.Integer(), nullable=False),
            sa.Column("document_sync_version", sa.Integer(), nullable=False),
            sa.Column("state", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "document_id",
                "server_sequence",
                name="uq_document_sync_snapshot_sequence",
            ),
        )
        op.create_index(
            "ix_document_sync_snapshots_user_id",
            "document_sync_snapshots",
            ["user_id"],
        )
        op.create_index(
            "ix_document_sync_snapshots_document_id",
            "document_sync_snapshots",
            ["document_id"],
        )
        op.create_index(
            "ix_document_sync_snapshots_user_sequence",
            "document_sync_snapshots",
            ["user_id", "server_sequence"],
        )
    else:
        if not _has_index(
            "document_sync_snapshots", "ix_document_sync_snapshots_user_id"
        ):
            op.create_index(
                "ix_document_sync_snapshots_user_id",
                "document_sync_snapshots",
                ["user_id"],
            )
        if not _has_index(
            "document_sync_snapshots", "ix_document_sync_snapshots_document_id"
        ):
            op.create_index(
                "ix_document_sync_snapshots_document_id",
                "document_sync_snapshots",
                ["document_id"],
            )
        if not _has_index(
            "document_sync_snapshots", "ix_document_sync_snapshots_user_sequence"
        ):
            op.create_index(
                "ix_document_sync_snapshots_user_sequence",
                "document_sync_snapshots",
                ["user_id", "server_sequence"],
            )

    # --- recognition_runs ---------------------------------------------------
    if not _has_table("recognition_runs"):
        op.create_table(
            "recognition_runs",
            sa.Column("run_id", sa.Uuid(), primary_key=True),
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
            sa.Column(
                "page_id",
                sa.Uuid(),
                sa.ForeignKey("document_pages.page_id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "job_id",
                sa.Uuid(),
                sa.ForeignKey("recognition_jobs.job_id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "contract_version",
                sa.String(length=16),
                nullable=False,
                server_default="v1",
            ),
            sa.Column("scope", sa.String(length=16), nullable=False),
            sa.Column("mode", sa.String(length=32), nullable=False),
            sa.Column("idempotency_key", sa.Uuid(), nullable=True),
            sa.Column("source_snapshot", sa.JSON(), nullable=False),
            sa.Column(
                "state",
                sa.String(length=16),
                nullable=False,
                server_default="queued",
            ),
            sa.Column("result_ids", sa.JSON(), nullable=False),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint(
                "user_id",
                "idempotency_key",
                name="uq_recognition_run_user_idempotency",
            ),
        )
        op.create_index(
            "ix_recognition_runs_user_id", "recognition_runs", ["user_id"]
        )
        op.create_index(
            "ix_recognition_runs_document_id", "recognition_runs", ["document_id"]
        )
        op.create_index(
            "ix_recognition_runs_page_id", "recognition_runs", ["page_id"]
        )
        op.create_index(
            "ix_recognition_runs_job_id", "recognition_runs", ["job_id"]
        )
        op.create_index(
            "ix_recognition_runs_document_created",
            "recognition_runs",
            ["document_id", "created_at"],
        )
    else:
        for name, cols in (
            ("ix_recognition_runs_user_id", ["user_id"]),
            ("ix_recognition_runs_document_id", ["document_id"]),
            ("ix_recognition_runs_page_id", ["page_id"]),
            ("ix_recognition_runs_job_id", ["job_id"]),
            ("ix_recognition_runs_document_created", ["document_id", "created_at"]),
        ):
            if not _has_index("recognition_runs", name):
                op.create_index(name, "recognition_runs", cols)

    # --- upload_sessions / upload_parts -------------------------------------
    if not _has_table("upload_sessions"):
        op.create_table(
            "upload_sessions",
            sa.Column("upload_id", sa.Uuid(), primary_key=True),
            sa.Column(
                "user_id",
                sa.Uuid(),
                sa.ForeignKey("users.user_id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("idempotency_key", sa.Uuid(), nullable=True),
            sa.Column(
                "filename",
                sa.String(length=255),
                nullable=False,
                server_default="upload",
            ),
            sa.Column("content_type", sa.String(length=128), nullable=True),
            sa.Column("expected_size", sa.Integer(), nullable=False),
            sa.Column("expected_sha256", sa.String(length=64), nullable=False),
            sa.Column(
                "received_size",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
            sa.Column(
                "part_size",
                sa.Integer(),
                nullable=False,
                server_default=str(5 * 1024 * 1024),
            ),
            sa.Column(
                "status",
                sa.String(length=16),
                nullable=False,
                server_default="open",
            ),
            sa.Column(
                "document_id",
                sa.Uuid(),
                sa.ForeignKey("documents.document_id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("assembled_ref", sa.String(length=1024), nullable=True),
            sa.Column("parts_manifest", sa.JSON(), nullable=True),
            sa.Column("error_code", sa.String(length=64), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint(
                "user_id",
                "idempotency_key",
                name="uq_upload_session_user_idempotency",
            ),
        )
        op.create_index(
            "ix_upload_sessions_user_id", "upload_sessions", ["user_id"]
        )
        op.create_index(
            "ix_upload_sessions_user_status",
            "upload_sessions",
            ["user_id", "status"],
        )
    else:
        if not _has_index("upload_sessions", "ix_upload_sessions_user_id"):
            op.create_index(
                "ix_upload_sessions_user_id", "upload_sessions", ["user_id"]
            )
        if not _has_index("upload_sessions", "ix_upload_sessions_user_status"):
            op.create_index(
                "ix_upload_sessions_user_status",
                "upload_sessions",
                ["user_id", "status"],
            )

    if not _has_table("upload_parts"):
        op.create_table(
            "upload_parts",
            sa.Column("part_id", sa.Uuid(), primary_key=True),
            sa.Column(
                "upload_id",
                sa.Uuid(),
                sa.ForeignKey("upload_sessions.upload_id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("part_number", sa.Integer(), nullable=False),
            sa.Column("size_bytes", sa.Integer(), nullable=False),
            sa.Column("sha256", sa.String(length=64), nullable=False),
            sa.Column("storage_path", sa.String(length=1024), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "upload_id", "part_number", name="uq_upload_part_number"
            ),
        )
        op.create_index(
            "ix_upload_parts_upload_id", "upload_parts", ["upload_id"]
        )
    elif not _has_index("upload_parts", "ix_upload_parts_upload_id"):
        op.create_index(
            "ix_upload_parts_upload_id", "upload_parts", ["upload_id"]
        )

    # --- deletion_jobs ------------------------------------------------------
    if not _has_table("deletion_jobs"):
        op.create_table(
            "deletion_jobs",
            sa.Column("job_id", sa.Uuid(), primary_key=True),
            sa.Column(
                "user_id",
                sa.Uuid(),
                sa.ForeignKey("users.user_id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "status",
                sa.String(length=16),
                nullable=False,
                server_default="pending",
            ),
            sa.Column(
                "target_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
            sa.Column(
                "deleted_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
            sa.Column("target_refs", sa.JSON(), nullable=False),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index(
            "ix_deletion_jobs_user_id", "deletion_jobs", ["user_id"]
        )
    elif not _has_index("deletion_jobs", "ix_deletion_jobs_user_id"):
        op.create_index(
            "ix_deletion_jobs_user_id", "deletion_jobs", ["user_id"]
        )


def downgrade() -> None:
    if _has_table("deletion_jobs"):
        op.drop_table("deletion_jobs")

    if _has_table("upload_parts"):
        op.drop_table("upload_parts")
    if _has_table("upload_sessions"):
        op.drop_table("upload_sessions")

    if _has_table("recognition_runs"):
        op.drop_table("recognition_runs")

    if _has_table("document_sync_snapshots"):
        op.drop_table("document_sync_snapshots")

    if _has_table("sync_cursor_states"):
        op.drop_table("sync_cursor_states")

    if _has_table("sync_clients"):
        op.drop_table("sync_clients")

    for name in ("is_tombstone", "contract_version"):
        if _has_table("document_conflicts") and _has_column(
            "document_conflicts", name
        ):
            op.drop_column("document_conflicts", name)

    for name in ("compacted_at", "is_tombstone", "contract_version"):
        if _has_table("document_operations") and _has_column(
            "document_operations", name
        ):
            op.drop_column("document_operations", name)

    for name in (
        "source_page_revision",
        "source_bbox",
        "source_crop_sha256",
        "source_asset_ref",
    ):
        if _has_table("region_predictions") and _has_column(
            "region_predictions", name
        ):
            op.drop_column("region_predictions", name)

    for name in (
        "page_revision",
        "preview_asset_ref",
        "ink_asset_ref",
        "paper_template",
        "order_key",
        "updated_at",
    ):
        if _has_table("document_pages") and _has_column("document_pages", name):
            op.drop_column("document_pages", name)

    if _has_table("user_sessions"):
        if _has_index("user_sessions", "ix_user_sessions_session_id"):
            op.drop_index(
                "ix_user_sessions_session_id", table_name="user_sessions"
            )
        if _has_index("user_sessions", "ix_user_sessions_token_hash"):
            op.drop_index(
                "ix_user_sessions_token_hash", table_name="user_sessions"
            )
        for name in (
            "user_agent",
            "client_id",
            "device_name",
            "revoked_at",
            "last_seen_at",
            "session_id",
            "token_hash",
        ):
            if _has_column("user_sessions", name):
                op.drop_column("user_sessions", name)
