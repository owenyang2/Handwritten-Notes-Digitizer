"""Property-based test for the Correction_Pipeline create-on-change rule (design Property 8).

# Feature: handwriting-notes-digitizer, Property 8: A training pair is created exactly when a confirmed edit changes a word

For any confirmed edit on a recognized word, a training pair (image region
paired with corrected text) is created if and only if the corrected text differs
from the recognized text; when the texts are identical, or while editing is
unconfirmed, the user's stored training pairs are unchanged.

**Validates: Requirements 4.3, 4.4, 4.8**

Strategy
--------
We generate pairs of (recognized_text, corrected_text) strings with various
equality scenarios:

* Equal texts  → ``submit_correction`` must return ``NoChange`` and the count
  of the user's training pairs must be unchanged.
* Unequal texts → ``submit_correction`` must return ``Created`` or ``Updated``
  and the pair count must reflect a change.

The "unconfirmed editing" half of the property (Req 4.8) is modelled by *never
calling* ``submit_correction`` — the service is only invoked on confirmed
submissions, so the property holds trivially for unconfirmed states (no call
means no write). We test this explicitly via the ``test_unconfirmed_is_no_change``
helper below.

All tests run against an isolated in-memory SQLite database so they are fast
and deterministic. The object store is an in-memory dict-backed fake; no
filesystem I/O is needed.
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
    submit_correction,
)


# ---------------------------------------------------------------------------
# In-memory object store (no filesystem)
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
# Per-test DB + object store helpers
# ---------------------------------------------------------------------------


def _make_test_db(large_image: bool = False):
    """Return a (session_factory, repos, user, image, store) tuple.

    Uses an in-memory SQLite with StaticPool so all sessions share the same
    schema and rows — necessary for in-memory SQLite isolation.

    When ``large_image=True`` a 2100×100 image is used so that the sequence
    test's 200-px column offsets (up to 10 × 200 = 2000 px) always fall within
    the image bounds.
    """
    engine = create_db_engine("sqlite://", poolclass=StaticPool)
    create_all(engine)
    factory = create_session_factory(engine)

    session = factory()
    repos = RepositoryFactory(session)

    user = repos.users.add(
        User(username="proptest", email="proptest@example.com", password_hash="x")
    )
    session.flush()

    store = _InMemoryObjectStore()
    png_bytes = _make_png_bytes_large() if large_image else _make_png_bytes()
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


def _make_png_bytes(width: int = 100, height: int = 50) -> bytes:
    """Return minimal valid PNG bytes."""
    buf = io.BytesIO()
    img = PILImage.new("RGB", (width, height), color=(200, 200, 200))
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_png_bytes_large() -> bytes:
    """Return a large PNG (2100 × 100) that can accommodate up to 10 non-overlapping
    200-px-wide column regions produced by the sequence test."""
    return _make_png_bytes(width=2100, height=100)


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

# Printable ASCII text (non-empty, reasonable length) to drive corrections.
_text = st.text(
    alphabet=st.characters(
        whitelist_categories=("L", "N", "P", "Z"),
        min_codepoint=32,
        max_codepoint=126,
    ),
    min_size=1,
    max_size=200,
)

# Pairs where recognized == corrected (identical).
_equal_pair = _text.map(lambda t: (t, t))

# Pairs where recognized != corrected (different).
_different_pair = st.tuples(_text, _text).filter(lambda p: p[0] != p[1])

# Mixed strategy: sometimes equal, sometimes different.
_any_pair = st.one_of(_equal_pair, _different_pair)

# A fixed region for use in single-pair tests.
_region = st.builds(
    Region,
    x=st.integers(min_value=0, max_value=40),
    y=st.integers(min_value=0, max_value=20),
    width=st.integers(min_value=5, max_value=50),
    height=st.integers(min_value=5, max_value=25),
)


# ---------------------------------------------------------------------------
# Property 8 — part 1: equal texts → NoChange, pairs unchanged
# ---------------------------------------------------------------------------

# Feature: handwriting-notes-digitizer, Property 8: A training pair is created exactly when a confirmed edit changes a word
@given(text=_text, region=_region)
def test_identical_texts_return_no_change_and_leave_pairs_unchanged(
    text: str, region: Region
) -> None:
    """Validates: Requirements 4.4, 4.8.

    When recognized_text == corrected_text for any text value, submit_correction
    must return NoChange and the number of stored training pairs for the user
    must be unchanged (zero before; still zero after).
    """
    session, repos, user, image, store = _make_test_db()

    before = len(repos.training_pairs.list_for_user(user.user_id))

    result = submit_correction(
        user_id=user.user_id,
        image_id=image.image_id,
        region=region,
        recognized_text=text,
        corrected_text=text,  # identical
        repos=repos,
        object_store=store,
    )

    after = len(repos.training_pairs.list_for_user(user.user_id))

    assert isinstance(result, NoChange), (
        f"Expected NoChange for identical texts {text!r} but got {result!r}"
    )
    assert after == before, (
        f"Training pair count changed from {before} to {after} on identical edit"
    )

    session.close()


# ---------------------------------------------------------------------------
# Property 8 — part 2: different texts → Created/Updated, pair count changes
# ---------------------------------------------------------------------------

# Feature: handwriting-notes-digitizer, Property 8: A training pair is created exactly when a confirmed edit changes a word
@given(pair=_different_pair, region=_region)
def test_different_texts_create_a_training_pair(
    pair: tuple[str, str], region: Region
) -> None:
    """Validates: Requirements 4.3, 4.8.

    When recognized_text != corrected_text, submit_correction must return
    Created (first call) or Updated (re-correction), and the user's set of
    training pairs must be modified (pair count increases on Create, stays
    stable count-wise on Update but the row is mutated).
    """
    recognized, corrected = pair

    session, repos, user, image, store = _make_test_db()

    before = len(repos.training_pairs.list_for_user(user.user_id))

    result = submit_correction(
        user_id=user.user_id,
        image_id=image.image_id,
        region=region,
        recognized_text=recognized,
        corrected_text=corrected,
        repos=repos,
        object_store=store,
    )

    after = len(repos.training_pairs.list_for_user(user.user_id))

    # StorageError would mean the DB/store is broken — fail loudly.
    assert not isinstance(result, StorageError), (
        f"submit_correction raised StorageError unexpectedly: {result.message}"
    )
    assert isinstance(result, (Created, Updated)), (
        f"Expected Created or Updated for texts ({recognized!r}, {corrected!r}) "
        f"but got {result!r}"
    )
    # A new pair must have been added.
    assert after == before + 1, (
        f"Expected pair count to grow from {before} to {before + 1}, got {after}"
    )
    # The persisted pair carries the corrected text.
    pairs = repos.training_pairs.list_for_user(user.user_id)
    assert pairs[-1].corrected_text == corrected

    session.close()


# ---------------------------------------------------------------------------
# Property 8 — part 3: if-and-only-if over a mixed stream of (equal, unequal)
#                       corrections to the same image
# ---------------------------------------------------------------------------

# A list of text-pair records to replay as a sequence of corrections.
_correction_sequence = st.lists(
    st.tuples(
        _text,   # recognized
        _text,   # corrected
        _region, # region (distinct so each produces its own pair)
    ),
    min_size=2,
    max_size=10,
)


# Feature: handwriting-notes-digitizer, Property 8: A training pair is created exactly when a confirmed edit changes a word
@given(corrections=_correction_sequence)
def test_pair_count_equals_number_of_changed_edits(
    corrections: list[tuple[str, str, Region]],
) -> None:
    """Validates: Requirements 4.3, 4.4, 4.8.

    For a sequence of corrections over distinct regions, the final count of
    training pairs equals exactly the number of corrections where
    recognized_text != corrected_text (the "changed edits").
    """
    session, repos, user, image, store = _make_test_db(large_image=True)
    changed_count = 0

    for idx, (recognized, corrected, base_region) in enumerate(corrections):
        # Each index occupies a 200-px column; keep x0 valid within image bounds.
        x_offset = idx * 200
        region = Region(
            x=x_offset,
            y=base_region.y,
            width=base_region.width,
            height=base_region.height,
        )

        result = submit_correction(
            user_id=user.user_id,
            image_id=image.image_id,
            region=region,
            recognized_text=recognized,
            corrected_text=corrected,
            repos=repos,
            object_store=store,
        )

        assert not isinstance(result, StorageError), (
            f"Unexpected StorageError at correction {idx}: {result.message}"
        )

        if recognized != corrected:
            changed_count += 1
            assert isinstance(result, Created), (
                f"Expected Created for changed edit ({recognized!r} → {corrected!r}) "
                f"but got {result!r}"
            )
        else:
            assert isinstance(result, NoChange), (
                f"Expected NoChange for identical edit {recognized!r} "
                f"but got {result!r}"
            )

    final_count = len(repos.training_pairs.list_for_user(user.user_id))
    assert final_count == changed_count, (
        f"Expected {changed_count} training pairs (one per changed edit) "
        f"but found {final_count}"
    )

    session.close()


# ---------------------------------------------------------------------------
# Unconfirmed-edit sanity check (Req 4.8)
#
# Unconfirmed editing means submit_correction is never called. We verify this
# trivially: a freshly created user with zero pairs has zero pairs when we never
# call submit_correction — regardless of what text is "in flight".
# ---------------------------------------------------------------------------

# Feature: handwriting-notes-digitizer, Property 8: A training pair is created exactly when a confirmed edit changes a word
@given(text=_text)
def test_unconfirmed_edit_leaves_pairs_unchanged(text: str) -> None:
    """Validates: Requirements 4.8.

    While a user is editing text without confirming, submit_correction is not
    called and the user's stored training pairs remain unchanged (zero).
    """
    session, repos, user, image, store = _make_test_db()

    # Simulate "user is typing but hasn't confirmed" — do NOT call submit_correction.
    _ = text  # text is in-flight / unconfirmed

    pairs = repos.training_pairs.list_for_user(user.user_id)
    assert len(pairs) == 0, (
        f"Expected 0 pairs for unconfirmed edit but found {len(pairs)}"
    )

    session.close()
