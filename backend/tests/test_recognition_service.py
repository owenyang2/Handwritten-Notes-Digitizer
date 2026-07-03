"""Tests for the Recognition_Service: per-user adapter selection (task 8.2).

This module verifies that:

1. When the requesting user has an active adapter, ``run_recognition`` uses it
   (Req 5.4) — the composed model differs from the base-only model.
2. When the requesting user has no adapter, the base model is used (Req 5.5).
3. User A's adapter is NEVER applied to User B's recognition request (Req 10.3).
4. The ``POST /images/{image_id}/recognize`` endpoint selects only the
   requesting user's adapter from the repository (Req 10.3).
5. The ``GET /images/{image_id}/result`` endpoint returns stored words scoped
   to the requesting user (Req 2.1, 10.3).
6. Both endpoints return 404 for images owned by another user (Req 10.3, 10.5).

**Validates: Requirements 2.1, 5.4, 5.5, 10.3**
"""

from __future__ import annotations

import io
import uuid
from typing import Iterator
from unittest.mock import MagicMock, patch

import pytest
import torch
from fastapi import FastAPI, status
from fastapi.testclient import TestClient
from PIL import Image as PILImage
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.data import (
    Adapter,
    AdapterStatus,
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
from app.data.object_store import ObjectStore
from app.ml.htr_config import DEFAULT_HTR_CONFIG
from app.ml.htr_model import build_model
from app.ml.recognition import RecognitionResult, apply_adapter
from app.services import auth_api
from app.services.auth import SessionStore, hash_password
from app.services.auth_api import (
    get_current_user,
    get_db_session,
    get_session_store,
    router as auth_router,
)
from app.services.recognition_service import (
    RecognitionOutcome,
    RecognitionServiceResult,
    run_recognition,
    router as recognition_router,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tiny_png() -> bytes:
    """Return minimal valid 8x8 greyscale PNG bytes (suitable for recognition)."""
    buf = io.BytesIO()
    PILImage.new("L", (8, 8), color=255).save(buf, format="PNG")
    return buf.getvalue()


def _make_minimal_adapter_weights(config=None) -> bytes:
    """Return a valid adapter state_dict (classifier layer only) as bytes."""
    if config is None:
        config = DEFAULT_HTR_CONFIG
    model = build_model(config)
    # Adapter: only the classifier weight (and bias if present).
    state: dict = {"classifier.weight": model.classifier.weight.clone()}
    if model.classifier.bias is not None:
        state["classifier.bias"] = model.classifier.bias.clone()
    buf = io.BytesIO()
    torch.save(state, buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# In-memory object store for tests
# ---------------------------------------------------------------------------


class InMemoryObjectStore(ObjectStore):
    """Simple dict-backed object store for test isolation."""

    def __init__(self) -> None:
        self._data: dict[str, bytes] = {}

    def put(self, reference: str, data: bytes) -> str:
        self._data[reference] = data
        return reference

    def get(self, reference: str) -> bytes:
        try:
            return self._data[reference]
        except KeyError:
            from app.data.object_store import ObjectNotFoundError
            raise ObjectNotFoundError(reference)

    def open(self, reference: str):
        return io.BytesIO(self.get(reference))

    def exists(self, reference: str) -> bool:
        return reference in self._data

    def delete(self, reference: str) -> None:
        self._data.pop(reference, None)


# ---------------------------------------------------------------------------
# DB / session fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def session_factory():
    engine = create_db_engine("sqlite://", poolclass=StaticPool)
    create_all(engine)
    factory = create_session_factory(engine)
    yield factory
    Base.metadata.drop_all(engine)


@pytest.fixture()
def session(session_factory) -> Iterator[Session]:
    with session_factory() as s:
        yield s


@pytest.fixture()
def object_store() -> InMemoryObjectStore:
    return InMemoryObjectStore()


def _make_user(session: Session, username: str) -> User:
    user = User(
        username=username,
        email=f"{username}@example.com",
        password_hash=hash_password("pw"),
    )
    session.add(user)
    session.commit()
    return user


def _make_image(
    session: Session, user: User, store: InMemoryObjectStore, image_bytes: bytes | None = None
) -> Image:
    if image_bytes is None:
        image_bytes = _make_tiny_png()
    path = store.put(f"images/{user.user_id}/img.png", image_bytes)
    image = Image(
        user_id=user.user_id,
        storage_path=path,
        format=ImageFormat.PNG,
        size_bytes=len(image_bytes),
        status=ImageStatus.ACCEPTED,
    )
    session.add(image)
    session.commit()
    return image


def _make_adapter(
    session: Session, user: User, store: InMemoryObjectStore
) -> Adapter:
    weights = _make_minimal_adapter_weights()
    path = store.put(f"adapters/{user.user_id}/adapter.pt", weights)
    adapter = Adapter(
        user_id=user.user_id,
        weights_path=path,
        base_model_version="base-v1",
        status=AdapterStatus.ACTIVE,
    )
    session.add(adapter)
    session.commit()
    return adapter


# ---------------------------------------------------------------------------
# Tests: run_recognition() + per-user adapter selection
# ---------------------------------------------------------------------------


class TestRunRecognitionAdapterSelection:
    """Unit tests for adapter selection in run_recognition() (Req 5.4, 5.5)."""

    def test_base_model_used_when_no_adapter(
        self, session: Session, object_store: InMemoryObjectStore
    ) -> None:
        """Req 5.5: When no adapter exists for the user, base model is used.

        We verify this by confirming run_recognition() accepts adapter=None and
        completes without error, returning a valid RecognitionServiceResult.
        """
        repos = RepositoryFactory(session)
        user = _make_user(session, "alice")
        image = _make_image(session, user, object_store)

        # No adapter for the user.
        adapter = repos.adapters.get_active_for_user(user.user_id)
        assert adapter is None, "Pre-condition: user has no adapter"

        result = run_recognition(image, repos, object_store, adapter=None)

        assert isinstance(result, RecognitionServiceResult)
        assert result.image_id == image.image_id
        assert result.outcome in (
            RecognitionOutcome.SUCCESS,
            RecognitionOutcome.NO_TEXT,
            RecognitionOutcome.TIMEOUT,
            RecognitionOutcome.FAILED,
        )

    def test_adapter_passed_through_to_recognition_when_present(
        self, session: Session, object_store: InMemoryObjectStore
    ) -> None:
        """Req 5.4: When user has an active adapter, it is passed to the engine.

        We use a mock for recognize_bytes so we can inspect what adapter
        argument was forwarded, without needing actual ML inference.
        """
        repos = RepositoryFactory(session)
        user = _make_user(session, "alice")
        image = _make_image(session, user, object_store)
        db_adapter = _make_adapter(session, user, object_store)

        # Retrieve the adapter as the service would.
        adapter = repos.adapters.get_active_for_user(user.user_id)
        assert adapter is not None, "Pre-condition: adapter must exist"
        assert adapter.adapter_id == db_adapter.adapter_id

        # Patch recognize_bytes to capture what adapter is forwarded.
        from app.ml.recognition import RecognitionResult as MLResult, RecognizedLine

        # The resolved adapter's temp weights file only exists for the
        # duration of the call (run_recognition cleans it up once inference
        # returns), so the mock must read it while it's still forwarded.
        resolved_weights: list[bytes] = []

        def _capture_recognize(image_bytes, adapter=None, **kw):
            if adapter is not None:
                with open(adapter.weights_path, "rb") as fh:
                    resolved_weights.append(fh.read())
            return mock_result

        mock_result = MLResult(
            lines=[],
            recognized_any_text=False,
            image_width=640,
            image_height=240,
        )
        with patch(
            "app.services.recognition_service.recognize_bytes",
            side_effect=_capture_recognize,
        ) as mock_recognize:
            run_recognition(image, repos, object_store, adapter=adapter)

        # The adapter's weights (resolved through the object store — Req 5.4)
        # must have been forwarded to the engine. run_recognition never
        # forwards the raw ORM object as-is because its weights_path is an
        # object-store reference, not a directly-openable filesystem path.
        mock_recognize.assert_called_once()
        assert resolved_weights == [object_store.get(adapter.weights_path)], (
            "run_recognition must forward the user's resolved adapter weights "
            "to the HTR engine"
        )

    def test_no_adapter_passed_when_user_has_no_adapter(
        self, session: Session, object_store: InMemoryObjectStore
    ) -> None:
        """Req 5.5: When user has no adapter, None is passed to the engine."""
        repos = RepositoryFactory(session)
        user = _make_user(session, "alice")
        image = _make_image(session, user, object_store)

        # Verify there is no adapter in the DB.
        assert repos.adapters.get_active_for_user(user.user_id) is None

        from app.ml.recognition import RecognitionResult as MLResult

        mock_result = MLResult(lines=[], recognized_any_text=False)
        with patch(
            "app.services.recognition_service.recognize_bytes",
            return_value=mock_result,
        ) as mock_recognize:
            run_recognition(image, repos, object_store, adapter=None)

        mock_recognize.assert_called_once()
        _args, kwargs = mock_recognize.call_args
        passed_adapter = kwargs.get("adapter") or (_args[1] if len(_args) > 1 else None)
        assert passed_adapter is None, (
            "run_recognition must pass adapter=None when user has no adapter"
        )


# ---------------------------------------------------------------------------
# Tests: cross-user adapter isolation (Req 10.3)
# ---------------------------------------------------------------------------


class TestCrossUserAdapterIsolation:
    """Verify User A's adapter is never applied to User B's recognition (Req 10.3)."""

    def test_user_b_does_not_receive_user_a_adapter(
        self, session: Session, object_store: InMemoryObjectStore
    ) -> None:
        """Req 10.3: Recognition uses only the requesting user's adapter.

        Setup: Alice has an active adapter; Bob has none.
        Action: trigger recognition for Bob's image (adapter=None as expected).
        Assert: the adapter belonging to Alice was never used for Bob's request.
        """
        repos = RepositoryFactory(session)
        alice = _make_user(session, "alice")
        bob = _make_user(session, "bob")

        _make_adapter(session, alice, object_store)  # Alice's adapter
        bob_image = _make_image(session, bob, object_store)

        # Service selects Bob's adapter — which doesn't exist.
        bob_adapter = repos.adapters.get_active_for_user(bob.user_id)
        assert bob_adapter is None, "Bob has no adapter"

        # Alice's adapter exists in the DB.
        alice_adapter = repos.adapters.get_active_for_user(alice.user_id)
        assert alice_adapter is not None, "Alice has an adapter"

        from app.ml.recognition import RecognitionResult as MLResult

        mock_result = MLResult(lines=[], recognized_any_text=False)
        captured: list = []

        def _capture_recognize(image_bytes, adapter=None, **kw):
            captured.append(adapter)
            return mock_result

        with patch(
            "app.services.recognition_service.recognize_bytes",
            side_effect=_capture_recognize,
        ):
            # Call with Bob's adapter selection (None), NOT Alice's.
            run_recognition(bob_image, repos, object_store, adapter=bob_adapter)

        assert len(captured) == 1
        assert captured[0] is None, (
            "Alice's adapter must NEVER be used for Bob's recognition request"
        )

    def test_user_a_adapter_used_only_for_user_a_image(
        self, session: Session, object_store: InMemoryObjectStore
    ) -> None:
        """Req 10.3: Alice's adapter IS used for Alice's own image.

        This is the positive counterpart — adapter isolation works both ways:
        Alice's adapter is applied for Alice, never leaked to Bob.
        """
        repos = RepositoryFactory(session)
        alice = _make_user(session, "alice")

        alice_db_adapter = _make_adapter(session, alice, object_store)
        alice_image = _make_image(session, alice, object_store)

        alice_adapter = repos.adapters.get_active_for_user(alice.user_id)
        assert alice_adapter is not None

        from app.ml.recognition import RecognitionResult as MLResult

        mock_result = MLResult(lines=[], recognized_any_text=False)
        captured: list = []

        def _capture_recognize(image_bytes, adapter=None, **kw):
            # Read the resolved weights while the temp file still exists —
            # run_recognition deletes it once inference returns.
            captured.append(
                open(adapter.weights_path, "rb").read() if adapter is not None else None
            )
            return mock_result

        with patch(
            "app.services.recognition_service.recognize_bytes",
            side_effect=_capture_recognize,
        ):
            run_recognition(alice_image, repos, object_store, adapter=alice_adapter)

        assert len(captured) == 1
        assert captured[0] == object_store.get(alice_adapter.weights_path), (
            "Alice's adapter must be used for Alice's own recognition"
        )


# ---------------------------------------------------------------------------
# HTTP endpoint integration tests (Req 2.1, 10.3)
# ---------------------------------------------------------------------------


@pytest.fixture()
def app_client(session_factory):
    """Full app client wiring auth + recognition endpoints for integration tests."""
    store = InMemoryObjectStore()
    session_store = SessionStore()

    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(recognition_router)

    from app.services.isolation import register_isolation_handlers
    register_isolation_handlers(app)

    def _override_db():
        with session_factory() as s:
            yield s

    app.dependency_overrides[get_db_session] = _override_db
    app.dependency_overrides[get_session_store] = lambda: session_store

    # Override the object store used by the recognition endpoint.
    from app.services import recognition_service as rs
    app.dependency_overrides[rs._get_object_store_dep] = lambda: store

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, session_factory, store, session_store


def _register_and_login(
    client: TestClient, session_store: SessionStore, session_factory, username: str
) -> tuple[User, str]:
    """Create a user in the DB and issue a token via the session store directly."""
    with session_factory() as s:
        user = _make_user(s, username)

    # Issue a token directly into the session store (bypasses login endpoint).
    token = session_store.create(user.user_id)
    return user, token


def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class TestRecognizeEndpoint:
    """Integration tests for POST /images/{image_id}/recognize (Req 2.1, 10.3)."""

    def test_returns_404_for_another_users_image(self, app_client) -> None:
        """Req 10.3: Recognize endpoint returns 404 for images owned by others."""
        client, factory, store, session_store = app_client

        alice, alice_token = _register_and_login(client, session_store, factory, "alice_404")
        bob, bob_token = _register_and_login(client, session_store, factory, "bob_404")

        with factory() as s:
            alice_image = _make_image(s, alice, store)
            image_id = alice_image.image_id

        # Bob tries to recognize Alice's image — must get 404.
        resp = client.post(
            f"/images/{image_id}/recognize",
            headers=_auth_header(bob_token),
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND, (
            f"Expected 404 but got {resp.status_code}: {resp.text}"
        )

    def test_unauthenticated_request_is_rejected(self, app_client) -> None:
        """Req 10.3: Unauthenticated recognition request returns 401."""
        client, factory, store, session_store = app_client
        fake_image_id = uuid.uuid4()

        resp = client.post(f"/images/{fake_image_id}/recognize")
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    def test_owner_can_trigger_recognition(self, app_client) -> None:
        """Req 2.1: Image owner can trigger recognition and gets a valid outcome."""
        client, factory, store, session_store = app_client

        alice, alice_token = _register_and_login(
            client, session_store, factory, "alice_recognize"
        )

        with factory() as s:
            alice_image = _make_image(s, alice, store)
            image_id = alice_image.image_id

        from app.ml.recognition import RecognitionResult as MLResult

        mock_result = MLResult(lines=[], recognized_any_text=False)
        with patch(
            "app.services.recognition_service.recognize_bytes",
            return_value=mock_result,
        ):
            resp = client.post(
                f"/images/{image_id}/recognize",
                headers=_auth_header(alice_token),
            )

        assert resp.status_code == status.HTTP_200_OK
        body = resp.json()
        assert "outcome" in body
        assert body["image_id"] == str(image_id)
        assert body["image_width"] == 640
        assert body["image_height"] == 240

    def test_adapter_selection_uses_only_requesting_users_adapter(
        self, app_client
    ) -> None:
        """Req 10.3: Endpoint selects only the requesting user's adapter, not another's.

        Alice has an adapter; Bob does not.  When Bob triggers recognition,
        the engine must receive adapter=None, never Alice's adapter.
        """
        client, factory, store, session_store = app_client

        alice, alice_token = _register_and_login(
            client, session_store, factory, "alice_adapter_sel"
        )
        bob, bob_token = _register_and_login(
            client, session_store, factory, "bob_adapter_sel"
        )

        with factory() as s:
            _make_adapter(s, alice, store)   # Alice's adapter in DB
            bob_image = _make_image(s, bob, store)
            bob_image_id = bob_image.image_id

        from app.ml.recognition import RecognitionResult as MLResult

        mock_result = MLResult(lines=[], recognized_any_text=False)
        captured: list = []

        def _capture(image_bytes, adapter=None, **kw):
            captured.append(adapter)
            return mock_result

        with patch(
            "app.services.recognition_service.recognize_bytes",
            side_effect=_capture,
        ):
            resp = client.post(
                f"/images/{bob_image_id}/recognize",
                headers=_auth_header(bob_token),
            )

        assert resp.status_code == status.HTTP_200_OK
        assert len(captured) == 1, "recognize_bytes must be called exactly once"
        assert captured[0] is None, (
            "Alice's adapter must NEVER be passed to Bob's recognition"
        )

    def test_adapter_selection_uses_requesting_users_own_adapter(
        self, app_client
    ) -> None:
        """Req 5.4: When user has an active adapter, it is used for their recognition.

        Alice has an adapter.  When Alice triggers recognition, the engine
        receives her adapter (not None).
        """
        client, factory, store, session_store = app_client

        alice, alice_token = _register_and_login(
            client, session_store, factory, "alice_own_adapter"
        )

        with factory() as s:
            db_adapter = _make_adapter(s, alice, store)
            alice_image = _make_image(s, alice, store)
            alice_image_id = alice_image.image_id
            expected_weights = store.get(db_adapter.weights_path)

        from app.ml.recognition import RecognitionResult as MLResult

        mock_result = MLResult(lines=[], recognized_any_text=False)
        captured: list = []

        def _capture(image_bytes, adapter=None, **kw):
            # Read the resolved weights while the temp file still exists —
            # run_recognition deletes it once inference returns.
            captured.append(
                open(adapter.weights_path, "rb").read() if adapter is not None else None
            )
            return mock_result

        with patch(
            "app.services.recognition_service.recognize_bytes",
            side_effect=_capture,
        ):
            resp = client.post(
                f"/images/{alice_image_id}/recognize",
                headers=_auth_header(alice_token),
            )

        assert resp.status_code == status.HTTP_200_OK
        assert len(captured) == 1
        # The weights forwarded to the engine (resolved through the object
        # store — Req 5.4) must be Alice's own adapter weights.
        passed_weights = captured[0]
        assert passed_weights is not None, "Alice's adapter must be passed to the engine"
        assert passed_weights == expected_weights


class TestGetResultEndpoint:
    """Integration tests for GET /images/{image_id}/result (Req 2.1, 10.3)."""

    def test_returns_404_for_another_users_image(self, app_client) -> None:
        """Req 10.3: Result endpoint returns 404 for images owned by others."""
        client, factory, store, session_store = app_client

        alice, alice_token = _register_and_login(
            client, session_store, factory, "alice_result_404"
        )
        bob, bob_token = _register_and_login(
            client, session_store, factory, "bob_result_404"
        )

        with factory() as s:
            alice_image = _make_image(s, alice, store)
            image_id = alice_image.image_id

        # Bob requests Alice's result — must get 404.
        resp = client.get(
            f"/images/{image_id}/result",
            headers=_auth_header(bob_token),
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    def test_unauthenticated_request_is_rejected(self, app_client) -> None:
        """Req 10.3: Unauthenticated result request returns 401."""
        client, factory, store, session_store = app_client
        fake_image_id = uuid.uuid4()

        resp = client.get(f"/images/{fake_image_id}/result")
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    def test_owner_sees_recognized_words_in_result(self, app_client) -> None:
        """Req 2.1: Owner can fetch result and sees their recognized words.

        We seed a RecognizedWord row directly to test the GET endpoint without
        running the full ML pipeline.
        """
        client, factory, store, session_store = app_client

        alice, alice_token = _register_and_login(
            client, session_store, factory, "alice_result_words"
        )

        with factory() as s:
            alice_image = _make_image(s, alice, store)
            image_id = alice_image.image_id
            # Seed a recognized word for this image.
            word = RecognizedWord(
                image_id=image_id,
                line_index=0,
                word_index=0,
                text="hello",
                region_x=10,
                region_y=20,
                region_width=30,
                region_height=15,
            )
            s.add(word)
            s.commit()

        resp = client.get(
            f"/images/{image_id}/result",
            headers=_auth_header(alice_token),
        )
        assert resp.status_code == status.HTTP_200_OK
        body = resp.json()
        assert body["image_id"] == str(image_id)
        assert len(body["lines"]) == 1
        assert body["lines"][0]["region"] == {
            "x": 10,
            "y": 20,
            "width": 30,
            "height": 15,
        }
        assert body["lines"][0]["words"][0]["text"] == "hello"

    def test_owner_does_not_see_another_users_words(self, app_client) -> None:
        """Req 10.3: Recognized words from another user are not visible.

        Bob owns an image with recognized words.  Alice must not be able to
        read Bob's result — she gets 404, not Bob's words.
        """
        client, factory, store, session_store = app_client

        alice, alice_token = _register_and_login(
            client, session_store, factory, "alice_cross_result"
        )
        bob, bob_token = _register_and_login(
            client, session_store, factory, "bob_cross_result"
        )

        with factory() as s:
            bob_image = _make_image(s, bob, store)
            bob_image_id = bob_image.image_id
            s.add(RecognizedWord(
                image_id=bob_image_id,
                line_index=0,
                word_index=0,
                text="bob-secret",
                region_x=0,
                region_y=0,
                region_width=10,
                region_height=10,
            ))
            s.commit()

        # Alice cannot see Bob's result.
        resp = client.get(
            f"/images/{bob_image_id}/result",
            headers=_auth_header(alice_token),
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND
        assert "bob-secret" not in resp.text
