"""Relational ORM models for the Handwriting Notes Digitizer.

These models implement the design's Data Models section: ``User``, ``Image``,
``RecognizedWord``, ``TrainingPair``, ``Adapter``, ``TrainingRun``, and
``EvaluationResult``. Key invariants enforced at the schema level:

* Every image is owned by exactly one user (Req 1.6, 10.x).
* A training pair is unique per ``(user_id, source_region_key)`` so that
  re-correcting a region upserts rather than duplicates (Req 4.7).
* At most one adapter row per user via a unique ``user_id`` (Req 5.3).
* ``consumed_by_run_id`` records the completed training that consumed a pair,
  which drives the "unused pair" count that triggers training (Req 5.1).
"""

from __future__ import annotations

import datetime as _dt
import uuid
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, created_at_column, new_uuid, utcnow
from .enums import (
    AdapterStatus,
    EvaluationAvailability,
    ImageFormat,
    ImageStatus,
    RecognitionJobState,
    TrainingExampleSplit,
    TrainingPairOrigin,
    TrainingRunState,
)


class User(Base):
    """An authenticated individual whose handwriting is personalized (Req 10)."""

    __tablename__ = "users"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=new_uuid
    )
    username: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[_dt.datetime] = created_at_column()

    images: Mapped[list["Image"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    training_pairs: Mapped[list["TrainingPair"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    sessions: Mapped[list["UserSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    documents: Mapped[list["Document"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    adapter: Mapped[Optional["Adapter"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )
    training_runs: Mapped[list["TrainingRun"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    evaluation_results: Mapped[list["EvaluationResult"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    training_examples: Mapped[list["TrainingExample"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    model_assignments: Mapped[list["ModelAssignment"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    document_operations: Mapped[list["DocumentOperation"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    document_conflicts: Mapped[list["DocumentConflict"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    sync_clients: Mapped[list["SyncClient"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    sync_cursor_state: Mapped[Optional["SyncCursorState"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )
    sync_snapshots: Mapped[list["DocumentSyncSnapshot"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    recognition_runs: Mapped[list["RecognitionRun"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    upload_sessions: Mapped[list["UploadSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    deletion_jobs: Mapped[list["DeletionJob"]] = relationship(
        back_populates="user"
    )
    assets: Mapped[list["Asset"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    ink_bundles: Mapped[list["InkBundle"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    consent_events: Mapped[list["ConsentEvent"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    vocabulary_entries: Mapped[list["UserVocabularyEntry"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class UserSession(Base):
    """A persistent authentication session for a user.

    The bearer credential is never stored.  ``token_hash`` is a one-way
    lookup key and ``session_id`` is the public/revocation identity used by
    account-management APIs.
    """

    __tablename__ = "user_sessions"

    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=new_uuid, unique=True, index=True
    )
    token_hash: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True
    )
    expires_at: Mapped[_dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[_dt.datetime] = created_at_column()
    last_seen_at: Mapped[Optional[_dt.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[Optional[_dt.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    device_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    client_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    user: Mapped["User"] = relationship(back_populates="sessions")


class Document(Base):
    """A logical grouping of multiple handwriting images (Notebook/Document)."""

    __tablename__ = "documents"

    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=new_uuid
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), default="Untitled Note")
    source_kind: Mapped[str] = mapped_column(String(32), default="images", nullable=False)
    original_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    active_revision_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[_dt.datetime] = created_at_column()
    updated_at: Mapped[_dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
    archived_at: Mapped[Optional[_dt.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    trashed_at: Mapped[Optional[_dt.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_favorite: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, index=True
    )
    deleted_at: Mapped[Optional[_dt.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    sync_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    default_language: Mapped[str] = mapped_column(
        String(32), default="en", nullable=False
    )
    course_context: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_metadata: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    user: Mapped["User"] = relationship(back_populates="documents")
    images: Mapped[list["Image"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="Image.created_at"
    )
    pages: Mapped[list["DocumentPage"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="DocumentPage.page_index"
    )
    revisions: Mapped[list["DocumentRevision"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="DocumentRevision.revision_number"
    )
    operations: Mapped[list["DocumentOperation"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="DocumentOperation.server_sequence"
    )
    conflicts: Mapped[list["DocumentConflict"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="DocumentConflict.created_at"
    )
    sync_snapshots: Mapped[list["DocumentSyncSnapshot"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="DocumentSyncSnapshot.server_sequence",
    )


class Image(Base):
    """An uploaded handwriting image, owned by its submitter (Req 1.6)."""

    __tablename__ = "images"

    image_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=new_uuid
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid, ForeignKey("documents.document_id", ondelete="CASCADE"), nullable=True, index=True
    )
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    format: Mapped[ImageFormat] = mapped_column(
        SAEnum(ImageFormat, name="image_format"), nullable=False
    )
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[ImageStatus] = mapped_column(
        SAEnum(ImageStatus, name="image_status"),
        default=ImageStatus.ACCEPTED,
        nullable=False,
    )
    created_at: Mapped[_dt.datetime] = created_at_column()

    user: Mapped["User"] = relationship(back_populates="images")
    document: Mapped[Optional["Document"]] = relationship(back_populates="images")
    recognized_words: Mapped[list["RecognizedWord"]] = relationship(
        back_populates="image", cascade="all, delete-orphan"
    )
    line_reviews: Mapped[list["ImageLineReview"]] = relationship(
        back_populates="image", cascade="all, delete-orphan"
    )


class RecognizedWord(Base):
    """A recognized word with its source region coordinates (Req 2.4, 3.2)."""

    __tablename__ = "recognized_words"

    word_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=new_uuid
    )
    image_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("images.image_id", ondelete="CASCADE"), nullable=False, index=True
    )
    line_index: Mapped[int] = mapped_column(Integer, nullable=False)
    word_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # Region {x, y, width, height} in image pixel coordinates.
    region_x: Mapped[int] = mapped_column(Integer, nullable=False)
    region_y: Mapped[int] = mapped_column(Integer, nullable=False)
    region_width: Mapped[int] = mapped_column(Integer, nullable=False)
    region_height: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    image: Mapped["Image"] = relationship(back_populates="recognized_words")


class ImageLineReview(Base):
    """Persisted line-level review and exclusion state for an image."""

    __tablename__ = "image_line_reviews"
    __table_args__ = (
        UniqueConstraint("image_id", "line_index", name="uq_image_line_review"),
    )

    review_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=new_uuid
    )
    image_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("images.image_id", ondelete="CASCADE"), nullable=False, index=True
    )
    line_index: Mapped[int] = mapped_column(Integer, nullable=False)
    reviewed_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    excluded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    region_x: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    region_y: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    region_width: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    region_height: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[_dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )

    image: Mapped["Image"] = relationship(back_populates="line_reviews")


class DocumentPage(Base):
    """One rasterized page of an imported document."""

    __tablename__ = "document_pages"
    __table_args__ = (
        UniqueConstraint("document_id", "page_index", name="uq_document_page_index"),
    )

    page_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=new_uuid
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("documents.document_id", ondelete="CASCADE"), nullable=False, index=True
    )
    page_index: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    original_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    raster_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    image_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid, ForeignKey("images.image_id", ondelete="SET NULL"), nullable=True
    )
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    dpi: Mapped[int] = mapped_column(Integer, nullable=False, default=200)
    rotation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    coordinate_space: Mapped[str] = mapped_column(
        String(64), nullable=False, default="page-raster-v1"
    )
    transforms: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[_dt.datetime] = created_at_column()
    updated_at: Mapped[_dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
    order_key: Mapped[str] = mapped_column(
        String(128), nullable=False, default="00000000000000000000"
    )
    paper_template: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    ink_asset_ref: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    active_ink_bundle_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid,
        ForeignKey("ink_bundles.bundle_id", ondelete="SET NULL"),
        nullable=True,
    )
    preview_asset_ref: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    page_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    document: Mapped["Document"] = relationship(back_populates="pages")
    regions: Mapped[list["DocumentRegion"]] = relationship(
        back_populates="page", cascade="all, delete-orphan", order_by="DocumentRegion.order_index"
    )


class DocumentRegion(Base):
    """A stable crop on a document page (prose, math, or figure)."""

    __tablename__ = "document_regions"

    region_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=new_uuid
    )
    page_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("document_pages.page_id", ondelete="CASCADE"), nullable=False, index=True
    )
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    content_type: Mapped[str] = mapped_column(String(32), nullable=False, default="math_display")
    bbox_x: Mapped[int] = mapped_column(Integer, nullable=False)
    bbox_y: Mapped[int] = mapped_column(Integer, nullable=False)
    bbox_width: Mapped[int] = mapped_column(Integer, nullable=False)
    bbox_height: Mapped[int] = mapped_column(Integer, nullable=False)
    coordinate_space: Mapped[str] = mapped_column(
        String(64), nullable=False, default="page-raster-v1"
    )
    created_at: Mapped[_dt.datetime] = created_at_column()
    updated_at: Mapped[_dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
    page_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    source_crop_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    crop_sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    page: Mapped["DocumentPage"] = relationship(back_populates="regions")
    predictions: Mapped[list["RegionPrediction"]] = relationship(
        back_populates="region", cascade="all, delete-orphan"
    )
    reviews: Mapped[list["RegionReview"]] = relationship(
        back_populates="region", cascade="all, delete-orphan"
    )
    review_revisions: Mapped[list["ReviewRevision"]] = relationship(
        back_populates="region", cascade="all, delete-orphan"
    )
    training_examples: Mapped[list["TrainingExample"]] = relationship(
        back_populates="region", cascade="all, delete-orphan"
    )


class RegionPrediction(Base):
    """One engine result for a region. Reruns create new rows; reviews stay."""

    __tablename__ = "region_predictions"

    prediction_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=new_uuid
    )
    region_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("document_regions.region_id", ondelete="CASCADE"), nullable=False, index=True
    )
    model_assignment_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid,
        ForeignKey("model_assignments.assignment_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    engine_id: Mapped[str] = mapped_column(String(64), nullable=False)
    engine_version: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    raw_output: Mapped[str] = mapped_column(Text, nullable=False, default="")
    score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ok")
    latency_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    alternatives: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    provenance: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    settings: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    # Immutable source provenance captured at inference time. The parent region
    # may later be moved/split/merged, so export and audit must not reconstruct
    # the old source crop from mutable current coordinates.
    source_asset_ref: Mapped[Optional[str]] = mapped_column(
        String(1024), nullable=True
    )
    source_crop_sha256: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True
    )
    source_bbox: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    source_page_revision: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )
    created_at: Mapped[_dt.datetime] = created_at_column()

    region: Mapped["DocumentRegion"] = relationship(back_populates="predictions")
    model_assignment: Mapped[Optional["ModelAssignment"]] = relationship()


class RegionReview(Base):
    """Immutable reviewed text/LaTeX for a region."""

    __tablename__ = "region_reviews"

    review_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=new_uuid
    )
    region_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("document_regions.region_id", ondelete="CASCADE"), nullable=False, index=True
    )
    prediction_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid, ForeignKey("region_predictions.prediction_id", ondelete="SET NULL"), nullable=True
    )
    reviewed_text: Mapped[str] = mapped_column(Text, nullable=False)
    excluded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    crop_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    crop_sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    author_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[_dt.datetime] = created_at_column()

    region: Mapped["DocumentRegion"] = relationship(back_populates="reviews")


class ModelArtifact(Base):
    """Recorded identity of a loadable recognition backend."""

    __tablename__ = "model_artifacts"

    artifact_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=new_uuid
    )
    backend_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    model_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    source_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    revision: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    upstream_commit: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    weight_sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    license: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    dtype: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    tokenizer_hash: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    processor_hash: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    processor_config: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    supported_inputs: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    benchmark_version: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    loaded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[_dt.datetime] = created_at_column()


class DocumentRevision(Base):
    """Immutable document-level snapshot used by export and audit workflows."""

    __tablename__ = "document_revisions"
    __table_args__ = (
        UniqueConstraint("document_id", "revision_number", name="uq_document_revision_number"),
    )

    revision_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("documents.document_id", ondelete="CASCADE"), nullable=False, index=True
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    author_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True
    )
    parent_revision_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid, nullable=True)
    # Snapshot of ordered block ids and review versions.  Crops/predictions
    # remain normalized in their own tables; this JSON is an audit pointer.
    block_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[_dt.datetime] = created_at_column()

    document: Mapped["Document"] = relationship(back_populates="revisions")


class DocumentOperation(Base):
    """One accepted, idempotent client mutation in the document sync log.

    ``server_sequence`` is the cursor exposed to clients.  It is deliberately
    database-assigned and global so a client can resume a pull after any
    process restart without relying on timestamps.  ``client_operation_id``
    is the caller's idempotency key; the same key is never applied twice for a
    user/client pair.
    """

    __tablename__ = "document_operations"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "client_id",
            "client_operation_id",
            name="uq_document_operation_client_key",
        ),
        Index(
            "ix_document_operations_document_sequence",
            "document_id",
            "server_sequence",
        ),
    )

    server_sequence: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    operation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, unique=True, default=new_uuid, nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("documents.document_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    client_id: Mapped[str] = mapped_column(String(128), nullable=False)
    client_operation_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    base_sync_version: Mapped[int] = mapped_column(Integer, nullable=False)
    document_sync_version: Mapped[int] = mapped_column(Integer, nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    operation_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    contract_version: Mapped[str] = mapped_column(
        String(16), nullable=False, default="v1"
    )
    is_tombstone: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    compacted_at: Mapped[Optional[_dt.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[_dt.datetime] = created_at_column()

    user: Mapped["User"] = relationship(back_populates="document_operations")
    document: Mapped["Document"] = relationship(back_populates="operations")


class DocumentConflict(Base):
    """A client mutation rejected by optimistic document-version checking.

    Conflicts retain the submitted operation payload instead of silently
    dropping it.  A client can inspect the record, choose a resolution, and
    retry as a new operation after reconciling with the server state.
    """

    __tablename__ = "document_conflicts"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "client_id",
            "client_operation_id",
            name="uq_document_conflict_client_key",
        ),
        Index(
            "ix_document_conflicts_document_status",
            "document_id",
            "status",
        ),
    )

    conflict_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=new_uuid
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("documents.document_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    client_id: Mapped[str] = mapped_column(String(128), nullable=False)
    client_operation_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    base_sync_version: Mapped[int] = mapped_column(Integer, nullable=False)
    current_sync_version: Mapped[int] = mapped_column(Integer, nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    operation_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending"
    )
    resolution: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    contract_version: Mapped[str] = mapped_column(
        String(16), nullable=False, default="v1"
    )
    is_tombstone: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    created_at: Mapped[_dt.datetime] = created_at_column()
    resolved_at: Mapped[Optional[_dt.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped["User"] = relationship(back_populates="document_conflicts")
    document: Mapped["Document"] = relationship(back_populates="conflicts")


class SyncClient(Base):
    """One registered client cursor owned by one account.

    A cursor is an acknowledgement, not a receipt merely because a pull was
    requested. This distinction prevents compaction from discarding a
    tombstone before an offline device has durably applied it.
    """

    __tablename__ = "sync_clients"
    __table_args__ = (
        UniqueConstraint("user_id", "client_id", name="uq_sync_client_user_client"),
        Index("ix_sync_clients_user_last_seen", "user_id", "last_seen_at"),
    )

    client_record_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=new_uuid
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    client_id: Mapped[str] = mapped_column(String(128), nullable=False)
    platform: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    device_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    cursor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_seen_at: Mapped[_dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
    revoked_at: Mapped[Optional[_dt.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[_dt.datetime] = created_at_column()

    user: Mapped["User"] = relationship(back_populates="sync_clients")


class SyncCursorState(Base):
    """Per-account cursor watermark produced by operation compaction."""

    __tablename__ = "sync_cursor_states"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.user_id", ondelete="CASCADE"),
        primary_key=True,
    )
    cursor_floor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[_dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="sync_cursor_state")


class DocumentSyncSnapshot(Base):
    """Compacted canonical state from which a stale client can resync."""

    __tablename__ = "document_sync_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "server_sequence",
            name="uq_document_sync_snapshot_sequence",
        ),
        Index(
            "ix_document_sync_snapshots_user_sequence",
            "user_id",
            "server_sequence",
        ),
    )

    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=new_uuid
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("documents.document_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    server_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    document_sync_version: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[_dt.datetime] = created_at_column()

    user: Mapped["User"] = relationship(back_populates="sync_snapshots")
    document: Mapped["Document"] = relationship(back_populates="sync_snapshots")


class ReviewRevision(Base):
    """Canonical immutable review revision for a document region."""

    __tablename__ = "review_revisions"

    review_revision_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=new_uuid
    )
    region_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("document_regions.region_id", ondelete="CASCADE"), nullable=False, index=True
    )
    prediction_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid, ForeignKey("region_predictions.prediction_id", ondelete="SET NULL"), nullable=True
    )
    document_revision_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid, ForeignKey("document_revisions.revision_id", ondelete="SET NULL"), nullable=True, index=True
    )
    reviewed_text: Mapped[str] = mapped_column(Text, nullable=False)
    excluded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    crop_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    crop_sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    author_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[_dt.datetime] = created_at_column()

    region: Mapped["DocumentRegion"] = relationship(back_populates="review_revisions")


class TrainingExample(Base):
    """Explicitly approved reviewed crop eligible for personalization."""

    __tablename__ = "training_examples"
    __table_args__ = (
        UniqueConstraint("user_id", "review_revision_id", name="uq_training_example_review"),
    )

    example_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True
    )
    region_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("document_regions.region_id", ondelete="CASCADE"), nullable=False, index=True
    )
    review_revision_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("review_revisions.review_revision_id", ondelete="CASCADE"), nullable=False
    )
    crop_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    split: Mapped[TrainingExampleSplit] = mapped_column(
        SAEnum(TrainingExampleSplit, name="training_example_split", native_enum=False),
        default=TrainingExampleSplit.UNASSIGNED,
        nullable=False,
    )
    consent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[_dt.datetime] = created_at_column()
    updated_at: Mapped[_dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="training_examples")
    region: Mapped["DocumentRegion"] = relationship(back_populates="training_examples")


class RecognitionJob(Base):
    """Database-backed page recognition job with retry/stale recovery metadata."""

    __tablename__ = "recognition_jobs"

    job_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid, ForeignKey("documents.document_id", ondelete="CASCADE"), nullable=True, index=True
    )
    page_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid, ForeignKey("document_pages.page_id", ondelete="CASCADE"), nullable=True, index=True
    )
    image_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid, ForeignKey("images.image_id", ondelete="CASCADE"), nullable=True, index=True
    )
    mode: Mapped[str] = mapped_column(String(32), nullable=False, default="formula")
    region_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    state: Mapped[RecognitionJobState] = mapped_column(
        SAEnum(RecognitionJobState, name="recognition_job_state", native_enum=False),
        default=RecognitionJobState.QUEUED,
        nullable=False,
        index=True,
    )
    progress_current: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    deadline_at: Mapped[Optional[_dt.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    result_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[_dt.datetime] = created_at_column()
    started_at: Mapped[Optional[_dt.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[_dt.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[_dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    user: Mapped["User"] = relationship()


class RecognitionRun(Base):
    """Immutable request envelope for a selected page/region conversion.

    ``RecognitionJob`` remains the worker lifecycle row used by the existing
    executor. This record is the stable API/audit contract: it captures the
    exact source selection and input hashes before any worker can observe a
    later crop edit.
    """

    __tablename__ = "recognition_runs"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "idempotency_key",
            name="uq_recognition_run_user_idempotency",
        ),
        Index(
            "ix_recognition_runs_document_created",
            "document_id",
            "created_at",
        ),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=new_uuid
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("documents.document_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    page_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("document_pages.page_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    job_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid,
        ForeignKey("recognition_jobs.job_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    contract_version: Mapped[str] = mapped_column(
        String(16), nullable=False, default="v1"
    )
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    idempotency_key: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid, nullable=True
    )
    source_snapshot: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    result_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[_dt.datetime] = created_at_column()
    updated_at: Mapped[_dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
    finished_at: Mapped[Optional[_dt.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped["User"] = relationship(back_populates="recognition_runs")
    document: Mapped["Document"] = relationship()
    page: Mapped["DocumentPage"] = relationship()


class UploadSession(Base):
    """Owner-scoped resumable upload manifest.

    Parts are content-addressed by their recorded digest and stored separately
    until ``complete`` verifies contiguous ordering, total size, and the final
    SHA-256. The original upload is never inferred from a filename or MIME hint.
    """

    __tablename__ = "upload_sessions"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "idempotency_key",
            name="uq_upload_session_user_idempotency",
        ),
        Index(
            "ix_upload_sessions_user_status",
            "user_id",
            "status",
        ),
    )

    upload_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=new_uuid
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    idempotency_key: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid, nullable=True
    )
    purpose: Mapped[str] = mapped_column(
        String(32), nullable=False, default="asset", server_default="asset"
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False, default="upload")
    content_type: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    expected_size: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    received_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    part_size: Mapped[int] = mapped_column(Integer, nullable=False, default=5 * 1024 * 1024)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    document_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid,
        ForeignKey("documents.document_id", ondelete="SET NULL"),
        nullable=True,
    )
    page_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid,
        ForeignKey("document_pages.page_id", ondelete="SET NULL"),
        nullable=True,
    )
    ink_bundle_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid,
        ForeignKey("ink_bundles.bundle_id", ondelete="SET NULL"),
        nullable=True,
    )
    assembled_ref: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    parts_manifest: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    error_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    expires_at: Mapped[_dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[_dt.datetime] = created_at_column()
    updated_at: Mapped[_dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
    completed_at: Mapped[Optional[_dt.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped["User"] = relationship(back_populates="upload_sessions")
    parts: Mapped[list["UploadPart"]] = relationship(
        back_populates="upload",
        cascade="all, delete-orphan",
        order_by="UploadPart.part_number",
    )


class UploadPart(Base):
    """One immutable, owner-derived part of a resumable upload."""

    __tablename__ = "upload_parts"
    __table_args__ = (
        UniqueConstraint("upload_id", "part_number", name="uq_upload_part_number"),
    )

    part_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=new_uuid
    )
    upload_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("upload_sessions.upload_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    part_number: Mapped[int] = mapped_column(Integer, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    created_at: Mapped[_dt.datetime] = created_at_column()

    upload: Mapped["UploadSession"] = relationship(back_populates="parts")


class DeletionJob(Base):
    """Durable evidence for an account deletion workflow."""

    __tablename__ = "deletion_jobs"

    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=new_uuid
    )
    # SET NULL preserves the deletion receipt after the user row is removed.
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid,
        ForeignKey("users.user_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    target_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deleted_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    target_refs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[_dt.datetime] = created_at_column()
    completed_at: Mapped[Optional[_dt.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped[Optional["User"]] = relationship(back_populates="deletion_jobs")


class TrainingPair(Base):
    """A labeled (region crop, corrected text) pair owned by a user.

    The unique constraint on ``(user_id, source_region_key)`` makes
    re-corrections an upsert rather than a duplicate (Req 4.7). ``image_id`` is
    nullable to allow calibration pairs that are not tied to a recognized image
    region. ``consumed_by_run_id`` is set when a *completed* training consumes
    the pair, which drives the unused-pair trigger count (Req 5.1).
    """

    __tablename__ = "training_pairs"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "source_region_key", name="uq_training_pair_user_region"
        ),
    )

    pair_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=new_uuid
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True
    )
    image_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid, ForeignKey("images.image_id", ondelete="SET NULL"), nullable=True
    )
    source_region_key: Mapped[str] = mapped_column(String(512), nullable=False)
    region_crop_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    # Label; accepts up to 10,000 characters per corrected word (Req 4.1).
    corrected_text: Mapped[str] = mapped_column(Text, nullable=False)
    origin: Mapped[TrainingPairOrigin] = mapped_column(
        SAEnum(TrainingPairOrigin, name="training_pair_origin"), nullable=False
    )
    consumed_by_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid,
        ForeignKey("training_runs.run_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[_dt.datetime] = created_at_column()
    updated_at: Mapped[_dt.datetime] = mapped_column(
        # updated_at advances on re-correction (Req 4.7).
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )

    user: Mapped["User"] = relationship(back_populates="training_pairs")
    consumed_by_run: Mapped[Optional["TrainingRun"]] = relationship(
        back_populates="consumed_pairs"
    )


class Adapter(Base):
    """A small per-user PEFT adapter; exactly one per user (Req 5.3)."""

    __tablename__ = "adapters"

    adapter_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=new_uuid
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.user_id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    weights_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    base_model_version: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[AdapterStatus] = mapped_column(
        SAEnum(AdapterStatus, name="adapter_status"),
        default=AdapterStatus.ACTIVE,
        nullable=False,
    )
    created_at: Mapped[_dt.datetime] = created_at_column()

    user: Mapped["User"] = relationship(back_populates="adapter")


class ModelAssignment(Base):
    """Immutable, auditable model resolution for one user and task.

    A row represents one exact base/adapter combination.  The resolver reuses
    an existing row for the same combination and retires the previous active
    row when a new combination is selected.  Predictions point at this row so
    a later model change can never rewrite the provenance of old output.
    """

    __tablename__ = "model_assignments"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "assignment_key", name="uq_model_assignment_user_key"
        ),
        Index(
            "uq_model_assignment_active_task",
            "user_id",
            "task",
            unique=True,
            sqlite_where=text("active = 1"),
            postgresql_where=text("active = true"),
        ),
    )

    assignment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=new_uuid
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    task: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    assignment_key: Mapped[str] = mapped_column(String(512), nullable=False)
    base_model_version: Mapped[str] = mapped_column(String(255), nullable=False)
    adapter_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid, ForeignKey("adapters.adapter_id", ondelete="SET NULL"), nullable=True
    )
    adapter_version: Mapped[str] = mapped_column(
        String(255), nullable=False, default="base"
    )
    activation_reason: Mapped[str] = mapped_column(
        String(64), nullable=False, default="base_default"
    )
    metrics: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[_dt.datetime] = created_at_column()
    retired_at: Mapped[Optional[_dt.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped["User"] = relationship(back_populates="model_assignments")


class TrainingRun(Base):
    """An adapter training attempt and its lifecycle state (Req 5.6, 5.7)."""

    __tablename__ = "training_runs"

    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=new_uuid
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True
    )
    state: Mapped[TrainingRunState] = mapped_column(
        SAEnum(TrainingRunState, name="training_run_state"),
        default=TrainingRunState.QUEUED,
        nullable=False,
    )
    epochs_run: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reject_reason: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    started_at: Mapped[Optional[_dt.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[Optional[_dt.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped["User"] = relationship(back_populates="training_runs")
    # Pairs consumed by this (completed) run; the membership/junction expressed
    # through TrainingPair.consumed_by_run_id (Req 5.1).
    consumed_pairs: Mapped[list["TrainingPair"]] = relationship(
        back_populates="consumed_by_run"
    )
    run_pairs: Mapped[list["TrainingRunPair"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    evaluation_result: Mapped[Optional["EvaluationResult"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", uselist=False
    )


class TrainingRunPair(Base):
    """Junction recording which pairs were used by a run and the held-out split.

    ``is_held_out`` distinguishes the reserved held-out set (Req 6.1) from the
    pairs used for training within a single run.
    """

    __tablename__ = "training_run_pairs"
    __table_args__ = (
        UniqueConstraint("run_id", "pair_id", name="uq_run_pair"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("training_runs.run_id", ondelete="CASCADE"), nullable=False
    )
    pair_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("training_pairs.pair_id", ondelete="CASCADE"), nullable=False
    )
    is_held_out: Mapped[bool] = mapped_column(default=False, nullable=False)

    run: Mapped["TrainingRun"] = relationship(back_populates="run_pairs")


class EvaluationResult(Base):
    """Before/after CER and WER measurements for a training run (Req 7.3)."""

    __tablename__ = "evaluation_results"

    eval_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=new_uuid
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("training_runs.run_id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Base model on the user's held-out set (Req 7.1).
    before_cer: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    before_wer: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # Adapter on the user's held-out set (Req 7.2).
    after_cer: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    after_wer: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # before - after (Req 7.3).
    delta_cer: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    delta_wer: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # Adapter on the general English eval set (Req 6.2).
    general_cer: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    general_wer: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    availability: Mapped[EvaluationAvailability] = mapped_column(
        SAEnum(EvaluationAvailability, name="evaluation_availability"),
        default=EvaluationAvailability.AVAILABLE,
        nullable=False,
    )

    run: Mapped["TrainingRun"] = relationship(back_populates="evaluation_result")
    user: Mapped["User"] = relationship(back_populates="evaluation_results")


class Asset(Base):
    """Owner-scoped binary object metadata (ink, rasters, originals, exports)."""

    __tablename__ = "assets"
    __table_args__ = (
        UniqueConstraint("user_id", "content_hash", "media_type", name="uq_asset_user_hash_type"),
        Index("ix_assets_user_created", "user_id", "created_at"),
    )

    asset_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=new_uuid
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    media_type: Mapped[str] = mapped_column(String(128), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    object_ref: Mapped[str] = mapped_column(String(1024), nullable=False)
    encryption_key_ref: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    retention_class: Mapped[str] = mapped_column(
        String(32), nullable=False, default="account"
    )
    scan_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending"
    )
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="upload")
    source_asset_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid, ForeignKey("assets.asset_id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[_dt.datetime] = created_at_column()

    user: Mapped["User"] = relationship(back_populates="assets")


class InkBundle(Base):
    """Validated ``ink_v1`` stroke bundle attached to one owner-scoped page."""

    __tablename__ = "ink_bundles"
    __table_args__ = (
        UniqueConstraint("page_id", "content_hash", name="uq_ink_bundle_page_hash"),
        Index(
            "uq_ink_bundle_page_revision",
            "page_id",
            "revision_number",
            unique=True,
        ),
        Index("ix_ink_bundles_page_created", "page_id", "created_at"),
    )

    bundle_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=new_uuid
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True
    )
    page_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("document_pages.page_id", ondelete="CASCADE"), nullable=False, index=True
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("assets.asset_id", ondelete="CASCADE"), nullable=False
    )
    schema_name: Mapped[str] = mapped_column(String(32), nullable=False, default="ink_v1")
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    parent_bundle_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid,
        ForeignKey("ink_bundles.bundle_id", ondelete="SET NULL"),
        nullable=True,
    )
    stroke_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    point_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    coordinate_space: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    capture: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    native_asset_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid, ForeignKey("assets.asset_id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[_dt.datetime] = created_at_column()

    user: Mapped["User"] = relationship(back_populates="ink_bundles")
    page: Mapped["DocumentPage"] = relationship(
        foreign_keys=[page_id],
    )
    asset: Mapped["Asset"] = relationship(foreign_keys=[asset_id])
    parent_bundle: Mapped[Optional["InkBundle"]] = relationship(
        remote_side=[bundle_id],
        foreign_keys=[parent_bundle_id],
        uselist=False,
    )


class ConsentEvent(Base):
    """Immutable record of a training-consent choice and its policy version."""

    __tablename__ = "consent_events"
    __table_args__ = (
        Index("ix_consent_events_user_created", "user_id", "created_at"),
    )

    event_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=new_uuid
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True
    )
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    scope: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    document_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid, ForeignKey("documents.document_id", ondelete="SET NULL"), nullable=True
    )
    sample_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    created_at: Mapped[_dt.datetime] = created_at_column()

    user: Mapped["User"] = relationship(back_populates="consent_events")


class UserVocabularyEntry(Base):
    """Level-0 personal dictionary used to rescore recognition candidates."""

    __tablename__ = "user_vocabulary_entries"
    __table_args__ = (
        UniqueConstraint("user_id", "token", name="uq_user_vocabulary_token"),
    )

    entry_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=new_uuid
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True
    )
    token: Mapped[str] = mapped_column(String(128), nullable=False)
    preferred_form: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[_dt.datetime] = created_at_column()

    user: Mapped["User"] = relationship(back_populates="vocabulary_entries")


class PasswordResetToken(Base):
    """Hashed, single-use password recovery token. The raw token is never stored."""

    __tablename__ = "password_reset_tokens"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True
    )
    expires_at: Mapped[_dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    used_at: Mapped[Optional[_dt.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[_dt.datetime] = created_at_column()
