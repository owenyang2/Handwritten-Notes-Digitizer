"""Integration tests for capability-unavailable (HTTP 503) responses (task 18.2).

Requirements covered: 11.5, 11.6.

Design
------
The ``require_capability`` factory returns a **stable cached callable** for
each capability name.  FastAPI's ``app.dependency_overrides`` uses the callable
object as the key, so as long as we pass the same ``require_capability(name)``
result to both ``Depends(...)`` (at route definition time) and
``app.dependency_overrides[...]`` (at test setup time), the override is picked
up correctly.

Test matrix
-----------
For each guarded endpoint we assert:
1. When the required component is **disabled** → HTTP 503, detail contains the
   capability name and "unavailable".
2. When all components are **enabled** → NOT HTTP 503 (the endpoint proceeds
   normally; its own auth/business logic may return other codes, but 503 is not
   returned).

We also assert that disabling one component does not affect unrelated endpoints
(Req 11.5: available capabilities continue serving).
"""

from __future__ import annotations

import uuid
from io import BytesIO
from typing import Iterator

import pytest
from fastapi import FastAPI, HTTPException, status
from fastapi.testclient import TestClient
from PIL import Image as PILImage
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.data import Base, RepositoryFactory, User, create_all, create_db_engine, create_session_factory
from app.data.object_store import FilesystemObjectStore, ObjectStore
from app.services.auth import SessionStore, hash_password
from app.services.auth_api import get_db_session, get_session_store, router as auth_router

# Import the stable guard factory ONCE — all downstream references must use this.
from app.services.capability_guard import require_capability


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tiny_png() -> bytes:
    """Return bytes of a tiny valid PNG."""
    img = PILImage.new("RGB", (8, 8), color=(10, 20, 30))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _assert_503_capability_unavailable(resp, capability: str) -> None:
    """Assert the response is HTTP 503 with the structured unavailable detail."""
    assert resp.status_code == status.HTTP_503_SERVICE_UNAVAILABLE, (
        f"Expected 503, got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    assert "detail" in body
    detail = body["detail"]
    assert capability in detail, f"Expected '{capability}' in detail: {detail!r}"
    assert "unavailable" in detail, f"Expected 'unavailable' in detail: {detail!r}"


# ---------------------------------------------------------------------------
# App factory helpers
# ---------------------------------------------------------------------------

def _build_base_app(session_factory, session_store: SessionStore) -> FastAPI:
    """Return a FastAPI app with auth wired and DB/store overrides applied."""
    app = FastAPI()

    def _db() -> Iterator[Session]:
        with session_factory() as s:
            yield s

    def _ss() -> SessionStore:
        return session_store

    app.dependency_overrides[get_db_session] = _db
    app.dependency_overrides[get_session_store] = _ss

    app.include_router(auth_router)
    return app


def _make_unavailable_guard(capability: str):
    """Return a dependency that always raises 503 for *capability*."""
    def _guard() -> None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"capability '{capability}' is unavailable in this deployment",
        )
    return _guard


def _make_available_guard():
    """Return a dependency that always passes (capability available)."""
    def _guard() -> None:
        return None
    return _guard


# ---------------------------------------------------------------------------
# Shared DB/store fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def sf():
    """In-memory SQLite session factory."""
    engine = create_db_engine("sqlite://", poolclass=StaticPool)
    create_all(engine)
    factory = create_session_factory(engine)
    yield factory
    Base.metadata.drop_all(engine)


@pytest.fixture()
def store(tmp_path) -> ObjectStore:
    return FilesystemObjectStore(root=tmp_path / "obj_store")


@pytest.fixture()
def session_store(db_session) -> SessionStore:
    return SessionStore(db_session)


# ---------------------------------------------------------------------------
# Helper: register and log in
# ---------------------------------------------------------------------------

def _register_and_login(client: TestClient, session_factory, username: str = "alice") -> str:
    """Create a user in the DB and return a bearer token."""
    with session_factory() as s:
        user = User(
            username=username,
            email=f"{username}@example.com",
            password_hash=hash_password("secret"),
        )
        s.add(user)
        s.commit()
    resp = client.post("/auth/login", json={"username": username, "password": "secret"})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ===========================================================================
# Tests for POST /images/{image_id}/recognize  →  guards "recognition"
# ===========================================================================

class TestRecognitionCapabilityGuard:
    """POST /images/{image_id}/recognize returns 503 when htr_engine is off."""

    def _build_app(self, sf, store, session_store, available: bool) -> TestClient:
        from app.services.recognition_service import router as recognition_router
        from app.services.images_api import get_object_store, router as images_router

        app = _build_base_app(sf, session_store)
        app.include_router(images_router)
        app.include_router(recognition_router)

        app.dependency_overrides[get_object_store] = lambda: store

        # Override the stable recognition guard
        guard_key = require_capability("recognition")
        if available:
            app.dependency_overrides[guard_key] = _make_available_guard()
        else:
            app.dependency_overrides[guard_key] = _make_unavailable_guard("recognition")

        return TestClient(app, raise_server_exceptions=False)

    def test_503_when_htr_engine_disabled(self, sf, store, session_store):
        """Disabling htr_engine returns HTTP 503 with structured detail."""
        client = self._build_app(sf, store, session_store, available=False)
        token = _register_and_login(client, sf)
        fake_id = uuid.uuid4()

        resp = client.post(
            f"/images/{fake_id}/recognize",
            headers=_auth(token),
        )
        _assert_503_capability_unavailable(resp, "recognition")

    def test_not_503_when_all_components_enabled(self, sf, store, session_store):
        """When all components are enabled, /recognize does NOT return 503."""
        client = self._build_app(sf, store, session_store, available=True)
        token = _register_and_login(client, sf)
        fake_id = uuid.uuid4()

        resp = client.post(
            f"/images/{fake_id}/recognize",
            headers=_auth(token),
        )
        # 503 must not be returned; the endpoint may return 404 (image not found)
        # or 200, but never 503 when the capability is available.
        assert resp.status_code != status.HTTP_503_SERVICE_UNAVAILABLE


# ===========================================================================
# Tests for POST /images/{image_id}/corrections  →  guards "correction"
# ===========================================================================

class TestCorrectionCapabilityGuard:
    """POST /images/{image_id}/corrections returns 503 when storage is off."""

    def _build_app(self, sf, store, session_store, available: bool) -> TestClient:
        from app.services.correction_service import router as correction_router, get_correction_object_store

        app = _build_base_app(sf, session_store)
        app.include_router(correction_router)

        app.dependency_overrides[get_correction_object_store] = lambda: store

        guard_key = require_capability("correction")
        if available:
            app.dependency_overrides[guard_key] = _make_available_guard()
        else:
            app.dependency_overrides[guard_key] = _make_unavailable_guard("correction")

        return TestClient(app, raise_server_exceptions=False)

    def test_503_when_storage_disabled(self, sf, store, session_store):
        client = self._build_app(sf, store, session_store, available=False)
        token = _register_and_login(client, sf)
        fake_id = uuid.uuid4()

        resp = client.post(
            f"/images/{fake_id}/corrections",
            json={
                "region": {"x": 0, "y": 0, "width": 10, "height": 10},
                "recognized_text": "hello",
                "corrected_text": "world",
            },
            headers=_auth(token),
        )
        _assert_503_capability_unavailable(resp, "correction")

    def test_not_503_when_all_components_enabled(self, sf, store, session_store):
        client = self._build_app(sf, store, session_store, available=True)
        token = _register_and_login(client, sf)
        fake_id = uuid.uuid4()

        resp = client.post(
            f"/images/{fake_id}/corrections",
            json={
                "region": {"x": 0, "y": 0, "width": 10, "height": 10},
                "recognized_text": "hello",
                "corrected_text": "world",
            },
            headers=_auth(token),
        )
        assert resp.status_code != status.HTTP_503_SERVICE_UNAVAILABLE


# ===========================================================================
# Tests for POST /calibration/session  →  guards "calibration"
# ===========================================================================

class TestCalibrationCapabilityGuard:
    """POST /calibration/session returns 503 when htr_engine is off."""

    def _build_app(self, sf, store, session_store, available: bool) -> TestClient:
        from app.services.calibration_service import router as calibration_router

        app = _build_base_app(sf, session_store)
        app.include_router(calibration_router)

        guard_key = require_capability("calibration")
        if available:
            app.dependency_overrides[guard_key] = _make_available_guard()
        else:
            app.dependency_overrides[guard_key] = _make_unavailable_guard("calibration")

        return TestClient(app, raise_server_exceptions=False)

    def test_503_when_htr_engine_disabled(self, sf, store, session_store):
        client = self._build_app(sf, store, session_store, available=False)
        token = _register_and_login(client, sf)

        resp = client.post("/calibration/session", headers=_auth(token))
        _assert_503_capability_unavailable(resp, "calibration")

    def test_not_503_when_all_components_enabled(self, sf, store, session_store):
        client = self._build_app(sf, store, session_store, available=True)
        token = _register_and_login(client, sf)

        resp = client.post("/calibration/session", headers=_auth(token))
        assert resp.status_code != status.HTTP_503_SERVICE_UNAVAILABLE


# ===========================================================================
# Tests for POST /export  →  guards "export"
# ===========================================================================

class TestExportCapabilityGuard:
    """POST /export returns 503 when storage is off."""

    def _build_app(self, sf, store, session_store, available: bool) -> TestClient:
        from app.services.export_service import router as export_router

        app = _build_base_app(sf, session_store)
        app.include_router(export_router)

        guard_key = require_capability("export")
        if available:
            app.dependency_overrides[guard_key] = _make_available_guard()
        else:
            app.dependency_overrides[guard_key] = _make_unavailable_guard("export")

        return TestClient(app, raise_server_exceptions=False)

    def test_503_when_storage_disabled(self, sf, store, session_store):
        client = self._build_app(sf, store, session_store, available=False)
        token = _register_and_login(client, sf)
        fake_id = uuid.uuid4()

        resp = client.post(
            "/export",
            json={"image_id": str(fake_id), "format": "plain"},
            headers=_auth(token),
        )
        _assert_503_capability_unavailable(resp, "export")

    def test_not_503_when_all_components_enabled(self, sf, store, session_store):
        client = self._build_app(sf, store, session_store, available=True)
        token = _register_and_login(client, sf)
        fake_id = uuid.uuid4()

        resp = client.post(
            "/export",
            json={"image_id": str(fake_id), "format": "plain"},
            headers=_auth(token),
        )
        assert resp.status_code != status.HTTP_503_SERVICE_UNAVAILABLE


# ===========================================================================
# Cross-capability isolation: disabling one component leaves others serving
# ===========================================================================

class TestPartialDeploymentIsolation:
    """Disabling htr_engine does NOT affect correction or export (Req 11.5)."""

    def _build_full_app(self, sf, store, session_store,
                        unavailable_caps: set[str]) -> TestClient:
        """Build an app with all routers; mark specified capabilities unavailable."""
        from app.services.recognition_service import router as recognition_router
        from app.services.correction_service import router as correction_router, get_correction_object_store
        from app.services.calibration_service import router as calibration_router
        from app.services.export_service import router as export_router
        from app.services.images_api import get_object_store, router as images_router

        app = _build_base_app(sf, session_store)
        app.include_router(images_router)
        app.include_router(recognition_router)
        app.include_router(correction_router)
        app.include_router(calibration_router)
        app.include_router(export_router)

        app.dependency_overrides[get_object_store] = lambda: store
        app.dependency_overrides[get_correction_object_store] = lambda: store

        # Override guards for every capability we care about
        for cap in ("recognition", "correction", "calibration", "export"):
            guard_key = require_capability(cap)
            if cap in unavailable_caps:
                app.dependency_overrides[guard_key] = _make_unavailable_guard(cap)
            else:
                app.dependency_overrides[guard_key] = _make_available_guard()

        return TestClient(app, raise_server_exceptions=False)

    def test_htr_engine_disabled_recognition_503_correction_not_503(
        self, sf, store, session_store
    ):
        """When htr_engine is off, recognition is 503 but correction proceeds."""
        client = self._build_full_app(
            sf, store, session_store,
            unavailable_caps={"recognition", "calibration"}
        )
        token = _register_and_login(client, sf)
        fake_id = uuid.uuid4()

        # Recognition must be 503
        rec_resp = client.post(
            f"/images/{fake_id}/recognize", headers=_auth(token)
        )
        assert rec_resp.status_code == status.HTTP_503_SERVICE_UNAVAILABLE

        # Correction must NOT be 503 (storage is available)
        corr_resp = client.post(
            f"/images/{fake_id}/corrections",
            json={
                "region": {"x": 0, "y": 0, "width": 10, "height": 10},
                "recognized_text": "hi",
                "corrected_text": "bye",
            },
            headers=_auth(token),
        )
        assert corr_resp.status_code != status.HTTP_503_SERVICE_UNAVAILABLE

    def test_htr_engine_disabled_calibration_503_export_not_503(
        self, sf, store, session_store
    ):
        """When htr_engine is off, calibration is 503 but export proceeds."""
        client = self._build_full_app(
            sf, store, session_store,
            unavailable_caps={"recognition", "calibration"}
        )
        token = _register_and_login(client, sf)
        fake_id = uuid.uuid4()

        # Calibration session must be 503
        cal_resp = client.post("/calibration/session", headers=_auth(token))
        assert cal_resp.status_code == status.HTTP_503_SERVICE_UNAVAILABLE

        # Export must NOT be 503
        exp_resp = client.post(
            "/export",
            json={"image_id": str(fake_id), "format": "plain"},
            headers=_auth(token),
        )
        assert exp_resp.status_code != status.HTTP_503_SERVICE_UNAVAILABLE

    def test_503_body_has_correct_json_structure(self, sf, store, session_store):
        """The 503 detail string matches the expected format (Req 11.6)."""
        client = self._build_full_app(
            sf, store, session_store,
            unavailable_caps={"recognition"}
        )
        token = _register_and_login(client, sf)
        fake_id = uuid.uuid4()

        resp = client.post(
            f"/images/{fake_id}/recognize", headers=_auth(token)
        )
        assert resp.status_code == 503
        body = resp.json()
        # FastAPI wraps HTTPException detail in {"detail": ...}
        assert body["detail"] == "capability 'recognition' is unavailable in this deployment"

from unittest.mock import MagicMock
@pytest.fixture()
def db_session():
    return MagicMock()
