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
