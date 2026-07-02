"""Calibration_Service: optional cold-start flow for the Handwriting Notes Digitizer.

This module implements:

Task 12.1 — calibration session prompt generation:

* ``create_calibration_session`` — pure business logic. Generates a session
  with between 3 and 10 prompt sentences, each between 1 and 200 characters in
  length, and stores the session (keyed by ``session_id``) in the in-memory
  session store. Returns the ``session_id`` and the list of prompt strings.

* ``POST /calibration/session`` — the authenticated HTTP endpoint that creates
  and returns a new calibration session (Req 9.1, 9.2).

Task 12.2 — calibration sample submission:

* ``submit_calibration_sample`` — pure business logic. Validates the submitted
  image (non-empty, ≤10 MB, decodable PNG/JPEG, detectable handwriting region),
  then creates a ``Training_Pair`` with ``origin=CALIBRATION`` using the known
  prompt text and stores it in the same repository as correction pairs (Req 9.3,
  9.4, 9.5). Invalid submissions return a ``CalibrationRejection`` with a reason
  and create no pair.

* ``POST /calibration/{session_id}/samples`` — the authenticated HTTP endpoint
  that accepts a multipart upload (image file + prompt_index) and delegates to
  ``submit_calibration_sample`` (Req 9.3, 9.4, 9.5).

Design notes (Requirements 9.1–9.5)
--------------------------------------
* **Optionality (Req 9.1)**: The endpoints are simply absent from a request
  unless the user chooses to call them. The system functions fully without the
  calibration flow; nothing in the core upload/recognition/correction/export
  pipeline depends on calibration.
* **Prompt bounds (Req 9.2)**: Each call to ``create_calibration_session``
  presents between ``MIN_PROMPTS`` (3) and ``MAX_PROMPTS`` (10) prompts
  inclusive. Each prompt is between 1 and 200 characters inclusive.
* **Storage**: Sessions are held in an in-memory dict keyed by ``session_id``
  (UUID). This is intentionally lightweight for the demo; a production system
  would persist sessions in the DB.
* **Prompt pool**: A curated pool of English sentence prompts (each ≤ 200 chars)
  is used as the source. A random subset is drawn per session using
  ``random.sample`` so prompts vary across sessions.
* **Calibration pairs (Req 9.5)**: Calibration ``Training_Pair`` records use
  ``origin=TrainingPairOrigin.CALIBRATION`` and are stored in the same
  ``training_pairs`` table / repository as correction pairs so the
  Personalization_Service naturally includes them (Req 9.6).
* **Handwriting detection (Req 9.4)**: The image is preprocessed and the
  projection-profile segmenter checks for at least one ink region; images with
  no detectable handwriting are rejected.
* **Region key for calibration**: Because calibration images are not tied to
  a pre-stored ``image_id``, the region key is derived from the session id and
  prompt index, ensuring the upsert constraint still works and re-submissions
  update rather than duplicate.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from io import BytesIO
from typing import Dict, List, Union

# ---------------------------------------------------------------------------
# Calibration prompt bounds (Req 9.2)
# ---------------------------------------------------------------------------

MIN_PROMPTS: int = 3
MAX_PROMPTS: int = 10
MAX_PROMPT_LENGTH: int = 200

# ---------------------------------------------------------------------------
# Curated prompt pool (all prompts are 1–200 chars, English)
# ---------------------------------------------------------------------------

_PROMPT_POOL: List[str] = [
    "The quick brown fox jumps over the lazy dog.",
    "Pack my box with five dozen liquor jugs.",
    "How vexingly quick daft zebras jump!",
    "The five boxing wizards jump quickly.",
    "Bright vixens jump; dozy fowl quack.",
    "Sphinx of black quartz, judge my vow.",
    "Two driven jocks help fax my big quiz.",
    "Quick zephyrs blow, vexing daft Jim.",
    "The jay, pig, fox, zebra and my wolves quack.",
    "Blowzy night-frumps vex'd Jack Q.",
    "Waltz, bad nymph, for quick jigs vex.",
    "Jived fox nymph grabs quick waltz.",
    "Glib jocks quiz nymph to vex dwarf.",
    "Amazingly few discotheques provide jukeboxes.",
    "Heavy boxes perform quick waltzes and jigs.",
    "Crazy Fredrick bought many very exquisite opal jewels.",
    "We promptly judged antique ivory buckles for the next prize.",
    "A mad boxer shot a quick, gloved jab to the jaw of his dizzy opponent.",
    "Jaded zombies acted quaintly but kept driving their oxen forward.",
    "The job requires extra pluck and zeal from every young wage earner.",
    "Six big juicy steaks sizzled in a pan as five workmen left the quarry.",
    "Just keep examining every low bid quoted for zinc etchings.",
    "My faxed joke won a pager in the cable TV quiz show.",
    "A quorum of judges quickly examined every black zebra with prized vow.",
    "Sixty zippers were quickly picked from the woven jute bag.",
    "A large fawn jumped quickly over white zinc boxes.",
    "The public was amazed to view the quickness and dexterity of the juggler.",
    "We quickly seized the black axle and just saved it from going under the bus.",
    "Murky haze enveloped a city as jarring quakes broke fifty-six windows.",
    "Jack quietly moved up front and seized the big ball of wax.",
]

assert all(1 <= len(p) <= MAX_PROMPT_LENGTH for p in _PROMPT_POOL), (
    "All prompts in _PROMPT_POOL must be between 1 and 200 characters."
)
assert len(_PROMPT_POOL) >= MAX_PROMPTS, (
    "Prompt pool must have at least MAX_PROMPTS entries."
)

# ---------------------------------------------------------------------------
# In-memory session store
# ---------------------------------------------------------------------------

# Maps session_id (UUID) -> list of prompt strings.
_SESSION_STORE: Dict[uuid.UUID, List[str]] = {}


# ---------------------------------------------------------------------------
# Domain result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CalibrationSession:
    """A successfully created calibration session (Req 9.2)."""

    session_id: uuid.UUID
    prompts: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Core business logic
# ---------------------------------------------------------------------------


def create_calibration_session(
    *,
    num_prompts: int | None = None,
    rng: random.Random | None = None,
    session_store: Dict[uuid.UUID, List[str]] | None = None,
) -> CalibrationSession:
    """Generate a new calibration session with 3–10 English prompt sentences.

    Parameters
    ----------
    num_prompts:
        Number of prompts to include. Must be between MIN_PROMPTS and
        MAX_PROMPTS inclusive. When ``None`` (the default), a random count
        within [MIN_PROMPTS, MAX_PROMPTS] is chosen.
    rng:
        Optional :class:`random.Random` instance for deterministic tests.
        Defaults to ``random`` module-level functions when ``None``.
    session_store:
        Optional external dict to use for session storage. Defaults to the
        module-level ``_SESSION_STORE`` (useful for test isolation).

    Returns
    -------
    CalibrationSession
        A value object carrying the new ``session_id`` and the prompt list.

    Raises
    ------
    ValueError
        If ``num_prompts`` is outside [MIN_PROMPTS, MAX_PROMPTS].
    """
    _rng = rng if rng is not None else random
    _store = session_store if session_store is not None else _SESSION_STORE

    if num_prompts is None:
        count = _rng.randint(MIN_PROMPTS, MAX_PROMPTS)
    else:
        if not (MIN_PROMPTS <= num_prompts <= MAX_PROMPTS):
            raise ValueError(
                f"num_prompts must be between {MIN_PROMPTS} and {MAX_PROMPTS} "
                f"inclusive, got {num_prompts}."
            )
        count = num_prompts

    prompts: List[str] = _rng.sample(_PROMPT_POOL, count)
    session_id = uuid.uuid4()
    _store[session_id] = prompts

    return CalibrationSession(session_id=session_id, prompts=prompts)


def get_session_prompts(
    session_id: uuid.UUID,
    *,
    session_store: Dict[uuid.UUID, List[str]] | None = None,
) -> List[str] | None:
    """Retrieve the prompts for an existing calibration session.

    Returns ``None`` if the session does not exist.
    """
    _store = session_store if session_store is not None else _SESSION_STORE
    return _store.get(session_id)


# ---------------------------------------------------------------------------
# Calibration sample submission — task 12.2 (Req 9.3, 9.4, 9.5)
# ---------------------------------------------------------------------------

# Maximum accepted calibration image size: 10 MiB (Req 9.4).
CALIBRATION_MAX_BYTES: int = 10 * 1024 * 1024  # 10 MB


class CalibrationRejectReason(str):
    """String constants for the human-readable rejection reasons (Req 9.4)."""

    EMPTY = "empty"
    SIZE_LIMIT = "size_limit"
    INVALID_IMAGE = "invalid_image"
    NO_HANDWRITING = "no_handwriting"


@dataclass(frozen=True)
class CalibrationAccepted:
    """The calibration sample was valid and a Training_Pair was created."""

    pair_id: uuid.UUID


@dataclass(frozen=True)
class CalibrationRejection:
    """The calibration sample was invalid; no Training_Pair was created (Req 9.4)."""

    reason: str
    message: str


@dataclass(frozen=True)
class CalibrationStorageError:
    """A storage-layer failure occurred; no pair was written."""

    message: str


SubmitCalibrationResult = Union[
    CalibrationAccepted, CalibrationRejection, CalibrationStorageError
]


def _detect_handwriting_region(image_bytes: bytes) -> bool:
    """Return True if the image contains at least one detectable ink region.

    Uses the projection-profile segmenter (``segment_image``) which detects
    ink blobs. An image passes this check iff ``recognized_any_region`` is
    True (i.e. at least one word-level ink region is found).
    """
    from app.ml.segmentation import segment_image

    try:
        result = segment_image(image_bytes)
        return result.recognized_any_region
    except Exception:
        # If the segmenter itself errors we treat it as no region found.
        return False


def _make_calibration_region_key(
    session_id: uuid.UUID, prompt_index: int
) -> str:
    """Build a stable region key for a calibration pair.

    Calibration pairs are not tied to a stored ``image_id``; the session id +
    prompt index uniquely identify the (user, region) slot, so re-submitting
    the same prompt in the same session is an upsert rather than a duplicate.
    """
    return f"calibration:{session_id}:{prompt_index}"


def submit_calibration_sample(
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    prompt_index: int,
    image_bytes: bytes,
    repos,  # RepositoryFactory — typed as Any to avoid circular import
    object_store,  # ObjectStore — typed as Any to avoid circular import
    *,
    session_store: Dict[uuid.UUID, List[str]] | None = None,
) -> SubmitCalibrationResult:
    """Validate a calibration handwriting image and create a Training_Pair.

    Parameters
    ----------
    user_id:
        The authenticated user submitting the sample.
    session_id:
        The calibration session this submission belongs to.
    prompt_index:
        0-based index into the session's prompt list that the user wrote.
    image_bytes:
        Raw bytes of the uploaded handwriting image.
    repos:
        Repository factory scoped to the request session.
    object_store:
        Object store for persisting the image crop.
    session_store:
        Optional external session dict (defaults to module-level store).

    Returns
    -------
    CalibrationAccepted
        A Training_Pair was created with origin=CALIBRATION (Req 9.3, 9.5).
    CalibrationRejection
        The submission was invalid (Req 9.4); no pair was written.
    CalibrationStorageError
        A storage failure occurred; no pair was written.
    """
    from app.data.enums import TrainingPairOrigin
    from app.data.models import TrainingPair
    from app.services.image_validator import RejectionReason, validate_image

    # ------------------------------------------------------------------
    # Validation (Req 9.4)
    # ------------------------------------------------------------------

    # Rule 1: non-empty image
    if not image_bytes:
        return CalibrationRejection(
            reason=CalibrationRejectReason.EMPTY,
            message="The submitted image is empty.",
        )

    # Rule 2: ≤ 10 MB and decodable PNG/JPEG (reuse image_validator)
    validation = validate_image(image_bytes, max_bytes=CALIBRATION_MAX_BYTES)
    if not validation.accepted:
        if validation.reason == RejectionReason.SIZE_LIMIT:
            return CalibrationRejection(
                reason=CalibrationRejectReason.SIZE_LIMIT,
                message=validation.message or "The image exceeds the maximum allowed size of 10 megabytes.",
            )
        else:
            # INVALID_IMAGE or UNSUPPORTED_FORMAT
            return CalibrationRejection(
                reason=CalibrationRejectReason.INVALID_IMAGE,
                message=validation.message or "The file is not a valid image.",
            )

    # Rule 3: detectable handwriting region (Req 9.4)
    if not _detect_handwriting_region(image_bytes):
        return CalibrationRejection(
            reason=CalibrationRejectReason.NO_HANDWRITING,
            message="No handwriting region was detected in the submitted image.",
        )

    # ------------------------------------------------------------------
    # Resolve prompt text from session (Req 9.3)
    # ------------------------------------------------------------------
    _store = session_store if session_store is not None else _SESSION_STORE
    prompts = _store.get(session_id)
    if prompts is None:
        return CalibrationRejection(
            reason="unknown_session",
            message=f"Calibration session {session_id} not found.",
        )
    if not (0 <= prompt_index < len(prompts)):
        return CalibrationRejection(
            reason="invalid_prompt_index",
            message=(
                f"prompt_index {prompt_index} is out of range for a session "
                f"with {len(prompts)} prompts."
            ),
        )
    prompt_text = prompts[prompt_index]

    # ------------------------------------------------------------------
    # Persist Training_Pair (Req 9.5 — same store as corrections)
    # ------------------------------------------------------------------
    try:
        # Store the submitted image as the region crop for this pair.
        crop_path = object_store.put_region_crop(
            str(user_id), image_bytes, suffix=".png"
        )

        region_key = _make_calibration_region_key(session_id, prompt_index)

        # Upsert: if the user re-submits the same prompt in the same session,
        # update the existing pair rather than creating a duplicate.
        # Calibration pairs feed the same Personalization_Service pipeline as
        # correction pairs (Req 9.6), including the same threshold trigger
        # that enqueues adapter training once enough unused pairs accumulate
        # (Req 5.1) — imported lazily to avoid a circular import at module
        # load time (personalization_service does not import calibration).
        from app.services.personalization_service import maybe_enqueue_training

        existing = repos.training_pairs.get_by_region_key(user_id, region_key)
        if existing is not None:
            existing.corrected_text = prompt_text
            existing.region_crop_path = crop_path
            repos.session.flush()
            maybe_enqueue_training(user_id, repos)
            repos.session.commit()
            return CalibrationAccepted(pair_id=existing.pair_id)

        pair = repos.training_pairs.add(
            TrainingPair(
                user_id=user_id,
                image_id=None,  # calibration pairs are not tied to an image record
                source_region_key=region_key,
                region_crop_path=crop_path,
                corrected_text=prompt_text,
                origin=TrainingPairOrigin.CALIBRATION,
            )
        )
        repos.session.flush()
        maybe_enqueue_training(user_id, repos)
        repos.session.commit()
        return CalibrationAccepted(pair_id=pair.pair_id)

    except Exception as exc:  # noqa: BLE001
        try:
            repos.session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return CalibrationStorageError(str(exc))


# ---------------------------------------------------------------------------
# FastAPI router  (Req 9.1, 9.2, 9.3, 9.4, 9.5)
# ---------------------------------------------------------------------------

from functools import lru_cache  # noqa: E402

from fastapi import APIRouter, Depends, File, Form, UploadFile, status as http_status  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from app.data.models import User  # noqa: E402
from app.data.object_store import FilesystemObjectStore, ObjectStore  # noqa: E402
from app.data.repositories import RepositoryFactory  # noqa: E402
from app.services.auth_api import get_current_user, get_repositories  # noqa: E402
from app.services.capability_guard import require_capability  # noqa: E402

router = APIRouter(tags=["calibration"])


# --------------------------------------------------------------------------- #
# Object store dependency                                                       #
# --------------------------------------------------------------------------- #

@lru_cache(maxsize=1)
def _default_calibration_object_store() -> ObjectStore:
    """Process-wide default object store for calibration pairs."""
    return FilesystemObjectStore()


def get_calibration_object_store() -> ObjectStore:
    """Provide the object store. Overridden in tests for isolation."""
    return _default_calibration_object_store()


# --------------------------------------------------------------------------- #
# Request / Response schemas                                                    #
# --------------------------------------------------------------------------- #


class CalibrationSessionResponse(BaseModel):
    """Response for ``POST /calibration/session``."""

    session_id: uuid.UUID
    prompts: List[str]


class CalibrationSampleResponse(BaseModel):
    """Response when a calibration sample is accepted."""

    status: str = "accepted"
    pair_id: uuid.UUID


# --------------------------------------------------------------------------- #
# Endpoints                                                                     #
# --------------------------------------------------------------------------- #


@router.post(
    "/calibration/session",
    status_code=http_status.HTTP_201_CREATED,
    response_model=CalibrationSessionResponse,
    summary="Create a calibration session",
    description=(
        "Creates a new calibration session presenting between 3 and 10 English "
        "prompt sentences, each between 1 and 200 characters long (Req 9.2). "
        "Calibration is entirely optional — the system functions without it "
        "(Req 9.1). Requires authentication. Returns 503 when the calibration "
        "capability is unavailable (Req 11.6)."
    ),
)
def create_session_endpoint(
    _guard: None = Depends(require_capability("calibration")),
    user: User = Depends(get_current_user),
) -> CalibrationSessionResponse:
    """POST /calibration/session (Req 9.1, 9.2).

    Returns a new session_id and the list of prompt sentences the user
    should handwrite. Each subsequent submission endpoint will reference this
    session_id.
    """
    session = create_calibration_session()
    return CalibrationSessionResponse(
        session_id=session.session_id,
        prompts=session.prompts,
    )


@router.post(
    "/calibration/{session_id}/samples",
    status_code=http_status.HTTP_201_CREATED,
    response_model=CalibrationSampleResponse,
    summary="Submit a calibration handwriting sample",
    description=(
        "Submit a handwriting image for a specific prompt in the calibration "
        "session. The image must be non-empty, ≤10 MB, decodable as PNG/JPEG, "
        "and contain a detectable handwriting region (Req 9.3, 9.4). "
        "On success a Training_Pair with origin=calibration is stored in the "
        "same store as correction pairs (Req 9.5). "
        "Requires authentication."
    ),
)
def submit_sample_endpoint(
    session_id: uuid.UUID,
    prompt_index: int = Form(..., description="0-based index of the prompt the user wrote"),
    file: UploadFile = File(..., description="Handwriting image file (PNG or JPEG)"),
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
    store: ObjectStore = Depends(get_calibration_object_store),
) -> CalibrationSampleResponse:
    """POST /calibration/{session_id}/samples (Req 9.3, 9.4, 9.5).

    Multipart body: ``file`` (image bytes) and ``prompt_index`` (int).
    Returns 201 with ``pair_id`` on success; 422 with reason on rejection.
    """
    image_bytes = file.file.read()

    result = submit_calibration_sample(
        user_id=user.user_id,
        session_id=session_id,
        prompt_index=prompt_index,
        image_bytes=image_bytes,
        repos=repos,
        object_store=store,
    )

    if isinstance(result, CalibrationAccepted):
        return CalibrationSampleResponse(pair_id=result.pair_id)

    if isinstance(result, CalibrationRejection):
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"reason": result.reason, "message": result.message},
        )

    # CalibrationStorageError
    raise HTTPException(
        status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=result.message,
    )


__all__ = [
    # Constants
    "MIN_PROMPTS",
    "MAX_PROMPTS",
    "MAX_PROMPT_LENGTH",
    "CALIBRATION_MAX_BYTES",
    # Domain types
    "CalibrationSession",
    "CalibrationRejectReason",
    "CalibrationAccepted",
    "CalibrationRejection",
    "CalibrationStorageError",
    "SubmitCalibrationResult",
    # Business logic
    "create_calibration_session",
    "get_session_prompts",
    "submit_calibration_sample",
    # FastAPI
    "router",
    "CalibrationSessionResponse",
    "CalibrationSampleResponse",
    "create_session_endpoint",
    "submit_sample_endpoint",
    "get_calibration_object_store",
]
