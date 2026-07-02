"""Unit + integration tests for the Auth + Session layer (task 3.1).

Covers:
* Password hashing/verification (salting, tamper resistance, format).
* The in-memory session store.
* ``AuthService`` authenticate/login behavior.
* ``POST /auth/login`` issuing a token and rejecting bad credentials.
* The ``get_current_user`` auth gate: authenticated requests pass, while
  unauthenticated submissions are rejected with an authentication-required
  message and store nothing (Req 1.5, 10.1, 10.4).
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
from app.services.auth import (
    AuthService,
    InvalidCredentials,
    SessionStore,
    hash_password,
    verify_password,
)
from app.services import auth_api
from app.services.auth_api import (
    AUTH_REQUIRED_MESSAGE,
    INVALID_CREDENTIALS_MESSAGE,
    get_current_user,
    get_db_session,
    get_session_store,
    router as auth_router,
)


# --------------------------------------------------------------------------- #
# Password hashing
# --------------------------------------------------------------------------- #


def test_hash_password_is_not_plaintext_and_verifies() -> None:
    encoded = hash_password("correct horse battery staple")
    assert "correct horse battery staple" not in encoded
    assert encoded.startswith("pbkdf2_sha256$")
    assert verify_password("correct horse battery staple", encoded) is True


def test_verify_rejects_wrong_password() -> None:
    encoded = hash_password("s3cret")
    assert verify_password("not-it", encoded) is False


def test_hash_uses_random_salt_so_same_password_differs() -> None:
    a = hash_password("samepass")
    b = hash_password("samepass")
    assert a != b
    # ...yet both verify against the original password.
    assert verify_password("samepass", a)
    assert verify_password("samepass", b)


def test_verify_detects_tampering() -> None:
    encoded = hash_password("pw")
    algo, iters, salt_b64, hash_b64 = encoded.split("$")
    # Flip a character in the stored derived key.
    flipped = ("A" if hash_b64[0] != "A" else "B") + hash_b64[1:]
    tampered = "$".join([algo, iters, salt_b64, flipped])
    assert verify_password("pw", tampered) is False


@pytest.mark.parametrize(
    "bad",
    ["", "not-an-encoded-hash", "pbkdf2_sha256$x$y", "bcrypt$1$salt$hash"],
)
def test_verify_returns_false_for_malformed_encoded(bad: str) -> None:
    assert verify_password("pw", bad) is False


def test_hash_password_rejects_non_string() -> None:
    with pytest.raises(TypeError):
        hash_password(12345)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Session store
# --------------------------------------------------------------------------- #


def test_session_store_create_resolve_revoke() -> None:
    store = SessionStore()
    uid = uuid.uuid4()
    token = store.create(uid)
    assert isinstance(token, str) and token
    assert store.resolve(token) == uid
    assert store.revoke(token) is True
    assert store.resolve(token) is None
    assert store.revoke(token) is False


def test_session_store_resolve_unknown_and_empty() -> None:
    store = SessionStore()
    assert store.resolve(None) is None
    assert store.resolve("") is None
    assert store.resolve("never-issued") is None


def test_session_store_tokens_are_unique() -> None:
    store = SessionStore()
    uid = uuid.uuid4()
    tokens = {store.create(uid) for _ in range(50)}
    assert len(tokens) == 50


# --------------------------------------------------------------------------- #
# In-memory DB fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture()
def session_factory():
    """A shared in-memory SQLite engine usable across threads (TestClient)."""
    engine = create_db_engine(
        "sqlite://",
        poolclass=StaticPool,
    )
    create_all(engine)
    factory = create_session_factory(engine)
    yield factory
    Base.metadata.drop_all(engine)


@pytest.fixture()
def db_session(session_factory) -> Iterator[Session]:
    with session_factory() as s:
        yield s


def _make_user(session: Session, username: str, password: str) -> User:
    user = User(
        username=username,
        email=f"{username}@example.com",
        password_hash=hash_password(password),
    )
    session.add(user)
    session.commit()
    return user


# --------------------------------------------------------------------------- #
# AuthService
# --------------------------------------------------------------------------- #


def test_auth_service_authenticate_success(db_session: Session) -> None:
    _make_user(db_session, "alice", "hunter2")
    repos = RepositoryFactory(db_session)
    svc = AuthService(repos.users, SessionStore())

    user = svc.authenticate("alice", "hunter2")
    assert user.username == "alice"


def test_auth_service_authenticate_wrong_password(db_session: Session) -> None:
    _make_user(db_session, "alice", "hunter2")
    repos = RepositoryFactory(db_session)
    svc = AuthService(repos.users, SessionStore())

    with pytest.raises(InvalidCredentials):
        svc.authenticate("alice", "wrong")


def test_auth_service_authenticate_unknown_user(db_session: Session) -> None:
    repos = RepositoryFactory(db_session)
    svc = AuthService(repos.users, SessionStore())

    with pytest.raises(InvalidCredentials):
        svc.authenticate("ghost", "whatever")


def test_auth_service_login_issues_resolvable_token(db_session: Session) -> None:
    user = _make_user(db_session, "alice", "hunter2")
    repos = RepositoryFactory(db_session)
    store = SessionStore()
    svc = AuthService(repos.users, store)

    token, logged_in = svc.login("alice", "hunter2")
    assert logged_in.user_id == user.user_id
    assert store.resolve(token) == user.user_id


# --------------------------------------------------------------------------- #
# API: login + auth gate
# --------------------------------------------------------------------------- #


@pytest.fixture()
def client(session_factory) -> Iterator[tuple[TestClient, SessionStore, object]]:
    """Build an app wiring the auth router plus a gated test endpoint.

    The gated endpoint writes an Image when reached; tests assert that an
    unauthenticated call never reaches it (nothing stored).
    """
    store = SessionStore()

    def _override_db_session() -> Iterator[Session]:
        with session_factory() as s:
            yield s

    def _override_store() -> SessionStore:
        return store

    app = FastAPI()
    app.include_router(auth_router)

    @app.post("/protected/submit")
    def protected_submit(
        user: User = Depends(get_current_user),
        repos: RepositoryFactory = Depends(auth_api.get_repositories),
    ) -> dict[str, str]:
        # Only reachable when authenticated. Persists data to prove the gate
        # runs *before* any storage on unauthenticated requests.
        image = repos.images.add(
            Image(
                user_id=user.user_id,
                storage_path="images/test.png",
                format=ImageFormat.PNG,
                size_bytes=10,
                status=ImageStatus.ACCEPTED,
            )
        )
        repos.session.commit()
        return {"image_id": str(image.image_id)}

    app.dependency_overrides[get_db_session] = _override_db_session
    app.dependency_overrides[get_session_store] = _override_store

    with TestClient(app) as c:
        yield c, store, session_factory


def _count(session: Session) -> int:
    from sqlalchemy import func, select

    return session.scalar(select(func.count()).select_from(Image)) or 0


def test_login_endpoint_returns_token(client) -> None:
    c, store, factory = client
    with factory() as s:
        _make_user(s, "alice", "hunter2")

    resp = c.post("/auth/login", json={"username": "alice", "password": "hunter2"})
    assert resp.status_code == status.HTTP_200_OK
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert store.resolve(body["access_token"]) is not None


def test_login_endpoint_rejects_bad_credentials(client) -> None:
    c, store, factory = client
    with factory() as s:
        _make_user(s, "alice", "hunter2")

    resp = c.post("/auth/login", json={"username": "alice", "password": "nope"})
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED
    assert resp.json()["detail"] == INVALID_CREDENTIALS_MESSAGE


def test_authenticated_request_passes_gate(client) -> None:
    c, store, factory = client
    with factory() as s:
        _make_user(s, "alice", "hunter2")

    login = c.post("/auth/login", json={"username": "alice", "password": "hunter2"})
    token = login.json()["access_token"]

    resp = c.post(
        "/protected/submit",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == status.HTTP_200_OK
    with factory() as s:
        assert _count(s) == 1


def test_unauthenticated_request_rejected_and_stores_nothing(client) -> None:
    c, store, factory = client
    with factory() as s:
        _make_user(s, "alice", "hunter2")
        assert _count(s) == 0

    # No Authorization header / no session cookie.
    resp = c.post("/protected/submit")
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED
    assert resp.json()["detail"] == AUTH_REQUIRED_MESSAGE

    # Nothing was persisted by the rejected submission (Req 1.5, 10.4).
    with factory() as s:
        assert _count(s) == 0


def test_invalid_token_rejected_and_stores_nothing(client) -> None:
    c, store, factory = client
    with factory() as s:
        _make_user(s, "alice", "hunter2")

    resp = c.post(
        "/protected/submit",
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED
    assert resp.json()["detail"] == AUTH_REQUIRED_MESSAGE
    with factory() as s:
        assert _count(s) == 0


# --------------------------------------------------------------------------- #
# API: register / logout / me / session cookie (Option A auth flow)
# --------------------------------------------------------------------------- #


def test_register_creates_user_and_sets_session_cookie(client) -> None:
    c, store, factory = client

    resp = c.post(
        "/auth/register",
        json={"username": "carol", "email": "carol@example.com", "password": "s3cret"},
    )
    assert resp.status_code == status.HTTP_201_CREATED
    body = resp.json()
    assert store.resolve(body["access_token"]) is not None
    # The session cookie is set so the browser authenticates automatically.
    assert "session" in resp.cookies
    # The user was persisted.
    with factory() as s:
        assert s.query(User).filter(User.username == "carol").one_or_none() is not None


def test_register_rejects_duplicate_username(client) -> None:
    c, store, factory = client
    with factory() as s:
        _make_user(s, "dave", "pw12345")

    resp = c.post(
        "/auth/register",
        json={"username": "dave", "email": "dave2@example.com", "password": "other"},
    )
    assert resp.status_code == status.HTTP_409_CONFLICT


def test_login_sets_session_cookie_that_authenticates(client) -> None:
    c, store, factory = client
    with factory() as s:
        _make_user(s, "erin", "hunter2")

    login = c.post("/auth/login", json={"username": "erin", "password": "hunter2"})
    assert login.status_code == status.HTTP_200_OK
    assert "session" in login.cookies

    # TestClient persists cookies on the client, so the gated call authenticates
    # via the cookie alone (no Authorization header).
    resp = c.post("/protected/submit")
    assert resp.status_code == status.HTTP_200_OK


def test_me_returns_profile_when_authenticated(client) -> None:
    c, store, factory = client
    with factory() as s:
        _make_user(s, "frank", "hunter2")

    c.post("/auth/login", json={"username": "frank", "password": "hunter2"})
    resp = c.get("/auth/me")
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["username"] == "frank"


def test_me_unauthenticated_is_rejected(client) -> None:
    c, store, factory = client
    resp = c.get("/auth/me")
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


def test_logout_revokes_session(client) -> None:
    c, store, factory = client
    with factory() as s:
        _make_user(s, "grace", "hunter2")

    login = c.post("/auth/login", json={"username": "grace", "password": "hunter2"})
    token = login.json()["access_token"]
    assert store.resolve(token) is not None

    resp = c.post("/auth/logout")
    assert resp.status_code == status.HTTP_204_NO_CONTENT
    # Token no longer resolves after logout.
    assert store.resolve(token) is None
