"""Property-based test for recognition timeout retaining the image (design Property 5).

# Feature: handwriting-notes-digitizer, Property 5: Recognition timeout retains the image

*For any* recognition attempt whose processing exceeds the time budget, the
service returns a timeout error result and the accepted image remains stored
and associated with the user.

**Validates: Requirements 2.6**

Strategy
--------
We exercise :func:`~app.services.recognition_service.run_recognition` directly
(no HTTP layer needed — the property lives in the service orchestration logic).
A minimal in-memory SQLite database is wired up for each example; the
``recognize_bytes`` function in the ML layer is patched to sleep for a duration
that reliably exceeds the timeout budget so the service always times out.

For every generated example we assert all four properties from the task spec:

1. The returned outcome is ``TIMEOUT`` (not SUCCESS, NO_TEXT, or FAILED).
2. The image is still present in the DB after the timeout (not deleted).
3. The image is still associated with the submitting user.
4. The image status in the DB has been updated to ``TIMEOUT``.

Generators vary:
- image byte payloads (different sizes, simulating varied uploads)
- user context (unique per example via UUIDs)

The timeout budget is fixed at 0.05 s and the patched sleep is 0.5 s (10×
margin), which eliminates flakiness from OS thread-scheduling variance while
keeping each iteration fast.
"""

from __future__ import annotations

import tempfile
import time
import uuid
from contextlib import contextmanager
from typing import Iterator
from unittest.mock import patch

from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy.pool import StaticPool

from app.data import (
    Image,
    ImageFormat,
    ImageStatus,
    RepositoryFactory,
    User,
    create_all,
    create_db_engine,
    create_session_factory,
)
from app.data.object_store import FilesystemObjectStore
from app.services.recognition_service import RecognitionOutcome, run_recognition

# ---------------------------------------------------------------------------
# Timing constants
# ---------------------------------------------------------------------------

# The timeout budget passed to run_recognition for every example.
_TIMEOUT_BUDGET: float = 0.05  # seconds

# The patched recognize_bytes sleeps for this long — 10× the budget — so the
# future's deadline always fires before the thread finishes sleeping,
# regardless of scheduling jitter.
_SLEEP_DURATION: float = 0.5  # seconds

# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

# Small byte payloads stored in the object store so run_recognition can read
# them. We never actually decode them (recognize_bytes is patched).
_image_bytes_strategy = st.binary(min_size=1, max_size=512)

# Combined scenario: everything we need to construct one example.
_scenario = st.fixed_dictionaries(
    {
        "image_bytes": _image_bytes_strategy,
    }
)


# ---------------------------------------------------------------------------
# In-memory DB + object-store context manager
# ---------------------------------------------------------------------------


@contextmanager
def _fresh_db() -> Iterator[tuple[RepositoryFactory, FilesystemObjectStore, User, Image]]:
    """Yield a fresh in-memory DB with one user and one ACCEPTED image."""
    engine = create_db_engine("sqlite://", poolclass=StaticPool)
    create_all(engine)
    factory = create_session_factory(engine)

    with tempfile.TemporaryDirectory() as tmpdir:
        store = FilesystemObjectStore(root=tmpdir)

        with factory() as session:
            repos = RepositoryFactory(session)

            user = repos.users.add(
                User(
                    username=f"u-{uuid.uuid4().hex[:8]}",
                    email=f"{uuid.uuid4().hex[:8]}@test.example",
                    password_hash="placeholder",
                )
            )
            session.flush()

            # Store a placeholder so object_store.get() succeeds inside
            # run_recognition before it calls the (patched) recognize_bytes.
            storage_ref = store.put_image_bytes(
                str(user.user_id),
                b"placeholder",
                suffix=".png",
            )

            image = repos.images.add(
                Image(
                    user_id=user.user_id,
                    storage_path=storage_ref,
                    format=ImageFormat.PNG,
                    size_bytes=len(b"placeholder"),
                    status=ImageStatus.ACCEPTED,
                )
            )
            session.commit()

            yield repos, store, user, image


# ---------------------------------------------------------------------------
# Property 5: Recognition timeout retains the image
# ---------------------------------------------------------------------------


# Feature: handwriting-notes-digitizer, Property 5: Recognition timeout retains the image
@given(scenario=_scenario)
def test_recognition_timeout_retains_image(scenario: dict) -> None:
    """Validates: Requirements 2.6

    For any recognition attempt that times out:
    - The service returns a TIMEOUT outcome (not SUCCESS, NO_TEXT, or FAILED)
    - The image is retained in the DB (not deleted)
    - The image is still associated with the submitting user
    - The image status in the DB is updated to TIMEOUT
    """

    # Patch recognize_bytes to sleep for much longer than the timeout budget.
    # The 10× margin ensures the future's deadline fires first regardless of
    # OS thread-scheduling variance, eliminating flakiness.
    def _slow_recognize(image_bytes: bytes, adapter=None, model=None, config=None):
        time.sleep(_SLEEP_DURATION)

    with _fresh_db() as (repos, store, user, image):
        image_id = image.image_id
        user_id = user.user_id

        with patch(
            "app.services.recognition_service.recognize_bytes",
            side_effect=_slow_recognize,
        ):
            result = run_recognition(
                image=image,
                repos=repos,
                object_store=store,
                adapter=None,
                timeout=_TIMEOUT_BUDGET,
            )

        # --- Assert 1: outcome is TIMEOUT, not SUCCESS / NO_TEXT / FAILED ---
        assert result.outcome == RecognitionOutcome.TIMEOUT, (
            f"Expected TIMEOUT outcome but got {result.outcome!r}"
        )
        assert result.outcome != RecognitionOutcome.SUCCESS
        assert result.outcome != RecognitionOutcome.NO_TEXT
        assert result.outcome != RecognitionOutcome.FAILED

        # result carries the correct image_id.
        assert result.image_id == image_id

        # An error message must be present for a TIMEOUT result (Req 2.6).
        assert result.error_message is not None and len(result.error_message) > 0

        # No recognized lines on a timeout.
        assert result.lines == []

        # --- Assert 2 & 3: image retained in the DB, still owned by the user ---
        retrieved_image = repos.images.get(user_id, image_id)
        assert retrieved_image is not None, (
            "Image was removed from the DB after a timeout — it must be retained (Req 2.6)"
        )
        assert retrieved_image.image_id == image_id
        assert retrieved_image.user_id == user_id, (
            "Image is no longer associated with the submitting user after a timeout"
        )

        # --- Assert 4: image status updated to TIMEOUT ----------------------
        assert retrieved_image.status == ImageStatus.TIMEOUT, (
            f"Expected image status TIMEOUT but got {retrieved_image.status!r}"
        )
