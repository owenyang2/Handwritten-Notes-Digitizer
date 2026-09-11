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
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    Uuid,
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


class UserSession(Base):
    """A persistent authentication session for a user."""

    __tablename__ = "user_sessions"

    token: Mapped[str] = mapped_column(String(255), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True
    )
    expires_at: Mapped[_dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[_dt.datetime] = created_at_column()

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
    created_at: Mapped[_dt.datetime] = created_at_column()

    region: Mapped["DocumentRegion"] = relationship(back_populates="predictions")


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
