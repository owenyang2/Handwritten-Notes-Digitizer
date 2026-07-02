"""End-to-end integration test for the full deployment flow (task 19.3).

Asserts that a full deployment exposes upload, recognition, inline correction,
personalization status, and export through the API (Req 11.4).

The test uses FastAPI's TestClient with the full ``app`` from ``main.py`` and
overrides:
- The DB session → isolated in-memory SQLite
- The session store → fresh in-process store
- The object stores → filesystem stores backed by a tmp directory
- The ML model calls (recognize_bytes) → mocked so no GPU/weights are needed
- The capability guards → bypassed so all capabilities are "available"

Flow verified:
  1. POST /images          → accepted (201), returns image_id
  2. POST /images/{id}/recognize → returns outcome=no_text or success
  3. POST /images/{id}/corrections (with a seeded recognized word) → created/updated
  4. GET  /personalization/status → valid status object (training_state / adapter_active)
  5. POST /export          → returns exported text (or empty message)

Validates: Requirements 11.4
"""

from __future__ import annotations

import io
import uuid
from typing import Iterator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image as PILImage
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
from app.data.object_store import FilesystemObjectStore
from app.main import create_app
from app.services.auth import SessionStore, hash_password
from app.services.auth_api import get_db_session, get_session_store
from app.services.images_api import get_object_store
from app.services.correction_service import get_correction_object_store
from app.services.capability_guard import require_capability


# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------


def _make_png_bytes(size: tuple[int, int] = (32, 32)) -> bytes:
    """Render a small solid-colour PNG and return its bytes."""
    buf = io.BytesIO()
    PILImage.new("RGB", size, color=(120, 180, 240)).save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def session_factory():
    """In-memory SQLite database, isolated per test."""
    engine = create_db_engine("sqlite://", poolclass=StaticPool)
    create_all(engine)
    factory = create_session_factory(engine)
    yield factory
    Base.metadata.drop_all(engine)


@pytest.fixture()
def object_store(tmp_path):
    """Filesystem object store backed by a temporary directory."""
    return FilesystemObjectStore(root=tmp_path / "store")


@pytest.fixture()
def session_store() -> SessionStore:
    """Fresh in-process session token store."""
    return SessionStore()


def _no_op_capability_guard():
    """Replacement capability guard that always allows the request through."""
    return None


@pytest.fixture()
def e2e_client(session_factory, object_store, session_store) -> Iterator[tuple]:
    """Full-app TestClient with all dependencies overridden for isolation.

    ML calls are mocked via patch so no model weights or GPU are required.
    Capability guards are bypassed so all endpoints are reachable regardless
    of environment variables.
    """
    app = create_app()

    # ---- DB session override ------------------------------------------------
    def _override_db() -> Iterator:
        with session_factory() as s:
            yield s

    app.dependency_overrides[get_db_session] = _override_db

    # ---- Session store override ---------------------------------------------
    app.dependency_overrides[get_session_store] = lambda: session_store

    # ---- Object store overrides (shared store for upload + correction) ------
    app.dependency_overrides[get_object_store] = lambda: object_store
    app.dependency_overrides[get_correction_object_store] = lambda: object_store

    # ---- Capability guard bypass -------------------------------------------
    # require_capability returns a cached callable per capability name.
    # We must override each one so the capability check is skipped.
    for cap_name in ("recognition", "correction", "export", "calibration",
                     "personalization", "image_upload"):
        guard = require_capability(cap_name)
        app.dependency_overrides[guard] = _no_op_capability_guard

    with TestClient(app, raise_server_exceptions=True) as client:
        yield client, session_factory, object_store, session_store


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _register_user(session_factory, username: str = "e2e_alice") -> uuid.UUID:
    """Insert a user into the DB and return their user_id."""
    with session_factory() as s:
        user = User(
            username=username,
            email=f"{username}@example.com",
            password_hash=hash_password("pw"),
        )
        s.add(user)
        s.commit()
        return user.user_id


def _login(client: TestClient, username: str = "e2e_alice", password: str = "pw") -> str:
    """Authenticate and return a bearer token."""
    resp = client.post("/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    return resp.json()["access_token"]


def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# E2E full-flow test
# ---------------------------------------------------------------------------


class TestFullDeploymentFlow:
    """End-to-end integration tests for the full deployment flow (Req 11.4).

    Covers: upload → recognition → correction → personalization status → export
    """

    def test_full_flow_authenticated(self, e2e_client) -> None:
        """Assert the full API surface is accessible for an authenticated user.

        Step 1: Upload an image → image_id returned (201).
        Step 2: Recognize the image → outcome returned (200).
        Step 3: Submit a correction → Training_Pair created (200).
        Step 4: Check personalization status → valid status object (200).
        Step 5: Export text → exported text or empty response (200).

        Validates: Requirements 11.4
        """
        client, sf, store, ss = e2e_client

        # ---- Setup: register and log in -----
        _register_user(sf, "e2e_alice")
        token = _login(client, "e2e_alice")
        headers = _auth_header(token)

        # ================================================================
        # Step 1: Upload an image (POST /images)
        # ================================================================
        png_bytes = _make_png_bytes()
        upload_resp = client.post(
            "/images",
            files={"file": ("handwriting.png", png_bytes, "image/png")},
            headers=headers,
        )
        assert upload_resp.status_code == 201, (
            f"Upload failed ({upload_resp.status_code}): {upload_resp.text}"
        )
        upload_body = upload_resp.json()
        assert upload_body["status"] == "accepted"
        image_id = uuid.UUID(upload_body["image_id"])  # validates it is a UUID
        assert image_id is not None

        # ================================================================
        # Step 2: Recognize the image (POST /images/{image_id}/recognize)
        # Mock recognize_bytes so no ML model is needed.
        # ================================================================
        from app.ml.recognition import RecognitionResult as MLResult

        mock_result = MLResult(lines=[], recognized_any_text=False)
        with patch(
            "app.services.recognition_service.recognize_bytes",
            return_value=mock_result,
        ):
            recognize_resp = client.post(
                f"/images/{image_id}/recognize",
                headers=headers,
            )

        assert recognize_resp.status_code == 200, (
            f"Recognition failed ({recognize_resp.status_code}): {recognize_resp.text}"
        )
        recognize_body = recognize_resp.json()
        assert "outcome" in recognize_body
        assert recognize_body["image_id"] == str(image_id)
        # outcome is one of the valid RecognitionOutcome values
        assert recognize_body["outcome"] in ("success", "no_text", "timeout", "failed")

        # ================================================================
        # Step 3: Submit a correction (POST /images/{image_id}/corrections)
        # Seed a RecognizedWord so the correction has a valid region target.
        # ================================================================
        with sf() as s:
            # Ensure the image is in ACCEPTED status so correction can find it.
            img_record = s.get(Image, image_id)
            if img_record is not None:
                img_record.status = ImageStatus.ACCEPTED
            # Seed a recognized word.
            word = RecognizedWord(
                image_id=image_id,
                line_index=0,
                word_index=0,
                text="helo",
                region_x=10,
                region_y=10,
                region_width=50,
                region_height=20,
            )
            s.add(word)
            s.commit()

        correction_payload = {
            "region": {"x": 10, "y": 10, "width": 50, "height": 20},
            "recognized_text": "helo",
            "corrected_text": "hello",
        }
        correction_resp = client.post(
            f"/images/{image_id}/corrections",
            json=correction_payload,
            headers=headers,
        )
        assert correction_resp.status_code == 200, (
            f"Correction failed ({correction_resp.status_code}): {correction_resp.text}"
        )
        correction_body = correction_resp.json()
        # Response must be created/updated/no_change
        assert correction_body["status"] in ("created", "updated", "no_change"), (
            f"Unexpected correction status: {correction_body}"
        )
        if correction_body["status"] in ("created", "updated"):
            # A pair_id UUID must be present
            uuid.UUID(correction_body["pair_id"])

        # ================================================================
        # Step 4: Check personalization status (GET /personalization/status)
        # ================================================================
        status_resp = client.get(
            "/personalization/status",
            headers=headers,
        )
        assert status_resp.status_code == 200, (
            f"Personalization status failed ({status_resp.status_code}): {status_resp.text}"
        )
        status_body = status_resp.json()
        # Must contain required fields
        assert "training_state" in status_body
        assert "adapter_active" in status_body
        assert status_body["training_state"] in ("none", "in_progress", "completed")
        assert isinstance(status_body["adapter_active"], bool)

        # ================================================================
        # Step 5: Export text (POST /export)
        # ================================================================
        export_payload = {
            "image_id": str(image_id),
            "format": "plain",
        }
        export_resp = client.post(
            "/export",
            json=export_payload,
            headers=headers,
        )
        assert export_resp.status_code == 200, (
            f"Export failed ({export_resp.status_code}): {export_resp.text}"
        )
        export_body = export_resp.json()
        # Response must be success, empty, or error
        assert export_body["status"] in ("success", "empty", "error"), (
            f"Unexpected export status: {export_body}"
        )

    def test_full_flow_requires_authentication_for_all_steps(
        self, e2e_client
    ) -> None:
        """Assert that all capability endpoints require authentication (Req 10.1).

        An unauthenticated request to each major endpoint must be rejected
        with 401 before any data is stored or read.
        """
        client, sf, store, ss = e2e_client

        fake_image_id = uuid.uuid4()

        # POST /images — unauthenticated
        upload_resp = client.post(
            "/images",
            files={"file": ("hw.png", _make_png_bytes(), "image/png")},
        )
        assert upload_resp.status_code == 401, (
            f"Expected 401 for unauthenticated upload, got {upload_resp.status_code}"
        )

        # POST /images/{id}/recognize — unauthenticated
        recognize_resp = client.post(f"/images/{fake_image_id}/recognize")
        assert recognize_resp.status_code == 401, (
            f"Expected 401 for unauthenticated recognition, got {recognize_resp.status_code}"
        )

        # POST /images/{id}/corrections — unauthenticated
        correction_resp = client.post(
            f"/images/{fake_image_id}/corrections",
            json={
                "region": {"x": 0, "y": 0, "width": 10, "height": 10},
                "recognized_text": "a",
                "corrected_text": "b",
            },
        )
        assert correction_resp.status_code == 401, (
            f"Expected 401 for unauthenticated correction, got {correction_resp.status_code}"
        )

        # GET /personalization/status — unauthenticated
        status_resp = client.get("/personalization/status")
        assert status_resp.status_code == 401, (
            f"Expected 401 for unauthenticated personalization status, got {status_resp.status_code}"
        )

        # POST /export — unauthenticated
        export_resp = client.post(
            "/export",
            json={"image_id": str(fake_image_id), "format": "plain"},
        )
        assert export_resp.status_code == 401, (
            f"Expected 401 for unauthenticated export, got {export_resp.status_code}"
        )

    def test_upload_capability_exposed_via_ui_entrypoint(
        self, e2e_client
    ) -> None:
        """Assert POST /images endpoint is reachable and returns image_id (Req 11.4)."""
        client, sf, store, ss = e2e_client
        _register_user(sf, "e2e_upload")
        token = _login(client, "e2e_upload")

        resp = client.post(
            "/images",
            files={"file": ("hw.png", _make_png_bytes(), "image/png")},
            headers=_auth_header(token),
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["status"] == "accepted"
        # image_id must be a valid UUID
        image_id = uuid.UUID(body["image_id"])
        assert image_id is not None

    def test_recognition_capability_exposed_via_ui_entrypoint(
        self, e2e_client
    ) -> None:
        """Assert POST /images/{id}/recognize is reachable (Req 11.4)."""
        client, sf, store, ss = e2e_client
        _register_user(sf, "e2e_recog")
        token = _login(client, "e2e_recog")
        headers = _auth_header(token)

        # Upload first
        upload_resp = client.post(
            "/images",
            files={"file": ("hw.png", _make_png_bytes(), "image/png")},
            headers=headers,
        )
        assert upload_resp.status_code == 201
        image_id = upload_resp.json()["image_id"]

        from app.ml.recognition import RecognitionResult as MLResult

        mock_result = MLResult(lines=[], recognized_any_text=False)
        with patch(
            "app.services.recognition_service.recognize_bytes",
            return_value=mock_result,
        ):
            recog_resp = client.post(
                f"/images/{image_id}/recognize",
                headers=headers,
            )

        assert recog_resp.status_code == 200
        body = recog_resp.json()
        assert body["outcome"] in ("success", "no_text", "timeout", "failed")

    def test_personalization_status_capability_exposed_via_ui_entrypoint(
        self, e2e_client
    ) -> None:
        """Assert GET /personalization/status is reachable and returns valid schema (Req 11.4)."""
        client, sf, store, ss = e2e_client
        _register_user(sf, "e2e_ps")
        token = _login(client, "e2e_ps")

        resp = client.get(
            "/personalization/status",
            headers=_auth_header(token),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "training_state" in body
        assert "adapter_active" in body
        assert body["training_state"] in ("none", "in_progress", "completed")
        assert isinstance(body["adapter_active"], bool)

    def test_export_capability_exposed_via_ui_entrypoint(
        self, e2e_client
    ) -> None:
        """Assert POST /export is reachable (Req 11.4).

        Uses an image with a seeded recognized word so we get a success export.
        """
        client, sf, store, ss = e2e_client
        _register_user(sf, "e2e_export")
        token = _login(client, "e2e_export")
        headers = _auth_header(token)

        # Upload
        upload_resp = client.post(
            "/images",
            files={"file": ("hw.png", _make_png_bytes(), "image/png")},
            headers=headers,
        )
        assert upload_resp.status_code == 201
        image_id_str = upload_resp.json()["image_id"]
        image_id = uuid.UUID(image_id_str)

        # Seed a recognized word directly so export returns text.
        with sf() as s:
            word = RecognizedWord(
                image_id=image_id,
                line_index=0,
                word_index=0,
                text="hello",
                region_x=0,
                region_y=0,
                region_width=50,
                region_height=20,
            )
            s.add(word)
            s.commit()

        export_resp = client.post(
            "/export",
            json={"image_id": image_id_str, "format": "plain"},
            headers=headers,
        )
        assert export_resp.status_code == 200
        body = export_resp.json()
        assert body["status"] == "success"
        assert "hello" in body["text"]
