"""Property-based test for training-failure retention (design Property 16).

# Feature: handwriting-notes-digitizer, Property 16: Training failure retains the prior active configuration

When a training run fails — regardless of the failure mode (None adapter_result,
None general_eval, or an exception raised during training) — the user's
previously active adapter must be exactly preserved, and if the user had no
adapter before the run, they still have none after.

**Validates: Requirements 5.7**

Requirement 5.7: IF an Adapter training does not complete successfully, THEN
THE Personalization_Service SHALL retain the user's previously active
recognition configuration and record an indication that the training attempt
did not succeed.

Strategy
--------
The property is tested at the ``complete_training_run`` service boundary using:

* An in-memory SQLite database for fast, fully isolated iterations.
* An in-memory dict-backed object store (no filesystem I/O).
* All three failure modes exposed by ``complete_training_run``:
  1. ``adapter_result=None`` — training produced no adapter (Case 1, FAILED).
  2. ``general_eval=None`` — evaluation metrics unavailable (Case 1, FAILED).
  3. An exception raised during ``complete_training_run`` itself (simulated by
     an object store that raises on ``put_adapter_weights``).

For each generated example we:

1. Optionally pre-install an existing adapter (weights_path set to a fake path).
2. Record the adapter state before the failing run (path or None).
3. Invoke ``complete_training_run`` with a failing scenario (or catch the
   exception if the store raises).
4. Query ``repos.adapters.get_for_user(user_id)`` after the call.
5. Assert that the adapter's weights_path (or None) is identical to what it was
   before the failing run.

The test also checks that the run's final state is FAILED (not ACCEPTED or
REJECTED) for None-result failures, confirming that an indication of failure is
recorded.

Generators
----------
* ``has_prior_adapter`` — bool: whether the user had an adapter before the run.
* ``prior_weights_path`` — a non-empty string representing a fake prior adapter
  weights path (only relevant when ``has_prior_adapter=True``).
* ``failure_mode`` — one of "none_result", "none_eval", or "store_exception".
"""

from __future__ import annotations

import io
import uuid
from typing import BinaryIO, Optional

import pytest
import torch
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy.pool import StaticPool

from app.data import (
    Base,
    RepositoryFactory,
    TrainingRun,
    TrainingRunState,
    User,
    Adapter,
    AdapterStatus,
    create_all,
    create_db_engine,
    create_session_factory,
)
from app.data.object_store import ObjectStore, ObjectNotFoundError
from app.services.personalization_service import complete_training_run


# ---------------------------------------------------------------------------
# In-memory object store
# ---------------------------------------------------------------------------


class _InMemoryObjectStore(ObjectStore):
    """Dict-backed object store for fast, isolated property tests."""

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


class _FailingObjectStore(_InMemoryObjectStore):
    """Object store that raises RuntimeError when put_adapter_weights is called.

    Used to simulate a storage failure mid-run, triggering an exception path.
    """

    def put(self, reference: str, data: bytes) -> str:
        # Only fail on adapter weight writes (they contain 'adapter' in the path).
        if "adapter" in reference:
            raise RuntimeError(
                "Simulated storage failure: cannot write adapter weights"
            )
        return super().put(reference, data)


# ---------------------------------------------------------------------------
# Fake adapter result — satisfies adapter_result_to_bytes interface
# ---------------------------------------------------------------------------


class _FakeAdapterResult:
    """Minimal duck-typed AdapterResult with classifier tensors for serialisation."""

    def __init__(self) -> None:
        self.classifier_weight = torch.tensor([[1.0]])
        self.classifier_bias = torch.tensor([0.0])
        self.epochs_run = 1
        self.cer_history: list[float] = [0.5]


# ---------------------------------------------------------------------------
# Fake general-eval report — always passes the guardrail
# ---------------------------------------------------------------------------


class _PassingGeneralEval:
    """Duck-typed eval report whose metrics satisfy CER and WER guardrails."""

    before_cer: float = 0.5
    before_wer: float = 0.5
    after_cer: float = 0.5
    after_wer: float = 0.5
    delta_cer: float = 0.0
    delta_wer: float = 0.0
    availability: str = "available"


# ---------------------------------------------------------------------------
# Database setup helpers
# ---------------------------------------------------------------------------


def _make_test_db():
    """Return (engine, session_factory) backed by a fresh in-memory SQLite DB."""
    engine = create_db_engine("sqlite://", poolclass=StaticPool)
    create_all(engine)
    factory = create_session_factory(engine)
    return engine, factory


def _make_user(repos: RepositoryFactory, suffix: str = "") -> User:
    unique = uuid.uuid4().hex[:8]
    user = repos.users.add(
        User(
            username=f"prop16_{unique}{suffix}",
            email=f"prop16_{unique}{suffix}@example.com",
            password_hash="x",
        )
    )
    repos.session.flush()
    return user


def _make_running_run(repos: RepositoryFactory, user: User) -> TrainingRun:
    run = repos.training_runs.add(
        TrainingRun(user_id=user.user_id, state=TrainingRunState.RUNNING)
    )
    repos.session.flush()
    return run


def _install_prior_adapter(
    repos: RepositoryFactory, user: User, weights_path: str
) -> Adapter:
    """Insert a pre-existing ACTIVE adapter row for the user."""
    adapter = repos.adapters.add(
        Adapter(
            user_id=user.user_id,
            weights_path=weights_path,
            base_model_version="prior-1.0",
            status=AdapterStatus.ACTIVE,
        )
    )
    repos.session.flush()
    return adapter


def _get_adapter_path(repos: RepositoryFactory, user_id: uuid.UUID) -> Optional[str]:
    """Return the weights_path of the user's adapter, or None if none exists."""
    adapter = repos.adapters.get_for_user(user_id)
    return adapter.weights_path if adapter is not None else None


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

# Whether the user has a pre-existing adapter before the failing run.
_has_prior_adapter_st = st.booleans()

# A non-empty string representing a fake prior weights path.
_prior_weights_path_st = st.text(
    alphabet=st.characters(
        whitelist_categories=("Lu", "Ll", "Nd"),
        whitelist_characters="/_-.",
    ),
    min_size=1,
    max_size=64,
).filter(lambda s: s.strip() and "/" not in s[:1])

# One of three failure modes to exercise.
_failure_mode_st = st.sampled_from(["none_result", "none_eval", "store_exception"])


# ---------------------------------------------------------------------------
# Property 16 — Training failure retains the prior active configuration
# ---------------------------------------------------------------------------

# Feature: handwriting-notes-digitizer, Property 16: Training failure retains the prior active configuration
@given(
    has_prior_adapter=_has_prior_adapter_st,
    prior_weights_path=_prior_weights_path_st,
    failure_mode=_failure_mode_st,
)
@settings(max_examples=25)
def test_property_16_training_failure_retains_prior_adapter(
    has_prior_adapter: bool,
    prior_weights_path: str,
    failure_mode: str,
) -> None:
    """Property 16: Training failure retains the prior active configuration.

    For any combination of:
    - presence/absence of an existing adapter before the failing run, and
    - failure mode (None adapter_result, None general_eval, store exception),

    after ``complete_training_run`` fails the adapter state for the user must
    be **identical** to what it was before the run (same weights_path, or None
    if no adapter existed).

    **Validates: Requirements 5.7**
    """
    engine, factory = _make_test_db()
    try:
        with factory() as session:
            repos = RepositoryFactory(session)
            user = _make_user(repos)

            # ----------------------------------------------------------------
            # 1. Optionally install a prior adapter.
            # ----------------------------------------------------------------
            if has_prior_adapter:
                _install_prior_adapter(repos, user, prior_weights_path)

            # Snapshot the adapter state before the failing run.
            adapter_path_before = _get_adapter_path(repos, user.user_id)

            # ----------------------------------------------------------------
            # 2. Create a RUNNING training run.
            # ----------------------------------------------------------------
            run = _make_running_run(repos, user)

            # Commit setup state so that a subsequent rollback (in the
            # store_exception path) only reverts the failed run's mutations
            # and leaves the prior adapter intact.
            session.commit()

            # ----------------------------------------------------------------
            # 3. Invoke complete_training_run with the selected failure mode.
            # ----------------------------------------------------------------
            adapter_result = _FakeAdapterResult()
            general_eval = _PassingGeneralEval()
            store: ObjectStore = _InMemoryObjectStore()

            if failure_mode == "none_result":
                # Case: training produced no adapter (None adapter_result).
                outcome = complete_training_run(
                    user_id=user.user_id,
                    run_id=run.run_id,
                    adapter_result=None,  # training failed
                    general_eval=general_eval,
                    repos=repos,
                    object_store=store,
                    base_model_version="test-1.0",
                )
                session.commit()

                # The outcome must report failure (not accepted).
                assert not outcome.accepted, (
                    "Expected outcome.accepted=False for None adapter_result "
                    "(training failure), but got accepted=True."
                )
                # The run must be marked FAILED.
                session.expire(run)
                assert run.state == TrainingRunState.FAILED, (
                    f"Expected run.state=FAILED for None adapter_result, "
                    f"got {run.state!r}. Requirement 5.7: failure must be recorded."
                )

            elif failure_mode == "none_eval":
                # Case: general-eval metrics unavailable (None general_eval).
                outcome = complete_training_run(
                    user_id=user.user_id,
                    run_id=run.run_id,
                    adapter_result=adapter_result,
                    general_eval=None,  # evaluation unavailable
                    repos=repos,
                    object_store=store,
                    base_model_version="test-1.0",
                )
                session.commit()

                assert not outcome.accepted, (
                    "Expected outcome.accepted=False for None general_eval "
                    "(evaluation unavailable), but got accepted=True."
                )
                session.expire(run)
                assert run.state == TrainingRunState.FAILED, (
                    f"Expected run.state=FAILED for None general_eval, "
                    f"got {run.state!r}. Requirement 5.7: failure must be recorded."
                )

            else:
                # Case: store_exception — the object store raises during the
                # accepted path (mid-run failure). We expect an exception to
                # propagate; the adapter state must not have changed.
                assert failure_mode == "store_exception", (
                    f"Unexpected failure_mode: {failure_mode!r}"
                )
                store = _FailingObjectStore()

                with pytest.raises(RuntimeError, match="Simulated storage failure"):
                    complete_training_run(
                        user_id=user.user_id,
                        run_id=run.run_id,
                        adapter_result=adapter_result,  # valid result
                        general_eval=general_eval,      # valid eval
                        repos=repos,
                        object_store=store,             # raises on write
                        base_model_version="test-1.0",
                    )
                # The session is in an unknown state after an exception;
                # roll back to see the last committed state.
                session.rollback()

            # ----------------------------------------------------------------
            # 4. Assert: adapter state is identical to what it was before.
            # ----------------------------------------------------------------
            adapter_path_after = _get_adapter_path(repos, user.user_id)

            assert adapter_path_after == adapter_path_before, (
                f"[Property 16] Adapter state changed after a {failure_mode!r} "
                f"training failure. "
                f"Before: {adapter_path_before!r}, After: {adapter_path_after!r}. "
                f"has_prior_adapter={has_prior_adapter}. "
                "Requirement 5.7: the previously active configuration must be "
                "retained when training does not complete successfully."
            )

    finally:
        Base.metadata.drop_all(engine)


# ---------------------------------------------------------------------------
# Property 16 — No prior adapter: user still has none after failure
# ---------------------------------------------------------------------------

# Feature: handwriting-notes-digitizer, Property 16: Training failure retains the prior active configuration
@given(failure_mode=_failure_mode_st)
@settings(max_examples=25)
def test_property_16_no_prior_adapter_remains_absent_after_failure(
    failure_mode: str,
) -> None:
    """A user who had no adapter before a failing run still has none after.

    This is the specific case of Property 16 where there is no prior
    configuration to "retain": the invariant is that the absent adapter
    remains absent (no orphaned adapter is accidentally created on failure).

    **Validates: Requirements 5.7**
    """
    engine, factory = _make_test_db()
    try:
        with factory() as session:
            repos = RepositoryFactory(session)
            user = _make_user(repos, suffix="_noprior")

            # Confirm no adapter exists before the run.
            assert repos.adapters.get_for_user(user.user_id) is None, (
                "Test setup error: user should have no adapter before the run."
            )

            run = _make_running_run(repos, user)
            # Commit setup so a rollback in store_exception path only reverts
            # the failed run's mutations.
            session.commit()

            store: ObjectStore = _InMemoryObjectStore()
            adapter_result = _FakeAdapterResult()
            general_eval = _PassingGeneralEval()

            if failure_mode == "none_result":
                complete_training_run(
                    user_id=user.user_id,
                    run_id=run.run_id,
                    adapter_result=None,
                    general_eval=general_eval,
                    repos=repos,
                    object_store=store,
                    base_model_version="test-1.0",
                )
                session.commit()

            elif failure_mode == "none_eval":
                complete_training_run(
                    user_id=user.user_id,
                    run_id=run.run_id,
                    adapter_result=adapter_result,
                    general_eval=None,
                    repos=repos,
                    object_store=store,
                    base_model_version="test-1.0",
                )
                session.commit()

            else:
                # store_exception
                store = _FailingObjectStore()
                with pytest.raises(RuntimeError):
                    complete_training_run(
                        user_id=user.user_id,
                        run_id=run.run_id,
                        adapter_result=adapter_result,
                        general_eval=general_eval,
                        repos=repos,
                        object_store=store,
                        base_model_version="test-1.0",
                    )
                session.rollback()

            # After any failure mode: adapter must still be absent.
            assert repos.adapters.get_for_user(user.user_id) is None, (
                f"[Property 16] An adapter was created during a {failure_mode!r} "
                "training failure, but no adapter should exist for a user who had "
                "none before the run. Requirement 5.7: prior configuration retained."
            )

    finally:
        Base.metadata.drop_all(engine)
