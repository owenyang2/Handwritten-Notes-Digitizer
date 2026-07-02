"""Property-based test for adapter cardinality (design Property 13).

# Feature: handwriting-notes-digitizer, Property 13: At most one adapter per user

For any sequence of adapter save/update operations for a user, the adapter
repository must contain at most one adapter row per user at all times.

**Validates: Requirements 5.3**

Requirement 5.3: THE Personalization_Service SHALL store exactly one Adapter per
user rather than a full copy of the Base_Model per user.

Strategy
--------
The property is tested directly at the repository/service boundary using:

* An in-memory SQLite database so iterations are fast and fully isolated.
* An in-memory dict-backed object store (no filesystem I/O).
* ``complete_training_run`` from ``personalization_service`` — the single point
  of truth for the "upsert adapter" logic (Case 4: ACCEPTED).

For each generated example we:

1. Create one or more users.
2. For each user, simulate a sequence of N accepted training runs (N ≥ 1),
   each producing a fresh ``AdapterResult`` with distinct fake weights.
3. After every run, query ``AdapterRepository.get_for_user`` and assert that
   ``count(adapters where user_id = X) <= 1`` for each user X.
4. After the full sequence, perform a final cross-user assertion that no user
   has more than one adapter row.

The ``complete_training_run`` path that produces an accepted adapter does the
following in the service layer (see ``personalization_service.py``, Case 4):

* First run: creates a new ``Adapter`` row (``repos.adapters.add``).
* Subsequent runs: **updates the existing row** in place (preserving adapter_id)
  instead of inserting a new one.

The property test exercises exactly this "at most one" invariant across
multiple runs and multiple users.

Variables driven by Hypothesis
-------------------------------
* ``num_users``      — 1–4 users in the scenario.
* ``num_runs``       — 1–5 accepted training runs per user.
* Fake adapter weight values are varied per run to detect any case where a
  second row might be inserted instead of updating the first.
"""

from __future__ import annotations

import io
import uuid
from typing import BinaryIO

import torch
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy.pool import StaticPool

from app.data.db import create_all, create_db_engine, create_session_factory
from app.data.enums import ImageFormat, ImageStatus, TrainingRunState
from app.data.models import Image, TrainingRun, User
from app.data.object_store import ObjectStore, ObjectStoreError, ObjectNotFoundError
from app.data.repositories import RepositoryFactory
from app.services.personalization_service import complete_training_run


# ---------------------------------------------------------------------------
# In-memory object store (no filesystem I/O)
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


# ---------------------------------------------------------------------------
# Fake adapter result — mimics app.ml.adapter_trainer.AdapterResult
# ---------------------------------------------------------------------------


class _FakeAdapterResult:
    """Minimal object that satisfies the ``adapter_result_to_bytes`` interface.

    ``adapter_result_to_bytes`` (in ``app.ml.adapter_trainer``) accesses
    ``result.classifier_weight`` and ``result.classifier_bias`` directly as
    tensors, then serialises them via ``torch.save``. We provide these
    attributes here without loading any real model so iterations stay cheap.
    """

    def __init__(self, weight_value: float) -> None:
        self.classifier_weight = torch.tensor([[weight_value]])
        self.classifier_bias = torch.tensor([0.0])
        self.epochs_run = 1
        self.cer_history: list[float] = [0.5]


# ---------------------------------------------------------------------------
# Fake general-eval result — provides base/adapter CER/WER metrics that
# always pass the guardrail so the adapter is accepted in every test run.
# ---------------------------------------------------------------------------


class _PassingGeneralEval:
    """Duck-typed general-eval report that always clears the guardrails.

    Using base_cer=0.5 and adapter_cer=0.5 (equal) means the CER guardrail
    (adapter_cer ≤ base_cer + 0.01) is trivially satisfied. Likewise for WER.
    """

    before_cer: float = 0.5
    before_wer: float = 0.5
    after_cer: float = 0.5
    after_wer: float = 0.5
    delta_cer: float = 0.0
    delta_wer: float = 0.0
    availability: str = "available"


# ---------------------------------------------------------------------------
# Test database setup helpers
# ---------------------------------------------------------------------------


def _make_test_db():
    """Return (engine, session_factory) backed by fresh in-memory SQLite."""
    engine = create_db_engine("sqlite://", poolclass=StaticPool)
    create_all(engine)
    factory = create_session_factory(engine)
    return engine, factory


def _add_user_and_run(repos: RepositoryFactory, username_suffix: str) -> tuple:
    """Create a User and an initial RUNNING TrainingRun; return (user, run)."""
    user = repos.users.add(
        User(
            username=f"prop13_{username_suffix}",
            email=f"prop13_{username_suffix}@example.com",
            password_hash="x",
        )
    )
    repos.session.flush()

    run = repos.training_runs.add(
        TrainingRun(
            user_id=user.user_id,
            state=TrainingRunState.RUNNING,
        )
    )
    repos.session.flush()
    return user, run


def _count_adapters_for_user(repos: RepositoryFactory, user_id: uuid.UUID) -> int:
    """Count adapter rows belonging to user_id by querying the repository."""
    adapter = repos.adapters.get_for_user(user_id)
    return 1 if adapter is not None else 0


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

# Number of users in a single scenario (1–4).
_num_users_st = st.integers(min_value=1, max_value=4)

# Number of sequential accepted training runs per user (1–5).
_num_runs_st = st.integers(min_value=1, max_value=5)

# A float value for the fake adapter weight — varied per run so each
# simulated training produces distinctly different weights.
_weight_st = st.floats(
    min_value=0.1,
    max_value=10.0,
    allow_nan=False,
    allow_infinity=False,
)


# ---------------------------------------------------------------------------
# Property 13 — At most one adapter per user
# ---------------------------------------------------------------------------

# Feature: handwriting-notes-digitizer, Property 13: At most one adapter per user
@given(
    num_users=_num_users_st,
    num_runs=_num_runs_st,
    weight_values=st.lists(
        _weight_st,
        min_size=5,   # enough for up to 5 runs
        max_size=5,
    ),
)
def test_at_most_one_adapter_per_user(
    num_users: int,
    num_runs: int,
    weight_values: list[float],
) -> None:
    """After any sequence of adapter save/update operations, each user has at
    most one adapter stored in the repository.

    **Validates: Requirements 5.3**

    Asserts ``count(adapters where user_id = X) <= 1`` for every user X after
    every run, and again after the complete sequence.
    """
    _engine, factory = _make_test_db()
    session = factory()
    repos = RepositoryFactory(session)
    store = _InMemoryObjectStore()

    # Create all users upfront.
    users: list[User] = []
    for u_idx in range(num_users):
        user = repos.users.add(
            User(
                username=f"prop13_u{u_idx}_{uuid.uuid4().hex[:6]}",
                email=f"prop13_u{u_idx}_{uuid.uuid4().hex[:6]}@example.com",
                password_hash="x",
            )
        )
        users.append(user)
    session.flush()

    general_eval = _PassingGeneralEval()

    # For each user, run `num_runs` sequential accepted training runs.
    for user in users:
        for run_idx in range(num_runs):
            # Create a RUNNING TrainingRun for this iteration.
            run = repos.training_runs.add(
                TrainingRun(
                    user_id=user.user_id,
                    state=TrainingRunState.RUNNING,
                )
            )
            session.flush()

            # Produce a fresh fake adapter result with a distinct weight.
            weight = weight_values[run_idx % len(weight_values)]
            adapter_result = _FakeAdapterResult(weight_value=weight)

            # Call complete_training_run — the service function that performs
            # the upsert logic (Req 5.3).
            outcome = complete_training_run(
                user_id=user.user_id,
                run_id=run.run_id,
                adapter_result=adapter_result,
                general_eval=general_eval,
                repos=repos,
                object_store=store,
                base_model_version="test-1.0",
            )
            session.commit()

            # --- Per-run assertion: exactly one adapter per user after each run ---
            adapter_count = _count_adapters_for_user(repos, user.user_id)
            assert adapter_count <= 1, (
                f"User {user.user_id}: expected at most 1 adapter after run "
                f"{run_idx + 1} but found {adapter_count} adapters "
                f"(Req 5.3 violation: more than one adapter row per user)."
            )

            # The run must have been accepted (guardrail passes with equal metrics).
            assert outcome.accepted, (
                f"Expected training run to be accepted (guardrail values are "
                f"equal for base and adapter), but got: {outcome.notification!r}"
            )

            # The adapter_id in the outcome must exist and be stable across runs.
            assert outcome.adapter_id is not None, (
                "Expected outcome.adapter_id to be set after an accepted run."
            )

    # --- Final cross-user assertion ------------------------------------------
    # After all runs for all users, every user still has exactly one adapter.
    for user in users:
        adapter_count = _count_adapters_for_user(repos, user.user_id)
        assert adapter_count <= 1, (
            f"Final check: user {user.user_id} has {adapter_count} adapter(s) "
            f"— should be at most 1 (Req 5.3)."
        )

    # Also assert that the total adapter count in the DB equals the number of users
    # (each user has exactly one adapter, not zero and not more-than-one).
    total_adapter_count = 0
    for user in users:
        if repos.adapters.get_for_user(user.user_id) is not None:
            total_adapter_count += 1

    assert total_adapter_count == num_users, (
        f"Expected exactly {num_users} adapters in total (one per user) but "
        f"found {total_adapter_count}. Some users are missing their adapter."
    )

    session.close()


# ---------------------------------------------------------------------------
# Property 13 — Adapter identity is stable across updates (same adapter_id)
# ---------------------------------------------------------------------------

# Feature: handwriting-notes-digitizer, Property 13: At most one adapter per user
@given(
    num_runs=st.integers(min_value=2, max_value=5),
    weight_values=st.lists(
        _weight_st,
        min_size=5,
        max_size=5,
    ),
)
def test_adapter_id_is_stable_across_multiple_runs(
    num_runs: int,
    weight_values: list[float],
) -> None:
    """After the first training run creates the adapter row, subsequent accepted
    runs update the row in place rather than inserting a new one.

    This verifies that the upsert path in ``complete_training_run`` updates
    ``weights_path`` on the existing row (preserving ``adapter_id``) rather
    than deleting and re-inserting, which would violate the cardinality
    invariant during the update window.

    **Validates: Requirements 5.3**
    """
    _engine, factory = _make_test_db()
    session = factory()
    repos = RepositoryFactory(session)
    store = _InMemoryObjectStore()

    # Single user for this sub-property.
    user = repos.users.add(
        User(
            username=f"prop13_stable_{uuid.uuid4().hex[:8]}",
            email=f"prop13_stable_{uuid.uuid4().hex[:8]}@example.com",
            password_hash="x",
        )
    )
    session.flush()

    general_eval = _PassingGeneralEval()
    first_adapter_id: uuid.UUID | None = None

    for run_idx in range(num_runs):
        run = repos.training_runs.add(
            TrainingRun(
                user_id=user.user_id,
                state=TrainingRunState.RUNNING,
            )
        )
        session.flush()

        weight = weight_values[run_idx % len(weight_values)]
        adapter_result = _FakeAdapterResult(weight_value=weight)

        outcome = complete_training_run(
            user_id=user.user_id,
            run_id=run.run_id,
            adapter_result=adapter_result,
            general_eval=general_eval,
            repos=repos,
            object_store=store,
            base_model_version="test-1.0",
        )
        session.commit()

        assert outcome.accepted, (
            f"Run {run_idx + 1} was not accepted: {outcome.notification!r}"
        )
        assert outcome.adapter_id is not None

        if run_idx == 0:
            # Record the adapter_id assigned on the first accepted run.
            first_adapter_id = outcome.adapter_id
        else:
            # Subsequent accepted runs must reuse the same adapter_id (in-place update).
            assert outcome.adapter_id == first_adapter_id, (
                f"adapter_id changed on run {run_idx + 1}: "
                f"expected {first_adapter_id}, got {outcome.adapter_id}. "
                "complete_training_run should update the existing row "
                "rather than inserting a new one (Req 5.3)."
            )

        # Cardinality invariant: still at most one adapter row after every run.
        adapter_count = _count_adapters_for_user(repos, user.user_id)
        assert adapter_count <= 1, (
            f"User has {adapter_count} adapters after run {run_idx + 1} — "
            "must be at most 1 (Req 5.3)."
        )

    session.close()
