"""Integration tests for training trigger timing and end-to-end adapter use.

Covers three integration scenarios from task 14.15:

1. **Threshold trigger timing** (Req 5.1)
   Calling ``maybe_enqueue_training`` when the user has ≥ 20 unused pairs
   creates a QUEUED run.  The enqueue call itself is the timing proxy for the
   60-second requirement — the system must begin training *within* 60 s of the
   threshold being reached, meaning the trigger fires synchronously at the
   correction/calibration call that pushes the count to ≥ 20.

2. **End-to-end adapter application** (Req 5.4)
   When a user has an active adapter in the DB, the recognition endpoint
   applies *that user's* adapter (i.e. passes it to the HTR engine).  Verified
   via a TestClient + mock for ``recognize_bytes`` so we can inspect the adapter
   argument without running real ML inference.

3. **General-set evaluation recorded post-training** (Req 6.2)
   After ``complete_training_run`` finishes successfully, an ``EvaluationResult``
   row is stored in the DB for the completed run.

**Validates: Requirements 5.1, 5.4, 6.2**
"""

from __future__ import annotations

import io
import time
import uuid
from typing import BinaryIO
from unittest.mock import MagicMock, patch

import pytest
import torch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image as PILImage
from sqlalchemy.pool import StaticPool

from app.data import (
    Adapter,
    AdapterStatus,
    Base,
    EvaluationResult,
    Image,
    ImageFormat,
    ImageStatus,
    RepositoryFactory,
    TrainingPair,
    TrainingPairOrigin,
    TrainingRun,
    TrainingRunState,
    User,
    create_all,
    create_db_engine,
    create_session_factory,
)
from app.data.object_store import ObjectNotFoundError, ObjectStore
from app.ml.htr_config import DEFAULT_HTR_CONFIG
from app.ml.htr_model import build_model
from app.services.auth import SessionStore, hash_password
from app.services.auth_api import (
    get_current_user,
    get_db_session,
    get_session_store,
    router as auth_router,
)
from app.services.personalization_service import (
    TRAINING_THRESHOLD,
    TrainingOutcome,
    TrainingTriggerResult,
    complete_training_run,
    maybe_enqueue_training,
)
from app.services.recognition_service import (
    RecognitionOutcome,
    router as recognition_router,
)


# ---------------------------------------------------------------------------
# Shared in-memory object store
# ---------------------------------------------------------------------------


class _InMemoryObjectStore(ObjectStore):
    """Dict-backed object store for fast, isolated integration tests."""

    def __init__(self) -> None:
        self._data: dict[str, bytes] = {}

    def put(self, reference: str, data: bytes) -> str:
        self._data[reference] = data
        return reference

    def get(self, reference: str) -> bytes:
        try:
            return self._data[reference]
        except KeyError as exc:
            raise ObjectNotFoundError(f"Not found: {reference!r}") from exc

    def open(self, reference: str) -> BinaryIO:
        return io.BytesIO(self.get(reference))

    def exists(self, reference: str) -> bool:
        return reference in self._data

    def delete(self, reference: str) -> None:
        self._data.pop(reference, None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_png_bytes(width: int = 32, height: int = 32) -> bytes:
    """Return minimal valid PNG bytes for use as a stored image."""
    buf = io.BytesIO()
    PILImage.new("L", (width, height), color=255).save(buf, format="PNG")
    return buf.getvalue()


def _make_adapter_weights_bytes() -> bytes:
    """Return a valid adapter state_dict (classifier layer only) as bytes."""
    model = build_model(DEFAULT_HTR_CONFIG)
    state: dict = {"classifier.weight": model.classifier.weight.clone()}
    if model.classifier.bias is not None:
        state["classifier.bias"] = model.classifier.bias.clone()
    buf = io.BytesIO()
    torch.save(state, buf)
    return buf.getvalue()


def _setup_db():
    """Create an in-memory SQLite database with StaticPool and return helpers.

    Returns
    -------
    (session_factory, session, repos, object_store)
    """
    engine = create_db_engine("sqlite://", poolclass=StaticPool)
    create_all(engine)
    factory = create_session_factory(engine)
    session = factory()
    repos = RepositoryFactory(session)
    store = _InMemoryObjectStore()
    return factory, session, repos, store


def _make_user(session, username: str = "alice") -> User:
    user = User(
        username=username,
        email=f"{username}@example.com",
        password_hash=hash_password("pw"),
    )
    session.add(user)
    session.commit()
    return user


def _make_unused_pair(repos: RepositoryFactory, user: User, idx: int) -> TrainingPair:
    """Create a training pair with no consumed_by_run_id (unused)."""
    pair = TrainingPair(
        user_id=user.user_id,
        source_region_key=f"region_{idx}",
        region_crop_path=f"crops/crop_{idx}.png",
        corrected_text=f"word{idx}",
        origin=TrainingPairOrigin.CORRECTION,
        consumed_by_run_id=None,
    )
    repos.training_pairs.add(pair)
    repos.session.commit()
    return pair


def _make_image(session, user: User, store: _InMemoryObjectStore) -> Image:
    """Store a tiny PNG and create an Image row owned by user."""
    png = _make_png_bytes()
    path = store.put(f"images/{user.user_id}/img.png", png)
    image = Image(
        user_id=user.user_id,
        storage_path=path,
        format=ImageFormat.PNG,
        size_bytes=len(png),
        status=ImageStatus.ACCEPTED,
    )
    session.add(image)
    session.commit()
    return image


def _make_active_adapter(session, user: User, store: _InMemoryObjectStore) -> Adapter:
    """Persist adapter weights and create an ACTIVE Adapter row for user."""
    weights = _make_adapter_weights_bytes()
    path = store.put(f"adapters/{user.user_id}/adapter.pt", weights)
    adapter = Adapter(
        user_id=user.user_id,
        weights_path=path,
        base_model_version="test-v1",
        status=AdapterStatus.ACTIVE,
    )
    session.add(adapter)
    session.commit()
    return adapter


# ===========================================================================
# Test 1: Threshold trigger timing — enqueue within 60 s (Req 5.1)
# ===========================================================================


class TestThresholdTriggerTiming:
    """Req 5.1: Enqueue happens synchronously when ≥ 20 unused pairs are present.

    The 60-second window is satisfied because ``maybe_enqueue_training`` is
    called inline on every correction/calibration that could push the count
    to the threshold.  These tests verify the enqueue fires at/above the
    threshold and measure the elapsed time to show it completes well within
    the 60-second budget.
    """

    def test_enqueue_fires_at_exactly_threshold(self):
        """Exactly 20 unused pairs → ENQUEUED + QUEUED run created (Req 5.1)."""
        _, session, repos, _ = _setup_db()
        user = _make_user(session)

        for i in range(TRAINING_THRESHOLD):
            _make_unused_pair(repos, user, i)

        result = maybe_enqueue_training(user.user_id, repos)

        assert result is TrainingTriggerResult.ENQUEUED, (
            f"Expected ENQUEUED at threshold, got {result}"
        )
        runs = repos.training_runs.list_for_user(user.user_id)
        assert len(runs) == 1, f"Expected 1 queued run, got {len(runs)}"
        assert runs[0].state == TrainingRunState.QUEUED

    def test_enqueue_fires_above_threshold(self):
        """More than 20 unused pairs → still ENQUEUED (Req 5.1)."""
        _, session, repos, _ = _setup_db()
        user = _make_user(session)

        for i in range(30):
            _make_unused_pair(repos, user, i)

        result = maybe_enqueue_training(user.user_id, repos)

        assert result is TrainingTriggerResult.ENQUEUED
        runs = repos.training_runs.list_for_user(user.user_id)
        assert len(runs) == 1
        assert runs[0].state == TrainingRunState.QUEUED

    def test_no_enqueue_below_threshold(self):
        """19 unused pairs (one below threshold) → BELOW_THRESHOLD (Req 5.1)."""
        _, session, repos, _ = _setup_db()
        user = _make_user(session)

        for i in range(TRAINING_THRESHOLD - 1):
            _make_unused_pair(repos, user, i)

        result = maybe_enqueue_training(user.user_id, repos)

        assert result is TrainingTriggerResult.BELOW_THRESHOLD
        runs = repos.training_runs.list_for_user(user.user_id)
        assert len(runs) == 0, "No run should be created below the threshold"

    def test_enqueue_completes_well_within_60_seconds(self):
        """Enqueue call itself completes in << 60 s (timing proxy for Req 5.1).

        The requirement states training SHALL BEGIN within 60 seconds of the
        threshold being reached.  The trigger is synchronous: the enqueue
        fires immediately when the count reaches threshold.  Measuring the
        wall-clock time of the enqueue call confirms this constraint is met.
        """
        _, session, repos, _ = _setup_db()
        user = _make_user(session)

        for i in range(TRAINING_THRESHOLD):
            _make_unused_pair(repos, user, i)

        start = time.monotonic()
        result = maybe_enqueue_training(user.user_id, repos)
        elapsed = time.monotonic() - start

        assert result is TrainingTriggerResult.ENQUEUED
        # The enqueue (which is the trigger for the 60-second window) must
        # complete well within the 60-second budget.  In-memory SQLite makes
        # this orders of magnitude faster, but we document the constraint.
        SIXTY_SECONDS = 60.0
        assert elapsed < SIXTY_SECONDS, (
            f"Enqueue took {elapsed:.3f}s, exceeding the 60-second window (Req 5.1)"
        )

    def test_enqueue_creates_run_scoped_to_correct_user(self):
        """The QUEUED run must be associated with the user who reached threshold."""
        _, session, repos, _ = _setup_db()
        alice = _make_user(session, "alice")
        bob = _make_user(session, "bob")

        # Only alice reaches the threshold.
        for i in range(TRAINING_THRESHOLD):
            _make_unused_pair(repos, alice, i)

        # Bob has zero pairs.
        bob_result = maybe_enqueue_training(bob.user_id, repos)
        alice_result = maybe_enqueue_training(alice.user_id, repos)

        assert bob_result is TrainingTriggerResult.BELOW_THRESHOLD
        assert alice_result is TrainingTriggerResult.ENQUEUED

        alice_runs = repos.training_runs.list_for_user(alice.user_id)
        bob_runs = repos.training_runs.list_for_user(bob.user_id)

        assert len(alice_runs) == 1 and alice_runs[0].user_id == alice.user_id
        assert len(bob_runs) == 0


# ===========================================================================
# Test 2: End-to-end adapter application at inference (Req 5.4)
# ===========================================================================


class TestAdapterAppliedEndToEndAtInference:
    """Req 5.4: Recognition endpoint applies the user's adapter at inference time.

    Uses a TestClient + mock for ``recognize_bytes`` to capture the adapter
    argument forwarded to the HTR engine, without running real ML inference.
    """

    @pytest.fixture()
    def app_and_deps(self):
        """Build a minimal FastAPI app wiring auth + recognition endpoints."""
        engine = create_db_engine("sqlite://", poolclass=StaticPool)
        create_all(engine)
        factory = create_session_factory(engine)
        store = _InMemoryObjectStore()
        session = factory()
        session_store = SessionStore(session)

        app = FastAPI()
        app.include_router(auth_router)
        app.include_router(recognition_router)

        from app.services.isolation import register_isolation_handlers
        register_isolation_handlers(app)

        def _override_db():
            with factory() as s:
                yield s

        app.dependency_overrides[get_db_session] = _override_db
        app.dependency_overrides[get_session_store] = lambda: session_store

        from app.services import recognition_service as rs
        app.dependency_overrides[rs._get_object_store_dep] = lambda: store

        yield app, factory, store, session_store
        Base.metadata.drop_all(engine)

    def _register_user_and_get_token(
        self,
        factory,
        session_store: SessionStore,
        username: str,
        store: _InMemoryObjectStore,
    ) -> tuple[User, str]:
        """Create a user and issue a session token directly."""
        with factory() as s:
            user = _make_user(s, username)
        token = session_store.create(user.user_id)
        return user, token

    def _auth_header(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def test_adapter_passed_to_engine_when_user_has_adapter(self, app_and_deps):
        """Req 5.4: When user has an active adapter, it is forwarded to the engine.

        Asserts end-to-end: the HTTP recognition endpoint retrieves the user's
        adapter from the DB and passes it through to ``recognize_bytes``.
        """
        app, factory, store, session_store = app_and_deps

        alice, token = self._register_user_and_get_token(
            factory, session_store, "alice_e2e", store
        )

        # Seed an adapter and an image for alice.
        with factory() as s:
            db_adapter = _make_active_adapter(s, alice, store)
            alice_image = _make_image(s, alice, store)
            image_id = alice_image.image_id
            expected_weights = store.get(db_adapter.weights_path)

        from app.ml.recognition import RecognitionResult as MLResult

        mock_result = MLResult(lines=[], recognized_any_text=False)
        captured: list = []

        def _capture_recognize(image_bytes, adapter=None, **kw):
            # Read the resolved weights while the temp file still exists —
            # run_recognition deletes it once inference returns.
            captured.append(
                open(adapter.weights_path, "rb").read() if adapter is not None else None
            )
            return mock_result

        with TestClient(app, raise_server_exceptions=True) as client:
            with patch(
                "app.services.recognition_service.recognize_bytes",
                side_effect=_capture_recognize,
            ):
                resp = client.post(
                    f"/images/{image_id}/recognize",
                    headers=self._auth_header(token),
                )

        assert resp.status_code == 200, f"Unexpected status: {resp.status_code} {resp.text}"

        # The weights forwarded to the engine (resolved through the object
        # store — Req 5.4) must be alice's own adapter weights.
        assert len(captured) == 1, "recognize_bytes must be called exactly once"
        passed_weights = captured[0]
        assert passed_weights is not None, (
            "Req 5.4: adapter must be passed to the engine when one exists"
        )
        assert passed_weights == expected_weights, "Wrong adapter weights passed"

    def test_no_adapter_passed_when_user_has_none(self, app_and_deps):
        """Req 5.5: When user has no adapter, None is forwarded to the engine."""
        app, factory, store, session_store = app_and_deps

        bob, token = self._register_user_and_get_token(
            factory, session_store, "bob_no_adapter", store
        )

        with factory() as s:
            bob_image = _make_image(s, bob, store)
            image_id = bob_image.image_id

        from app.ml.recognition import RecognitionResult as MLResult

        mock_result = MLResult(lines=[], recognized_any_text=False)
        captured: list = []

        def _capture_recognize(image_bytes, adapter=None, **kw):
            captured.append(adapter)
            return mock_result

        with TestClient(app, raise_server_exceptions=True) as client:
            with patch(
                "app.services.recognition_service.recognize_bytes",
                side_effect=_capture_recognize,
            ):
                resp = client.post(
                    f"/images/{image_id}/recognize",
                    headers=self._auth_header(token),
                )

        assert resp.status_code == 200

        assert len(captured) == 1
        assert captured[0] is None, (
            "Req 5.5: adapter must be None when user has no adapter"
        )

    def test_user_b_adapter_not_used_for_user_a_recognition(self, app_and_deps):
        """Req 5.4 / 10.3: User B's adapter is never applied to User A's request."""
        app, factory, store, session_store = app_and_deps

        alice, alice_token = self._register_user_and_get_token(
            factory, session_store, "alice_isolation", store
        )
        bob, _ = self._register_user_and_get_token(
            factory, session_store, "bob_isolation", store
        )

        # Bob has an adapter; alice does not.
        with factory() as s:
            _make_active_adapter(s, bob, store)
            alice_image = _make_image(s, alice, store)
            image_id = alice_image.image_id

        from app.ml.recognition import RecognitionResult as MLResult

        mock_result = MLResult(lines=[], recognized_any_text=False)
        captured: list = []

        def _capture_recognize(image_bytes, adapter=None, **kw):
            captured.append(adapter)
            return mock_result

        with TestClient(app, raise_server_exceptions=True) as client:
            with patch(
                "app.services.recognition_service.recognize_bytes",
                side_effect=_capture_recognize,
            ):
                resp = client.post(
                    f"/images/{image_id}/recognize",
                    headers=self._auth_header(alice_token),
                )

        assert resp.status_code == 200

        assert len(captured) == 1
        assert captured[0] is None, (
            "Bob's adapter must NEVER be passed for Alice's recognition request (Req 10.3)"
        )


# ===========================================================================
# Test 3: General-set measurement recorded after complete_training_run (Req 6.2)
# ===========================================================================


class TestEvaluationResultRecordedPostTraining:
    """Req 6.2: After complete_training_run, an EvaluationResult is in the DB.

    Uses a fake AdapterResult and a fake general-eval object to avoid real
    ML inference while still exercising the full post-training outcome path.
    """

    @pytest.fixture()
    def db_setup(self):
        """Provide a per-test isolated in-memory DB."""
        engine = create_db_engine("sqlite://", poolclass=StaticPool)
        create_all(engine)
        factory = create_session_factory(engine)
        session = factory()
        repos = RepositoryFactory(session)
        store = _InMemoryObjectStore()
        yield repos, session, store
        session.close()
        Base.metadata.drop_all(engine)

    def _make_running_run(self, repos, user: User) -> TrainingRun:
        """Create a TrainingRun in RUNNING state for the given user."""
        run = TrainingRun(
            user_id=user.user_id,
            state=TrainingRunState.RUNNING,
        )
        repos.training_runs.add(run)
        repos.session.commit()
        return run

    def _make_fake_adapter_result(self):
        """Return a minimal fake AdapterResult compatible with complete_training_run."""
        from app.ml.adapter_trainer import AdapterResult
        model = build_model(DEFAULT_HTR_CONFIG)
        return AdapterResult(
            classifier_weight=model.classifier.weight.detach().clone(),
            classifier_bias=(
                model.classifier.bias.detach().clone()
                if model.classifier.bias is not None
                else torch.zeros(model.classifier.weight.shape[0])
            ),
            epochs_run=3,
            cer_history=[0.5, 0.4, 0.35],
        )

    def _make_fake_general_eval(
        self,
        base_cer: float = 0.10,
        base_wer: float = 0.15,
        adapter_cer: float = 0.08,
        adapter_wer: float = 0.12,
    ):
        """Return a simple namespace that general_eval duck-typing expects."""
        class _FakeEval:
            before_cer = base_cer
            before_wer = base_wer
            after_cer = adapter_cer
            after_wer = adapter_wer
            delta_cer = base_cer - adapter_cer
            delta_wer = base_wer - adapter_wer
            availability = "available"

        return _FakeEval()

    def test_evaluation_result_persisted_on_accepted_run(self, db_setup):
        """Req 6.2: An EvaluationResult is stored in the DB after a successful run.

        After ``complete_training_run`` with a passing general-eval, an
        ``EvaluationResult`` row is readable via the repository.
        """
        repos, session, store = db_setup
        user = _make_user(session)
        run = self._make_running_run(repos, user)

        adapter_result = self._make_fake_adapter_result()
        general_eval = self._make_fake_general_eval()

        outcome = complete_training_run(
            user_id=user.user_id,
            run_id=run.run_id,
            adapter_result=adapter_result,
            general_eval=general_eval,
            repos=repos,
            object_store=store,
        )
        session.commit()

        assert outcome.accepted, (
            f"Expected adapter to be accepted, got: accepted={outcome.accepted}, "
            f"guardrail={outcome.guardrail_result}, notification={outcome.notification}"
        )

        # Evaluation result must now exist in the DB for this run.
        eval_result = repos.evaluation_results.get_for_run(user.user_id, run.run_id)
        assert eval_result is not None, (
            "Req 6.2: EvaluationResult must be persisted after complete_training_run"
        )
        assert eval_result.run_id == run.run_id
        assert eval_result.user_id == user.user_id

    def test_evaluation_result_records_general_set_metrics(self, db_setup):
        """Req 6.2: The EvaluationResult captures the general-set CER and WER.

        The ``general_cer`` and ``general_wer`` fields on the EvaluationResult
        represent the adapter's performance on the general English evaluation set.
        """
        repos, session, store = db_setup
        user = _make_user(session)
        run = self._make_running_run(repos, user)

        adapter_cer = 0.07
        adapter_wer = 0.11

        adapter_result = self._make_fake_adapter_result()
        general_eval = self._make_fake_general_eval(
            base_cer=0.10, base_wer=0.15,
            adapter_cer=adapter_cer, adapter_wer=adapter_wer,
        )

        outcome = complete_training_run(
            user_id=user.user_id,
            run_id=run.run_id,
            adapter_result=adapter_result,
            general_eval=general_eval,
            repos=repos,
            object_store=store,
        )
        session.commit()

        assert outcome.accepted

        eval_result = repos.evaluation_results.get_for_run(user.user_id, run.run_id)
        assert eval_result is not None

        # general_cer / general_wer must match the adapter's general-set values.
        assert eval_result.general_cer == pytest.approx(adapter_cer), (
            f"Expected general_cer={adapter_cer}, got {eval_result.general_cer}"
        )
        assert eval_result.general_wer == pytest.approx(adapter_wer), (
            f"Expected general_wer={adapter_wer}, got {eval_result.general_wer}"
        )

    def test_evaluation_result_persisted_on_rejected_run(self, db_setup):
        """Req 6.2: EvaluationResult is persisted even when the guardrail rejects.

        The requirement says the evaluation happens after training completes;
        the result must be recorded regardless of acceptance/rejection to
        preserve evidence of the general-set measurement.
        """
        repos, session, store = db_setup
        user = _make_user(session)
        run = self._make_running_run(repos, user)

        adapter_result = self._make_fake_adapter_result()
        # Adapter CER vastly exceeds base CER → guardrail rejects.
        general_eval = self._make_fake_general_eval(
            base_cer=0.10, base_wer=0.15,
            adapter_cer=0.30,   # +20 pp → REJECTED
            adapter_wer=0.15,
        )

        outcome = complete_training_run(
            user_id=user.user_id,
            run_id=run.run_id,
            adapter_result=adapter_result,
            general_eval=general_eval,
            repos=repos,
            object_store=store,
        )
        session.commit()

        assert not outcome.accepted, "Adapter should have been rejected (CER too high)"

        # Even on rejection the EvaluationResult must be stored (Req 6.2).
        eval_result = repos.evaluation_results.get_for_run(user.user_id, run.run_id)
        assert eval_result is not None, (
            "Req 6.2: EvaluationResult must be persisted even when adapter is rejected"
        )
        assert eval_result.run_id == run.run_id

    def test_evaluation_result_list_for_user_contains_new_record(self, db_setup):
        """Req 6.2: The user's evaluation results list grows by 1 after a completed run."""
        repos, session, store = db_setup
        user = _make_user(session)

        before_count = len(repos.evaluation_results.list_for_user(user.user_id))

        run = self._make_running_run(repos, user)
        adapter_result = self._make_fake_adapter_result()
        general_eval = self._make_fake_general_eval()

        complete_training_run(
            user_id=user.user_id,
            run_id=run.run_id,
            adapter_result=adapter_result,
            general_eval=general_eval,
            repos=repos,
            object_store=store,
        )
        session.commit()

        after_count = len(repos.evaluation_results.list_for_user(user.user_id))
        assert after_count == before_count + 1, (
            f"Expected evaluation results to grow by 1, "
            f"before={before_count}, after={after_count}"
        )

    def test_no_evaluation_result_when_training_fails(self, db_setup):
        """Req 6.2 (negative case): No EvaluationResult when adapter_result is None.

        When training fails outright (adapter_result=None) there is nothing to
        evaluate; the service must not produce a spurious EvaluationResult row.
        """
        repos, session, store = db_setup
        user = _make_user(session)
        run = self._make_running_run(repos, user)

        outcome = complete_training_run(
            user_id=user.user_id,
            run_id=run.run_id,
            adapter_result=None,  # training failed
            general_eval=None,
            repos=repos,
            object_store=store,
        )
        session.commit()

        assert not outcome.accepted
        assert outcome.run_final_state == TrainingRunState.FAILED

        eval_result = repos.evaluation_results.get_for_run(user.user_id, run.run_id)
        assert eval_result is None, (
            "No EvaluationResult should be recorded when training fails with None adapter"
        )
