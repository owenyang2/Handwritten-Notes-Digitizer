"""Correction_Pipeline service: training-pair capture on confirmed edits.

This module implements the design's Correction_Pipeline component (Requirements
4.3–4.8):

* ``submit_correction`` - the core business logic. On a confirmed edit where
  ``corrected_text`` differs from ``recognized_text``, a ``TrainingPair`` is
  created (with the region crop stored in the object store) or updated (upsert
  keyed by ``(user_id, source_region_key)``). When the texts are identical no
  write is performed and ``NoChange`` is returned.

* ``POST /images/{image_id}/corrections`` - the HTTP endpoint that authenticates
  the caller, verifies image ownership, and delegates to ``submit_correction``.

Design details
--------------
* **Deterministic region key**: ``f"{image_id}:{region.x}:{region.y}:{region.width}:{region.height}"``
  is the stable key that makes re-corrections an upsert (Req 4.7).
* **Region crop**: the original image bytes are fetched from the object store,
  cropped to the word's bounding box, and the crop is stored back in the object
  store. The crop path is persisted on the ``TrainingPair`` record (Req 4.3).
* **Origin**: correction pairs always carry ``TrainingPairOrigin.CORRECTION``
  (Req 9.5 – both correction and calibration origins feed the same pipeline).
* **Persistence within 5 s**: the synchronous DB commit on the FastAPI path
  ensures the pair is persisted within the normal request cycle, well inside the
  5-second budget (Req 4.5).
* **Storage failure handling**: any exception from the object store or DB is
  caught and mapped to ``StorageError``; the caller (the HTTP handler) then
  returns a 500 so the Editor retains the displayed text and prior pairs are
  unchanged (Req 4.6).
* **No write while unconfirmed**: ``submit_correction`` is only called on
  *confirmed* edits (Req 4.8 — the frontend/tests are responsible for not
  calling on every keystroke; this service stores nothing unless called).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from io import BytesIO
from typing import Union

from app.data.enums import TrainingPairOrigin
from app.data.models import TrainingPair
from app.data.object_store import ObjectStore
from app.data.repositories import RepositoryFactory
from app.services.personalization_service import (
    maybe_enqueue_training,
)


# ---------------------------------------------------------------------------
# Domain value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Region:
    """A rectangular bounding box in image pixel coordinates."""

    x: int
    y: int
    width: int
    height: int


# ---------------------------------------------------------------------------
# Return-type union for submit_correction
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Created:
    """A new ``TrainingPair`` was created."""

    pair_id: uuid.UUID


@dataclass(frozen=True)
class Updated:
    """An existing ``TrainingPair`` for the region was updated (re-correction)."""

    pair_id: uuid.UUID


@dataclass(frozen=True)
class NoChange:
    """The corrected text equals the recognized text; nothing was written."""


@dataclass(frozen=True)
class StorageError:
    """A storage failure occurred; prior pairs are unchanged (Req 4.6)."""

    message: str


SubmitCorrectionResult = Union[Created, Updated, NoChange, StorageError]


# ---------------------------------------------------------------------------
# Region key helper
# ---------------------------------------------------------------------------


def make_region_key(image_id: uuid.UUID, region: Region) -> str:
    """Build the deterministic key that identifies a corrected region.

    The key is stable across re-corrections of the same word, which is what
    drives the upsert (Req 4.7) and guarantees no duplicates.
    """
    return f"{image_id}:{region.x}:{region.y}:{region.width}:{region.height}"


# ---------------------------------------------------------------------------
# Core business logic
# ---------------------------------------------------------------------------


def _crop_image(image_bytes: bytes, region: Region) -> tuple[bytes, Region]:
    """Crop ``image_bytes`` to ``region`` and return (PNG bytes, clamped region).

    Regions submitted by the editor are in the backend-preprocessed image
    coordinate space, so the crop must come from that same preprocessed image
    rather than the raw upload. The crop is clamped to the processed image
    dimensions so out-of-range regions produce the overlap area rather than an
    error.
    """
    from PIL import Image

    from app.ml.preprocessing import preprocess

    processed = preprocess(image_bytes)
    image = Image.fromarray(processed.gray).convert("RGB")
    iw, ih = image.size
    # Clamp to bounds.
    x0 = max(0, region.x)
    y0 = max(0, region.y)
    x1 = min(iw, region.x + region.width)
    y1 = min(ih, region.y + region.height)
    crop = image.crop((x0, y0, x1, y1))
    clamped_region = Region(x=x0, y=y0, width=x1 - x0, height=y1 - y0)
    buf = BytesIO()
    crop.save(buf, format="PNG")
    return buf.getvalue(), clamped_region


def _apply_correction_to_recognized_word(
    repos: RepositoryFactory,
    user_id: uuid.UUID,
    image_id: uuid.UUID,
    region: Region,
    corrected_text: str,
    line_index: int | None,
    word_index: int | None,
) -> None:
    """Update the stored ``RecognizedWord`` text so the correction is reflected.

    Export and any subsequent result fetch assemble text from ``RecognizedWord``
    rows, so a confirmed correction must update the matching word's text
    (Req 8.3). The word is matched by (line_index, word_index) when those are
    provided, otherwise by an exact region match. If no matching word is found
    (e.g. a synthetic/calibration correction with no recognized word), this is a
    no-op.
    """
    words = repos.recognized_words.list_for_image(user_id, image_id)
    match = None
    if line_index is not None and word_index is not None:
        for w in words:
            if w.line_index == line_index and w.word_index == word_index:
                match = w
                break
    if match is None:
        for w in words:
            if (
                w.region_x == region.x
                and w.region_y == region.y
                and w.region_width == region.width
                and w.region_height == region.height
            ):
                match = w
                break
    if match is not None and match.text != corrected_text:
        match.text = corrected_text
        repos.session.flush()


def submit_correction(
    user_id: uuid.UUID,
    image_id: uuid.UUID,
    region: Region,
    recognized_text: str,
    corrected_text: str,
    repos: RepositoryFactory,
    object_store: ObjectStore,
    line_index: int | None = None,
    word_index: int | None = None,
    store_if_unchanged: bool = False,
) -> SubmitCorrectionResult:
    """Capture a confirmed correction as a ``TrainingPair`` when the text changed.

    Parameters
    ----------
    user_id:
        The authenticated user making the correction.
    image_id:
        The image the corrected word belongs to (ownership already verified by
        the caller).
    region:
        Bounding box of the corrected word in image pixel coordinates.
    recognized_text:
        The original recognized text for the word.
    corrected_text:
        The user's replacement text after editing.
    repos:
        Repository factory scoped to the request session.
    object_store:
        Object store used to fetch the source image and persist the crop.

    Returns
    -------
    Created
        A new training pair was created (corrected_text ≠ recognized_text and
        no prior pair for this region existed).
    Updated
        An existing pair for this region was updated with the new text.
    NoChange
        corrected_text == recognized_text; nothing was stored (Req 4.4).
    StorageError
        A storage-layer failure occurred; no data was changed (Req 4.6).
    """
    # Req 4.4 / 4.8: identical word edit → no write. Reviewed line crops may
    # intentionally be saved even when the recognizer was already correct,
    # because a verified line image + transcription is useful training data.
    if corrected_text == recognized_text and not store_if_unchanged:
        return NoChange()

    try:
        # Fetch the source image bytes so we can extract the region crop.
        image_record = repos.images.get(user_id, image_id)
        if image_record is None:
            return StorageError("Image not found for the requesting user.")

        image_bytes = object_store.get(image_record.storage_path)

        # Crop the word region from the full image.
        crop_bytes, clamped_region = _crop_image(image_bytes, region)

        # Persist the crop in the object store (Req 4.3).
        crop_path = object_store.put_region_crop(
            str(user_id), crop_bytes, suffix=".png"
        )

        # Build the deterministic region key (Req 4.7).
        region_key = make_region_key(image_id, clamped_region)

        # Upsert: look for an existing pair for this (user, region) (Req 4.7).
        existing = repos.training_pairs.get_by_region_key(user_id, region_key)

        if existing is not None:
            # Update the corrected text and crop path in-place.
            existing.corrected_text = corrected_text
            existing.region_crop_path = crop_path
            repos.session.flush()
            # Reflect the correction in the stored recognized word so export and
            # result fetches show the corrected text (Req 8.3).
            _apply_correction_to_recognized_word(
                repos,
                user_id,
                image_id,
                # Use the originally supplied region for matching so that we
                # update the exact `RecognizedWord.region_*` values that the
                # Editor stored (indices may be omitted).
                region,
                corrected_text,
                line_index, word_index,
            )
            # Enqueue personalization if unused-pair threshold is reached (Req 5.1).
            # The TrainingRun row (if any) is committed in the same transaction.
            maybe_enqueue_training(user_id, repos)
            repos.session.commit()
            return Updated(pair_id=existing.pair_id)

        # Create a fresh pair (Req 4.3, 4.5).
        pair = repos.training_pairs.add(
            TrainingPair(
                user_id=user_id,
                image_id=image_id,
                source_region_key=region_key,
                region_crop_path=crop_path,
                corrected_text=corrected_text,
                origin=TrainingPairOrigin.CORRECTION,
            )
        )
        repos.session.flush()
        # Reflect the correction in the stored recognized word so export and
        # result fetches show the corrected text (Req 8.3).
        _apply_correction_to_recognized_word(
            repos,
            user_id,
            image_id,
            # Use the originally supplied region for matching so the stored
            # RecognizedWord is updated even when the crop clamped to bounds.
            region,
            corrected_text,
            line_index, word_index,
        )
        # Enqueue personalization if unused-pair threshold is reached (Req 5.1).
        # The new TrainingRun (if any) is committed together with the new pair.
        maybe_enqueue_training(user_id, repos)
        repos.session.commit()
        return Created(pair_id=pair.pair_id)

    except Exception as exc:  # noqa: BLE001
        # Any storage failure (object store I/O, DB error, etc.) must not leave
        # partial writes: roll back any pending changes and return StorageError
        # so prior pairs are unchanged (Req 4.6).
        try:
            repos.session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return StorageError(str(exc))


# ---------------------------------------------------------------------------
# FastAPI router
# ---------------------------------------------------------------------------

import uuid as _uuid_mod  # noqa: E402  (kept at bottom to avoid circular imports)

from fastapi import APIRouter, Depends, status as http_status  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from app.data.models import User  # noqa: E402
from app.data.object_store import FilesystemObjectStore  # noqa: E402
from app.services.auth_api import get_current_user, get_repositories  # noqa: E402
from app.services.capability_guard import require_capability  # noqa: E402
from app.services.isolation import get_or_404  # noqa: E402

router = APIRouter(tags=["corrections"])


# --------------------------------------------------------------------------- #
# Object store dependency (mirrors images_api pattern)
# --------------------------------------------------------------------------- #

from functools import lru_cache  # noqa: E402


@lru_cache(maxsize=1)
def _default_correction_object_store() -> ObjectStore:
    """Process-wide default object store for the correction pipeline."""
    return FilesystemObjectStore()


def get_correction_object_store() -> ObjectStore:
    """Provide the object store. Overridden in tests for isolation."""
    return _default_correction_object_store()


# --------------------------------------------------------------------------- #
# Request / response schemas
# --------------------------------------------------------------------------- #


class RegionSchema(BaseModel):
    """Bounding box of the corrected word in image pixel coordinates."""

    x: int = Field(..., ge=0, description="Left edge in pixels")
    y: int = Field(..., ge=0, description="Top edge in pixels")
    width: int = Field(..., gt=0, description="Width in pixels")
    height: int = Field(..., gt=0, description="Height in pixels")


class CorrectionRequest(BaseModel):
    """Payload for ``POST /images/{image_id}/corrections``."""

    region: RegionSchema
    recognized_text: str = Field(
        ..., description="The original recognized text for the word"
    )
    corrected_text: str = Field(
        ...,
        max_length=10_000,
        description="The user's corrected text (up to 10,000 characters, Req 4.1)",
    )
    line_index: int | None = Field(
        default=None, description="Line index of the corrected word (reading order)"
    )
    word_index: int | None = Field(
        default=None, description="Word index of the corrected word within its line"
    )
    store_if_unchanged: bool = Field(
        default=False,
        description="Persist reviewed line crops even when recognized/corrected text match",
    )


class CorrectionCreatedResponse(BaseModel):
    """Returned when a new training pair is created."""

    status: str = "created"
    pair_id: _uuid_mod.UUID


class CorrectionUpdatedResponse(BaseModel):
    """Returned when an existing pair is updated (re-correction)."""

    status: str = "updated"
    pair_id: _uuid_mod.UUID


class CorrectionNoChangeResponse(BaseModel):
    """Returned when corrected text equals recognized text; nothing stored."""

    status: str = "no_change"


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #


@router.post(
    "/images/{image_id}/corrections",
    status_code=http_status.HTTP_200_OK,
    summary="Submit a confirmed word correction",
    description=(
        "On a confirmed edit, creates or updates a Training_Pair when the "
        "corrected text differs from the recognized text. When texts are "
        "identical, returns no_change without any storage write. "
        "Requires authentication. Returns 503 when the correction capability "
        "is unavailable (Req 11.6)."
    ),
)
def submit_correction_endpoint(
    image_id: _uuid_mod.UUID,
    payload: CorrectionRequest,
    _guard: None = Depends(require_capability("correction")),
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
    store: ObjectStore = Depends(get_correction_object_store),
):
    """POST /images/{image_id}/corrections (Req 4.3–4.8, 10.4).

    Ownership of the image is enforced through the per-user-scoped repository:
    ``repos.images.get(user_id, image_id)`` returns ``None`` for images owned
    by another user, and ``submit_correction`` maps that to ``StorageError``
    (image not found), which becomes a 404 via :func:`get_or_404` on the image
    lookup path.
    """
    from fastapi import HTTPException

    region = Region(
        x=payload.region.x,
        y=payload.region.y,
        width=payload.region.width,
        height=payload.region.height,
    )

    result = submit_correction(
        user_id=user.user_id,
        image_id=image_id,
        region=region,
        recognized_text=payload.recognized_text,
        corrected_text=payload.corrected_text,
        repos=repos,
        object_store=store,
        line_index=payload.line_index,
        word_index=payload.word_index,
        store_if_unchanged=payload.store_if_unchanged,
    )

    if isinstance(result, Created):
        return CorrectionCreatedResponse(pair_id=result.pair_id)
    if isinstance(result, Updated):
        return CorrectionUpdatedResponse(pair_id=result.pair_id)
    if isinstance(result, NoChange):
        return CorrectionNoChangeResponse()
    if isinstance(result, StorageError):
        # Image-not-found is a 404; other storage failures are 500.
        if "not found" in result.message.lower():
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail=result.message,
            )
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=result.message,
        )


__all__ = [
    "Region",
    "Created",
    "Updated",
    "NoChange",
    "StorageError",
    "SubmitCorrectionResult",
    "make_region_key",
    "submit_correction",
    "router",
    "get_correction_object_store",
    "RegionSchema",
    "CorrectionRequest",
    "CorrectionCreatedResponse",
    "CorrectionUpdatedResponse",
    "CorrectionNoChangeResponse",
    "submit_correction_endpoint",
]
