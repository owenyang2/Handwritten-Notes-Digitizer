"""Property-based test for image ownership (design Property 3).

# Feature: handwriting-notes-digitizer, Property 3: Accepted images are owned by their submitter

This test exercises ``POST /images`` against **Property 3** from design.md:

    For any accepted image submission by an authenticated user, the stored
    image's owning user identifier equals the submitting user's identifier.

**Validates: Requirements 1.6**

Strategy
--------
Hypothesis generates:
* A random authenticated user (varying username/email/password to cover
  different identities).
* A valid image payload — a tiny PNG or JPEG synthesised at runtime so the
  validator always accepts it.

For each generated example the test:
1. Registers the user in an isolated in-memory database.
2. Authenticates and obtains a bearer token via POST /auth/login.
3. Submits the image via POST /images with that token.
4. Asserts the endpoint returns HTTP 201 (accepted).
5. Fetches the stored Image row and asserts Image.user_id == user.user_id.

The test uses the *real* ``submit_image`` endpoint wired to the real auth
dependency, repository layer, and (in-memory) object store so no mocking hides
ownership bugs. The only overrides are the database session and object store
dependencies, which are pointed at isolated, per-session in-memory fixtures
rather than the production backends.
"""

from __future__ import annotations

import io
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
from hypothesis import given, settings
from hypothesis import strategies as st
from PIL import Image as PILImage
from sqlalchemy import select
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
from app.data.object_store import ObjectStore
from app.services import auth_api
from app.services.auth import SessionStore, hash_password
from app.services.auth_api import (
    get_db_session,
    get_session_store,
    router as auth_router,
)
from app.services.images_api import get_object_store, router as images_router


# --------------------------------------------------------------------------- #
# In-memory object store (replaces filesystem for tests)
# --------------------------------------------------------------------------- #


class _InMemoryObjectStore(ObjectStore):
    """Trivial in-memory object store used to avoid touching the filesystem."""

    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}

    def put(self, reference: str, data: bytes) -> str:
        self._store[reference] = data
        return reference

    def get(self, reference: str) -> bytes:
        return self._store[reference]

    def open(self, reference: str):  # type: ignore[override]
        return io.BytesIO(self._store[reference])

    def exists(self, reference: str) -> bool:
        return reference in self._store

    def delete(self, reference: str) -> None:
        self._store.pop(reference, None)


# --------------------------------------------------------------------------- #
# Test application factory
# --------------------------------------------------------------------------- #


def _build_app() -> tuple[TestClient, Any]:
    """Build the test FastAPI app with isolated in-memory DB + object store.

    Returns the TestClient and the session factory so tests can inspect
    persisted rows directly.
    """
    engine = create_db_engine("sqlite://", poolclass=StaticPool)
    create_all(engine)
    factory = create_session_factory(engine)
    session = factory()
    store = SessionStore(session)
    object_store = _InMemoryObjectStore()

    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(images_router)

    def _override_db_session():
        with factory() as s:
            yield s

    app.dependency_overrides[get_db_session] = _override_db_session
    app.dependency_overrides[get_session_store] = lambda: store
    app.dependency_overrides[get_object_store] = lambda: object_store

    return TestClient(app), factory


# Build once; Hypothesis reuses across all generated examples.
_CLIENT, _FACTORY = _build_app()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _make_valid_png(width: int = 8, height: int = 8) -> bytes:
    """Return bytes of a minimal valid PNG image."""
    buf = io.BytesIO()
    PILImage.new("RGB", (width, height), color=(100, 150, 200)).save(buf, format="PNG")
    return buf.getvalue()


def _make_valid_jpeg(width: int = 8, height: int = 8) -> bytes:
    """Return bytes of a minimal valid JPEG image."""
    buf = io.BytesIO()
    PILImage.new("RGB", (width, height), color=(200, 100, 50)).save(buf, format="JPEG")
    return buf.getvalue()


def _register_and_login(username: str, password: str) -> tuple[uuid.UUID, str]:
    """Create a user in the shared in-memory DB and return (user_id, token)."""
    with _FACTORY() as s:
        repos = RepositoryFactory(s)
        user = repos.users.add(
            User(
                username=username,
                email=f"{username}@ownership-test.example",
                password_hash=hash_password(password),
            )
        )
        s.commit()
        user_id = user.user_id

    resp = _CLIENT.post(
        "/auth/login", json={"username": username, "password": password}
    )
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    token = resp.json()["access_token"]
    return user_id, token


def _get_stored_image(image_id: uuid.UUID) -> Image:
    """Fetch the Image row from the DB by image_id."""
    with _FACTORY() as s:
        row = s.get(Image, image_id)
    assert row is not None, f"Image {image_id} not found in database"
    return row


# --------------------------------------------------------------------------- #
# Hypothesis strategies
# --------------------------------------------------------------------------- #

# Generate unique username suffixes to prevent username collisions across
# Hypothesis examples in the same test session. We combine a counter-like UUID
# with a short alphabetic slug so each example gets a fresh user.
_username_slug = st.text(
    alphabet=st.characters(
        whitelist_categories=["Ll"],  # lowercase letters only
        whitelist_characters="",
    ),
    min_size=4,
    max_size=12,
)

# Generate a unique username by appending a UUID hex prefix so there are no
# collisions across the >=100 Hypothesis examples run in a single session.
_unique_username = st.builds(
    lambda slug: f"u{uuid.uuid4().hex[:8]}{slug}",
    slug=_username_slug,
)

_password = st.text(min_size=6, max_size=32)

# Image format: PNG or JPEG.
_image_payload = st.sampled_from(["PNG", "JPEG"]).map(
    lambda fmt: _make_valid_png() if fmt == "PNG" else _make_valid_jpeg()
)


# --------------------------------------------------------------------------- #
# Property 3: Accepted images are owned by their submitter
# --------------------------------------------------------------------------- #

# Feature: handwriting-notes-digitizer, Property 3: Accepted images are owned by their submitter
@given(
    username=_unique_username,
    password=_password,
    image_bytes=_image_payload,
)
@settings(max_examples=25)
def test_accepted_image_is_owned_by_submitter(
    username: str,
    password: str,
    image_bytes: bytes,
) -> None:
    """Validates: Requirements 1.6.

    For any accepted image submission by an authenticated user, the stored
    Image.user_id equals the submitting user's user_id.
    """
    # Register the user and authenticate.
    submitting_user_id, token = _register_and_login(username, password)

    # Submit a valid image as the authenticated user.
    resp = _CLIENT.post(
        "/images",
        files={"file": ("image.png", image_bytes, "image/png")},
        headers={"Authorization": f"Bearer {token}"},
    )

    # The image must be accepted (HTTP 201).
    assert resp.status_code == 201, (
        f"Expected 201 for valid image, got {resp.status_code}: {resp.text}"
    )

    image_id = uuid.UUID(resp.json()["image_id"])

    # Fetch the stored Image row and verify ownership.
    stored_image = _get_stored_image(image_id)

    # Property 3: the stored image's owning user identifier equals the
    # submitting user's identifier (Req 1.6).
    assert stored_image.user_id == submitting_user_id, (
        f"Ownership mismatch: stored Image.user_id={stored_image.user_id!r} "
        f"but submitting user_id={submitting_user_id!r}"
    )
