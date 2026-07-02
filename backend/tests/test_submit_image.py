"""Unit and integration tests for the POST /images endpoint (task 4.2).

Covers:
* Authenticated upload of a valid PNG — 201 with image_id; bytes in the object
  store; Image record in DB owned by the submitting user (Req 1.1, 1.6, 10.4).
* Authenticated upload of a valid JPEG — 201 accepted.
* Upload of an unsupported format (GIF) — 422 with reason=unsupported_format.
* Upload of oversized payload — 422 with reason=size_limit.
* Upload of empty/undecodable bytes — 422 with reason=invalid_image.
* Unauthenticated upload — 401, nothing stored (Req 1.5, 10.4).
* Invalid token — 401, nothing stored.
"""

from __future__ import annotations

import uuid
from io import BytesIO
from typing import Iterator

import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient
from PIL import Image as PILImage
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.data import (
    Base,
    Image,
    RepositoryFactory,
    User,
    create_all,
    create_db_engine,
    create_session_factory,
)
from app.data.object_store import FilesystemObjectStore, ObjectStore
from app.services.auth import SessionStore, hash_password
from app.services import auth_api
from app.services.auth_api import get_db_session, get_session_store, router as auth_router
from app.services.images_api import get_object_store, router as images_router


# --------------------------------------------------------------------------- #
# Image encoding helpers
# --------------------------------------------------------------------------- #


def _encode(image_format: str, *, size: tuple[int, int] = (8, 8)) -> bytes:
    """Render a tiny solid-colour image and return encoded bytes."""
    img = PILImage.new("RGB", size, color=(100, 150, 200))
    buf = BytesIO()
    img.save(buf, format=image_format)
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture()
def session_factory():
    engine = create_db_engine("sqlite://", poolclass=StaticPool)
    create_all(engine)
    factory = create_session_factory(engine)
    yield factory
    Base.metadata.drop_all(engine)


@pytest.fixture()
def object_store(tmp_path) -> ObjectStore:
    """Isolated filesystem object store backed by a temporary directory."""
    return FilesystemObjectStore(root=tmp_path / "store")


@pytest.fixture()
def client(session_factory, object_store) -> Iterator[tuple[TestClient, SessionStore]]:
    """Build an app wiring auth + images routers with test overrides."""
    store = SessionStore()

    def _override_db() -> Iterator[Session]:
        with session_factory() as s:
            yield s

    def _override_session_store() -> SessionStore:
        return store

    def _override_object_store() -> ObjectStore:
        return object_store

    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(images_router)

    app.dependency_overrides[get_db_session] = _override_db
    app.dependency_overrides[get_session_store] = _override_session_store
    app.dependency_overrides[get_object_store] = _override_object_store

    with TestClient(app, raise_server_exceptions=True) as c:
        yield c, store, session_factory


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _register_and_login(client: TestClient, session_factory, username: str = "alice") -> str:
    """Create a user and return a valid bearer token."""
    with session_factory() as s:
        user = User(
            username=username,
            email=f"{username}@example.com",
            password_hash=hash_password("hunter2"),
        )
        s.add(user)
        s.commit()
    resp = client.post("/auth/login", json={"username": username, "password": "hunter2"})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _count_images(session_factory) -> int:
    with session_factory() as s:
        return s.scalar(select(func.count()).select_from(Image)) or 0


def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------------------- #
# Acceptance tests (Req 1.1, 1.6)
# --------------------------------------------------------------------------- #


def test_submit_valid_png_returns_201_with_image_id(client):
    c, store, sf = client
    token = _register_and_login(c, sf)

    resp = c.post(
        "/images",
        files={"file": ("photo.png", _encode("PNG"), "image/png")},
        headers=_auth_header(token),
    )

    assert resp.status_code == status.HTTP_201_CREATED, resp.text
    body = resp.json()
    assert body["status"] == "accepted"
    uuid.UUID(body["image_id"])  # validates it is a UUID


def test_submit_valid_jpeg_returns_201(client):
    c, store, sf = client
    token = _register_and_login(c, sf)

    resp = c.post(
        "/images",
        files={"file": ("photo.jpg", _encode("JPEG"), "image/jpeg")},
        headers=_auth_header(token),
    )

    assert resp.status_code == status.HTTP_201_CREATED, resp.text
    assert resp.json()["status"] == "accepted"


def test_accepted_image_is_stored_in_db(client):
    c, store, sf = client
    token = _register_and_login(c, sf)

    assert _count_images(sf) == 0
    c.post(
        "/images",
        files={"file": ("photo.png", _encode("PNG"), "image/png")},
        headers=_auth_header(token),
    )
    assert _count_images(sf) == 1


def test_accepted_image_is_owned_by_submitting_user(client):
    """Image.user_id must equal the authenticated user's user_id (Req 1.6)."""
    c, store, sf = client
    token = _register_and_login(c, sf)

    # Retrieve the user_id from the login response.
    login_resp = c.post("/auth/login", json={"username": "alice", "password": "hunter2"})
    user_id = uuid.UUID(login_resp.json()["user_id"])

    c.post(
        "/images",
        files={"file": ("photo.png", _encode("PNG"), "image/png")},
        headers=_auth_header(token),
    )

    with sf() as s:
        images = list(s.scalars(select(Image)).all())
    assert len(images) == 1
    assert images[0].user_id == user_id


def test_accepted_image_bytes_are_written_to_object_store(client, object_store):
    c, store, sf = client
    token = _register_and_login(c, sf)

    resp = c.post(
        "/images",
        files={"file": ("photo.png", _encode("PNG"), "image/png")},
        headers=_auth_header(token),
    )
    assert resp.status_code == 201

    # There should now be exactly one stored object.
    with sf() as s:
        image = s.scalars(select(Image)).one()
    assert object_store.exists(image.storage_path)


def test_storage_path_on_image_record_is_non_empty(client):
    c, store, sf = client
    token = _register_and_login(c, sf)

    c.post(
        "/images",
        files={"file": ("photo.png", _encode("PNG"), "image/png")},
        headers=_auth_header(token),
    )

    with sf() as s:
        image = s.scalars(select(Image)).one()
    assert image.storage_path and len(image.storage_path) > 0


# --------------------------------------------------------------------------- #
# Rejection tests (Req 1.2, 1.3, 1.4)
# --------------------------------------------------------------------------- #


def test_unsupported_format_returns_422_with_reason(client):
    c, store, sf = client
    token = _register_and_login(c, sf)

    resp = c.post(
        "/images",
        files={"file": ("anim.gif", _encode("GIF"), "image/gif")},
        headers=_auth_header(token),
    )

    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    detail = resp.json()["detail"]
    assert detail["reason"] == "unsupported_format"
    assert _count_images(sf) == 0


def test_oversized_payload_returns_422_with_size_limit_reason(client, tmp_path):
    """Anything that exceeds 10 MB must be rejected with reason=size_limit."""
    c, store, sf = client
    token = _register_and_login(c, sf)

    # Build a minimal payload that the validator will see as over-limit.
    # We rely on the validator's max_bytes default (10 MB); a valid large
    # PNG that is > 10 MB is hard to construct cheaply, so we encode a tiny
    # PNG and then replace the bytes with 10 MB + 1 of zeros and test that
    # the validator path is hit.  The raw bytes won't decode as an image but
    # the size check runs first.
    oversized = b"\x00" * (10 * 1024 * 1024 + 1)

    resp = c.post(
        "/images",
        files={"file": ("big.png", oversized, "image/png")},
        headers=_auth_header(token),
    )

    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    detail = resp.json()["detail"]
    assert detail["reason"] == "size_limit"
    assert _count_images(sf) == 0


def test_empty_file_returns_422_with_invalid_image_reason(client):
    c, store, sf = client
    token = _register_and_login(c, sf)

    resp = c.post(
        "/images",
        files={"file": ("empty.png", b"", "image/png")},
        headers=_auth_header(token),
    )

    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    detail = resp.json()["detail"]
    assert detail["reason"] == "invalid_image"
    assert _count_images(sf) == 0


def test_undecodable_bytes_return_422_with_invalid_image_reason(client):
    c, store, sf = client
    token = _register_and_login(c, sf)

    resp = c.post(
        "/images",
        files={"file": ("corrupt.png", b"not an image at all", "image/png")},
        headers=_auth_header(token),
    )

    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert resp.json()["detail"]["reason"] == "invalid_image"


# --------------------------------------------------------------------------- #
# Auth gate tests (Req 1.5, 10.4)
# --------------------------------------------------------------------------- #


def test_unauthenticated_upload_returns_401(client):
    c, store, sf = client
    # No Authorization header.
    resp = c.post(
        "/images",
        files={"file": ("photo.png", _encode("PNG"), "image/png")},
    )
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


def test_unauthenticated_upload_stores_nothing(client):
    c, store, sf = client
    assert _count_images(sf) == 0

    c.post(
        "/images",
        files={"file": ("photo.png", _encode("PNG"), "image/png")},
    )

    assert _count_images(sf) == 0


def test_invalid_token_returns_401(client):
    c, store, sf = client
    resp = c.post(
        "/images",
        files={"file": ("photo.png", _encode("PNG"), "image/png")},
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


def test_invalid_token_stores_nothing(client):
    c, store, sf = client
    c.post(
        "/images",
        files={"file": ("photo.png", _encode("PNG"), "image/png")},
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert _count_images(sf) == 0


def test_two_users_each_own_their_own_images(client):
    """Images from different users are kept separate (Req 10.5, defense in depth)."""
    c, store, sf = client
    token_alice = _register_and_login(c, sf, "alice")
    token_bob = _register_and_login(c, sf, "bob")

    c.post(
        "/images",
        files={"file": ("a.png", _encode("PNG"), "image/png")},
        headers=_auth_header(token_alice),
    )
    c.post(
        "/images",
        files={"file": ("b.png", _encode("PNG"), "image/png")},
        headers=_auth_header(token_bob),
    )

    with sf() as s:
        all_images = list(s.scalars(select(Image)).all())

    assert len(all_images) == 2
    owner_ids = {img.user_id for img in all_images}
    assert len(owner_ids) == 2, "Each image must be owned by a different user"
