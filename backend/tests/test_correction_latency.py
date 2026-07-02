"""Integration test for correction persistence latency (Req 4.5).

Verifies that a Training_Pair created by ``submit_correction`` is fully
persisted in the database within 5 seconds of the call being made.

**Validates: Requirements 4.5**

Design note
-----------
Requirement 4.5 states:
    "WHEN a Training_Pair is created, THE Correction_Pipeline SHALL store the
    Training_Pair associated with the user's account within 5 seconds of
    creation."

The ``submit_correction`` service function performs a synchronous commit on the
FastAPI request path, so the pair is durable before the function returns. The
wall-clock elapsed time measured here covers the full path: in-memory object
store write, PIL crop, repository upsert, and SQLite commit. For a realistic
in-memory SQLite setup this is well inside the 5-second budget; the assertion
is therefore a hard correctness check, not a performance target.

Test setup is intentionally identical to ``test_property_create_on_change.py``
(in-memory SQLite via StaticPool, dict-backed object store, PIL-generated PNG)
so the isolation guarantees are the same.
"""

from __future__ import annotations

import io
import time
import uuid
from typing import BinaryIO

import pytest
from PIL import Image as PILImage
from sqlalchemy.pool import StaticPool

from app.data.db import create_all, create_db_engine, create_session_factory
from app.data.enums import ImageFormat, ImageStatus
from app.data.models import Image, User
from app.data.object_store import ObjectNotFoundError, ObjectStore
from app.data.repositories import RepositoryFactory
from app.services.correction_service import (
    Created,
    Region,
    StorageError,
    submit_correction,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LATENCY_LIMIT_SECONDS = 5.0

# ---------------------------------------------------------------------------
# In-memory object store (no filesystem I/O)
# ---------------------------------------------------------------------------


class _InMemoryObjectStore(ObjectStore):
    """Dict-backed object store for fast, isolated integration tests."""

    def __init__(self) -> None:
        self._data: dict[str, bytes] = {}

    def put(self, reference: str, data: bytes) -> str:
        self._data[reference] = data
        return reference

    def get(self, reference: str) -> bytes:
        try:
            return self._data[reference]
        except KeyError as exc:
            raise ObjectNotFoundError(f"Not found: {reference!r}") from exc

    def open(self, reference: str) -> BinaryIO:
        return io.BytesIO(self.get(reference))

    def exists(self, reference: str) -> bool:
        return reference in self._data

    def delete(self, reference: str) -> None:
        self._data.pop(reference, None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_png_bytes(width: int = 100, height: int = 50) -> bytes:
    """Return minimal valid PNG bytes for an in-memory image."""
    buf = io.BytesIO()
    img = PILImage.new("RGB", (width, height), color=(200, 200, 200))
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_test_db():
    """Return a (session, repos, user, image, store) ready for a correction call.

    Uses an in-memory SQLite with StaticPool so all sessions share the same
    schema — required for in-memory SQLite across flush/commit cycles.
    """
    engine = create_db_engine("sqlite://", poolclass=StaticPool)
    create_all(engine)
    factory = create_session_factory(engine)

    session = factory()
    repos = RepositoryFactory(session)

    user = repos.users.add(
        User(
            username=f"latency_user_{uuid.uuid4().hex[:8]}",
            email=f"latency_{uuid.uuid4().hex[:8]}@example.com",
            password_hash="hashed",
        )
    )
    session.flush()

    store = _InMemoryObjectStore()
    png_bytes = _make_png_bytes()
    storage_path = store.put_image_bytes(str(user.user_id), png_bytes, suffix=".png")

    image = repos.images.add(
        Image(
            user_id=user.user_id,
            storage_path=storage_path,
            format=ImageFormat.PNG,
            size_bytes=len(png_bytes),
            status=ImageStatus.ACCEPTED,
        )
    )
    session.commit()

    return session, repos, user, image, store


# ---------------------------------------------------------------------------
# Latency integration tests
# ---------------------------------------------------------------------------


def test_correction_persistence_latency_sample_1():
    """A short corrected word is persisted within 5 seconds (Req 4.5).

    Sample: recognized "hello" → corrected "Hello" (capitalisation fix).
    Measures wall-clock time from the start of ``submit_correction`` to
    the moment the returned ``Created`` result confirms the pair is in the DB.
    """
    session, repos, user, image, store = _make_test_db()
    region = Region(x=0, y=0, width=40, height=20)

    start = time.monotonic()
    result = submit_correction(
        user_id=user.user_id,
        image_id=image.image_id,
        region=region,
        recognized_text="hello",
        corrected_text="Hello",
        repos=repos,
        object_store=store,
    )
    elapsed = time.monotonic() - start

    assert not isinstance(result, StorageError), (
        f"submit_correction returned StorageError: {result.message}"
    )
    assert isinstance(result, Created), (
        f"Expected Created but got {result!r}"
    )

    # Confirm the pair is actually readable from the DB.
    pairs = repos.training_pairs.list_for_user(user.user_id)
    assert len(pairs) == 1, f"Expected 1 pair in DB, found {len(pairs)}"
    assert pairs[0].corrected_text == "Hello"

    assert elapsed < LATENCY_LIMIT_SECONDS, (
        f"Correction persistence took {elapsed:.3f}s, exceeding the {LATENCY_LIMIT_SECONDS}s limit (Req 4.5)"
    )

    session.close()


def test_correction_persistence_latency_sample_2():
    """A multi-word corrected phrase is persisted within 5 seconds (Req 4.5).

    Sample: recognized "teh quik brown fox" → corrected "the quick brown fox".
    Tests a slightly larger corrected_text value (still a single word slot).
    """
    session, repos, user, image, store = _make_test_db()
    region = Region(x=5, y=5, width=60, height=25)

    start = time.monotonic()
    result = submit_correction(
        user_id=user.user_id,
        image_id=image.image_id,
        region=region,
        recognized_text="teh quik brown fox",
        corrected_text="the quick brown fox",
        repos=repos,
        object_store=store,
    )
    elapsed = time.monotonic() - start

    assert not isinstance(result, StorageError), (
        f"submit_correction returned StorageError: {result.message}"
    )
    assert isinstance(result, Created), (
        f"Expected Created but got {result!r}"
    )

    pairs = repos.training_pairs.list_for_user(user.user_id)
    assert len(pairs) == 1, f"Expected 1 pair in DB, found {len(pairs)}"
    assert pairs[0].corrected_text == "the quick brown fox"

    assert elapsed < LATENCY_LIMIT_SECONDS, (
        f"Correction persistence took {elapsed:.3f}s, exceeding the {LATENCY_LIMIT_SECONDS}s limit (Req 4.5)"
    )

    session.close()


def test_correction_persistence_latency_sample_3():
    """A longer corrected text (100 characters) is persisted within 5 seconds (Req 4.5).

    Sample: a 100-character recognised OCR artefact → corrected English sentence.
    Exercises a more realistic payload size while still being well within the
    10,000-character limit (Req 4.1).
    """
    session, repos, user, image, store = _make_test_db()
    region = Region(x=10, y=10, width=80, height=30)

    recognized = "Th1s 1s a r3cogniz3d t3xt w1th many err0rs that n33d t0 b3 c0rr3ct3d by th3 us3r in th3 3d1t0r."
    corrected  = "This is a recognized text with many errors that need to be corrected by the user in the editor."

    start = time.monotonic()
    result = submit_correction(
        user_id=user.user_id,
        image_id=image.image_id,
        region=region,
        recognized_text=recognized,
        corrected_text=corrected,
        repos=repos,
        object_store=store,
    )
    elapsed = time.monotonic() - start

    assert not isinstance(result, StorageError), (
        f"submit_correction returned StorageError: {result.message}"
    )
    assert isinstance(result, Created), (
        f"Expected Created but got {result!r}"
    )

    pairs = repos.training_pairs.list_for_user(user.user_id)
    assert len(pairs) == 1, f"Expected 1 pair in DB, found {len(pairs)}"
    assert pairs[0].corrected_text == corrected

    assert elapsed < LATENCY_LIMIT_SECONDS, (
        f"Correction persistence took {elapsed:.3f}s, exceeding the {LATENCY_LIMIT_SECONDS}s limit (Req 4.5)"
    )

    session.close()
