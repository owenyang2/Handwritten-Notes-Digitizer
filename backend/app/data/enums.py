"""Enumerations used by the relational data model.

These mirror the design's Data Models section exactly. Using string-valued
``enum.Enum`` subclasses keeps stored values stable and human-readable.
"""

from __future__ import annotations

import enum


class ImageFormat(str, enum.Enum):
    """Accepted image formats, determined from decoded content (Req 1.1, 1.2)."""

    PNG = "PNG"
    JPEG = "JPEG"


class ImageStatus(str, enum.Enum):
    """Lifecycle status of an uploaded image (Req 2.5-2.7)."""

    ACCEPTED = "accepted"
    RECOGNIZED = "recognized"
    NO_TEXT = "no_text"
    TIMEOUT = "timeout"
    ERROR = "error"


class TrainingPairOrigin(str, enum.Enum):
    """Where a training pair came from; both feed the same pipeline (Req 9.5, 9.6)."""

    CORRECTION = "correction"
    CALIBRATION = "calibration"


class AdapterStatus(str, enum.Enum):
    """Active/superseded status of a per-user adapter (Req 5.3)."""

    ACTIVE = "active"
    SUPERSEDED = "superseded"


class TrainingRunState(str, enum.Enum):
    """State machine for an adapter training run (Req 5.6, 5.7, 6.3, 6.4)."""

    QUEUED = "queued"
    RUNNING = "running"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    FAILED = "failed"


class EvaluationAvailability(str, enum.Enum):
    """Whether an accuracy measurement is available (Req 7.6, 7.7)."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class RecognitionJobState(str, enum.Enum):
    """Durable state for page/image recognition work.

    These values are intentionally independent of the legacy ``ImageStatus``
    values: an image can remain accepted while a retry is queued, and a page
    job can contain several independently persisted formula predictions.
    """

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    NO_TEXT = "no_text"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class TrainingExampleSplit(str, enum.Enum):
    """Auditable split assignment for reviewed examples."""

    TRAIN = "train"
    DEV = "dev"
    TEST = "test"
    UNASSIGNED = "unassigned"


class ModelTask(str, enum.Enum):
    """Recognition task names used by the model-assignment registry.

    These values are deliberately stored as ordinary strings in the registry
    so adding a new task does not require a database enum migration.  The enum
    still gives callers one canonical vocabulary at the application boundary.
    """

    PROSE_RASTER = "prose_raster"
    MATH_RASTER = "math_raster"
    MATH_INK = "math_ink"
    RERANKER = "reranker"
    MIXED_RASTER = "mixed_raster"
    FIGURE = "figure"
