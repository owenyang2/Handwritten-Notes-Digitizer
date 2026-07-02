"""Async Training Worker: consumes QUEUED ``TrainingRun`` rows end to end.

The design describes an "Async Training Worker" that the Personalization_Service
hands jobs to (see design.md's Component Map and "Asynchronous Training
Architecture"). Every other piece of that pipeline already existed and was
covered by tests — the threshold trigger
(:func:`app.services.personalization_service.maybe_enqueue_training`), the
per-user lock (:func:`~app.services.personalization_service.acquire_training_lock`
/ :func:`~app.services.personalization_service.release_training_lock`), the PEFT
trainer (:func:`app.ml.adapter_trainer.train_adapter`), the CER/WER metrics
(:mod:`app.services.evaluation_service`), and the accept/reject guardrail +
finalisation (:func:`~app.services.personalization_service.complete_training_run`)
— but nothing ever actually called them in sequence outside of tests. This
module is that missing glue: a lightweight in-process background thread (the
design explicitly allows "an in-process task queue such as a thread/async
worker for the demo") that polls for QUEUED runs and drives them to completion.

Pipeline per run
----------------
1. Acquire the per-user training lock (QUEUED → RUNNING).
2. Load the user's unused ``TrainingPair`` rows and split them into a training
   set and a held-out set (Req 6.1).
3. Decode each pair's stored region crop into a model-ready tensor and train a
   PEFT adapter (last-layer fine-tune) on top of the shared frozen base model,
   with early stopping (Req 5.2, 6.5).
4. Evaluate CER/WER before/after on the user's own held-out set (Req 7.1, 7.2)
   *and* on the fixed general English evaluation set (Req 6.2), the latter
   feeding the accept/reject guardrail (Req 6.3, 6.4).
5. Finalise via ``complete_training_run`` (persists the adapter on acceptance,
   marks pairs consumed, records the outcome) and patch the persisted
   ``EvaluationResult`` so its before/after/delta fields reflect the user's own
   held-out set while ``general_cer``/``general_wer`` reflect the general set
   (Req 7.3).
6. Release the per-user lock, recording the terminal state.

Any unexpected exception for a given run is caught, the run is marked FAILED,
and the worker continues polling — one user's bad data or a training bug must
never crash the process or block other users' training (Req 5.7).
"""

from __future__ import annotations

import logging
import threading
import uuid
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config.settings import get_settings
from app.data.enums import TrainingRunState
from app.data.models import TrainingPair, TrainingRun, TrainingRunPair
from app.data.object_store import ObjectStore
from app.data.repositories import RepositoryFactory
from app.ml.adapter_trainer import AdapterResult, adapter_result_to_bytes, train_adapter
from app.ml.general_eval_set import load_general_eval_set
from app.ml.htr_config import DEFAULT_HTR_CONFIG
from app.ml.recognition import apply_adapter, get_base_model, recognize_crop
from app.ml.train_base import LabeledSample
from app.services.evaluation_service import BeforeAfterReport, compute_before_after
from app.services.personalization_service import (
    TRAINING_THRESHOLD,
    LockAcquireResult,
    TrainingOutcome,
    acquire_training_lock,
    complete_training_run,
    release_training_lock,
    split_held_out,
)

logger = logging.getLogger(__name__)

# How often the worker polls for QUEUED runs. Well within the 60-second
# trigger budget (Req 5.1).
POLL_INTERVAL_SECONDS: float = 3.0

# Seed used for adapter training determinism (Req 11.3); derived from the
# global settings seed so re-running the whole system is reproducible.
_ADAPTER_SEED_OFFSET = 9001


# --------------------------------------------------------------------------- #
# Crop decoding: region crop bytes -> model-ready LabeledSample
# --------------------------------------------------------------------------- #


def _crop_bytes_to_labeled_sample(crop_bytes: bytes, text: str) -> Optional[LabeledSample]:
    """Decode a stored region-crop image into a ``(C, H, W)`` LabeledSample.

    Mirrors :func:`app.ml.iam_dataset._load_real_image`'s normalisation so
    per-user crops are preprocessed identically to how the base model was
    trained. Returns ``None`` if the crop cannot be decoded (corrupt/missing
    file) so the caller can skip that pair rather than crash the whole run.
    """
    import io

    import torch
    from PIL import Image, UnidentifiedImageError

    config = DEFAULT_HTR_CONFIG
    try:
        with Image.open(io.BytesIO(crop_bytes)) as img:
            gray = img.convert("L")
            orig_w, orig_h = gray.size
            target_h = config.image_height
            target_w = max(1, round(orig_w * (target_h / max(orig_h, 1))))
            resized = gray.resize((target_w, target_h))
            values = list(resized.getdata())
    except (UnidentifiedImageError, OSError, ValueError):
        return None

    tensor = torch.tensor(values, dtype=torch.float32).reshape(target_h, target_w)
    tensor = (tensor / 255.0).unsqueeze(0)
    if config.input_channels > 1:
        tensor = tensor.repeat(config.input_channels, 1, 1)
    return LabeledSample(image=tensor, text=text)


def _synthetic_general_eval_crop_bytes(sample_id: str, text: str) -> bytes:
    """Deterministic placeholder crop for a general-eval-set reference text.

    The general evaluation set (``general_eval_manifest.json``) ships only
    reference transcriptions, not images (see ``GENERAL_EVAL_SET.md`` — real
    IAM line images are not redistributed for licensing reasons). To exercise
    the guardrail's actual recognise-and-compare pipeline end to end without a
    licensed IAM copy, we generate a stable synthetic placeholder image per
    sample using the same deterministic generator the base-model training
    pipeline uses for synthetic IAM samples, keyed by the sample id so it is
    reproducible across runs (Req 11.3).
    """
    import io

    from PIL import Image as PILImage

    from app.ml.iam_dataset import IamSampleRef, synthetic_image

    ref = IamSampleRef(sample_id=sample_id, split="test", text=text, image="")
    tensor = synthetic_image(ref, config=DEFAULT_HTR_CONFIG, base_seed=get_settings().random_seed)
    array = (tensor.squeeze(0).clamp(0, 1).numpy() * 255).astype("uint8")
    buf = io.BytesIO()
    PILImage.fromarray(array, mode="L").save(buf, format="PNG")
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# Core per-run processing
# --------------------------------------------------------------------------- #


def _run_adapter_training(
    user_id: uuid.UUID,
    run_id: uuid.UUID,
    repos: RepositoryFactory,
    object_store: ObjectStore,
) -> TrainingOutcome:
    """Execute one training run end to end and return its outcome.

    Assumes the run has already been transitioned to RUNNING by
    :func:`acquire_training_lock`. Never raises for expected/data-driven
    failures (empty dataset, undecodable crops) — those are reported as a
    FAILED outcome via ``complete_training_run(adapter_result=None, ...)`` so
    the caller can commit a clean terminal state either way.
    """
    from app.data.base import utcnow

    run: TrainingRun | None = repos.training_runs.get(user_id, run_id)
    if run is not None:
        run.started_at = utcnow()
        repos.session.flush()

    # Mirrors maybe_enqueue_training's threshold source (Settings, configurable
    # via HND_PERSONALIZATION_TRIGGER_THRESHOLD) so this defensive re-check
    # can never be stricter than the trigger that enqueued the run.
    threshold = getattr(get_settings(), "personalization_trigger_threshold", TRAINING_THRESHOLD)

    pairs: Sequence[TrainingPair] = repos.training_pairs.list_unused(user_id)
    if len(pairs) < threshold:
        # Should not normally happen (the trigger only enqueues at/above
        # threshold), but guard defensively against races/manual runs.
        logger.warning(
            "Training run %s for user %s has only %d unused pairs (< %d); failing.",
            run_id, user_id, len(pairs), threshold,
        )
        return complete_training_run(
            user_id, run_id, None, None, repos, object_store,
        )

    training_pairs_orm, held_out_pairs_orm = split_held_out(list(pairs))

    # Record which pairs this run trained on vs. held out (Req 6.1) for audit.
    for pair in training_pairs_orm:
        repos.session.add(TrainingRunPair(run_id=run_id, pair_id=pair.pair_id, is_held_out=False))
    for pair in held_out_pairs_orm:
        repos.session.add(TrainingRunPair(run_id=run_id, pair_id=pair.pair_id, is_held_out=True))
    repos.session.flush()

    def to_sample(pair: TrainingPair) -> Optional[LabeledSample]:
        try:
            crop_bytes = object_store.get(pair.region_crop_path)
        except Exception:
            logger.warning("Could not read region crop for pair %s; skipping.", pair.pair_id)
            return None
        return _crop_bytes_to_labeled_sample(crop_bytes, pair.corrected_text)

    training_samples = [s for s in (to_sample(p) for p in training_pairs_orm) if s is not None]
    held_out_samples = [s for s in (to_sample(p) for p in held_out_pairs_orm) if s is not None]

    all_pair_ids = [p.pair_id for p in training_pairs_orm] + [p.pair_id for p in held_out_pairs_orm]

    if not training_samples:
        logger.warning("Training run %s for user %s has no decodable training crops; failing.", run_id, user_id)
        return complete_training_run(
            user_id, run_id, None, None, repos, object_store,
        )

    base_model = get_base_model()

    try:
        adapter_result: AdapterResult | None = train_adapter(
            base_model,
            training_samples,
            held_out_samples,
            seed=get_settings().random_seed + _ADAPTER_SEED_OFFSET,
            max_epochs=get_settings().max_training_epochs,
        )
    except Exception:
        logger.exception("Adapter training raised for run %s (user %s).", run_id, user_id)
        adapter_result = None

    if adapter_result is None:
        return complete_training_run(
            user_id, run_id, None, None, repos, object_store,
        )

    # --- Build recognisers over the base model and the newly trained adapter.
    def base_recogniser(crop_bytes: bytes) -> str:
        return recognize_crop(crop_bytes, base_model)

    adapter_bytes = adapter_result_to_bytes(adapter_result)
    adapted_model = apply_adapter(base_model, adapter_bytes)

    def adapter_recogniser(crop_bytes: bytes) -> str:
        return recognize_crop(crop_bytes, adapted_model)

    # --- Req 7.1/7.2: before/after on the user's own held-out set.
    user_held_out_pairs = [
        (object_store.get(p.region_crop_path), p.corrected_text)
        for p in held_out_pairs_orm
        if object_store.exists(p.region_crop_path)
    ]
    user_report: BeforeAfterReport = compute_before_after(
        user_held_out_pairs, base_recogniser, adapter_recogniser
    )

    # --- Req 6.2/6.3/6.4: before/after on the fixed general English eval set
    #     (feeds the accept/reject guardrail).
    general_set = load_general_eval_set()
    general_pairs = [
        (_synthetic_general_eval_crop_bytes(sample.sample_id, sample.text), sample.text)
        for sample in general_set.samples
    ]
    general_report: BeforeAfterReport = compute_before_after(
        general_pairs, base_recogniser, adapter_recogniser
    )

    outcome = complete_training_run(
        user_id,
        run_id,
        adapter_result,
        general_report,
        repos,
        object_store,
        base_model_version="1.0",
        pair_ids=all_pair_ids,
    )

    # complete_training_run persists an EvaluationResult sourced from the
    # *general* report for ACCEPTED/REJECTED outcomes (it is the guardrail
    # input). Patch before/after/delta to reflect the user's own held-out set
    # (Req 7.1-7.3); general_cer/general_wer (already correct) are untouched.
    if run is not None and run.state in (TrainingRunState.ACCEPTED, TrainingRunState.REJECTED):
        eval_result = repos.evaluation_results.get_for_run(user_id, run_id)
        if eval_result is not None:
            from app.data.enums import EvaluationAvailability

            eval_result.before_cer = user_report.before_cer
            eval_result.before_wer = user_report.before_wer
            eval_result.after_cer = user_report.after_cer
            eval_result.after_wer = user_report.after_wer
            eval_result.delta_cer = user_report.delta_cer
            eval_result.delta_wer = user_report.delta_wer
            eval_result.availability = (
                EvaluationAvailability.AVAILABLE
                if user_report.availability == "available"
                else EvaluationAvailability.UNAVAILABLE
            )
            repos.session.flush()

    if run is not None:
        run.finished_at = utcnow()
        repos.session.flush()

    return outcome


def _process_run(
    user_id: uuid.UUID,
    run_id: uuid.UUID,
    session_factory: sessionmaker[Session],
    object_store: ObjectStore,
) -> None:
    """Acquire the lock, run training, and always release the lock/commit."""
    session = session_factory()
    try:
        repos = RepositoryFactory(session)
        lock_result = acquire_training_lock(user_id, run_id, repos)
        if lock_result != LockAcquireResult.ACQUIRED:
            # Already running (or the run vanished/changed state) — nothing
            # to do; another worker tick or thread owns it.
            session.commit()
            return
        session.commit()  # persist the QUEUED -> RUNNING transition promptly.

        try:
            outcome = _run_adapter_training(user_id, run_id, repos, object_store)
            final_state = outcome.run_final_state
        except Exception:
            logger.exception(
                "Unhandled error processing training run %s for user %s; marking FAILED.",
                run_id, user_id,
            )
            session.rollback()
            final_state = TrainingRunState.FAILED
            # Re-open repos on a clean session state after rollback.
            repos = RepositoryFactory(session)

        release_training_lock(user_id, run_id, repos, final_state)
        session.commit()
    except Exception:
        logger.exception("Training worker failed to process run %s for user %s.", run_id, user_id)
        try:
            session.rollback()
        except Exception:  # noqa: BLE001
            pass
    finally:
        session.close()


def process_pending_training_runs(
    session_factory: sessionmaker[Session],
    object_store: ObjectStore,
) -> int:
    """Find every QUEUED ``TrainingRun`` (across all users) and process it.

    Returns the number of runs processed. This is the polling body used by the
    background thread, but it is also directly callable (e.g. from a script or
    a test) to synchronously drain the queue without waiting on the thread.
    """
    session = session_factory()
    try:
        stmt = select(TrainingRun.run_id, TrainingRun.user_id).where(
            TrainingRun.state == TrainingRunState.QUEUED
        )
        queued = list(session.execute(stmt).all())
    finally:
        session.close()

    for run_id, user_id in queued:
        _process_run(user_id, run_id, session_factory, object_store)

    return len(queued)


# --------------------------------------------------------------------------- #
# Background thread lifecycle
# --------------------------------------------------------------------------- #

_worker_thread: threading.Thread | None = None
_stop_event = threading.Event()


def _worker_loop(session_factory: sessionmaker[Session], object_store: ObjectStore) -> None:
    while not _stop_event.is_set():
        try:
            process_pending_training_runs(session_factory, object_store)
        except Exception:
            logger.exception("Training worker poll iteration failed.")
        _stop_event.wait(POLL_INTERVAL_SECONDS)


def start_background_worker(
    session_factory: sessionmaker[Session],
    object_store: ObjectStore,
) -> None:
    """Start the polling background thread (idempotent)."""
    global _worker_thread

    if _worker_thread is not None and _worker_thread.is_alive():
        return

    _stop_event.clear()
    _worker_thread = threading.Thread(
        target=_worker_loop,
        args=(session_factory, object_store),
        name="training-worker",
        daemon=True,
    )
    _worker_thread.start()
    logger.info("Personalization training worker started (poll interval %.1fs).", POLL_INTERVAL_SECONDS)


def stop_background_worker(timeout: float = 5.0) -> None:
    """Signal the worker thread to stop and wait briefly for it to exit."""
    global _worker_thread

    _stop_event.set()
    if _worker_thread is not None:
        _worker_thread.join(timeout=timeout)
        _worker_thread = None


__all__ = [
    "POLL_INTERVAL_SECONDS",
    "process_pending_training_runs",
    "start_background_worker",
    "stop_background_worker",
]
