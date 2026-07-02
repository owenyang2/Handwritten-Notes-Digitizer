"""Property-based test for per-user training-set selection (design Property 26).

# Feature: handwriting-notes-digitizer, Property 26: A user's training set is exactly that user's pairs across all origins

This test exercises the ``TrainingPairRepository.list_for_user`` accessor — the
function that the Personalization_Service uses to gather a user's training
pairs before a training run — against the design's **Property 26**:

    For any multi-user scenario with mixed correction and calibration pairs,
    calling ``list_for_user(user_id)`` for user X returns *exactly* user X's
    pairs (both origins) and *none* of any other user's pairs.

**Validates: Requirements 9.6, 10.2**

Requirement 9.6: WHEN a user submits at least one valid handwriting image during
the calibration flow, THE Personalization_Service SHALL train Adapters using
calibration Training_Pairs and correction Training_Pairs through the same
personalization pipeline.

Requirement 10.2: THE Personalization_Service SHALL train each user's Adapter
using only Training_Pairs associated with that user's account, and SHALL NOT
use Training_Pairs associated with any other account.

Strategy
--------
For each generated example we:

1. Draw 1–5 users; for each user draw between 0–6 training pairs whose origins
   are independently chosen from CORRECTION and CALIBRATION.
2. Persist all users and all pairs to a fresh in-memory SQLite database.
3. For every user X, call ``repos.training_pairs.list_for_user(user_X_id)``
   and assert the three correctness dimensions:

   a. **Completeness** — every pair owned by user X appears in the result
      (no pair belonging to X is silently excluded, regardless of origin).
   b. **Exclusivity** — no pair owned by any *other* user appears in the result
      (cross-user contamination from either correction or calibration origin is
      detected).
   c. **Exactness** — the returned set's pair_id set equals user X's pair_id
      set (a.k.a. no more, no less — combining a and b into one crisp check).

The test runs ≥ 100 iterations (enforced by the project-wide Hypothesis
``default`` profile in ``tests/conftest.py``).
"""

from __future__ import annotations

import uuid
from typing import Sequence

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy.pool import StaticPool

from app.data import (
    Base,
    RepositoryFactory,
    TrainingPair,
    TrainingPairOrigin,
    User,
    create_all,
    create_db_engine,
    create_session_factory,
)


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

# One "user spec": a list of origins for the pairs that user owns.
# Each user gets 0–6 training pairs; each pair's origin is chosen independently.
_origin_st: st.SearchStrategy[str] = st.sampled_from(
    [TrainingPairOrigin.CORRECTION, TrainingPairOrigin.CALIBRATION]
)

_user_pair_origins_st: st.SearchStrategy[list] = st.lists(
    _origin_st, min_size=0, max_size=6
)

# A scenario is a list of 1–5 such user-pair-origin lists.
_scenario_st: st.SearchStrategy[list[list]] = st.lists(
    _user_pair_origins_st, min_size=1, max_size=5
)


# ---------------------------------------------------------------------------
# DB setup helpers
# ---------------------------------------------------------------------------


def _make_test_db():
    """Return (engine, session_factory) backed by a fresh in-memory SQLite DB."""
    engine = create_db_engine("sqlite://", poolclass=StaticPool)
    create_all(engine)
    factory = create_session_factory(engine)
    return engine, factory


# ---------------------------------------------------------------------------
# Property 26
# ---------------------------------------------------------------------------

# Feature: handwriting-notes-digitizer, Property 26: A user's training set is exactly that user's pairs across all origins
@given(user_pair_origins_by_user=_scenario_st)
def test_per_user_training_set_is_exactly_that_users_pairs_across_all_origins(
    user_pair_origins_by_user: list[list],
) -> None:
    """Validates: Requirements 9.6, 10.2.

    For any collection of users each holding an arbitrary mix of CORRECTION and
    CALIBRATION training pairs, ``list_for_user(user_id)`` returns *exactly*
    the calling user's pairs — all of them (completeness) and only them
    (exclusivity) — regardless of origin.

    Correctness dimensions verified for every user X:

    1. **Completeness**: every pair_id assigned to X is present in the result.
    2. **Exclusivity**: no pair_id assigned to another user appears in the result.
    3. **Exactness**: the returned pair_id set equals X's pair_id set exactly
       (i.e. |returned ∩ X_pairs| == |X_pairs| == |returned|).
    """
    engine, factory = _make_test_db()

    # --- Persist all users and their pairs ---------------------------------
    # user_ids_with_pair_ids: list of (user_id, set_of_pair_ids) per user.
    user_records: list[tuple[uuid.UUID, set[uuid.UUID]]] = []

    with factory() as session:
        repos = RepositoryFactory(session)

        for u_idx, pair_origins in enumerate(user_pair_origins_by_user):
            # Each user gets a unique username/email so no constraint is hit.
            suffix = uuid.uuid4().hex[:8]
            user = User(
                username=f"prop26_u{u_idx}_{suffix}",
                email=f"prop26_u{u_idx}_{suffix}@example.com",
                password_hash="x",
            )
            repos.users.add(user)
            session.flush()

            pair_ids: set[uuid.UUID] = set()
            for p_idx, origin in enumerate(pair_origins):
                pair = TrainingPair(
                    user_id=user.user_id,
                    # Unique region key per (user, pair) — calibration pairs
                    # are not tied to a recognized image, so image_id stays None.
                    source_region_key=f"u{u_idx}_p{p_idx}_{suffix}",
                    region_crop_path=f"crops/u{u_idx}/p{p_idx}.png",
                    corrected_text=f"sample text {p_idx}",
                    origin=origin,
                )
                repos.training_pairs.add(pair)
                session.flush()
                pair_ids.add(pair.pair_id)

            user_records.append((user.user_id, pair_ids))

        session.commit()

    # --- For every user, verify the three correctness dimensions ------------
    with factory() as session:
        repos = RepositoryFactory(session)

        for user_id, expected_pair_ids in user_records:
            returned_pairs: Sequence[TrainingPair] = (
                repos.training_pairs.list_for_user(user_id)
            )
            returned_pair_ids: set[uuid.UUID] = {p.pair_id for p in returned_pairs}

            # 1. Completeness: all of user X's pairs are returned.
            missing = expected_pair_ids - returned_pair_ids
            assert not missing, (
                f"list_for_user({user_id}) is missing {len(missing)} pair(s) "
                f"that belong to this user (Req 10.2 / completeness). "
                f"Missing pair_ids: {missing}"
            )

            # 2. Exclusivity: no other user's pairs contaminate the result.
            other_pair_ids: set[uuid.UUID] = set()
            for other_uid, other_pids in user_records:
                if other_uid != user_id:
                    other_pair_ids.update(other_pids)

            leaked = returned_pair_ids & other_pair_ids
            assert not leaked, (
                f"list_for_user({user_id}) returned {len(leaked)} pair(s) "
                f"that belong to OTHER users (Req 10.2 / exclusivity). "
                f"Leaked pair_ids: {leaked}"
            )

            # 3. Exactness: no unexpected extras and no missing pairs.
            assert returned_pair_ids == expected_pair_ids, (
                f"list_for_user({user_id}) returned pair set "
                f"{returned_pair_ids!r} but expected exactly "
                f"{expected_pair_ids!r} (Req 10.2 / exactness)."
            )

    # Clean up schema.
    Base.metadata.drop_all(engine)


# ---------------------------------------------------------------------------
# Sub-property: both CORRECTION and CALIBRATION origins appear in the result
# (Req 9.6 — same pipeline for both origins)
# ---------------------------------------------------------------------------

# Feature: handwriting-notes-digitizer, Property 26: A user's training set is exactly that user's pairs across all origins
@given(
    num_correction=st.integers(min_value=1, max_value=4),
    num_calibration=st.integers(min_value=1, max_value=4),
    num_other_users=st.integers(min_value=0, max_value=3),
    other_user_pair_counts=st.lists(
        st.integers(min_value=0, max_value=4), min_size=0, max_size=3
    ),
)
def test_both_origins_included_in_training_set(
    num_correction: int,
    num_calibration: int,
    num_other_users: int,
    other_user_pair_counts: list[int],
) -> None:
    """When a user has both CORRECTION and CALIBRATION pairs, all of them are
    returned by ``list_for_user`` — confirming both origins feed the same
    selection path (Req 9.6).

    Also verifies that having other users' pairs in the same database does not
    affect the result.

    **Validates: Requirements 9.6, 10.2**
    """
    engine, factory = _make_test_db()

    with factory() as session:
        repos = RepositoryFactory(session)

        suffix = uuid.uuid4().hex[:8]

        # Create the focal user with known correction and calibration counts.
        focal_user = User(
            username=f"prop26_focal_{suffix}",
            email=f"prop26_focal_{suffix}@example.com",
            password_hash="x",
        )
        repos.users.add(focal_user)
        session.flush()

        focal_pair_ids: set[uuid.UUID] = set()

        for i in range(num_correction):
            pair = TrainingPair(
                user_id=focal_user.user_id,
                source_region_key=f"focal_corr_{i}_{suffix}",
                region_crop_path=f"crops/focal/corr_{i}.png",
                corrected_text=f"correction {i}",
                origin=TrainingPairOrigin.CORRECTION,
            )
            repos.training_pairs.add(pair)
            session.flush()
            focal_pair_ids.add(pair.pair_id)

        for i in range(num_calibration):
            pair = TrainingPair(
                user_id=focal_user.user_id,
                source_region_key=f"focal_calib_{i}_{suffix}",
                region_crop_path=f"crops/focal/calib_{i}.png",
                corrected_text=f"calibration {i}",
                origin=TrainingPairOrigin.CALIBRATION,
            )
            repos.training_pairs.add(pair)
            session.flush()
            focal_pair_ids.add(pair.pair_id)

        # Pad other_user_pair_counts to num_other_users length.
        padded_counts = (other_user_pair_counts + [0] * num_other_users)[:num_other_users]

        # Create other users with their pairs (noise that must not bleed through).
        for u_idx, pair_count in enumerate(padded_counts):
            other_suffix = uuid.uuid4().hex[:8]
            other_user = User(
                username=f"prop26_other_{u_idx}_{other_suffix}",
                email=f"prop26_other_{u_idx}_{other_suffix}@example.com",
                password_hash="x",
            )
            repos.users.add(other_user)
            session.flush()

            for p_idx in range(pair_count):
                origin = (
                    TrainingPairOrigin.CORRECTION
                    if p_idx % 2 == 0
                    else TrainingPairOrigin.CALIBRATION
                )
                other_pair = TrainingPair(
                    user_id=other_user.user_id,
                    source_region_key=f"other_{u_idx}_p{p_idx}_{other_suffix}",
                    region_crop_path=f"crops/other_{u_idx}/p{p_idx}.png",
                    corrected_text=f"other text {p_idx}",
                    origin=origin,
                )
                repos.training_pairs.add(other_pair)
                session.flush()

        session.commit()

    with factory() as session:
        repos = RepositoryFactory(session)

        returned = repos.training_pairs.list_for_user(focal_user.user_id)
        returned_ids = {p.pair_id for p in returned}
        returned_origins = {p.origin for p in returned}

        # Exactness.
        assert returned_ids == focal_pair_ids, (
            f"Expected exactly {len(focal_pair_ids)} pairs for focal user "
            f"({num_correction} correction + {num_calibration} calibration), "
            f"but got {len(returned_ids)} pairs. "
            f"Extra: {returned_ids - focal_pair_ids}, "
            f"Missing: {focal_pair_ids - returned_ids}"
        )

        # Both origins present (since we inserted at least 1 of each).
        assert TrainingPairOrigin.CORRECTION in returned_origins, (
            "CORRECTION origin pairs are missing from the training set "
            "(Req 9.6 — both origins must feed the same pipeline)."
        )
        assert TrainingPairOrigin.CALIBRATION in returned_origins, (
            "CALIBRATION origin pairs are missing from the training set "
            "(Req 9.6 — both origins must feed the same pipeline)."
        )

        # Correct counts per origin.
        corr_count = sum(
            1 for p in returned if p.origin == TrainingPairOrigin.CORRECTION
        )
        calib_count = sum(
            1 for p in returned if p.origin == TrainingPairOrigin.CALIBRATION
        )
        assert corr_count == num_correction, (
            f"Expected {num_correction} correction pairs, got {corr_count}."
        )
        assert calib_count == num_calibration, (
            f"Expected {num_calibration} calibration pairs, got {calib_count}."
        )

    Base.metadata.drop_all(engine)
