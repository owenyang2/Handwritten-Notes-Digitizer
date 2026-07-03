"""Property-based test for adapter isolation at recognition (design Property 27).

# Feature: handwriting-notes-digitizer, Property 27: Recognition uses only the requesting user's adapter

This test exercises the Recognition_Service's adapter routing against the
design's **Property 27**:

    For any set of users that each may have an adapter, recognizing a given
    user's image applies only that user's adapter and never any other user's
    adapter.

**Validates: Requirements 10.3**

Strategy
--------
Each Hypothesis example generates a multi-user scenario with 0, 1, or 2 users
that have adapters stored in the database. The test then picks a requesting user
and verifies that when ``run_recognition`` is called the engine receives:

  - The requesting user's adapter object (not ``None``) when that user has an
    active adapter.
  - ``None`` when the requesting user has no active adapter.
  - Never any other user's adapter object, regardless of how many other users
    have adapters.

``recognize_bytes`` is patched to capture the ``adapter`` argument passed to it
(so we can inspect it) and returns a minimal fake ``RecognitionResult`` without
performing real inference (keeping 100+ iterations cheap).

The test covers three key correctness dimensions:

1. **User with adapter → engine receives that user's adapter**: adapter identity
   is verified via ``adapter_id`` (not mere existence).
2. **User without adapter → engine receives ``None``**: the base-only path
   (Req 5.5) is exercised.
3. **Cross-user isolation**: the engine is never called with another user's
   adapter, even when other users in the same DB have adapters.

The test runs ≥100 iterations (enforced by the project-wide Hypothesis
``default`` profile configured in ``tests/conftest.py``).
"""

from __future__ import annotations

import io
import uuid
from dataclasses import dataclass, field
from typing import Optional
from unittest.mock import patch

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from PIL import Image as PILImage
from sqlalchemy.pool import StaticPool

from app.data import (
    Adapter,
    AdapterStatus,
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
from app.data.object_store import ObjectNotFoundError, ObjectStore
from app.ml.recognition import RecognitionResult
from app.services.auth import hash_password
from app.services.recognition_service import RecognitionOutcome, run_recognition

# ---------------------------------------------------------------------------
# Minimal in-memory object store (identical to the one used in property 5/6)
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
# Fake RecognitionResult returned by the patched engine
# ---------------------------------------------------------------------------


def _fake_recognize(image_bytes: bytes, adapter: object = None, **kwargs) -> RecognitionResult:
    """Fake engine that records the adapter it was called with and returns no text."""
    return RecognitionResult(lines=[], recognized_any_text=False)


# ---------------------------------------------------------------------------
# Scenario data structures
# ---------------------------------------------------------------------------


@dataclass
class _UserSpec:
    """Describes one user in the generated scenario."""

    username: str
    has_adapter: bool
    is_requesting: bool


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

# Generate 1–4 users; exactly one is the "requesting" user (the one whose
# image is being recognized). Each user independently may or may not have an
# active adapter. The strategy produces a list of _UserSpec dicts.
#
# Multi-user scenarios with at least one "other" user who has an adapter are
# the interesting case for cross-user isolation; the strategy can produce them
# naturally since `has_adapter` and the identity of the requesting user vary
# independently.

_num_users_st: st.SearchStrategy[int] = st.integers(min_value=1, max_value=4)


def _users_strategy(num_users: int) -> st.SearchStrategy[list[dict]]:
    # Draw `num_users` booleans for has_adapter, then pick one index as the
    # requesting user.
    has_adapter_flags = st.lists(
        st.booleans(), min_size=num_users, max_size=num_users
    )
    requesting_idx = st.integers(min_value=0, max_value=num_users - 1)
    return st.tuples(has_adapter_flags, requesting_idx).map(
        lambda t: [
            {
                "username": f"user{i}",
                "has_adapter": t[0][i],
                "is_requesting": (i == t[1]),
            }
            for i in range(num_users)
        ]
    )


_scenario_st: st.SearchStrategy[list[dict]] = _num_users_st.flatmap(_users_strategy)


# ---------------------------------------------------------------------------
# Property test
# ---------------------------------------------------------------------------


# Feature: handwriting-notes-digitizer, Property 27: Recognition uses only the requesting user's adapter
@given(user_specs=_scenario_st)
@settings(suppress_health_check=[HealthCheck.too_slow])
def test_recognition_uses_only_requesting_users_adapter(
    user_specs: list[dict],
) -> None:
    """Validates: Requirements 10.3.

    For any combination of users (0–4), each potentially having an active
    adapter, recognizing the requesting user's image passes exactly that user's
    adapter (or ``None``) to the engine and never any other user's adapter.

    Specifically:
    - When the requesting user has an active adapter: the adapter passed to the
      engine has that user's ``adapter_id`` (not ``None``, not another user's).
    - When the requesting user has no active adapter: the adapter passed to the
      engine is ``None``.
    - No other user's ``adapter_id`` is ever passed to the engine.
    """
    # --- Build a fresh in-memory DB for this example ----------------------
    engine = create_db_engine("sqlite://", poolclass=StaticPool)
    create_all(engine)
    session_factory = create_session_factory(engine)
    store = _InMemoryObjectStore()

    # Persist users and, for those with adapters, an Adapter row *and* its
    # weight bytes in the object store — mirroring production, where
    # personalization_service.complete_training_run always writes the bytes
    # via ObjectStore.put_adapter_weights before setting weights_path. The
    # recognition service resolves that reference through the object store
    # (Req 5.4), so each user's adapter needs real, distinguishable bytes
    # here to verify isolation.
    requesting_user_id: Optional[uuid.UUID] = None
    requesting_user_adapter_bytes: Optional[bytes] = None
    other_adapter_bytes: list[bytes] = []

    with session_factory() as s:
        for spec in user_specs:
            user = User(
                username=spec["username"],
                email=f"{spec['username']}@example.com",
                password_hash=hash_password("pw"),
            )
            s.add(user)
            s.flush()

            if spec["has_adapter"]:
                weights_path = f"adapters/{spec['username']}/weights.pt"
                weight_bytes = f"weights-for-{spec['username']}".encode("utf-8")
                store.put(weights_path, weight_bytes)

                adapter = Adapter(
                    user_id=user.user_id,
                    weights_path=weights_path,
                    base_model_version="base-v1",
                    status=AdapterStatus.ACTIVE,
                )
                s.add(adapter)
                s.flush()

                if spec["is_requesting"]:
                    requesting_user_id = user.user_id
                    requesting_user_adapter_bytes = weight_bytes
                else:
                    other_adapter_bytes.append(weight_bytes)
            else:
                if spec["is_requesting"]:
                    requesting_user_id = user.user_id
                    # requesting user has no adapter → engine should receive None

        s.commit()

    assert requesting_user_id is not None, "No requesting user in generated scenario"

    # Store a tiny PNG for the requesting user's image.
    img_bytes = _tiny_png_bytes()
    storage_path = store.put(
        f"images/{requesting_user_id}/img.png", img_bytes
    )

    with session_factory() as s:
        image = Image(
            user_id=requesting_user_id,
            storage_path=storage_path,
            format=ImageFormat.PNG,
            size_bytes=len(img_bytes),
            status=ImageStatus.ACCEPTED,
        )
        s.add(image)
        s.commit()
        image_id = image.image_id

    # --- Run recognition, capturing the adapter passed to the engine ------
    # The resolved adapter's temp weights file is cleaned up by
    # run_recognition once inference returns (see _resolve_adapter_for_inference
    # / the `finally` block in run_recognition), so its bytes must be read
    # *inside* this callback, while the file still exists.
    captured: dict = {"adapter": "NOT_SET", "weights_bytes": None}

    def _capturing_recognize(image_bytes: bytes, adapter: object = None, **kwargs) -> RecognitionResult:
        captured["adapter"] = adapter
        if adapter is not None:
            with open(adapter.weights_path, "rb") as fh:
                captured["weights_bytes"] = fh.read()
        return RecognitionResult(lines=[], recognized_any_text=False)

    with session_factory() as s:
        repos = RepositoryFactory(s)
        image_obj = repos.images.get(requesting_user_id, image_id)

        # Fetch the adapter exactly as the service would (Req 5.4/5.5).
        expected_adapter = repos.adapters.get_active_for_user(requesting_user_id)

        with patch(
            "app.services.recognition_service.recognize_bytes",
            side_effect=_capturing_recognize,
        ):
            result = run_recognition(image_obj, repos, store, adapter=expected_adapter)

    # --- Assertions -------------------------------------------------------

    assert captured["adapter"] is not "NOT_SET", (
        "recognize_bytes was never called — the engine did not run"
    )

    if requesting_user_adapter_bytes is not None:
        # The requesting user has an active adapter → engine must receive it.
        # run_recognition resolves the ObjectStore reference to a real,
        # locally-openable file (see _resolve_adapter_for_inference) before
        # forwarding it, so identity is verified via the resolved weight
        # bytes rather than the ORM object (which is never forwarded as-is).
        assert captured["adapter"] is not None, (
            "Engine received None but the requesting user has an active adapter"
        )
        resolved_bytes = captured["weights_bytes"]
        assert resolved_bytes == requesting_user_adapter_bytes, (
            "Engine received adapter weights that do not match the requesting "
            "user's own adapter"
        )
        # Must not be any other user's adapter.
        for other_bytes in other_adapter_bytes:
            assert resolved_bytes != other_bytes, (
                "Engine received another user's adapter weights instead of the "
                "requesting user's own adapter"
            )
    else:
        # The requesting user has no active adapter → engine must receive None.
        assert captured["adapter"] is None, (
            f"Engine received a non-None adapter {captured['adapter']} "
            "but the requesting user has no active adapter"
        )

    # Verify the service completed (NO_TEXT because our fake returns no text).
    assert result.outcome == RecognitionOutcome.NO_TEXT, (
        f"Unexpected outcome {result.outcome!r} — expected NO_TEXT from fake engine"
    )

    # Clean up.
    Base.metadata.drop_all(engine)
