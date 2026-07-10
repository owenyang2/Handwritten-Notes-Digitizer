"""Tests for cross-user resource isolation (task 3.2, Req 10.5).

These tests prove the shared isolation mechanism in
``app.services.isolation`` turns a per-user-scoped repository "ownership miss"
(``None``) into an HTTP **404 (not 403)** so the existence of another user's
data is never disclosed.

Two layers are exercised:

* **Unit** - ``get_or_404`` / ``require_owned`` raise a 404 ``ResourceNotFound``
  on ``None`` and pass values through otherwise; ``CrossUserAccessError`` maps to
  a 404 via the registered handler.
* **Integration** - a small app wires gated endpoints for *training pairs*,
  *recognized words* (recognized text), and *adapters* through the real auth
  gate and scoped repositories. We assert that:
    - the owner gets their resource (200), and
    - another authenticated user gets **404** (never 403) with a generic
      message that does not reveal the resource exists, while the owner's data
      remains intact.
"""

from __future__ import annotations

import uuid
from typing import Iterator

import pytest
from fastapi import Depends, FastAPI, status
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.data import (
    Adapter,
    AdapterStatus,
    Base,
    Image,
    ImageFormat,
    ImageStatus,
    RecognizedWord,
    RepositoryFactory,
    TrainingPair,
    TrainingPairOrigin,
    User,
    create_all,
    create_db_engine,
    create_session_factory,
)
from app.services import auth_api
from app.services.auth import SessionStore, hash_password
from app.services.auth_api import (
    get_current_user,
    get_db_session,
    get_session_store,
    router as auth_router,
)
from app.services.isolation import (
    RESOURCE_NOT_FOUND_MESSAGE,
    CrossUserAccessError,
    ResourceNotFound,
    get_or_404,
    register_isolation_handlers,
    require_owned,
)


# --------------------------------------------------------------------------- #
# Unit tests: the helpers and exception types
# --------------------------------------------------------------------------- #
def test_get_or_404_passes_through_non_none() -> None:
    sentinel = object()
    assert get_or_404(sentinel) is sentinel


def test_get_or_404_raises_404_on_none() -> None:
    with pytest.raises(ResourceNotFound) as exc:
        get_or_404(None)
    assert exc.value.status_code == status.HTTP_404_NOT_FOUND
    # The generic message must not hint that the resource exists.
    assert exc.value.detail == RESOURCE_NOT_FOUND_MESSAGE


def test_get_or_404_never_uses_403() -> None:
    with pytest.raises(ResourceNotFound) as exc:
        get_or_404(None)
    assert exc.value.status_code != status.HTTP_403_FORBIDDEN


def test_require_owned_is_get_or_404_alias() -> None:
    sentinel = object()
    assert require_owned(sentinel) is sentinel
    with pytest.raises(ResourceNotFound):
        require_owned(None)


def test_resource_not_found_custom_detail() -> None:
    err = ResourceNotFound("custom")
    assert err.status_code == status.HTTP_404_NOT_FOUND
    assert err.detail == "custom"


def test_cross_user_access_error_default_message() -> None:
    err = CrossUserAccessError()
    assert err.message == RESOURCE_NOT_FOUND_MESSAGE


# --------------------------------------------------------------------------- #
# Integration: DB + auth fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture()
def session_factory():
    engine = create_db_engine("sqlite://", poolclass=StaticPool)
    create_all(engine)
    factory = create_session_factory(engine)
    yield factory
    Base.metadata.drop_all(engine)


def _make_user(session: Session, username: str, password: str = "pw") -> User:
    user = User(
        username=username,
        email=f"{username}@example.com",
        password_hash=hash_password(password),
    )
    session.add(user)
    session.commit()
    return user


@pytest.fixture()
def app_client(db_session, session_factory):
    """App exposing owned-resource endpoints gated by auth + isolation.

    Each GET endpoint funnels a per-user-scoped repository lookup through the
    isolation helpers, so a cross-user request yields a 404. A dedicated
    ``/raise-cross-user`` endpoint exercises the ``CrossUserAccessError`` handler.
    """
    store = SessionStore(db_session)

    def _override_db_session() -> Iterator[Session]:
        with session_factory() as s:
            yield s

    def _override_store() -> SessionStore:
        return store

    app = FastAPI()
    app.include_router(auth_router)
    register_isolation_handlers(app)

    @app.get("/training-pairs/{pair_id}")
    def get_training_pair(
        pair_id: uuid.UUID,
        user: User = Depends(get_current_user),
        repos: RepositoryFactory = Depends(auth_api.get_repositories),
    ) -> dict:
        pair = require_owned(repos.training_pairs.get(user.user_id, pair_id))
        return {"pair_id": str(pair.pair_id), "text": pair.corrected_text}

    @app.get("/recognized-words/{word_id}")
    def get_recognized_word(
        word_id: uuid.UUID,
        user: User = Depends(get_current_user),
        repos: RepositoryFactory = Depends(auth_api.get_repositories),
    ) -> dict:
        word = get_or_404(repos.recognized_words.get(user.user_id, word_id))
        return {"word_id": str(word.word_id), "text": word.text}

    @app.get("/adapter")
    def get_adapter(
        user: User = Depends(get_current_user),
        repos: RepositoryFactory = Depends(auth_api.get_repositories),
    ) -> dict:
        adapter = require_owned(repos.adapters.get_for_user(user.user_id))
        return {"adapter_id": str(adapter.adapter_id)}

    @app.get("/raise-cross-user")
    def raise_cross_user(
        _user: User = Depends(get_current_user),
    ) -> dict:
        raise CrossUserAccessError()

    app.dependency_overrides[get_db_session] = _override_db_session
    app.dependency_overrides[get_session_store] = _override_store

    with TestClient(app) as client:
        yield client, session_factory


def _login(client: TestClient, username: str, password: str = "pw") -> str:
    resp = client.post(
        "/auth/login", json={"username": username, "password": password}
    )
    assert resp.status_code == status.HTTP_200_OK
    return resp.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------------------- #
# Cross-user isolation: training pairs
# --------------------------------------------------------------------------- #
def test_owner_reads_training_pair_but_other_user_gets_404(app_client) -> None:
    client, factory = app_client
    with factory() as s:
        alice = _make_user(s, "alice")
        _make_user(s, "bob")
        repos = RepositoryFactory(s)
        pair = repos.training_pairs.add(
            TrainingPair(
                user_id=alice.user_id,
                source_region_key="r0",
                region_crop_path="crop.png",
                corrected_text="secret-correction",
                origin=TrainingPairOrigin.CORRECTION,
            )
        )
        s.commit()
        pair_id = pair.pair_id

    alice_token = _login(client, "alice")
    bob_token = _login(client, "bob")

    # Owner sees it.
    owner = client.get(f"/training-pairs/{pair_id}", headers=_auth(alice_token))
    assert owner.status_code == status.HTTP_200_OK
    assert owner.json()["text"] == "secret-correction"

    # Another user gets 404 (not 403) with a generic message that does not
    # disclose the pair exists or reveal its contents.
    other = client.get(f"/training-pairs/{pair_id}", headers=_auth(bob_token))
    assert other.status_code == status.HTTP_404_NOT_FOUND
    assert other.status_code != status.HTTP_403_FORBIDDEN
    assert other.json()["detail"] == RESOURCE_NOT_FOUND_MESSAGE
    assert "secret-correction" not in other.text

    # A response for a wholly nonexistent pair is indistinguishable.
    missing = client.get(
        f"/training-pairs/{uuid.uuid4()}", headers=_auth(bob_token)
    )
    assert missing.status_code == status.HTTP_404_NOT_FOUND
    assert missing.json()["detail"] == other.json()["detail"]

    # The owner's data is unchanged after the denied access.
    with factory() as s:
        assert RepositoryFactory(s).training_pairs.get(alice.user_id, pair_id) is not None


# --------------------------------------------------------------------------- #
# Cross-user isolation: recognized text (RecognizedWord, scoped via Image)
# --------------------------------------------------------------------------- #
def test_owner_reads_recognized_word_but_other_user_gets_404(app_client) -> None:
    client, factory = app_client
    with factory() as s:
        alice = _make_user(s, "alice")
        _make_user(s, "bob")
        repos = RepositoryFactory(s)
        image = repos.images.add(
            Image(
                user_id=alice.user_id,
                storage_path="img.png",
                format=ImageFormat.PNG,
                size_bytes=10,
                status=ImageStatus.RECOGNIZED,
            )
        )
        s.commit()
        word = repos.recognized_words.add(
            RecognizedWord(
                image_id=image.image_id,
                line_index=0,
                word_index=0,
                text="confidential",
                region_x=1,
                region_y=2,
                region_width=3,
                region_height=4,
            )
        )
        s.commit()
        word_id = word.word_id

    alice_token = _login(client, "alice")
    bob_token = _login(client, "bob")

    owner = client.get(f"/recognized-words/{word_id}", headers=_auth(alice_token))
    assert owner.status_code == status.HTTP_200_OK
    assert owner.json()["text"] == "confidential"

    other = client.get(f"/recognized-words/{word_id}", headers=_auth(bob_token))
    assert other.status_code == status.HTTP_404_NOT_FOUND
    assert other.status_code != status.HTTP_403_FORBIDDEN
    assert other.json()["detail"] == RESOURCE_NOT_FOUND_MESSAGE
    assert "confidential" not in other.text


# --------------------------------------------------------------------------- #
# Cross-user isolation: adapters
# --------------------------------------------------------------------------- #
def test_owner_reads_adapter_but_other_user_gets_404(app_client) -> None:
    client, factory = app_client
    with factory() as s:
        alice = _make_user(s, "alice")
        _make_user(s, "bob")
        repos = RepositoryFactory(s)
        adapter = repos.adapters.add(
            Adapter(
                user_id=alice.user_id,
                weights_path="adapter.pt",
                base_model_version="base-v1",
                status=AdapterStatus.ACTIVE,
            )
        )
        s.commit()
        adapter_id = adapter.adapter_id

    alice_token = _login(client, "alice")
    bob_token = _login(client, "bob")

    owner = client.get("/adapter", headers=_auth(alice_token))
    assert owner.status_code == status.HTTP_200_OK
    assert owner.json()["adapter_id"] == str(adapter_id)

    # Bob has no adapter; the lookup misses and yields a 404 (not 403), the same
    # response he'd get for someone else's adapter - existence not disclosed.
    other = client.get("/adapter", headers=_auth(bob_token))
    assert other.status_code == status.HTTP_404_NOT_FOUND
    assert other.status_code != status.HTTP_403_FORBIDDEN
    assert other.json()["detail"] == RESOURCE_NOT_FOUND_MESSAGE

    # Alice's adapter is intact after Bob's denied access.
    with factory() as s:
        assert RepositoryFactory(s).adapters.get_for_user(alice.user_id) is not None


# --------------------------------------------------------------------------- #
# CrossUserAccessError handler maps domain error to 404
# --------------------------------------------------------------------------- #
def test_cross_user_access_error_renders_as_404(app_client) -> None:
    client, factory = app_client
    with factory() as s:
        _make_user(s, "alice")

    token = _login(client, "alice")
    resp = client.get("/raise-cross-user", headers=_auth(token))
    assert resp.status_code == status.HTTP_404_NOT_FOUND
    assert resp.status_code != status.HTTP_403_FORBIDDEN
    assert resp.json()["detail"] == RESOURCE_NOT_FOUND_MESSAGE
