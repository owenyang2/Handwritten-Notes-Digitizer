"""Property-based test for the Correction_Pipeline idempotent re-correction rule (design Property 9).

# Feature: handwriting-notes-digitizer, Property 9: Re-correcting a region is idempotent and last-write-wins

For any sequence of confirmed corrections that all target the same image region,
the system must end up with exactly one training pair for that region whose
corrected_text equals the most recent correction in the sequence, with no
duplicate pairs for that region. The pair_id assigned on the first correction
must remain stable (unchanged) across all subsequent re-corrections.

**Validates: Requirements 4.7**

Strategy
--------
We generate a non-empty list of (recognized_text, corrected_text) pairs where
every corrected_text differs from its recognized_text (so each call must either
create or update a pair — never NoChange). A fixed region is used throughout,
meaning all corrections target the exact same ``source_region_key``.

For each generated sequence we:

1. Apply each correction in order via ``submit_correction``.
2. Assert every result is ``Created`` (first call) or ``Updated`` (subsequent).
3. After the full sequence, query the user's training pairs for the region.
4. Assert exactly ONE pair exists for that region.
5. Assert the pair's ``corrected_text`` equals the LAST correction.
6. Assert the ``pair_id`` is the same as the one returned on the FIRST call
   (last-write-wins but the primary key / identity is stable — Req 4.7).
7. Assert there are no duplicate pairs for the region anywhere in the store.

All tests run against an isolated in-memory SQLite database and an in-memory
dict-backed object store — no filesystem I/O, fast and deterministic.
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
from app.data.enums import ImageFormat, ImageStatus
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


def _make_png_bytes(width: int = 200, height: int = 100) -> bytes:
    """Return minimal valid PNG bytes at the given dimensions."""
    buf = io.BytesIO()
    img = PILImage.new("RGB", (width, height), color=(200, 200, 200))
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_test_db():
    """Return a (session, repos, user, image, store) tuple backed by in-memory SQLite.

    Uses StaticPool so all sessions share the same schema, which is required for
    in-memory SQLite isolation between calls.
    """
    engine = create_db_engine("sqlite://", poolclass=StaticPool)
    create_all(engine)
    factory = create_session_factory(engine)

    session = factory()
    repos = RepositoryFactory(session)

    user = repos.users.add(
        User(username="idempotent_test", email="idempotent@example.com", password_hash="x")
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
# Hypothesis strategies
# ---------------------------------------------------------------------------

# Printable ASCII text (non-empty, reasonable length).
_text = st.text(
    alphabet=st.characters(
        whitelist_categories=("L", "N", "P", "Z"),
        min_codepoint=32,
        max_codepoint=126,
    ),
    min_size=1,
    max_size=200,
)

# Pairs where recognized != corrected so each call must create/update.
_different_pair = st.tuples(_text, _text).filter(lambda p: p[0] != p[1])

# A sequence of at least 2 such pairs (so there is always at least one re-correction).
_correction_sequence = st.lists(
    _different_pair,
    min_size=2,
    max_size=10,
)

# A fixed region used for all corrections in the sequence (same region key throughout).
_fixed_region = st.builds(
    Region,
    x=st.integers(min_value=0, max_value=50),
    y=st.integers(min_value=0, max_value=50),
    width=st.integers(min_value=5, max_value=100),
    height=st.integers(min_value=5, max_value=50),
)


# ---------------------------------------------------------------------------
# Property 9 — Re-correcting a region is idempotent and last-write-wins
# ---------------------------------------------------------------------------

# Feature: handwriting-notes-digitizer, Property 9: Re-correcting a region is idempotent and last-write-wins
@given(corrections=_correction_sequence, region=_fixed_region)
def test_re_correcting_same_region_is_idempotent_and_last_write_wins(
    corrections: list[tuple[str, str]],
    region: Region,
) -> None:
    """Validates: Requirements 4.7

    For any sequence of corrections all targeting the same image region:

    * The first call returns Created; subsequent calls return Updated.
    * After all corrections, exactly ONE training pair exists for that region.
    * The persisted corrected_text equals the LAST correction in the sequence.
    * The pair_id is stable — the same UUID throughout all re-corrections.
    * No duplicate pairs exist for the same region.
    """
    session, repos, user, image, store = _make_test_db()

    first_pair_id: uuid.UUID | None = None
    last_corrected_text: str = corrections[-1][1]

    for idx, (recognized, corrected) in enumerate(corrections):
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
            f"submit_correction raised StorageError at step {idx}: {result.message}"
        )
        assert not isinstance(result, NoChange), (
            f"Expected Created/Updated but got NoChange at step {idx} "
            f"(recognized={recognized!r}, corrected={corrected!r}). "
            "All generated pairs have recognized != corrected."
        )

        if idx == 0:
            # First correction must create a new pair.
            assert isinstance(result, Created), (
                f"Expected Created on first correction but got {result!r}"
            )
            first_pair_id = result.pair_id
        else:
            # All subsequent corrections to the same region must update.
            assert isinstance(result, Updated), (
                f"Expected Updated on re-correction at step {idx} but got {result!r}"
            )
            # pair_id must remain stable (same identity, last-write-wins Req 4.7).
            assert result.pair_id == first_pair_id, (
                f"pair_id changed on re-correction at step {idx}: "
                f"expected {first_pair_id}, got {result.pair_id}"
            )

    # --- Post-sequence assertions ------------------------------------------

    all_pairs = repos.training_pairs.list_for_user(user.user_id)
    region_key = make_region_key(image.image_id, region)

    # Filter to pairs that match this region key.
    pairs_for_region = [p for p in all_pairs if p.source_region_key == region_key]

    # EXACTLY one pair must exist for the region (no duplicates).
    assert len(pairs_for_region) == 1, (
        f"Expected exactly 1 training pair for region {region_key!r} "
        f"after {len(corrections)} corrections, found {len(pairs_for_region)}"
    )

    surviving_pair = pairs_for_region[0]

    # corrected_text must equal the LAST correction (last-write-wins).
    assert surviving_pair.corrected_text == last_corrected_text, (
        f"Expected corrected_text={last_corrected_text!r} (last correction) "
        f"but found {surviving_pair.corrected_text!r}"
    )

    # pair_id must be the one assigned on the first Create (stable identity).
    assert surviving_pair.pair_id == first_pair_id, (
        f"pair_id changed: expected {first_pair_id}, found {surviving_pair.pair_id}"
    )

    # Total pairs for the user must be exactly 1 (only one region was targeted).
    assert len(all_pairs) == 1, (
        f"Expected exactly 1 total training pair (one region corrected N times) "
        f"but found {len(all_pairs)}"
    )

    session.close()
