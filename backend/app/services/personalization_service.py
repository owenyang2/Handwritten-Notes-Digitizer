"""Personalization_Service – training-pair utilities.

This module provides:

* ``split_held_out`` – the held-out split helper used by the asynchronous
  adapter-training pipeline (Req 5, Req 6).
* ``maybe_enqueue_training`` – the threshold-trigger that enqueues a new
  training run when a user has accumulated enough unused pairs (Req 5.1).
* ``acquire_training_lock`` – transitions a QUEUED run to RUNNING, holding a
  per-user threading lock to prevent concurrent adapter trainings (Req 5.6).
* ``release_training_lock`` – transitions a RUNNING run to a terminal state
  (ACCEPTED / REJECTED / FAILED) and releases the per-user threading lock.

Unused-pair threshold trigger (Req 5.1)
-----------------------------------------
``maybe_enqueue_training(user_id, repos)`` checks whether training should be
started:

1. Count the user's pairs not yet consumed by a completed training run.
2. If the count is below ``TRAINING_THRESHOLD`` (20), return
   ``TrainingTriggerResult.BELOW_THRESHOLD`` and do nothing.
3. If a training run is already ``RUNNING`` for the user, return
   ``TrainingTriggerResult.ALREADY_RUNNING`` and do nothing.
4. Otherwise, create a new ``TrainingRun`` with ``state=QUEUED`` and return
   ``TrainingTriggerResult.ENQUEUED``.

Held-out split sizing (Req 6.1)
--------------------------------
When the Personalization_Service trains an Adapter it first carves out a
held-out evaluation set from the user's N pairs:

    held_out_size = max(round(0.2 × N), 5)   # floored at 5
    held_out_size = min(held_out_size, N)      # never exceeds N

The remaining pairs form the training set. Together the two sets are disjoint
and partition all N pairs. The split is deterministic (stable sort by
``pair_id``) so re-runs on the same data produce the same split.

Per-user training lock (Req 5.6)
----------------------------------
``acquire_training_lock(user_id, run_id, repos)`` prevents two concurrent
adapter trainings for the same user through two complementary mechanisms:

1. **DB-level guard** – ``repos.training_runs.get_running_for_user`` checks the
   persisted state and returns ``None`` if any run is already RUNNING.
2. **Thread-level guard** – a ``threading.Lock`` keyed by ``user_id`` prevents
   two threads racing to set a run to RUNNING simultaneously.

Together they ensure: at most one adapter training runs per user at a time
regardless of the concurrency model (single-process multi-thread or DB-only).

``release_training_lock(run_id, repos, final_state)`` persists the terminal
state and releases the thread-level lock so the next training can proceed.
"""

from __future__ import annotations

import enum
import random
import threading
import uuid
from dataclasses import dataclass
from typing import Any, Optional, Sequence, Tuple

from app.data.enums import AdapterStatus, EvaluationAvailability, TrainingRunState
from app.data.models import Adapter, EvaluationResult, TrainingRun
from app.ml.adapter_trainer import adapter_result_to_bytes

# --------------------------------------------------------------------------- #
# Per-user thread-level lock registry (Req 5.6)
# --------------------------------------------------------------------------- #

# Protects mutations to ``_user_locks`` itself.
_registry_lock: threading.Lock = threading.Lock()

# Maps user_id (str) → threading.Lock.  Locks are never removed so that a
# waiting thread does not observe the lock disappear mid-wait; the memory
# footprint is negligible for any realistic user count.
_user_locks: dict[str, threading.Lock] = {}

# Minimum number of unused training pairs required to enqueue a new training
# run (Req 5.1).
TRAINING_THRESHOLD: int = 20


# --------------------------------------------------------------------------- #
# Lock-acquisition result type
# --------------------------------------------------------------------------- #

class LockAcquireResult(str, enum.Enum):
    """Outcome of an ``acquire_training_lock`` call (Req 5.6)."""

    ACQUIRED = "acquired"
    """The per-user lock was obtained and the run transitioned to RUNNING."""

    ALREADY_RUNNING = "already_running"
    """Another training is already in the RUNNING state for this user."""

    NOT_FOUND = "not_found"
    """No QUEUED run with the given ``run_id`` exists for this user."""


# --------------------------------------------------------------------------- #
# Public lock-management functions (Req 5.6)
# --------------------------------------------------------------------------- #

def _get_user_lock(user_id: uuid.UUID | str) -> threading.Lock:
    """Return (creating if absent) the per-user threading lock."""
    key = str(user_id)
    # Fast path: lock already exists.
    lock = _user_locks.get(key)
    if lock is not None:
        return lock
    # Slow path: create under the registry guard to avoid duplicates.
    with _registry_lock:
        if key not in _user_locks:
            _user_locks[key] = threading.Lock()
        return _user_locks[key]


def acquire_training_lock(
    user_id: uuid.UUID | str,
    run_id: uuid.UUID | str,
    repos: Any,
) -> LockAcquireResult:
    """Transition a QUEUED run to RUNNING while holding the per-user mutex.

    The function combines a DB-level check (``get_running_for_user``) with a
    thread-level ``threading.Lock`` to prevent two adapter trainings running
    concurrently for the same user (Req 5.6).

    Parameters
    ----------
    user_id:
        The authenticated user who owns the training run.
    run_id:
        The ``run_id`` of the QUEUED :class:`~app.data.models.TrainingRun` to
        promote to RUNNING.
    repos:
        A :class:`~app.data.repositories.RepositoryFactory` (or compatible
        object) providing ``repos.training_runs``.

    Returns
    -------
    LockAcquireResult
        ``ACQUIRED``        – the thread-level lock is now held by the caller
                              and the run's state has been set to RUNNING
                              (caller must commit and later call
                              :func:`release_training_lock`).
        ``ALREADY_RUNNING`` – another run for this user is already RUNNING
                              (DB check); the lock is *not* held.
        ``NOT_FOUND``       – ``run_id`` is not a QUEUED run for ``user_id``;
                              the lock is *not* held.

    Notes
    -----
    The thread-level lock is acquired *before* the DB check so that two threads
    racing for the same user cannot both read "no running run" and both proceed.
    Callers **must** call :func:`release_training_lock` exactly once after a
    successful acquisition, ideally in a ``try/finally`` block.
    """
    uid = uuid.UUID(str(user_id)) if not isinstance(user_id, uuid.UUID) else user_id
    rid = uuid.UUID(str(run_id)) if not isinstance(run_id, uuid.UUID) else run_id

    user_lock = _get_user_lock(uid)
    # Acquire the thread-level lock (blocking).
    user_lock.acquire()

    try:
        # DB-level guard: another run may have been promoted between the last
        # ``maybe_enqueue_training`` check and now.
        running = repos.training_runs.get_running_for_user(uid)
        if running is not None:
            user_lock.release()
            return LockAcquireResult.ALREADY_RUNNING

        # Fetch the QUEUED run we were asked to promote.
        run = repos.training_runs.get(uid, rid)
        if run is None or run.state != TrainingRunState.QUEUED:
            user_lock.release()
            return LockAcquireResult.NOT_FOUND

        # Transition to RUNNING.
        run.state = TrainingRunState.RUNNING
        repos.training_runs.session.flush()
        # The thread-level lock remains HELD; the caller must call
        # release_training_lock when the training job finishes.
        return LockAcquireResult.ACQUIRED

    except Exception:
        # Safety net: always release on unexpected error so we don't deadlock.
        user_lock.release()
        raise


def release_training_lock(
    user_id: uuid.UUID | str,
    run_id: uuid.UUID | str,
    repos: Any,
    final_state: TrainingRunState,
) -> None:
    """Persist the terminal state and release the per-user threading lock.

    Parameters
    ----------
    user_id:
        The user whose lock is being released.
    run_id:
        The ``run_id`` of the RUNNING :class:`~app.data.models.TrainingRun`
        to close out.
    repos:
        A :class:`~app.data.repositories.RepositoryFactory` providing
        ``repos.training_runs``.
    final_state:
        The terminal state to record:
        ``ACCEPTED``, ``REJECTED``, or ``FAILED``.

    Raises
    ------
    ValueError
        If ``final_state`` is not one of the valid terminal states.

    Notes
    -----
    The caller is responsible for committing the session after this call.
    The thread-level lock is released whether or not the DB flush succeeds so
    that subsequent acquisitions are not blocked indefinitely.
    """
    terminal_states = {
        TrainingRunState.ACCEPTED,
        TrainingRunState.REJECTED,
        TrainingRunState.FAILED,
    }

    uid = uuid.UUID(str(user_id)) if not isinstance(user_id, uuid.UUID) else user_id
    rid = uuid.UUID(str(run_id)) if not isinstance(run_id, uuid.UUID) else run_id

    user_lock = _get_user_lock(uid)

    try:
        if final_state not in terminal_states:
            raise ValueError(
                f"release_training_lock: final_state must be one of "
                f"{terminal_states}, got {final_state!r}"
            )

        run = repos.training_runs.get(uid, rid)
        if run is not None:
            run.state = final_state
            repos.training_runs.session.flush()
    finally:
        # Always release to prevent deadlock even on DB errors or ValueError.
        try:
            user_lock.release()
        except RuntimeError:
            # Lock was not acquired (e.g., called without a prior acquire).
            pass


class TrainingTriggerResult(str, enum.Enum):
    """Outcome of a ``maybe_enqueue_training`` call (Req 5.1, 5.6)."""

    ENQUEUED = "enqueued"
    BELOW_THRESHOLD = "below_threshold"
    ALREADY_RUNNING = "already_running"


def maybe_enqueue_training(
    user_id: uuid.UUID | str,
    repos: Any,
) -> TrainingTriggerResult:
    """Enqueue a training run iff the unused-pair count is ≥ the trigger threshold.

    Parameters
    ----------
    user_id:
        The authenticated user whose pair count is checked.
    repos:
        A :class:`~app.data.repositories.RepositoryFactory` (or compatible
        object) providing ``repos.training_pairs`` and
        ``repos.training_runs``.

    Returns
    -------
    TrainingTriggerResult
        ``BELOW_THRESHOLD``  – fewer unused pairs than the threshold; nothing done.
        ``ALREADY_RUNNING``  – a run is already in the RUNNING state; nothing done.
        ``ENQUEUED``         – a new QUEUED ``TrainingRun`` was created and added
                               to the session (caller must commit).
    """
    # The threshold is configurable via ``HND_PERSONALIZATION_TRIGGER_THRESHOLD``
    # (Settings.personalization_trigger_threshold); ``TRAINING_THRESHOLD`` is
    # kept as the module-level default (and is what the test suite imports
    # directly), so read from settings with that constant as the fallback in
    # case settings construction ever fails to expose the field.
    from app.config.settings import get_settings

    threshold = getattr(get_settings(), "personalization_trigger_threshold", TRAINING_THRESHOLD)

    count = repos.training_pairs.count_unused(user_id)
    if count < threshold:
        return TrainingTriggerResult.BELOW_THRESHOLD

    running = repos.training_runs.get_running_for_user(user_id)
    if running is not None:
        return TrainingTriggerResult.ALREADY_RUNNING

    new_run = TrainingRun(
        user_id=uuid.UUID(str(user_id)) if not isinstance(user_id, uuid.UUID) else user_id,
        state=TrainingRunState.QUEUED,
    )
    repos.training_runs.add(new_run)
    return TrainingTriggerResult.ENQUEUED


def split_held_out(
    pairs: Sequence[Any],
    *,
    seed: int | None = None,
) -> Tuple[list[Any], list[Any]]:
    """Partition *pairs* into a training set and a held-out evaluation set.

    Parameters
    ----------
    pairs:
        The full collection of a user's :class:`~app.data.models.TrainingPair`
        objects (or any sequence for testing). Must support ``len``.
    seed:
        Optional integer random seed. When *None* (the default) the split is
        deterministic based on a stable sort of the input order; when provided
        the list is shuffled with the given seed before splitting, which can be
        useful for unit-test exercises of the sizing formula.

    Returns
    -------
    (training_pairs, held_out_pairs)
        Two disjoint lists that together contain every element of *pairs*
        exactly once.  ``len(held_out_pairs) == max(round(0.2 * N), 5)``
        bounded by N where ``N = len(pairs)``.

    Notes
    -----
    * The function works on **any** sequence so that unit tests can pass plain
      integers or strings without constructing ORM objects.
    * When *seed* is ``None`` the ordering of *pairs* is preserved (first N-k
      elements become training, last k become held-out) so that callers who
      pre-sort by ``created_at`` get a deterministic, temporally-ordered split.
    """
    n = len(pairs)

    # Compute held-out size: 20 % of N rounded, at least 5, never more than N.
    held_out_size = max(round(0.2 * n), 5)
    held_out_size = min(held_out_size, n)

    if seed is not None:
        rng = random.Random(seed)
        shuffled = list(pairs)
        rng.shuffle(shuffled)
    else:
        shuffled = list(pairs)

    training_pairs = shuffled[: n - held_out_size]
    held_out_pairs = shuffled[n - held_out_size :]

    return training_pairs, held_out_pairs


# --------------------------------------------------------------------------- #
# Guardrail types and logic (Req 6.3, 6.4)
# --------------------------------------------------------------------------- #

# Thresholds (absolute, proportion units where 1.0 == 100 %).
_CER_TOLERANCE: float = 0.01   # 1.0 percentage point
_WER_TOLERANCE: float = 0.02   # 2.0 percentage points


class GuardrailResult(str, enum.Enum):
    """Outcome of a guardrail evaluation (Req 6.3, 6.4).

    The general-set guardrail accepts a newly trained adapter iff its CER does
    not exceed the base CER by more than 1.0 pp AND its WER does not exceed the
    base WER by more than 2.0 pp.  Either violation causes rejection.
    """

    ACCEPTED = "accepted"
    """Both CER and WER constraints satisfied — adapter is safe to activate."""

    REJECTED_CER = "rejected_cer"
    """Adapter CER exceeds base CER by more than 1.0 pp (Req 6.3)."""

    REJECTED_WER = "rejected_wer"
    """Adapter WER exceeds base WER by more than 2.0 pp (Req 6.4)."""


def evaluate_guardrail(
    adapter_cer: float,
    base_cer: float,
    adapter_wer: float,
    base_wer: float,
) -> GuardrailResult:
    """Apply the general-set accept/reject guardrail (Req 6.3, 6.4).

    The adapter is accepted if and only if:

        adapter_cer ≤ base_cer + 0.01   (1.0 percentage point)  AND
        adapter_wer ≤ base_wer + 0.02   (2.0 percentage points)

    CER is checked first; if CER is violated the result is ``REJECTED_CER``
    regardless of the WER value.

    Parameters
    ----------
    adapter_cer:
        CER of the newly trained adapter on the general English evaluation set.
    base_cer:
        CER of the base model on the same general evaluation set.
    adapter_wer:
        WER of the newly trained adapter on the general English evaluation set.
    base_wer:
        WER of the base model on the same general evaluation set.

    Returns
    -------
    GuardrailResult
        ``ACCEPTED``     – both constraints satisfied.
        ``REJECTED_CER`` – CER constraint violated (adapter_cer > base_cer + 0.01).
        ``REJECTED_WER`` – WER constraint violated (and CER is within tolerance).
    """
    if adapter_cer > base_cer + _CER_TOLERANCE:
        return GuardrailResult.REJECTED_CER
    if adapter_wer > base_wer + _WER_TOLERANCE:
        return GuardrailResult.REJECTED_WER
    return GuardrailResult.ACCEPTED


# --------------------------------------------------------------------------- #
# Training-run completion outcome
# --------------------------------------------------------------------------- #

@dataclass
class TrainingOutcome:
    """The result of ``complete_training_run``.

    Attributes
    ----------
    accepted:
        ``True`` when the adapter passed the guardrail and was activated.
    guardrail_result:
        The ``GuardrailResult`` from the general-set check, or ``None`` when
        the run failed before evaluation could be performed.
    notification:
        Human-readable message suitable for surfacing to the user.  Always
        populated (describes acceptance, rejection reason, or failure reason).
    adapter_id:
        UUID of the (new or existing) active adapter, or ``None`` when no
        adapter is active.
    run_final_state:
        The terminal ``TrainingRunState`` the run was set to.
    """

    accepted: bool
    guardrail_result: Optional[GuardrailResult]
    notification: str
    adapter_id: Optional[uuid.UUID]
    run_final_state: TrainingRunState


# --------------------------------------------------------------------------- #
# Notification message helpers
# --------------------------------------------------------------------------- #

_REJECTION_MESSAGES: dict[GuardrailResult, str] = {
    GuardrailResult.REJECTED_CER: (
        "Personalization did not improve general recognition accuracy "
        "(CER increased by more than 1.0 pp on the general evaluation set). "
        "Your previous recognition configuration has been retained."
    ),
    GuardrailResult.REJECTED_WER: (
        "Personalization did not improve general recognition accuracy "
        "(WER increased by more than 2.0 pp on the general evaluation set). "
        "Your previous recognition configuration has been retained."
    ),
}

_ACCEPTED_MESSAGE = (
    "Personalization complete. Your adapter has been updated and will be "
    "applied to future recognition requests."
)

_FAILED_MESSAGE = (
    "Adapter training did not complete successfully. "
    "Your previous recognition configuration has been retained."
)


# --------------------------------------------------------------------------- #
# Public completion function
# --------------------------------------------------------------------------- #

def complete_training_run(
    user_id: uuid.UUID | str,
    run_id: uuid.UUID | str,
    adapter_result: Any,
    general_eval: Any,
    repos: Any,
    object_store: Any,
    *,
    base_model_version: str = "1.0",
    pair_ids: Optional[Sequence[uuid.UUID]] = None,
) -> TrainingOutcome:
    """Apply guardrails and finalise a training run.

    This function is the single point of truth for post-training outcome
    handling (Req 5.7, 6.3, 6.4, 6.6, 9.6, 10.2).  It:

    1. Checks guardrails using ``general_eval`` metrics.
    2. On **ACCEPTED**: saves adapter weights to the object store, upserts the
       ``Adapter`` row, marks the run's pairs as consumed
       (``consumed_by_run_id``), persists an ``EvaluationResult``, and sets the
       ``TrainingRun`` state to ``ACCEPTED``.
    3. On **REJECTED**: retains the prior adapter, records the rejection reason
       on the ``TrainingRun``, persists an ``EvaluationResult``, sets the state
       to ``REJECTED``, and returns a notification message.
    4. On **FAILED** (``adapter_result`` is ``None``): retains the prior
       adapter, sets the state to ``FAILED``, records a reason, and returns a
       notification.

    The caller is responsible for committing the session **after** this call.
    This function only flushes (not commits) so that the entire outcome
    participates in a single transaction.

    Parameters
    ----------
    user_id:
        The authenticated user who owns the training run.
    run_id:
        The ``run_id`` of the RUNNING ``TrainingRun`` being finalised.
    adapter_result:
        The :class:`~app.ml.adapter_trainer.AdapterResult` from training, or
        ``None`` to indicate that training failed (triggering FAILED handling).
    general_eval:
        An object (or ``None``) providing general-set evaluation metrics. When
        not ``None`` it must expose ``.general_cer``, ``.general_wer``,
        ``.base_cer`` (base CER on the general set), and ``.base_wer``.  A
        plain :class:`~app.services.evaluation_service.BeforeAfterReport` works
        if its ``before_cer``/``before_wer`` are treated as base metrics and
        ``after_cer``/``after_wer`` as adapter metrics on the general set. Pass
        ``None`` to skip guardrail evaluation (results in FAILED).
    repos:
        A :class:`~app.data.repositories.RepositoryFactory` providing
        ``repos.training_runs``, ``repos.adapters``,
        ``repos.training_pairs``, and ``repos.evaluation_results``.
    object_store:
        An :class:`~app.data.object_store.ObjectStore` instance for persisting
        adapter weight bytes.
    base_model_version:
        Version tag written to new ``Adapter`` rows.
    pair_ids:
        Optional explicit list of ``pair_id`` UUIDs to mark as consumed on
        acceptance.  When ``None``, the function marks the run's junction
        records via ``TrainingRun.run_pairs``.

    Returns
    -------
    TrainingOutcome
        Describes whether the adapter was accepted, the guardrail result, a
        user-facing notification, the active adapter id (if any), and the
        terminal state of the run.
    """
    uid = uuid.UUID(str(user_id)) if not isinstance(user_id, uuid.UUID) else user_id
    rid = uuid.UUID(str(run_id)) if not isinstance(run_id, uuid.UUID) else run_id

    # Fetch the run (must exist and belong to this user).
    run = repos.training_runs.get(uid, rid)

    # ------------------------------------------------------------------
    # Case 1: Training failed (adapter_result is None or general_eval is None).
    # ------------------------------------------------------------------
    if adapter_result is None or general_eval is None:
        if run is not None:
            run.state = TrainingRunState.FAILED
            run.reject_reason = "Training did not produce a valid adapter."
            repos.training_runs.session.flush()

        existing_adapter = repos.adapters.get_for_user(uid)
        adapter_id = existing_adapter.adapter_id if existing_adapter is not None else None

        return TrainingOutcome(
            accepted=False,
            guardrail_result=None,
            notification=_FAILED_MESSAGE,
            adapter_id=adapter_id,
            run_final_state=TrainingRunState.FAILED,
        )

    # ------------------------------------------------------------------
    # Case 2: Evaluate guardrail.
    # ------------------------------------------------------------------
    # general_eval may be a BeforeAfterReport (before=base, after=adapter on
    # general set) or a duck-typed object with the same attributes.
    base_cer = getattr(general_eval, "before_cer", None) or getattr(general_eval, "base_cer", None)
    base_wer = getattr(general_eval, "before_wer", None) or getattr(general_eval, "base_wer", None)
    adapter_cer = getattr(general_eval, "after_cer", None) or getattr(general_eval, "adapter_cer", None)
    adapter_wer = getattr(general_eval, "after_wer", None) or getattr(general_eval, "adapter_wer", None)

    # If any metric is unavailable treat as FAILED.
    if any(v is None for v in (base_cer, base_wer, adapter_cer, adapter_wer)):
        if run is not None:
            run.state = TrainingRunState.FAILED
            run.reject_reason = "General-set evaluation metrics unavailable."
            repos.training_runs.session.flush()

        existing_adapter = repos.adapters.get_for_user(uid)
        adapter_id = existing_adapter.adapter_id if existing_adapter is not None else None

        return TrainingOutcome(
            accepted=False,
            guardrail_result=None,
            notification=_FAILED_MESSAGE,
            adapter_id=adapter_id,
            run_final_state=TrainingRunState.FAILED,
        )

    guardrail = evaluate_guardrail(adapter_cer, base_cer, adapter_wer, base_wer)

    # ------------------------------------------------------------------
    # Case 3: REJECTED.
    # ------------------------------------------------------------------
    if guardrail != GuardrailResult.ACCEPTED:
        if run is not None:
            run.state = TrainingRunState.REJECTED
            run.reject_reason = guardrail.value
            repos.training_runs.session.flush()

        # Persist evaluation result even on rejection (records the evidence).
        _persist_eval_result(
            repos=repos, run_id=rid, user_id=uid,
            general_eval=general_eval,
        )

        existing_adapter = repos.adapters.get_for_user(uid)
        adapter_id = existing_adapter.adapter_id if existing_adapter is not None else None

        notification = _REJECTION_MESSAGES[guardrail]
        return TrainingOutcome(
            accepted=False,
            guardrail_result=guardrail,
            notification=notification,
            adapter_id=adapter_id,
            run_final_state=TrainingRunState.REJECTED,
        )

    # ------------------------------------------------------------------
    # Case 4: ACCEPTED.
    # ------------------------------------------------------------------
    # 4a. Serialize and save adapter weights to object store.
    weights_bytes = adapter_result_to_bytes(adapter_result)
    weights_path = object_store.put_adapter_weights(
        str(uid), weights_bytes, suffix=".pt"
    )

    # 4b. Upsert the Adapter row (exactly one per user, Req 5.3).
    existing_adapter = repos.adapters.get_for_user(uid)
    if existing_adapter is not None:
        # Update in place — preserve adapter_id (Req 5.3: at most one).
        existing_adapter.weights_path = weights_path
        existing_adapter.base_model_version = base_model_version
        existing_adapter.status = AdapterStatus.ACTIVE
        repos.adapters.session.flush()
        new_adapter = existing_adapter
    else:
        new_adapter = Adapter(
            user_id=uid,
            weights_path=weights_path,
            base_model_version=base_model_version,
            status=AdapterStatus.ACTIVE,
        )
        repos.adapters.add(new_adapter)

    # 4c. Mark consumed pairs (Req 5.1 — only on acceptance, Req 10.2).
    if pair_ids is not None:
        # Explicit list provided by caller (e.g., from run junction table).
        for pid in pair_ids:
            pair = repos.training_pairs.get(uid, pid)
            if pair is not None:
                pair.consumed_by_run_id = rid
    elif run is not None:
        # Derive from run_pairs junction.
        for run_pair in run.run_pairs:
            pair = repos.training_pairs.get(uid, run_pair.pair_id)
            if pair is not None:
                pair.consumed_by_run_id = rid

    repos.training_pairs.session.flush()

    # 4d. Persist evaluation result.
    _persist_eval_result(
        repos=repos, run_id=rid, user_id=uid,
        general_eval=general_eval,
    )

    # 4e. Transition run to ACCEPTED.
    if run is not None:
        run.state = TrainingRunState.ACCEPTED
        repos.training_runs.session.flush()

    return TrainingOutcome(
        accepted=True,
        guardrail_result=GuardrailResult.ACCEPTED,
        notification=_ACCEPTED_MESSAGE,
        adapter_id=new_adapter.adapter_id,
        run_final_state=TrainingRunState.ACCEPTED,
    )


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #

def _persist_eval_result(
    repos: Any,
    run_id: uuid.UUID,
    user_id: uuid.UUID,
    general_eval: Any,
) -> None:
    """Create and flush an EvaluationResult row from a general-eval report."""
    before_cer = getattr(general_eval, "before_cer", None)
    before_wer = getattr(general_eval, "before_wer", None)
    after_cer = getattr(general_eval, "after_cer", None)
    after_wer = getattr(general_eval, "after_wer", None)
    delta_cer = getattr(general_eval, "delta_cer", None)
    delta_wer = getattr(general_eval, "delta_wer", None)
    avail_str = getattr(general_eval, "availability", "unavailable")

    # Normalise availability to the enum.
    avail = (
        EvaluationAvailability.AVAILABLE
        if avail_str == "available"
        else EvaluationAvailability.UNAVAILABLE
    )

    # general_cer / general_wer are the adapter metrics on the general eval set.
    general_cer = after_cer
    general_wer = after_wer

    result = EvaluationResult(
        run_id=run_id,
        user_id=user_id,
        before_cer=before_cer,
        before_wer=before_wer,
        after_cer=after_cer,
        after_wer=after_wer,
        delta_cer=delta_cer,
        delta_wer=delta_wer,
        general_cer=general_cer,
        general_wer=general_wer,
        availability=avail,
    )
    repos.evaluation_results.add(result)


__all__ = [
    "TRAINING_THRESHOLD",
    "TrainingTriggerResult",
    "LockAcquireResult",
    "GuardrailResult",
    "TrainingOutcome",
    "maybe_enqueue_training",
    "split_held_out",
    "acquire_training_lock",
    "release_training_lock",
    "evaluate_guardrail",
    "complete_training_run",
]
