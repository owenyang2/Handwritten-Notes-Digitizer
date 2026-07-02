"""Tests for personalization_service – threshold trigger (task 14.1, Req 5.1).

Covers:
* Unit tests for ``maybe_enqueue_training`` covering BELOW_THRESHOLD,
  ALREADY_RUNNING, and ENQUEUED outcomes.
* Property 11: Training triggers exactly at the unused-pair threshold
  (≥ 20 unused pairs → ENQUEUED; < 20 → BELOW_THRESHOLD).
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy.orm import Session

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
from app.services.personalization_service import (
    TRAINING_THRESHOLD,
    TrainingTriggerResult,
    maybe_enqueue_training,
)


# --------------------------------------------------------------------------- #
# Shared fixtures (mirror the pattern used in test_data_access_scoping.py)
# --------------------------------------------------------------------------- #


@pytest.fixture()
def session() -> Session:
    engine = create_db_engine("sqlite://")
    create_all(engine)
    factory = create_session_factory(engine)
    with factory() as s:
        yield s
    Base.metadata.drop_all(engine)


@pytest.fixture()
def repos(session: Session) -> RepositoryFactory:
    return RepositoryFactory(session)


def _make_user(session: Session, username: str = "alice") -> User:
    user = User(
        username=username,
        email=f"{username}@example.com",
        password_hash="hashed",
    )
    session.add(user)
    session.commit()
    return user


def _make_unused_pair(repos: RepositoryFactory, user: User, region_key: str) -> TrainingPair:
    """Create a training pair with no consumed_by_run_id (i.e. unused)."""
    pair = repos.training_pairs.add(
        TrainingPair(
            user_id=user.user_id,
            source_region_key=region_key,
            region_crop_path=f"crop_{region_key}.png",
            corrected_text="hello",
            origin=TrainingPairOrigin.CORRECTION,
            consumed_by_run_id=None,
        )
    )
    repos.session.commit()
    return pair


def _make_consumed_pair(
    repos: RepositoryFactory, user: User, region_key: str, run_id
) -> TrainingPair:
    """Create a training pair already consumed by a completed training run."""
    pair = repos.training_pairs.add(
        TrainingPair(
            user_id=user.user_id,
            source_region_key=region_key,
            region_crop_path=f"crop_{region_key}.png",
            corrected_text="hello",
            origin=TrainingPairOrigin.CORRECTION,
            consumed_by_run_id=run_id,
        )
    )
    repos.session.commit()
    return pair


def _make_running_run(repos: RepositoryFactory, user: User) -> TrainingRun:
    run = repos.training_runs.add(
        TrainingRun(user_id=user.user_id, state=TrainingRunState.RUNNING)
    )
    repos.session.commit()
    return run


# --------------------------------------------------------------------------- #
# Unit tests
# --------------------------------------------------------------------------- #


def test_below_threshold_with_zero_pairs(session, repos) -> None:
    """No pairs at all → BELOW_THRESHOLD."""
    user = _make_user(session)
    result = maybe_enqueue_training(user.user_id, repos)
    assert result is TrainingTriggerResult.BELOW_THRESHOLD


def test_below_threshold_with_19_pairs(session, repos) -> None:
    """19 unused pairs (one below threshold) → BELOW_THRESHOLD."""
    user = _make_user(session)
    for i in range(TRAINING_THRESHOLD - 1):
        _make_unused_pair(repos, user, f"r{i}")

    result = maybe_enqueue_training(user.user_id, repos)
    assert result is TrainingTriggerResult.BELOW_THRESHOLD


def test_enqueued_at_exactly_threshold(session, repos) -> None:
    """Exactly 20 unused pairs → ENQUEUED and a QUEUED run is created."""
    user = _make_user(session)
    for i in range(TRAINING_THRESHOLD):
        _make_unused_pair(repos, user, f"r{i}")

    result = maybe_enqueue_training(user.user_id, repos)
    assert result is TrainingTriggerResult.ENQUEUED

    # A QUEUED run must now exist for the user.
    runs = repos.training_runs.list_for_user(user.user_id)
    assert len(runs) == 1
    assert runs[0].state == TrainingRunState.QUEUED
    assert runs[0].user_id == user.user_id


def test_enqueued_with_more_than_threshold(session, repos) -> None:
    """30 unused pairs → ENQUEUED."""
    user = _make_user(session)
    for i in range(30):
        _make_unused_pair(repos, user, f"r{i}")

    result = maybe_enqueue_training(user.user_id, repos)
    assert result is TrainingTriggerResult.ENQUEUED


def test_already_running_blocks_new_enqueue(session, repos) -> None:
    """≥20 unused pairs but a RUNNING run exists → ALREADY_RUNNING, no new run."""
    user = _make_user(session)
    for i in range(TRAINING_THRESHOLD):
        _make_unused_pair(repos, user, f"r{i}")
    _make_running_run(repos, user)

    result = maybe_enqueue_training(user.user_id, repos)
    assert result is TrainingTriggerResult.ALREADY_RUNNING

    # The only run present is the original RUNNING one; no new QUEUED run.
    runs = repos.training_runs.list_for_user(user.user_id)
    assert len(runs) == 1
    assert runs[0].state == TrainingRunState.RUNNING


def test_consumed_pairs_not_counted_toward_threshold(session, repos) -> None:
    """Pairs with consumed_by_run_id do not count; only truly unused pairs matter."""
    user = _make_user(session)
    # Create a completed run that consumed 25 pairs.
    completed_run = repos.training_runs.add(
        TrainingRun(user_id=user.user_id, state=TrainingRunState.ACCEPTED)
    )
    repos.session.commit()
    for i in range(25):
        _make_consumed_pair(repos, user, f"rc{i}", completed_run.run_id)

    # 19 unused pairs → still below threshold.
    for i in range(TRAINING_THRESHOLD - 1):
        _make_unused_pair(repos, user, f"ru{i}")

    result = maybe_enqueue_training(user.user_id, repos)
    assert result is TrainingTriggerResult.BELOW_THRESHOLD


def test_enqueued_run_has_correct_user_id(session, repos) -> None:
    """The newly-created QUEUED run is associated with the correct user."""
    user = _make_user(session)
    for i in range(TRAINING_THRESHOLD):
        _make_unused_pair(repos, user, f"r{i}")

    maybe_enqueue_training(user.user_id, repos)

    runs = repos.training_runs.list_for_user(user.user_id)
    assert runs[0].user_id == user.user_id


def test_completed_run_does_not_block_enqueue(session, repos) -> None:
    """A ACCEPTED or FAILED run must not block a new enqueue (only RUNNING blocks)."""
    user = _make_user(session)
    # A previously accepted run (completed, not running).
    repos.training_runs.add(
        TrainingRun(user_id=user.user_id, state=TrainingRunState.ACCEPTED)
    )
    repos.session.commit()

    for i in range(TRAINING_THRESHOLD):
        _make_unused_pair(repos, user, f"r{i}")

    result = maybe_enqueue_training(user.user_id, repos)
    assert result is TrainingTriggerResult.ENQUEUED


# --------------------------------------------------------------------------- #
# Property 11: Training triggers exactly at the unused-pair threshold
# --------------------------------------------------------------------------- #

# Feature: handwriting-notes-digitizer, Property 11: Training triggers exactly at the unused-pair threshold
@given(n_unused=st.integers(min_value=0, max_value=50))
@settings(max_examples=25)
def test_property_11_threshold_trigger(n_unused: int) -> None:
    """**Validates: Requirements 5.1**

    For any count of unused pairs n_unused:
    - If n_unused < 20: result is BELOW_THRESHOLD and no run is created.
    - If n_unused >= 20: result is ENQUEUED and exactly one QUEUED run is created.
    """
    engine = create_db_engine("sqlite://")
    create_all(engine)
    factory = create_session_factory(engine)

    try:
        with factory() as session:
            repos = RepositoryFactory(session)
            user = User(
                username="prop11_user",
                email="prop11@example.com",
                password_hash="hashed",
            )
            session.add(user)
            session.commit()

            for i in range(n_unused):
                repos.training_pairs.add(
                    TrainingPair(
                        user_id=user.user_id,
                        source_region_key=f"r{i}",
                        region_crop_path=f"crop{i}.png",
                        corrected_text="text",
                        origin=TrainingPairOrigin.CORRECTION,
                        consumed_by_run_id=None,
                    )
                )
            session.commit()

            result = maybe_enqueue_training(user.user_id, repos)

            runs = repos.training_runs.list_for_user(user.user_id)

            if n_unused < TRAINING_THRESHOLD:
                assert result is TrainingTriggerResult.BELOW_THRESHOLD, (
                    f"Expected BELOW_THRESHOLD for n_unused={n_unused}, got {result}"
                )
                assert len(runs) == 0, (
                    f"Expected no runs for n_unused={n_unused}, got {len(runs)}"
                )
            else:
                assert result is TrainingTriggerResult.ENQUEUED, (
                    f"Expected ENQUEUED for n_unused={n_unused}, got {result}"
                )
                assert len(runs) == 1, (
                    f"Expected exactly 1 run for n_unused={n_unused}, got {len(runs)}"
                )
                assert runs[0].state == TrainingRunState.QUEUED
    finally:
        Base.metadata.drop_all(engine)
