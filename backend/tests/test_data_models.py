"""Unit tests for the relational data models (task 2.1).

These tests verify that every model instantiates and persists against an
in-memory SQLite database, and that the schema-level constraints from the
design hold:

* unique ``(user_id, source_region_key)`` on ``TrainingPair`` (Req 4.7)
* unique ``user_id`` on ``Adapter`` -> at most one adapter per user (Req 5.3)
* ``consumed_by_run_id`` links a pair to the run that consumed it (Req 5.1)
* enum-valued columns round-trip correctly
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.data import (
    Adapter,
    AdapterStatus,
    Base,
    EvaluationAvailability,
    EvaluationResult,
    Image,
    ImageFormat,
    ImageStatus,
    RecognizedWord,
    TrainingPair,
    TrainingPairOrigin,
    TrainingRun,
    TrainingRunPair,
    TrainingRunState,
    User,
    create_all,
    create_db_engine,
    create_session_factory,
)


@pytest.fixture()
def session() -> Session:
    """A session backed by a fresh in-memory SQLite database per test."""
    engine = create_db_engine("sqlite://")  # in-memory
    create_all(engine)
    factory = create_session_factory(engine)
    with factory() as s:
        yield s
    Base.metadata.drop_all(engine)


def _make_user(session: Session, username: str = "alice") -> User:
    user = User(
        username=username,
        email=f"{username}@example.com",
        password_hash="hashed",
    )
    session.add(user)
    session.commit()
    return user


def test_all_models_instantiate_and_persist(session: Session) -> None:
    user = _make_user(session)

    image = Image(
        user_id=user.user_id,
        storage_path="objects/img1.png",
        format=ImageFormat.PNG,
        size_bytes=1024,
        status=ImageStatus.ACCEPTED,
    )
    session.add(image)
    session.commit()

    word = RecognizedWord(
        image_id=image.image_id,
        line_index=0,
        word_index=0,
        text="hello",
        region_x=1,
        region_y=2,
        region_width=30,
        region_height=10,
        confidence=0.97,
    )
    session.add(word)

    run = TrainingRun(user_id=user.user_id, state=TrainingRunState.ACCEPTED)
    session.add(run)
    session.commit()

    pair = TrainingPair(
        user_id=user.user_id,
        image_id=image.image_id,
        source_region_key=f"{image.image_id}:1,2,30,10",
        region_crop_path="objects/crop1.png",
        corrected_text="hello",
        origin=TrainingPairOrigin.CORRECTION,
        consumed_by_run_id=run.run_id,
    )
    session.add(pair)
    session.flush()  # populate pair.pair_id before referencing it

    run_pair = TrainingRunPair(run_id=run.run_id, pair_id=pair.pair_id, is_held_out=False)
    session.add(run_pair)

    adapter = Adapter(
        user_id=user.user_id,
        weights_path="objects/adapter.pt",
        base_model_version="base-v1",
        status=AdapterStatus.ACTIVE,
    )
    session.add(adapter)

    evaluation = EvaluationResult(
        run_id=run.run_id,
        user_id=user.user_id,
        before_cer=0.2,
        before_wer=0.4,
        after_cer=0.1,
        after_wer=0.2,
        delta_cer=0.1,
        delta_wer=0.2,
        general_cer=0.05,
        general_wer=0.1,
        availability=EvaluationAvailability.AVAILABLE,
    )
    session.add(evaluation)
    session.commit()

    # Relationships and enum values round-trip.
    session.refresh(user)
    assert user.images[0].image_id == image.image_id
    assert user.images[0].format is ImageFormat.PNG
    assert user.adapter is not None and user.adapter.status is AdapterStatus.ACTIVE
    assert pair.consumed_by_run_id == run.run_id
    assert pair.consumed_by_run.state is TrainingRunState.ACCEPTED
    assert evaluation.availability is EvaluationAvailability.AVAILABLE
    assert word.confidence == pytest.approx(0.97)
    # created_at / updated_at populated by defaults.
    assert pair.created_at is not None
    assert pair.updated_at is not None


def test_training_pair_unique_per_user_and_region(session: Session) -> None:
    """Re-using (user_id, source_region_key) must violate the unique constraint."""
    user = _make_user(session)
    key = "img:0,0,10,10"

    session.add(
        TrainingPair(
            user_id=user.user_id,
            source_region_key=key,
            region_crop_path="objects/c1.png",
            corrected_text="first",
            origin=TrainingPairOrigin.CORRECTION,
        )
    )
    session.commit()

    session.add(
        TrainingPair(
            user_id=user.user_id,
            source_region_key=key,
            region_crop_path="objects/c2.png",
            corrected_text="second",
            origin=TrainingPairOrigin.CORRECTION,
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_same_region_key_allowed_for_different_users(session: Session) -> None:
    """The unique constraint is scoped per user, not global."""
    alice = _make_user(session, "alice")
    bob = _make_user(session, "bob")
    key = "img:0,0,10,10"

    session.add_all(
        [
            TrainingPair(
                user_id=alice.user_id,
                source_region_key=key,
                region_crop_path="objects/a.png",
                corrected_text="a",
                origin=TrainingPairOrigin.CORRECTION,
            ),
            TrainingPair(
                user_id=bob.user_id,
                source_region_key=key,
                region_crop_path="objects/b.png",
                corrected_text="b",
                origin=TrainingPairOrigin.CALIBRATION,
            ),
        ]
    )
    session.commit()  # must not raise


def test_at_most_one_adapter_per_user(session: Session) -> None:
    """A second adapter for the same user must violate the unique constraint."""
    user = _make_user(session)
    session.add(
        Adapter(
            user_id=user.user_id,
            weights_path="objects/a1.pt",
            base_model_version="base-v1",
        )
    )
    session.commit()

    session.add(
        Adapter(
            user_id=user.user_id,
            weights_path="objects/a2.pt",
            base_model_version="base-v1",
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_unused_pair_filter_via_consumed_by_run_id(session: Session) -> None:
    """consumed_by_run_id distinguishes consumed from unused pairs (Req 5.1)."""
    user = _make_user(session)
    run = TrainingRun(user_id=user.user_id, state=TrainingRunState.ACCEPTED)
    session.add(run)
    session.commit()

    consumed = TrainingPair(
        user_id=user.user_id,
        source_region_key="k1",
        region_crop_path="c1.png",
        corrected_text="x",
        origin=TrainingPairOrigin.CORRECTION,
        consumed_by_run_id=run.run_id,
    )
    unused = TrainingPair(
        user_id=user.user_id,
        source_region_key="k2",
        region_crop_path="c2.png",
        corrected_text="y",
        origin=TrainingPairOrigin.CORRECTION,
    )
    session.add_all([consumed, unused])
    session.commit()

    unused_rows = (
        session.query(TrainingPair)
        .filter(
            TrainingPair.user_id == user.user_id,
            TrainingPair.consumed_by_run_id.is_(None),
        )
        .all()
    )
    assert len(unused_rows) == 1
    assert unused_rows[0].source_region_key == "k2"
