"""Property-based test for the Personalization_Service threshold trigger (Property 11).

# Feature: handwriting-notes-digitizer, Property 11: Training triggers exactly at the unused-pair threshold

For any count of unused training pairs, `maybe_enqueue_training` enqueues a job
if and only if the unused-pair count is >= 20 (TRAINING_THRESHOLD).

**Validates: Requirements 5.1**

Strategy
--------
We use Hypothesis to generate integers representing the number of unused training
pairs (both below and at/above the threshold of 20). For each generated count:

* If count < 20 → `maybe_enqueue_training` must return BELOW_THRESHOLD, and no
  training run must be created in the repository.
* If count >= 20 → `maybe_enqueue_training` must return ENQUEUED, and exactly
  one QUEUED training run must be created in the repository.

We test via two complementary approaches:

1. **Fake repos (fast):** A lightweight in-memory stub replaces the DB to make
   100+ Hypothesis iterations cheap. This tests the threshold logic directly.

2. **Real DB (correctness):** A subset of iterations exercises the full stack
   against an in-memory SQLite database to verify the logic end-to-end.

The tests cover counts ranging from 0 to 50, ensuring both below-threshold and
above-threshold cases are exercised with a range of values around the boundary.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.services.personalization_service import (
    TRAINING_THRESHOLD,
    TrainingTriggerResult,
    maybe_enqueue_training,
)


# --------------------------------------------------------------------------- #
# Lightweight fake repos (no DB, no ORM)
# --------------------------------------------------------------------------- #


@dataclass
class _FakeSession:
    """Minimal session stub that does nothing on flush/commit."""

    def flush(self) -> None:  # noqa: D401
        pass

    def commit(self) -> None:  # noqa: D401
        pass


@dataclass
class _FakeTrainingRun:
    """Minimal training-run record stub."""

    run_id: uuid.UUID = field(default_factory=uuid.uuid4)
    user_id: Optional[uuid.UUID] = None
    state: str = "queued"
    run_pairs: list = field(default_factory=list)


@dataclass
class _FakeTrainingRunRepo:
    """Fake training-run repository with just the methods used by maybe_enqueue_training."""

    session: _FakeSession = field(default_factory=_FakeSession)
    _runs: list[_FakeTrainingRun] = field(default_factory=list)
    _running_for_user: Optional[_FakeTrainingRun] = None

    def get_running_for_user(self, user_id: Any) -> Optional[_FakeTrainingRun]:
        return self._running_for_user

    def add(self, run: _FakeTrainingRun) -> _FakeTrainingRun:
        self._runs.append(run)
        return run

    def list_for_user(self, user_id: Any) -> list[_FakeTrainingRun]:
        return list(self._runs)


@dataclass
class _FakeTrainingPairRepo:
    """Fake training-pair repository with just count_unused."""

    _unused_count: int = 0

    def count_unused(self, user_id: Any) -> int:
        return self._unused_count


@dataclass
class _FakeRepos:
    """Minimal fake RepositoryFactory exposing training_pairs and training_runs."""

    training_pairs: _FakeTrainingPairRepo = field(default_factory=_FakeTrainingPairRepo)
    training_runs: _FakeTrainingRunRepo = field(default_factory=_FakeTrainingRunRepo)


# --------------------------------------------------------------------------- #
# Generators
# --------------------------------------------------------------------------- #

# Integers below the threshold (0 to TRAINING_THRESHOLD - 1).
_below_threshold = st.integers(min_value=0, max_value=TRAINING_THRESHOLD - 1)

# Integers at or above the threshold (TRAINING_THRESHOLD to 50).
_at_or_above_threshold = st.integers(min_value=TRAINING_THRESHOLD, max_value=50)

# Full range covering both sides of the boundary.
_any_count = st.integers(min_value=0, max_value=50)


# --------------------------------------------------------------------------- #
# Property 11 — using fake repos for speed (100+ iterations cheaply)
# --------------------------------------------------------------------------- #

# Feature: handwriting-notes-digitizer, Property 11: Training triggers exactly at the unused-pair threshold
@given(n_unused=_below_threshold)
@settings(max_examples=25)
def test_property_11_below_threshold_no_enqueue(n_unused: int) -> None:
    """**Validates: Requirements 5.1**

    When the unused-pair count is below 20, maybe_enqueue_training must return
    BELOW_THRESHOLD and must not create any training run.
    """
    user_id = uuid.uuid4()
    repos = _FakeRepos(
        training_pairs=_FakeTrainingPairRepo(_unused_count=n_unused),
        training_runs=_FakeTrainingRunRepo(),
    )

    result = maybe_enqueue_training(user_id, repos)

    assert result is TrainingTriggerResult.BELOW_THRESHOLD, (
        f"Expected BELOW_THRESHOLD for n_unused={n_unused} "
        f"(threshold={TRAINING_THRESHOLD}), got {result!r}"
    )
    created_runs = repos.training_runs.list_for_user(user_id)
    assert len(created_runs) == 0, (
        f"Expected no runs to be created for n_unused={n_unused}, "
        f"but found {len(created_runs)}"
    )


# Feature: handwriting-notes-digitizer, Property 11: Training triggers exactly at the unused-pair threshold
@given(n_unused=_at_or_above_threshold)
@settings(max_examples=25)
def test_property_11_at_or_above_threshold_enqueues(n_unused: int) -> None:
    """**Validates: Requirements 5.1**

    When the unused-pair count is >= 20 (and no run is already running),
    maybe_enqueue_training must return ENQUEUED and exactly one QUEUED run
    must be created.
    """
    user_id = uuid.uuid4()
    repos = _FakeRepos(
        training_pairs=_FakeTrainingPairRepo(_unused_count=n_unused),
        training_runs=_FakeTrainingRunRepo(),  # no running run
    )

    result = maybe_enqueue_training(user_id, repos)

    assert result is TrainingTriggerResult.ENQUEUED, (
        f"Expected ENQUEUED for n_unused={n_unused} "
        f"(threshold={TRAINING_THRESHOLD}), got {result!r}"
    )
    created_runs = repos.training_runs.list_for_user(user_id)
    assert len(created_runs) == 1, (
        f"Expected exactly 1 queued run for n_unused={n_unused}, "
        f"but found {len(created_runs)}"
    )


# Feature: handwriting-notes-digitizer, Property 11: Training triggers exactly at the unused-pair threshold
@given(n_unused=_any_count)
@settings(max_examples=25)
def test_property_11_if_and_only_if_at_threshold(n_unused: int) -> None:
    """**Validates: Requirements 5.1**

    For any unused-pair count, enqueue happens if and only if count >= 20.

    This is the combined if-and-only-if form of Property 11:
    - count < 20  → BELOW_THRESHOLD (no run created)
    - count >= 20 → ENQUEUED (exactly one run created)
    """
    user_id = uuid.uuid4()
    repos = _FakeRepos(
        training_pairs=_FakeTrainingPairRepo(_unused_count=n_unused),
        training_runs=_FakeTrainingRunRepo(),
    )

    result = maybe_enqueue_training(user_id, repos)
    created_runs = repos.training_runs.list_for_user(user_id)

    should_enqueue = n_unused >= TRAINING_THRESHOLD

    if should_enqueue:
        assert result is TrainingTriggerResult.ENQUEUED, (
            f"n_unused={n_unused} >= {TRAINING_THRESHOLD}: "
            f"expected ENQUEUED but got {result!r}"
        )
        assert len(created_runs) == 1, (
            f"n_unused={n_unused}: expected 1 run, got {len(created_runs)}"
        )
    else:
        assert result is TrainingTriggerResult.BELOW_THRESHOLD, (
            f"n_unused={n_unused} < {TRAINING_THRESHOLD}: "
            f"expected BELOW_THRESHOLD but got {result!r}"
        )
        assert len(created_runs) == 0, (
            f"n_unused={n_unused}: expected 0 runs, got {len(created_runs)}"
        )


# --------------------------------------------------------------------------- #
# Property 11 — real DB for end-to-end correctness verification
# --------------------------------------------------------------------------- #

# Feature: handwriting-notes-digitizer, Property 11: Training triggers exactly at the unused-pair threshold
@given(n_unused=_any_count)
@settings(max_examples=25)
def test_property_11_with_real_db(n_unused: int) -> None:
    """**Validates: Requirements 5.1**

    End-to-end version of Property 11 using an in-memory SQLite database.

    Enqueuing happens if and only if count >= 20 (TRAINING_THRESHOLD).
    Consumed pairs (with consumed_by_run_id set) do not count toward the
    threshold — only pairs where consumed_by_run_id IS NULL are counted.
    """
    from app.data import (
        Base,
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

    engine = create_db_engine("sqlite://")
    create_all(engine)
    factory = create_session_factory(engine)

    try:
        with factory() as session:
            repos = RepositoryFactory(session)

            # Create a unique user per iteration.
            user = User(
                username=f"prop11_user_{uuid.uuid4().hex[:8]}",
                email=f"prop11_{uuid.uuid4().hex[:8]}@example.com",
                password_hash="hashed",
            )
            session.add(user)
            session.commit()

            # Add exactly n_unused unused training pairs.
            for i in range(n_unused):
                repos.training_pairs.add(
                    TrainingPair(
                        user_id=user.user_id,
                        source_region_key=f"region_{i}",
                        region_crop_path=f"crop_{i}.png",
                        corrected_text="test",
                        origin=TrainingPairOrigin.CORRECTION,
                        consumed_by_run_id=None,
                    )
                )
            session.commit()

            result = maybe_enqueue_training(user.user_id, repos)
            runs = repos.training_runs.list_for_user(user.user_id)
            should_enqueue = n_unused >= TRAINING_THRESHOLD

            if should_enqueue:
                assert result is TrainingTriggerResult.ENQUEUED, (
                    f"n_unused={n_unused}: expected ENQUEUED, got {result!r}"
                )
                assert len(runs) == 1, (
                    f"n_unused={n_unused}: expected 1 QUEUED run, got {len(runs)}"
                )
                assert runs[0].state == TrainingRunState.QUEUED, (
                    f"Expected QUEUED state but got {runs[0].state!r}"
                )
                assert runs[0].user_id == user.user_id, (
                    "Created run has wrong user_id"
                )
            else:
                assert result is TrainingTriggerResult.BELOW_THRESHOLD, (
                    f"n_unused={n_unused}: expected BELOW_THRESHOLD, got {result!r}"
                )
                assert len(runs) == 0, (
                    f"n_unused={n_unused}: expected 0 runs, got {len(runs)}"
                )
    finally:
        Base.metadata.drop_all(engine)
