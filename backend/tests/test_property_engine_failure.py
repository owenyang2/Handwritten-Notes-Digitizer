"""Property-based test for engine failure behavior (design Property 6).

# Feature: handwriting-notes-digitizer, Property 6: Engine failure leaves stored data unchanged

This test exercises the Recognition_Service's failure-handling path against
the design's **Property 6**:

    For any recognition attempt in which the HTR engine fails (raises an
    exception), the service returns a recognition-failed error (FAILED outcome)
    and the user's stored data is identical before and after the attempt.

**Validates: Requirements 2.7**

Strategy
--------
A fresh in-memory SQLite database is initialized with a seeded authenticated
user and a single ACCEPTED image for each Hypothesis example.  The raw image
bytes are stored in an :class:`InMemoryObjectStore`.  Hypothesis generates
arbitrary exception types and messages; the test patches
``app.services.recognition_service.recognize_bytes`` to raise the generated
exception, then calls :func:`run_recognition` directly and asserts:

1. The returned outcome is :attr:`RecognitionOutcome.FAILED`.
2. The image status in the database is still ``ACCEPTED`` (unchanged).
3. No :class:`RecognizedWord` rows were created for the image.
4. The full persisted-data snapshot (image count, word count, image status) is
   byte-for-byte identical before and after the attempt.

The test runs ≥100 iterations (enforced by the project-wide Hypothesis
``default`` profile configured in ``tests/conftest.py``).
"""

from __future__ import annotations

import io
from typing import Type
from unittest.mock import patch

from hypothesis import given
from hypothesis import strategies as st
from PIL import Image as PILImage
from sqlalchemy import func, select
from sqlalchemy.pool import StaticPool

from app.data import (
    Base,
    Image,
    ImageFormat,
    ImageStatus,
    RecognizedWord,
    RepositoryFactory,
    User,
    create_all,
    create_db_engine,
    create_session_factory,
)
from app.data.object_store import ObjectNotFoundError, ObjectStore
from app.services.auth import hash_password
from app.services.recognition_service import RecognitionOutcome, run_recognition


# ---------------------------------------------------------------------------
# Minimal in-memory object store
# ---------------------------------------------------------------------------


class _InMemoryObjectStore(ObjectStore):
    """Dict-backed object store for test isolation (no filesystem I/O)."""

    def __init__(self) -> None:
        self._data: dict[str, bytes] = {}

    def put(self, reference: str, data: bytes) -> str:
        self._data[reference] = data
        return reference

    def get(self, reference: str) -> bytes:
        try:
            return self._data[reference]
        except KeyError:
            raise ObjectNotFoundError(reference)

    def open(self, reference: str):
        return io.BytesIO(self.get(reference))

    def exists(self, reference: str) -> bool:
        return reference in self._data

    def delete(self, reference: str) -> None:
        self._data.pop(reference, None)


# ---------------------------------------------------------------------------
# Image helper
# ---------------------------------------------------------------------------


def _tiny_png_bytes() -> bytes:
    """Return minimal valid 8×8 greyscale PNG bytes."""
    buf = io.BytesIO()
    PILImage.new("L", (8, 8), color=200).save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Persisted-data snapshot
# ---------------------------------------------------------------------------


def _snapshot(session_factory, image_id) -> dict:
    """Capture the persisted-data state relevant to engine-failure isolation."""
    with session_factory() as s:
        image_count = int(
            s.scalar(select(func.count()).select_from(Image)) or 0
        )
        word_count = int(
            s.scalar(select(func.count()).select_from(RecognizedWord)) or 0
        )
        image = s.get(Image, image_id)
        image_status = image.status if image is not None else None
    return {
        "image_count": image_count,
        "word_count": word_count,
        "image_status": image_status,
    }


# ---------------------------------------------------------------------------
# Exception type strategy
# ---------------------------------------------------------------------------

# A representative set of exception types the HTR engine might raise.
_EXCEPTION_TYPES: list[Type[Exception]] = [
    RuntimeError,
    ValueError,
    MemoryError,
    TypeError,
    OSError,
    IOError,
    Exception,
]

_exception_type_st: st.SearchStrategy[Type[Exception]] = st.sampled_from(
    _EXCEPTION_TYPES
)

# Arbitrary exception messages (printable ASCII, may be empty).
_message_st: st.SearchStrategy[str] = st.text(
    alphabet=st.characters(min_codepoint=32, max_codepoint=126),
    min_size=0,
    max_size=128,
)


# ---------------------------------------------------------------------------
# Property test
# ---------------------------------------------------------------------------


# Feature: handwriting-notes-digitizer, Property 6: Engine failure leaves stored data unchanged
@given(exc_type=_exception_type_st, exc_message=_message_st)
def test_engine_failure_leaves_stored_data_unchanged(
    exc_type: Type[Exception],
    exc_message: str,
) -> None:
    """Validates: Requirements 2.7.

    For any recognition attempt where the HTR engine raises an exception:
    - The service returns outcome FAILED.
    - The image status remains ACCEPTED (unchanged).
    - No RecognizedWord rows were created.
    - The persisted-data state (image count, word count, image status) is
      identical before and after the attempt.
    """
    # --- Build a fresh in-memory DB for this example ----------------------
    engine = create_db_engine("sqlite://", poolclass=StaticPool)
    create_all(engine)
    session_factory = create_session_factory(engine)
    store = _InMemoryObjectStore()

    # Seed: one user + one ACCEPTED image.
    with session_factory() as s:
        user = User(
            username="testuser",
            email="testuser@example.com",
            password_hash=hash_password("pw"),
        )
        s.add(user)
        s.flush()

        img_bytes = _tiny_png_bytes()
        storage_path = store.put(
            f"images/{user.user_id}/img.png", img_bytes
        )
        image = Image(
            user_id=user.user_id,
            storage_path=storage_path,
            format=ImageFormat.PNG,
            size_bytes=len(img_bytes),
            status=ImageStatus.ACCEPTED,
        )
        s.add(image)
        s.commit()
        image_id = image.image_id

    # --- Snapshot before the attempt --------------------------------------
    before = _snapshot(session_factory, image_id)

    # --- Run recognition with engine patched to raise ---------------------
    raised_exc = exc_type(exc_message) if exc_message else exc_type()

    with session_factory() as s:
        repos = RepositoryFactory(s)
        image_obj = repos.images.get(user.user_id, image_id)

        with patch(
            "app.services.recognition_service.recognize_bytes",
            side_effect=raised_exc,
        ):
            result = run_recognition(image_obj, repos, store)

    # --- Assertions -------------------------------------------------------

    # 1. Outcome must be FAILED (Req 2.7).
    assert result.outcome == RecognitionOutcome.FAILED, (
        f"Expected FAILED outcome when engine raises {exc_type.__name__}, "
        f"got {result.outcome!r}"
    )

    # 2. error_message must be set (service communicates the failure).
    assert result.error_message is not None and result.error_message != "", (
        "Expected a non-empty error_message on engine failure"
    )

    # 3. image_id in result matches the image that was processed.
    assert result.image_id == image_id

    # 4. Persisted-data state is identical before and after (Req 2.7).
    after = _snapshot(session_factory, image_id)
    assert after == before, (
        f"Persisted data changed after engine failure.\n"
        f"  before={before}\n"
        f"  after ={after}\n"
        f"  exc_type={exc_type.__name__!r}, exc_message={exc_message!r}"
    )

    # 5. Image status is still ACCEPTED (not modified by the failed attempt).
    assert after["image_status"] == ImageStatus.ACCEPTED, (
        f"Image status was changed from ACCEPTED to {after['image_status']!r} "
        f"despite engine failure (exc_type={exc_type.__name__!r})"
    )

    # 6. No RecognizedWord rows were created.
    assert after["word_count"] == 0, (
        f"RecognizedWord rows were created despite engine failure "
        f"(word_count={after['word_count']}, exc_type={exc_type.__name__!r})"
    )

    # Clean up (in-memory DB is GC'd, but explicit drop keeps things tidy).
    Base.metadata.drop_all(engine)
