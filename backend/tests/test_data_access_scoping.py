"""Unit tests for per-user data-access scoping (task 2.4, Req 10.5).

These tests verify the *defense in depth* guarantee of the repositories in
``app.data.repositories``: every query method is scoped by the authenticated
``user_id`` so a repository can never return, update, or delete a row owned by
another user. For each repository we set up two users (``alice`` and ``bob``),
give each owning rows, and assert that:

* an owner can read/list/delete their own rows, and
* the *other* user sees ``None``/empty for the same identifier and cannot
  mutate (update/delete) the owner's rows.

``RecognizedWord`` is special: it has no ``user_id`` column and is scoped via a
join to its parent :class:`Image`, so the cross-user assertions there exercise
that join boundary.
"""

from __future__ import annotations

import pytest
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


# --------------------------------------------------------------------------- #
# Fixtures / helpers
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


def _make_user(session: Session, username: str) -> User:
    user = User(
        username=username,
        email=f"{username}@example.com",
        password_hash="hashed",
    )
    session.add(user)
    session.commit()
    return user


@pytest.fixture()
def alice(session: Session) -> User:
    return _make_user(session, "alice")


@pytest.fixture()
def bob(session: Session) -> User:
    return _make_user(session, "bob")


def _make_image(repos: RepositoryFactory, owner: User, path: str = "img.png") -> Image:
    image = repos.images.add(
        Image(
            user_id=owner.user_id,
            storage_path=path,
            format=ImageFormat.PNG,
            size_bytes=123,
            status=ImageStatus.ACCEPTED,
        )
    )
    repos.session.commit()
    return image


def _make_recognized_word(
    repos: RepositoryFactory, image: Image, line: int = 0, word: int = 0
) -> RecognizedWord:
    rw = repos.recognized_words.add(
        RecognizedWord(
            image_id=image.image_id,
            line_index=line,
            word_index=word,
            text="hello",
            region_x=1,
            region_y=2,
            region_width=3,
            region_height=4,
        )
    )
    repos.session.commit()
    return rw


def _make_training_pair(
    repos: RepositoryFactory,
    owner: User,
    region_key: str = "r0",
    consumed_run_id=None,
) -> TrainingPair:
    pair = repos.training_pairs.add(
        TrainingPair(
            user_id=owner.user_id,
            source_region_key=region_key,
            region_crop_path="crop.png",
            corrected_text="corrected",
            origin=TrainingPairOrigin.CORRECTION,
            consumed_by_run_id=consumed_run_id,
        )
    )
    repos.session.commit()
    return pair


def _make_adapter(repos: RepositoryFactory, owner: User) -> Adapter:
    adapter = repos.adapters.add(
        Adapter(
            user_id=owner.user_id,
            weights_path="adapter.pt",
            base_model_version="base-v1",
            status=AdapterStatus.ACTIVE,
        )
    )
    repos.session.commit()
    return adapter


def _make_training_run(
    repos: RepositoryFactory, owner: User, state: TrainingRunState = TrainingRunState.RUNNING
) -> TrainingRun:
    run = repos.training_runs.add(TrainingRun(user_id=owner.user_id, state=state))
    repos.session.commit()
    return run


def _make_eval_result(
    repos: RepositoryFactory, owner: User, run: TrainingRun
) -> EvaluationResult:
    result = repos.evaluation_results.add(
        EvaluationResult(
            run_id=run.run_id,
            user_id=owner.user_id,
            before_cer=0.5,
            after_cer=0.4,
            availability=EvaluationAvailability.AVAILABLE,
        )
    )
    repos.session.commit()
    return result


# --------------------------------------------------------------------------- #
# ImageRepository
# --------------------------------------------------------------------------- #
def test_image_get_denies_other_user(repos, alice, bob) -> None:
    image = _make_image(repos, alice)

    assert repos.images.get(alice.user_id, image.image_id) is not None
    # Bob asking for Alice's image gets None (callers map to 404 - Req 10.5).
    assert repos.images.get(bob.user_id, image.image_id) is None


def test_image_list_is_scoped(repos, alice, bob) -> None:
    a_img = _make_image(repos, alice, "a.png")
    _make_image(repos, bob, "b.png")

    assert [i.image_id for i in repos.images.list_for_user(alice.user_id)] == [
        a_img.image_id
    ]
    # Bob never sees Alice's image in his list.
    assert a_img.image_id not in [i.image_id for i in repos.images.list_for_user(bob.user_id)]


def test_image_delete_denies_other_user(repos, alice, bob) -> None:
    image = _make_image(repos, alice)

    # Bob cannot delete Alice's image: returns False and the row survives.
    assert repos.images.delete(bob.user_id, image.image_id) is False
    assert repos.images.get(alice.user_id, image.image_id) is not None

    # Owner can delete their own image.
    assert repos.images.delete(alice.user_id, image.image_id) is True
    repos.session.commit()
    assert repos.images.get(alice.user_id, image.image_id) is None


# --------------------------------------------------------------------------- #
# RecognizedWordRepository (scoped via the Image join, no user_id column)
# --------------------------------------------------------------------------- #
def test_recognized_word_get_denies_other_user(repos, alice, bob) -> None:
    image = _make_image(repos, alice)
    word = _make_recognized_word(repos, image)

    assert repos.recognized_words.get(alice.user_id, word.word_id) is not None
    # Bob cannot read a word belonging to Alice's image.
    assert repos.recognized_words.get(bob.user_id, word.word_id) is None


def test_recognized_word_list_for_image_denies_other_user(repos, alice, bob) -> None:
    image = _make_image(repos, alice)
    _make_recognized_word(repos, image, line=0, word=0)
    _make_recognized_word(repos, image, line=0, word=1)

    owner_words = repos.recognized_words.list_for_image(alice.user_id, image.image_id)
    assert len(owner_words) == 2
    # Even with the correct image_id, Bob gets nothing because the image isn't his.
    assert repos.recognized_words.list_for_image(bob.user_id, image.image_id) == []


def test_recognized_word_list_for_image_reading_order(repos, alice) -> None:
    image = _make_image(repos, alice)
    # Insert out of reading order; expect line then word ordering back.
    _make_recognized_word(repos, image, line=1, word=0)
    _make_recognized_word(repos, image, line=0, word=1)
    _make_recognized_word(repos, image, line=0, word=0)

    words = repos.recognized_words.list_for_image(alice.user_id, image.image_id)
    assert [(w.line_index, w.word_index) for w in words] == [(0, 0), (0, 1), (1, 0)]


# --------------------------------------------------------------------------- #
# TrainingPairRepository
# --------------------------------------------------------------------------- #
def test_training_pair_get_denies_other_user(repos, alice, bob) -> None:
    pair = _make_training_pair(repos, alice)

    assert repos.training_pairs.get(alice.user_id, pair.pair_id) is not None
    assert repos.training_pairs.get(bob.user_id, pair.pair_id) is None


def test_training_pair_get_by_region_key_is_scoped(repos, alice, bob) -> None:
    # Both users use the SAME region key; the unique constraint is per-user so
    # this is allowed, and lookups must not cross the boundary.
    alice_pair = _make_training_pair(repos, alice, region_key="shared-key")
    bob_pair = _make_training_pair(repos, bob, region_key="shared-key")

    assert (
        repos.training_pairs.get_by_region_key(alice.user_id, "shared-key").pair_id
        == alice_pair.pair_id
    )
    assert (
        repos.training_pairs.get_by_region_key(bob.user_id, "shared-key").pair_id
        == bob_pair.pair_id
    )
    # A key only Alice owns is invisible to Bob.
    _make_training_pair(repos, alice, region_key="alice-only")
    assert repos.training_pairs.get_by_region_key(bob.user_id, "alice-only") is None


def test_training_pair_list_for_user_is_scoped(repos, alice, bob) -> None:
    a_pair = _make_training_pair(repos, alice, region_key="a")
    _make_training_pair(repos, bob, region_key="b")

    assert [p.pair_id for p in repos.training_pairs.list_for_user(alice.user_id)] == [
        a_pair.pair_id
    ]
    assert a_pair.pair_id not in [
        p.pair_id for p in repos.training_pairs.list_for_user(bob.user_id)
    ]


def test_training_pair_list_unused_and_count_are_scoped(repos, alice, bob) -> None:
    run = _make_training_run(repos, alice, state=TrainingRunState.ACCEPTED)
    # Alice has one unused and one consumed pair; Bob has one unused pair.
    _make_training_pair(repos, alice, region_key="a-unused")
    _make_training_pair(repos, alice, region_key="a-used", consumed_run_id=run.run_id)
    _make_training_pair(repos, bob, region_key="b-unused")

    alice_unused = repos.training_pairs.list_unused(alice.user_id)
    assert [p.source_region_key for p in alice_unused] == ["a-unused"]
    assert repos.training_pairs.count_unused(alice.user_id) == 1
    # Bob's unused count never includes Alice's pairs.
    assert repos.training_pairs.count_unused(bob.user_id) == 1


def test_training_pair_delete_denies_other_user(repos, alice, bob) -> None:
    pair = _make_training_pair(repos, alice)

    assert repos.training_pairs.delete(bob.user_id, pair.pair_id) is False
    assert repos.training_pairs.get(alice.user_id, pair.pair_id) is not None

    assert repos.training_pairs.delete(alice.user_id, pair.pair_id) is True
    repos.session.commit()
    assert repos.training_pairs.get(alice.user_id, pair.pair_id) is None


# --------------------------------------------------------------------------- #
# AdapterRepository (at most one per user)
# --------------------------------------------------------------------------- #
def test_adapter_get_for_user_is_scoped(repos, alice, bob) -> None:
    adapter = _make_adapter(repos, alice)

    assert repos.adapters.get_for_user(alice.user_id).adapter_id == adapter.adapter_id
    # Bob has no adapter and never sees Alice's.
    assert repos.adapters.get_for_user(bob.user_id) is None


def test_adapter_get_active_for_user_is_scoped(repos, alice, bob) -> None:
    _make_adapter(repos, alice)

    assert repos.adapters.get_active_for_user(alice.user_id) is not None
    assert repos.adapters.get_active_for_user(bob.user_id) is None


def test_adapter_delete_denies_other_user(repos, alice, bob) -> None:
    adapter = _make_adapter(repos, alice)

    # Bob deleting "his" adapter is a no-op and must not touch Alice's.
    assert repos.adapters.delete_for_user(bob.user_id) is False
    assert repos.adapters.get_for_user(alice.user_id).adapter_id == adapter.adapter_id

    assert repos.adapters.delete_for_user(alice.user_id) is True
    repos.session.commit()
    assert repos.adapters.get_for_user(alice.user_id) is None


# --------------------------------------------------------------------------- #
# TrainingRunRepository
# --------------------------------------------------------------------------- #
def test_training_run_get_denies_other_user(repos, alice, bob) -> None:
    run = _make_training_run(repos, alice)

    assert repos.training_runs.get(alice.user_id, run.run_id) is not None
    assert repos.training_runs.get(bob.user_id, run.run_id) is None


def test_training_run_list_for_user_is_scoped(repos, alice, bob) -> None:
    run = _make_training_run(repos, alice)
    _make_training_run(repos, bob)

    assert [r.run_id for r in repos.training_runs.list_for_user(alice.user_id)] == [
        run.run_id
    ]
    assert run.run_id not in [
        r.run_id for r in repos.training_runs.list_for_user(bob.user_id)
    ]


def test_training_run_get_running_for_user_is_scoped(repos, alice, bob) -> None:
    # Alice has a RUNNING run; Bob's only run is QUEUED.
    running = _make_training_run(repos, alice, state=TrainingRunState.RUNNING)
    _make_training_run(repos, bob, state=TrainingRunState.QUEUED)

    assert repos.training_runs.get_running_for_user(alice.user_id).run_id == running.run_id
    # Bob's lookup must not surface Alice's running run.
    assert repos.training_runs.get_running_for_user(bob.user_id) is None


# --------------------------------------------------------------------------- #
# EvaluationResultRepository
# --------------------------------------------------------------------------- #
def test_evaluation_result_get_denies_other_user(repos, alice, bob) -> None:
    run = _make_training_run(repos, alice, state=TrainingRunState.ACCEPTED)
    result = _make_eval_result(repos, alice, run)

    assert repos.evaluation_results.get(alice.user_id, result.eval_id) is not None
    assert repos.evaluation_results.get(bob.user_id, result.eval_id) is None


def test_evaluation_result_get_for_run_is_scoped(repos, alice, bob) -> None:
    run = _make_training_run(repos, alice, state=TrainingRunState.ACCEPTED)
    _make_eval_result(repos, alice, run)

    assert repos.evaluation_results.get_for_run(alice.user_id, run.run_id) is not None
    # Bob cannot read the evaluation of Alice's run even with the run id.
    assert repos.evaluation_results.get_for_run(bob.user_id, run.run_id) is None


def test_evaluation_result_list_and_latest_are_scoped(repos, alice, bob) -> None:
    run = _make_training_run(repos, alice, state=TrainingRunState.ACCEPTED)
    result = _make_eval_result(repos, alice, run)

    assert [r.eval_id for r in repos.evaluation_results.list_for_user(alice.user_id)] == [
        result.eval_id
    ]
    assert repos.evaluation_results.list_for_user(bob.user_id) == []

    assert repos.evaluation_results.latest_for_user(alice.user_id).eval_id == result.eval_id
    assert repos.evaluation_results.latest_for_user(bob.user_id) is None
