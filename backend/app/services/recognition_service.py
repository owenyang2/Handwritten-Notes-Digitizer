"""Recognition_Service: orchestration with timeout and error handling (task 8.1).

This module owns the recognition orchestration layer described in the design's
Recognition_Service component. It:

1. Wraps :func:`~app.ml.recognition.recognize_bytes` with a strict 10-second
   wall-clock budget (Req 2.3, 2.6). A ``concurrent.futures.ThreadPoolExecutor``
   is used so the blocking ML inference runs off the asyncio event loop while
   remaining cancellable via ``Future.cancel()`` and the executor timeout.
2. On timeout: updates the image status to ``TIMEOUT``, commits, and returns a
   structured timeout-error result while **retaining the accepted image** (Req
   2.6).
3. On HTR engine failure: returns a structured recognition-failed error result
   **without modifying stored data** (Req 2.7). The image stays at ``ACCEPTED``.
4. On success with no text detected: updates image status to ``NO_TEXT``,
   commits, and returns a structured no-text result (Req 2.5).
5. On success with recognized text: updates image status to ``RECOGNIZED``,
   persists ``RecognizedWord`` rows, commits, and returns the result (Req 2.1,
   2.2, 2.4).

The orchestration logic is encapsulated in
:func:`run_recognition` (pure-ish service function) and exposed via two FastAPI
endpoints:

* ``POST /images/{image_id}/recognize`` — trigger recognition (Req 2.3–2.7).
* ``GET  /images/{image_id}/result``    — fetch the stored result for display
  (Req 3.1–3.4).

Both endpoints are authenticated (via ``get_current_user``) and scoped to the
requesting user: a request for a resource owned by another user returns 404 so
existence is not disclosed (Req 10.3, 10.5).
"""

from __future__ import annotations

import concurrent.futures
import os
import tempfile
import uuid
from enum import Enum
from typing import Annotated, List, Optional, Tuple

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.data.enums import ImageStatus
from app.data.models import Image, RecognizedWord, User
from app.data.object_store import ObjectStore
from app.data.repositories import RepositoryFactory
from app.ml.recognition import RecognitionResult, recognize_bytes
from app.services.auth_api import get_current_user, get_repositories
from app.services.capability_guard import require_capability
from app.services.image_validator import validate_image
from app.services.isolation import get_or_404

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

RECOGNITION_TIMEOUT_SECONDS: float = 10.0

# --------------------------------------------------------------------------- #
# Service-layer result types
# --------------------------------------------------------------------------- #


class RecognitionOutcome(str, Enum):
    """Top-level outcome of a recognition attempt."""

    SUCCESS = "success"
    NO_TEXT = "no_text"
    TIMEOUT = "timeout"
    FAILED = "failed"


class RegionSchema(BaseModel):
    """Source region of a recognized word in image pixel coordinates (Req 2.4)."""

    x: int
    y: int
    width: int
    height: int


class RecognizedWordSchema(BaseModel):
    """A single recognized word with its text, region, and confidence."""

    word_id: Optional[uuid.UUID] = None
    line_index: int
    word_index: int
    text: str
    region: RegionSchema
    confidence: Optional[float] = None


class RecognizedLineSchema(BaseModel):
    """An ordered line of recognized words."""

    line_index: int
    region: RegionSchema
    text: str = ""
    alignment_status: str = "aligned"
    alignment_reason: Optional[str] = None
    words: List[RecognizedWordSchema]


class RecognitionServiceResult(BaseModel):
    """The unified response envelope returned by the recognition service.

    ``outcome`` distinguishes success/no-text/timeout/failed.  ``lines`` is
    populated only for ``SUCCESS``.  ``error_message`` is populated for
    ``TIMEOUT`` and ``FAILED``.
    """

    outcome: RecognitionOutcome
    image_id: uuid.UUID
    lines: List[RecognizedLineSchema] = []
    image_width: Optional[int] = None
    image_height: Optional[int] = None
    error_message: Optional[str] = None


# --------------------------------------------------------------------------- #
# Adapter resolution: ObjectStore reference -> a locally openable file
# --------------------------------------------------------------------------- #


class _ResolvedAdapter:
    """Adapter proxy whose ``weights_path`` is a real, directly-openable path.

    ``app.ml.recognition._compose_model`` intentionally has no ``ObjectStore``
    dependency and just ``open()``s ``adapter.weights_path`` (this is also
    exactly what the ml-layer unit tests exercise it with). The persisted
    ``Adapter.weights_path`` on the ORM model, however, is an *object-store
    reference* (e.g. ``"adapters/<user>/<file>.pt"``), not a filesystem path —
    so it must be materialised to a real file first.
    """

    __slots__ = ("weights_path",)

    def __init__(self, weights_path: str) -> None:
        self.weights_path = weights_path


def _resolve_adapter_for_inference(
    adapter: object | None,
    object_store: ObjectStore,
) -> Tuple[object | None, Optional[str]]:
    """Materialise an ORM ``Adapter``'s object-store weights to a temp file.

    Returns ``(adapter_for_ml_layer, tmp_path_to_clean_up)``. When ``adapter``
    is ``None`` or has no ``weights_path``, returns ``(adapter, None)``
    unchanged so the base model is used (Req 5.5). The caller is responsible
    for deleting ``tmp_path_to_clean_up`` once inference has finished.
    """
    weights_ref = getattr(adapter, "weights_path", None) if adapter is not None else None
    if not weights_ref:
        return adapter, None

    weights_bytes = object_store.get(weights_ref)
    fd, tmp_path = tempfile.mkstemp(suffix=".pt")
    with os.fdopen(fd, "wb") as fh:
        fh.write(weights_bytes)
    return _ResolvedAdapter(tmp_path), tmp_path


def _line_alignment_metadata(line_text: str, word_count: int) -> tuple[str, Optional[str]]:
    """Return alignment status for mapping a decoded line onto word regions.

    The base model decodes a whole line, while the UI edits detected word boxes.
    A token/box count mismatch means at least one piece of text would be folded
    into the wrong box or assigned to an empty box, so the frontend should treat
    that line as line-level text instead of region-confident words.
    """
    token_count = len(line_text.split())
    if token_count == word_count:
        return "aligned", None
    return (
        "uncertain",
        f"Decoded {token_count} text token(s) for {word_count} detected word region(s).",
    )


# --------------------------------------------------------------------------- #
# Core orchestration function (testable without FastAPI)
# --------------------------------------------------------------------------- #


def run_recognition(
    image: Image,
    repos: RepositoryFactory,
    object_store: ObjectStore,
    adapter: object | None = None,
    timeout: float = RECOGNITION_TIMEOUT_SECONDS,
) -> RecognitionServiceResult:
    """Orchestrate HTR recognition for *image* with timeout and error handling.

    This function is deliberately decoupled from FastAPI so it can be unit-
    tested without standing up an HTTP server.

    Parameters
    ----------
    image:
        The ``ACCEPTED`` :class:`~app.data.models.Image` to recognize.
    repos:
        Scoped repository factory; used to update image status and persist
        recognized words.
    object_store:
        Object store used to read the raw image bytes from *image.storage_path*.
    adapter:
        Optional per-user adapter (passed through to the HTR engine).  ``None``
        means use the base model only (Req 5.5).
    timeout:
        Wall-clock budget in seconds (default 10 s, Req 2.6).

    Returns
    -------
    RecognitionServiceResult
        A structured result whose ``outcome`` is one of:
        * ``SUCCESS``  — text was recognized; ``lines`` is populated.
        * ``NO_TEXT``  — recognition produced no text (Req 2.5).
        * ``TIMEOUT``  — budget overrun; image retained at ``TIMEOUT`` status
          (Req 2.6).
        * ``FAILED``   — HTR engine raised; stored data unchanged (Req 2.7).
    """
    image_id = image.image_id

    # Read the raw image bytes from the object store.
    try:
        image_bytes = object_store.get(image.storage_path)
    except Exception as exc:  # storage read failure → treat as engine failure
        return RecognitionServiceResult(
            outcome=RecognitionOutcome.FAILED,
            image_id=image_id,
            error_message=f"Failed to read image from storage: {exc}",
        )

    # app.ml.recognition._compose_model opens ``adapter.weights_path`` as a
    # plain filesystem path (it deliberately has no ObjectStore dependency —
    # the ml package stays decoupled from the data layer). The persisted
    # ``Adapter.weights_path`` is instead an *object-store reference*
    # (e.g. "adapters/<user>/<file>.pt", written by
    # personalization_service.complete_training_run via
    # ObjectStore.put_adapter_weights), so it must be resolved through the
    # object store here — the two are not the same thing, and passing the
    # reference straight through fails with "No such file or directory".
    adapter_tmp_path: Optional[str] = None
    try:
        try:
            resolved_adapter, adapter_tmp_path = _resolve_adapter_for_inference(
                adapter, object_store
            )
        except Exception as exc:
            # Missing/corrupt adapter weights are an engine-level failure
            # (Req 2.7) — stored data (and the prior-good adapter) is
            # untouched; the user can retry or the adapter can be retrained.
            return RecognitionServiceResult(
                outcome=RecognitionOutcome.FAILED,
                image_id=image_id,
                error_message=f"Failed to load adapter weights: {exc}",
            )

        # Run the blocking HTR inference in a thread so we can enforce a
        # wall-clock timeout without blocking the event loop.
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future: concurrent.futures.Future[RecognitionResult] = executor.submit(
            recognize_bytes, image_bytes, resolved_adapter
        )

        try:
            result: RecognitionResult = future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            # Cancel the inflight work and shut the thread down (best-effort).
            future.cancel()
            executor.shutdown(wait=False)

            # Update status to TIMEOUT; retain the image (Req 2.6).
            image.status = ImageStatus.TIMEOUT
            repos.session.commit()

            return RecognitionServiceResult(
                outcome=RecognitionOutcome.TIMEOUT,
                image_id=image_id,
                error_message="Recognition did not complete within the time budget.",
            )
        except Exception as exc:
            # HTR engine raised an unexpected error (Req 2.7).
            executor.shutdown(wait=False)

            # Do NOT modify stored data — image stays at ACCEPTED.
            return RecognitionServiceResult(
                outcome=RecognitionOutcome.FAILED,
                image_id=image_id,
                error_message=f"Recognition engine failure: {exc}",
            )
        finally:
            executor.shutdown(wait=False)

        # ---- Successful engine run ----
        if not result.recognized_any_text:
            # Engine ran but found nothing (Req 2.5).
            image.status = ImageStatus.NO_TEXT
            repos.session.commit()

            return RecognitionServiceResult(
                outcome=RecognitionOutcome.NO_TEXT,
                image_id=image_id,
                image_width=result.image_width,
                image_height=result.image_height,
            )

        # Persist recognized words and update status (Req 2.1, 2.2, 2.4).
        word_rows: list[RecognizedWord] = []
        line_schemas: list[RecognizedLineSchema] = []

        for line in result.lines:
            word_schemas: list[RecognizedWordSchema] = []
            line_text = getattr(line, "text", "").strip()
            if not line_text:
                line_text = " ".join(word.text for word in line.words if word.text)
            alignment_status, alignment_reason = _line_alignment_metadata(
                line_text, len(line.words)
            )

            for word in line.words:
                row = RecognizedWord(
                    image_id=image_id,
                    line_index=line.index,
                    word_index=word.index,
                    text=word.text,
                    region_x=word.region.x,
                    region_y=word.region.y,
                    region_width=word.region.width,
                    region_height=word.region.height,
                    confidence=word.confidence,
                )
                word_rows.append(row)
                word_schemas.append(
                    RecognizedWordSchema(
                        line_index=line.index,
                        word_index=word.index,
                        text=word.text,
                        region=RegionSchema(
                            x=word.region.x,
                            y=word.region.y,
                            width=word.region.width,
                            height=word.region.height,
                        ),
                        confidence=word.confidence,
                    )
                )

            line_schemas.append(
                RecognizedLineSchema(
                    line_index=line.index,
                    region=RegionSchema(
                        x=line.region.x,
                        y=line.region.y,
                        width=line.region.width,
                        height=line.region.height,
                    ),
                    text=line_text,
                    alignment_status=alignment_status,
                    alignment_reason=alignment_reason,
                    words=word_schemas,
                )
            )

        repos.recognized_words.add_all(word_rows)
        image.status = ImageStatus.RECOGNIZED
        repos.session.commit()

        # Back-fill word_ids from the flushed rows.
        for row, schema in zip(word_rows, [w for ls in line_schemas for w in ls.words]):
            schema.word_id = row.word_id

        return RecognitionServiceResult(
            outcome=RecognitionOutcome.SUCCESS,
            image_id=image_id,
            lines=line_schemas,
            image_width=result.image_width,
            image_height=result.image_height,
        )
    finally:
        # Clean up the ephemeral adapter weights file (Req: no adapter bytes
        # left lying around outside the object store after the request ends).
        if adapter_tmp_path is not None:
            try:
                os.unlink(adapter_tmp_path)
            except OSError:
                pass


# --------------------------------------------------------------------------- #
# FastAPI router
# --------------------------------------------------------------------------- #

router = APIRouter(tags=["recognition"])


def _get_object_store_dep() -> ObjectStore:
    """FastAPI dependency: provides the object store (overridable in tests)."""
    from app.services.images_api import get_object_store
    return get_object_store()


# ---- POST /images/{image_id}/recognize ------------------------------------ #


class RecognizeResponse(BaseModel):
    """Response envelope for POST /images/{image_id}/recognize."""

    outcome: RecognitionOutcome
    image_id: uuid.UUID
    lines: List[RecognizedLineSchema] = []
    image_width: Optional[int] = None
    image_height: Optional[int] = None
    error_message: Optional[str] = None


@router.post(
    "/images/{image_id}/recognize",
    response_model=RecognizeResponse,
    status_code=status.HTTP_200_OK,
    summary="Trigger recognition for an accepted image",
    description=(
        "Runs the HTR engine on the identified image with a 10-second timeout. "
        "Returns the recognition result, a no-text indicator, a timeout error "
        "(image retained), or an engine-failure error (data unchanged). "
        "Requires authentication. Returns 503 when the recognition capability "
        "is unavailable (Req 11.6)."
    ),
)
def recognize_image(
    image_id: uuid.UUID,
    _guard: None = Depends(require_capability("recognition")),
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
    store: ObjectStore = Depends(_get_object_store_dep),
) -> RecognizeResponse:
    """POST /images/{image_id}/recognize — run recognition (Req 2.3–2.7, 10.3).

    Scoped to the authenticated user: returns 404 for images owned by others
    (Req 10.3, 10.5).
    """
    image: Image = get_or_404(repos.images.get(user.user_id, image_id))

    # Select user's adapter if one exists (Req 5.4, 5.5 — wired fully in 8.2).
    adapter = repos.adapters.get_active_for_user(user.user_id)

    svc_result = run_recognition(image, repos, store, adapter=adapter)

    return RecognizeResponse(
        outcome=svc_result.outcome,
        image_id=svc_result.image_id,
        lines=svc_result.lines,
        image_width=svc_result.image_width,
        image_height=svc_result.image_height,
        error_message=svc_result.error_message,
    )


# ---- GET /images/{image_id}/result ---------------------------------------- #


class RecognitionResultResponse(BaseModel):
    """Response envelope for GET /images/{image_id}/result."""

    image_id: uuid.UUID
    status: ImageStatus
    image_width: Optional[int] = None
    image_height: Optional[int] = None
    lines: List[RecognizedLineSchema] = []


def _processed_dimensions(
    image: Image, object_store: ObjectStore
) -> tuple[Optional[int], Optional[int]]:
    """Return dimensions for the image coordinate space used by regions."""
    from app.ml.preprocessing import preprocess

    try:
        processed = preprocess(object_store.get(image.storage_path))
    except Exception:
        return None, None
    return processed.width, processed.height


@router.get(
    "/images/{image_id}/result",
    response_model=RecognitionResultResponse,
    status_code=status.HTTP_200_OK,
    summary="Fetch the recognition result for an image",
    description=(
        "Returns the image's current status and any recognized words stored "
        "for it. Requires authentication. Returns 404 for images owned by "
        "other users."
    ),
)
def get_recognition_result(
    image_id: uuid.UUID,
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
    store: ObjectStore = Depends(_get_object_store_dep),
) -> RecognitionResultResponse:
    """GET /images/{image_id}/result — fetch stored result (Req 3.1–3.4).

    Builds ``RecognizedLineSchema`` objects from the stored
    :class:`~app.data.models.RecognizedWord` rows in reading order
    (line_index asc, word_index asc — guaranteed by
    ``RecognizedWordRepository.list_for_image``).
    """
    image: Image = get_or_404(repos.images.get(user.user_id, image_id))
    word_rows = repos.recognized_words.list_for_image(user.user_id, image_id)

    # Group into lines by line_index.
    lines_by_index: dict[int, RecognizedLineSchema] = {}
    for row in word_rows:
        if row.line_index not in lines_by_index:
            # Placeholder region — updated once we have the first word of each line.
            lines_by_index[row.line_index] = RecognizedLineSchema(
                line_index=row.line_index,
                region=RegionSchema(x=0, y=0, width=0, height=0),
                text="",
                words=[],
            )
        word_schema = RecognizedWordSchema(
            word_id=row.word_id,
            line_index=row.line_index,
            word_index=row.word_index,
            text=row.text,
            region=RegionSchema(
                x=row.region_x,
                y=row.region_y,
                width=row.region_width,
                height=row.region_height,
            ),
            confidence=row.confidence,
        )
        lines_by_index[row.line_index].words.append(word_schema)

    lines = sorted(lines_by_index.values(), key=lambda ln: ln.line_index)
    for line in lines:
        if line.words:
            left = min(word.region.x for word in line.words)
            top = min(word.region.y for word in line.words)
            right = max(word.region.x + word.region.width for word in line.words)
            bottom = max(word.region.y + word.region.height for word in line.words)
            line.region = RegionSchema(
                x=left,
                y=top,
                width=right - left,
                height=bottom - top,
            )
        line.text = " ".join(word.text for word in line.words if word.text).strip()
        line.alignment_status, line.alignment_reason = _line_alignment_metadata(
            line.text, len(line.words)
        )
    image_width, image_height = _processed_dimensions(image, store)

    return RecognitionResultResponse(
        image_id=image.image_id,
        status=image.status,
        image_width=image_width,
        image_height=image_height,
        lines=lines,
    )


__all__ = [
    "router",
    "run_recognition",
    "RecognitionOutcome",
    "RecognitionServiceResult",
    "RegionSchema",
    "RecognizedWordSchema",
    "RecognizedLineSchema",
    "RecognizeResponse",
    "RecognitionResultResponse",
    "RECOGNITION_TIMEOUT_SECONDS",
]
