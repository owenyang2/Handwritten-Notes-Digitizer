"""Property-based test for the Correction_Pipeline storage-failure rule (design Property 10).

# Feature: handwriting-notes-digitizer, Property 10: Correction storage failure preserves prior pairs

For any correction attempt that results in a storage failure, the pipeline
returns a ``StorageError`` and the user's previously stored training pairs are
identical (in identity, text, and count) before and after the failed attempt.

**Validates: Requirements 4.6**

Strategy
--------
We use a ``FailingObjectStore`` that always raises an ``OSError`` on every
operation. This ensures any call that touches the object store (fetching the
source image bytes or storing the region crop) will fail deterministically.

For each generated scenario we:

1. Set up an in-memory SQLite DB with a real user and image.
2. Optionally seed some prior training pairs via a *working* object store so
   there is pre-existing state to preserve.
3. Switch to the ``FailingObjectStore`` and call ``submit_correction`` with a
   text change (recognized ≠ corrected, so a write would be attempted).
4. Assert:
   a. The return value is ``StorageError``.
   b. The set of training pairs after the attempt is identical to the set
      before (no partial writes, no row additions, no mutations).

All DB operations use in-memory SQLite with ``StaticPool`` for isolation and
speed. A minimum of 100 iterations are run (enforced by the ``conftest.py``
default Hypothesis profile).
"""

from __future__ import annotations

import io
import uuid
from typing import BinaryIO

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from PIL import Image as PILImage
from sqlalchemy.pool import StaticPool

from app.data.db import create_all, create_db_engine, create_session_factory
from app.data.enums import ImageFormat, ImageStatus, TrainingPairOrigin
from app.data.models import Image, TrainingPair, User
from app.data.object_store import ObjectStore, ObjectStoreError, ObjectNotFoundError
from app.data.repositories import RepositoryFactory
from app.services.correction_service import (
    Created,
    NoChange,
    Region,
    StorageError,
    Updated,
    make_region_key,
    submit_correction,
)


# ---------------------------------------------------------------------------
# In-memory object store (no filesystem) – used to seed prior pairs
# ---------------------------------------------------------------------------


class _InMemoryObjectStore(ObjectStore):
    """Dict-backed object store for fast, isolated property tests."""

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
# Failing object store – every operation raises, simulating I/O failure
# ---------------------------------------------------------------------------


class _FailingObjectStore(ObjectStore):
    """An object store whose every operation raises, simulating storage failure.

    Used to guarantee that any call into the object store (image fetch or
    crop storage) triggers the StorageError path in submit_correction (Req 4.6).
    """

    def put(self, reference: str, data: bytes) -> str:
        raise OSError("Simulated object-store write failure")

    def get(self, reference: str) -> bytes:
        raise OSError("Simulated object-store read failure")

    def open(self, reference: str) -> BinaryIO:
        raise OSError("Simulated object-store open failure")

    def exists(self, reference: str) -> bool:
        raise OSError("Simulated object-store exists failure")

    def delete(self, reference: str) -> None:
        raise OSError("Simulated object-store delete failure")


# ---------------------------------------------------------------------------
# Test database and fixture helpers
# ---------------------------------------------------------------------------


def _make_png_bytes(width: int = 100, height: int = 50) -> bytes:
    """Return minimal valid PNG bytes."""
    buf = io.BytesIO()
    img = PILImage.new("RGB", (width, height), color=(200, 200, 200))
    img.save(buf, format="PNG")
    return buf.getvalue()


def _setup_db():
    """Return (session, repos, user, image, working_store).

    Uses in-memory SQLite with StaticPool for full isolation.
    The image bytes are pre-loaded into the working store so that
    seeding prior pairs (via the working store) succeeds.
    """
    engine = create_db_engine("sqlite://", poolclass=StaticPool)
    create_all(engine)
    factory = create_session_factory(engine)
    session = factory()
    repos = RepositoryFactory(session)

    user = repos.users.add(
        User(username="failtest", email="failtest@example.com", password_hash="x")
    )
    session.flush()

    working_store = _InMemoryObjectStore()
    png_bytes = _make_png_bytes()
    storage_path = working_store.put_image_bytes(
        str(user.user_id), png_bytes, suffix=".png"
    )

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

    return session, repos, user, image, working_store


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

# Printable ASCII text — non-empty, reasonable length.
_text = st.text(
    alphabet=st.characters(
        whitelist_categories=("L", "N", "P", "Z"),
        min_codepoint=32,
        max_codepoint=126,
    ),
    min_size=1,
    max_size=200,
)

# Pairs where recognized != corrected (a write would be attempted).
_different_pair = st.tuples(_text, _text).filter(lambda p: p[0] != p[1])

# A fixed region within the 100×50 test image.
_region = st.builds(
    Region,
    x=st.integers(min_value=0, max_value=40),
    y=st.integers(min_value=0, max_value=20),
    width=st.integers(min_value=5, max_value=50),
    height=st.integers(min_value=5, max_value=25),
)

# Strategy: 0–5 prior pairs to seed before the failing attempt.
# Each prior pair is (recognized, corrected, region offset) with recognized != corrected.
_prior_pairs = st.lists(
    st.tuples(_text, _text, st.integers(min_value=0, max_value=4)).filter(
        lambda t: t[0] != t[1]
    ),
    min_size=0,
    max_size=5,
)


# ---------------------------------------------------------------------------
# Property 10 — part 1: no prior pairs; storage failure returns StorageError
#                        and leaves pairs count at zero
# ---------------------------------------------------------------------------

# Feature: handwriting-notes-digitizer, Property 10: Correction storage failure preserves prior pairs
@given(pair=_different_pair, region=_region)
def test_storage_failure_returns_storage_error_with_no_prior_pairs(
    pair: tuple[str, str], region: Region
) -> None:
    """Validates: Requirements 4.6.

    When no training pairs exist and a storage failure occurs, submit_correction
    must return StorageError and the training-pair count must remain zero.
    """
    recognized, corrected = pair

    session, repos, user, image, _working_store = _setup_db()
    failing_store = _FailingObjectStore()

    # Sanity: no pairs exist yet.
    before = repos.training_pairs.list_for_user(user.user_id)
    assert len(before) == 0

    result = submit_correction(
        user_id=user.user_id,
        image_id=image.image_id,
        region=region,
        recognized_text=recognized,
        corrected_text=corrected,
        repos=repos,
        object_store=failing_store,
    )

    after = repos.training_pairs.list_for_user(user.user_id)

    assert isinstance(result, StorageError), (
        f"Expected StorageError but got {result!r} for pair ({recognized!r}, {corrected!r})"
    )
    assert len(after) == 0, (
        f"Expected 0 training pairs after storage failure but found {len(after)}"
    )

    session.close()


# ---------------------------------------------------------------------------
# Property 10 — part 2: prior pairs exist; storage failure leaves them unchanged
# ---------------------------------------------------------------------------

# Feature: handwriting-notes-digitizer, Property 10: Correction storage failure preserves prior pairs
@given(prior=_prior_pairs, pair=_different_pair, region=_region)
def test_storage_failure_preserves_prior_pairs(
    prior: list[tuple[str, str, int]],
    pair: tuple[str, str],
    region: Region,
) -> None:
    """Validates: Requirements 4.6.

    When prior training pairs exist and a subsequent storage failure occurs,
    submit_correction must return StorageError and the previously stored
    training pairs must be identical (same count, same pair IDs, same corrected
    texts) after the failed attempt.
    """
    recognized, corrected = pair

    session, repos, user, image, working_store = _setup_db()

    # Seed prior training pairs via the working (non-failing) store.
    # Each prior pair gets a unique region by using a different x offset.
    seeded_regions: list[Region] = []
    for idx, (rec_text, corr_text, x_offset) in enumerate(prior):
        # Use distinct x-positions spaced by 6 pixels (within the 100-px image).
        # Clamp x_offset to stay within bounds: x_offset ∈ [0, 4], width=5.
        seeded_region = Region(x=x_offset * 6, y=0, width=5, height=5)
        seeded_regions.append(seeded_region)
        seed_result = submit_correction(
            user_id=user.user_id,
            image_id=image.image_id,
            region=seeded_region,
            recognized_text=rec_text,
            corrected_text=corr_text,
            repos=repos,
            object_store=working_store,
        )
        # Prior seed must succeed; if it doesn't, the test infrastructure is broken.
        assert isinstance(seed_result, (Created, Updated)), (
            f"Seeding prior pair {idx} failed unexpectedly: {seed_result!r}"
        )

    # Snapshot the state before the failing attempt.
    before_pairs = repos.training_pairs.list_for_user(user.user_id)
    before_ids = {p.pair_id for p in before_pairs}
    before_texts = {p.pair_id: p.corrected_text for p in before_pairs}
    before_count = len(before_pairs)

    # Now use a failing store for the next correction attempt.
    failing_store = _FailingObjectStore()

    # Choose a region that does NOT collide with any seeded region, so this is
    # a new pair attempt (not an upsert of an existing one).
    fresh_region = Region(x=50, y=30, width=10, height=10)

    result = submit_correction(
        user_id=user.user_id,
        image_id=image.image_id,
        region=fresh_region,
        recognized_text=recognized,
        corrected_text=corrected,
        repos=repos,
        object_store=failing_store,
    )

    # Assert: must be a StorageError.
    assert isinstance(result, StorageError), (
        f"Expected StorageError but got {result!r}"
    )

    # Assert: prior pairs are unchanged.
    after_pairs = repos.training_pairs.list_for_user(user.user_id)
    after_ids = {p.pair_id for p in after_pairs}
    after_texts = {p.pair_id: p.corrected_text for p in after_pairs}
    after_count = len(after_pairs)

    assert after_count == before_count, (
        f"Training pair count changed from {before_count} to {after_count} "
        f"after storage failure — partial write occurred"
    )
    assert after_ids == before_ids, (
        f"Training pair IDs changed after storage failure. "
        f"Added: {after_ids - before_ids}, Removed: {before_ids - after_ids}"
    )
    assert after_texts == before_texts, (
        f"Training pair corrected texts mutated after storage failure: "
        f"{before_texts} → {after_texts}"
    )

    session.close()


# ---------------------------------------------------------------------------
# Property 10 — part 3: upsert path — storage failure on re-correction of
#                        an existing region leaves the original pair unchanged
# ---------------------------------------------------------------------------

# Feature: handwriting-notes-digitizer, Property 10: Correction storage failure preserves prior pairs
@given(
    initial_pair=_different_pair,
    recorrection_pair=_different_pair,
    region=_region,
)
def test_storage_failure_on_recorrection_preserves_original_pair(
    initial_pair: tuple[str, str],
    recorrection_pair: tuple[str, str],
    region: Region,
) -> None:
    """Validates: Requirements 4.6.

    When re-correcting an existing region (the upsert path) and a storage
    failure occurs, submit_correction must return StorageError and the original
    training pair for that region must be unchanged (original corrected_text
    preserved, pair ID unchanged, count unchanged).
    """
    initial_recognized, initial_corrected = initial_pair
    recorr_recognized, recorr_corrected = recorrection_pair

    session, repos, user, image, working_store = _setup_db()

    # Create the initial correction via the working store.
    initial_result = submit_correction(
        user_id=user.user_id,
        image_id=image.image_id,
        region=region,
        recognized_text=initial_recognized,
        corrected_text=initial_corrected,
        repos=repos,
        object_store=working_store,
    )
    assert isinstance(initial_result, (Created, Updated)), (
        f"Initial correction failed unexpectedly: {initial_result!r}"
    )

    # Snapshot the state after the initial correction.
    before_pairs = repos.training_pairs.list_for_user(user.user_id)
    assert len(before_pairs) == 1, (
        f"Expected 1 prior pair after initial correction, got {len(before_pairs)}"
    )
    before_pair = before_pairs[0]
    original_pair_id = before_pair.pair_id
    original_corrected_text = before_pair.corrected_text

    # Now attempt re-correction using the failing store.
    failing_store = _FailingObjectStore()

    result = submit_correction(
        user_id=user.user_id,
        image_id=image.image_id,
        region=region,  # same region → would hit the upsert path
        recognized_text=recorr_recognized,
        corrected_text=recorr_corrected,
        repos=repos,
        object_store=failing_store,
    )

    # Assert: must be a StorageError.
    assert isinstance(result, StorageError), (
        f"Expected StorageError on re-correction but got {result!r}"
    )

    # Assert: the original pair is unchanged.
    after_pairs = repos.training_pairs.list_for_user(user.user_id)
    assert len(after_pairs) == 1, (
        f"Training pair count changed from 1 to {len(after_pairs)} after failure"
    )
    after_pair = after_pairs[0]
    assert after_pair.pair_id == original_pair_id, (
        "Training pair ID changed after storage failure"
    )
    assert after_pair.corrected_text == original_corrected_text, (
        f"Corrected text mutated from {original_corrected_text!r} to "
        f"{after_pair.corrected_text!r} after storage failure"
    )

    session.close()
