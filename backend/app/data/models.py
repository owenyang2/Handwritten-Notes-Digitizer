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
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Integer,
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
    adapter: Mapped[Optional["Adapter"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )
    training_runs: Mapped[list["TrainingRun"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    evaluation_results: Mapped[list["EvaluationResult"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
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
    recognized_words: Mapped[list["RecognizedWord"]] = relationship(
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
