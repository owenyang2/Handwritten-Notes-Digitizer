"""FastAPI router for personalization status and evaluation endpoints.

Implements:

* ``GET /personalization/status`` – returns the current training state for the
  authenticated user: whether training is in progress (RUNNING/QUEUED), whether
  an adapter is active, or if training previously failed or was rejected (Req
  5.6, 5.7).
* ``GET /personalization/evaluation`` – returns the most recent before/after
  CER and WER report for the authenticated user's held-out set, including
  deltas and availability indicators (Req 7.3, 5.7).

Both endpoints require authentication. Unavailable states are represented
explicitly rather than as HTTP errors so the UI can display appropriate messages
(e.g., "No training completed yet" or "Measurement unavailable").
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from pydantic import BaseModel

from app.config.settings import get_settings
from app.data.enums import EvaluationAvailability, TrainingRunState
from app.data.models import User
from app.data.repositories import RepositoryFactory
from app.services.auth_api import get_current_user, get_repositories
from app.services.images_api import get_object_store
from app.services.isolation import get_or_404

router = APIRouter(prefix="/personalization", tags=["personalization"])


# --------------------------------------------------------------------------- #
# Response schemas
# --------------------------------------------------------------------------- #


class PersonalizationStatusResponse(BaseModel):
    """Current personalization state for the authenticated user.

    Fields
    ------
    training_state:
        ``"in_progress"``  – a training run is QUEUED or RUNNING (Req 5.6).
        ``"completed"``    – the most recent run completed (ACCEPTED/REJECTED/FAILED).
        ``"none"``         – no training run exists for this user yet.
    adapter_active:
        Whether the user has an active adapter applied to recognition.
    last_run_outcome:
        ``"accepted"``  – the most recent run was accepted (adapter is live).
        ``"rejected"``  – the most recent run was rejected; prior config retained (Req 5.7).
        ``"failed"``    – the most recent run failed; prior config retained (Req 5.7).
        ``None``        – no run has completed yet (or no run exists).
    reject_reason:
        If ``last_run_outcome`` is ``"rejected"`` or ``"failed"``, the recorded
        reason string; otherwise ``None``.
    notification:
        A human-readable message describing the current state (may be ``None``
        when no training has been run).
    unused_training_pairs:
        Number of labeled examples (corrections + calibration samples) not yet
        consumed by a completed training run — the count that drives the
        training trigger (Req 5.1).
    training_trigger_threshold:
        How many unused pairs are required before adapter training is queued.
    max_training_epochs:
        Upper bound for epochs in one background training run.
    """

    training_state: Literal["in_progress", "completed", "none"]
    adapter_active: bool
    last_run_outcome: Optional[Literal["accepted", "rejected", "failed"]]
    reject_reason: Optional[str]
    notification: Optional[str]
    unused_training_pairs: int
    training_trigger_threshold: int
    max_training_epochs: int


class TrainingPairSummaryResponse(BaseModel):
    """Summary of one collected personalization training example."""

    pair_id: str
    origin: Literal["correction", "calibration"]
    corrected_text: str
    created_at: datetime
    used: bool


class TrainingPairListResponse(BaseModel):
    """Collection of the user's personalization training examples."""

    items: list[TrainingPairSummaryResponse]


class EvaluationResponse(BaseModel):
    """Before/after accuracy report for the authenticated user (Req 7.3).

    All metric fields are ``None`` when the measurement is unavailable (Req 7.6,
    7.7). ``availability`` summarises whether *both* before and after
    measurements were computed successfully.
    """

    availability: Literal["available", "unavailable"]
    before_cer: Optional[float]
    before_wer: Optional[float]
    after_cer: Optional[float]
    after_wer: Optional[float]
    delta_cer: Optional[float]
    delta_wer: Optional[float]
    # Human-readable description of why the evaluation is unavailable, or None.
    unavailable_reason: Optional[str]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

_TERMINAL_STATES = {
    TrainingRunState.ACCEPTED,
    TrainingRunState.REJECTED,
    TrainingRunState.FAILED,
}

_IN_PROGRESS_STATES = {
    TrainingRunState.QUEUED,
    TrainingRunState.RUNNING,
}

_NOTIFICATION_MESSAGES = {
    "in_progress": "Personalization training is in progress.",
    "none": None,
    "accepted": (
        "Personalization complete. Your adapter has been updated and will be "
        "applied to future recognition requests."
    ),
    "rejected": (
        "Personalization did not improve recognition accuracy. "
        "Your previous recognition configuration has been retained."
    ),
    "failed": (
        "Adapter training did not complete successfully. "
        "Your previous recognition configuration has been retained."
    ),
}


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #


@router.get(
    "/status",
    response_model=PersonalizationStatusResponse,
    summary="Get personalization training status",
    description=(
        "Returns whether adapter training is in progress, completed, or not yet "
        "started for the authenticated user, along with the outcome of the most "
        "recent training run and whether an adapter is currently active."
    ),
)
def get_personalization_status(
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
) -> PersonalizationStatusResponse:
    """Return the current personalization training state (Req 5.6, 5.7).

    Checks the user's training runs and adapter to determine:
    - Whether training is currently in progress (QUEUED or RUNNING).
    - The outcome of the most recent completed run.
    - Whether an active adapter exists.
    """
    uid: uuid.UUID = user.user_id
    settings = get_settings()
    unused_pairs = repos.training_pairs.count_unused(uid)
    threshold = settings.personalization_trigger_threshold

    def _with_progress(**kwargs: object) -> PersonalizationStatusResponse:
        return PersonalizationStatusResponse(
            unused_training_pairs=unused_pairs,
            training_trigger_threshold=threshold,
            max_training_epochs=settings.max_training_epochs,
            **kwargs,
        )

    # Check if any run is currently in progress (QUEUED or RUNNING).
    all_runs = repos.training_runs.list_for_user(uid)

    adapter = repos.adapters.get_active_for_user(uid)
    adapter_active = adapter is not None

    if not all_runs:
        return _with_progress(
            training_state="none",
            adapter_active=adapter_active,
            last_run_outcome=None,
            reject_reason=None,
            notification=_NOTIFICATION_MESSAGES["none"],
        )

    # Determine if any run is in-progress.
    in_progress_runs = [r for r in all_runs if r.state in _IN_PROGRESS_STATES]
    if in_progress_runs:
        return _with_progress(
            training_state="in_progress",
            adapter_active=adapter_active,
            last_run_outcome=None,
            reject_reason=None,
            notification=_NOTIFICATION_MESSAGES["in_progress"],
        )

    # All runs are terminal — find the most recently started one.
    terminal_runs = sorted(
        [r for r in all_runs if r.state in _TERMINAL_STATES],
        key=lambda r: (r.started_at is not None, r.started_at),
        reverse=True,
    )

    if not terminal_runs:
        # No terminal runs but some runs exist (should not happen, but handle
        # gracefully).
        return _with_progress(
            training_state="none",
            adapter_active=adapter_active,
            last_run_outcome=None,
            reject_reason=None,
            notification=_NOTIFICATION_MESSAGES["none"],
        )

    latest = terminal_runs[0]
    outcome_map = {
        TrainingRunState.ACCEPTED: "accepted",
        TrainingRunState.REJECTED: "rejected",
        TrainingRunState.FAILED: "failed",
    }
    outcome = outcome_map.get(latest.state)
    notification = _NOTIFICATION_MESSAGES.get(outcome) if outcome else None

    return _with_progress(
        training_state="completed",
        adapter_active=adapter_active,
        last_run_outcome=outcome,  # type: ignore[arg-type]
        reject_reason=latest.reject_reason if outcome in ("rejected", "failed") else None,
        notification=notification,
    )


@router.get(
    "/evaluation",
    response_model=EvaluationResponse,
    summary="Get before/after accuracy evaluation",
    description=(
        "Returns the most recent before/after CER and WER report on the user's "
        "held-out set, including deltas (before − after) and availability "
        "indicators. Returns unavailable when no evaluation has been run yet."
    ),
)
def get_evaluation(
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
) -> EvaluationResponse:
    """Return the most recent before/after evaluation report (Req 7.3).

    Uses :meth:`~app.data.repositories.EvaluationResultRepository.latest_for_user`
    to fetch the most recent evaluation result linked to the user's training
    runs. Returns an unavailable report when no evaluation exists.
    """
    uid: uuid.UUID = user.user_id

    result = repos.evaluation_results.latest_for_user(uid)

    if result is None:
        return EvaluationResponse(
            availability="unavailable",
            before_cer=None,
            before_wer=None,
            after_cer=None,
            after_wer=None,
            delta_cer=None,
            delta_wer=None,
            unavailable_reason="No evaluation has been run yet.",
        )

    # Map the DB availability enum to the response literal.
    availability: Literal["available", "unavailable"] = (
        "available"
        if result.availability == EvaluationAvailability.AVAILABLE
        else "unavailable"
    )

    unavailable_reason: Optional[str] = None
    if availability == "unavailable":
        unavailable_reason = (
            "Some measurements could not be computed "
            "(the held-out set may be too small or a computation failed)."
        )

    return EvaluationResponse(
        availability=availability,
        before_cer=result.before_cer,
        before_wer=result.before_wer,
        after_cer=result.after_cer,
        after_wer=result.after_wer,
        delta_cer=result.delta_cer,
        delta_wer=result.delta_wer,
        unavailable_reason=unavailable_reason,
    )


@router.get(
    "/training-pairs",
    response_model=TrainingPairListResponse,
    summary="List personalization training examples",
    description=(
        "Returns all collected personalization training examples for the authenticated "
        "user, including calibration samples and correction-derived examples."
    ),
)
def list_training_pairs(
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
) -> TrainingPairListResponse:
    pairs = repos.training_pairs.list_for_user(user.user_id)
    return TrainingPairListResponse(
        items=[
            TrainingPairSummaryResponse(
                pair_id=str(pair.pair_id),
                origin=pair.origin.value,  # type: ignore[arg-type]
                corrected_text=pair.corrected_text,
                created_at=pair.created_at,
                used=pair.consumed_by_run_id is not None,
            )
            for pair in pairs
        ]
    )


@router.get(
    "/training-pairs/{pair_id}/image",
    summary="Get personalization training example image",
    description=(
        "Returns the stored region-crop image for one personalization training "
        "example owned by the authenticated user."
    ),
)
def get_training_pair_image(
    pair_id: uuid.UUID,
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
    store=Depends(get_object_store),  # ObjectStore; avoid import cycle typing.
) -> Response:
    pair = get_or_404(repos.training_pairs.get(user.user_id, pair_id))
    image_bytes = store.get(pair.region_crop_path)
    return Response(content=image_bytes, media_type="image/png")


__all__ = [
    "router",
    "PersonalizationStatusResponse",
    "EvaluationResponse",
    "TrainingPairSummaryResponse",
    "TrainingPairListResponse",
    "get_personalization_status",
    "get_evaluation",
    "list_training_pairs",
    "get_training_pair_image",
]
