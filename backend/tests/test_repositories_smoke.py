"""Smoke tests for the per-user-scoped data-access layer (task 2.3).

These confirm the repositories import, construct over a session, and apply the
mandatory ``user_id`` scoping for basic get/list operations. The exhaustive
cross-user scoping property/unit tests are added in task 2.4.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.data import (
    Base,
    Image,
    ImageFormat,
    ImageStatus,
    RepositoryFactory,
    User,
    create_all,
    create_db_engine,
    create_session_factory,
)


@pytest.fixture()
def session() -> Session:
    engine = create_db_engine("sqlite://")
    create_all(engine)
    factory = create_session_factory(engine)
    with factory() as s:
        yield s
    Base.metadata.drop_all(engine)


def _make_user(session: Session, username: str) -> User:
    user = User(
        username=username,
        email=f"{username}@example.com",
        password_hash="hashed",
    )
    session.add(user)
    session.commit()
    return user


def test_image_get_is_scoped_by_user(session: Session) -> None:
    repos = RepositoryFactory(session)
    alice = _make_user(session, "alice")
    bob = _make_user(session, "bob")

    image = repos.images.add(
        Image(
            user_id=alice.user_id,
            storage_path="images/a.png",
            format=ImageFormat.PNG,
            size_bytes=100,
            status=ImageStatus.ACCEPTED,
        )
    )
    session.commit()

    # Owner sees the image; another user does not (returns None, not the row).
    assert repos.images.get(alice.user_id, image.image_id) is not None
    assert repos.images.get(bob.user_id, image.image_id) is None

    assert [i.image_id for i in repos.images.list_for_user(alice.user_id)] == [
        image.image_id
    ]
    assert repos.images.list_for_user(bob.user_id) == []


def test_user_lookup_helpers(session: Session) -> None:
    repos = RepositoryFactory(session)
    alice = _make_user(session, "alice")

    assert repos.users.get_by_id(alice.user_id) is not None
    assert repos.users.get_by_username("alice") is not None
    assert repos.users.get_by_email("alice@example.com") is not None
    assert repos.users.get_by_username("nobody") is None
