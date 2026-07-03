"""Integration tests for partial-deployment routing (task 18.3).

Requirements covered: 11.5, 11.6.

Design
------
These tests exercise *system-level* partial deployment: they build the full
FastAPI app with **all** routers registered, then simulate partial deployments
by toggling environment variables (HND_HTR_ENGINE_AVAILABLE,
HND_TRAINING_WORKER_AVAILABLE, HND_STORAGE_AVAILABLE) via ``monkeypatch``.

The capability guard reads ``os.environ`` at *request time*, so toggling an
env-var in a monkeypatched environment takes effect for the next request
without any restart.

The tests verify:
1. When ``htr_engine`` is disabled:
   - ``POST /images/{id}/recognize`` → 503
   - ``POST /calibration/session`` → 503
   - ``POST /export`` → NOT 503 (storage-only capability)
   - ``POST /images/{id}/corrections`` → NOT 503 (storage-only capability)

2. When ``training_worker`` is disabled:
   - The health endpoint reports ``personalization`` as unavailable
   - All other capabilities remain available per the registry
   - Recognition/correction/export/calibration endpoints do NOT return 503

3. When ``storage`` is disabled:
   - ALL capability endpoints return 503 (every capability depends on storage)
   - Health endpoint reports every capability as unavailable

4. Health endpoint consistency:
   - ``GET /health`` capability booleans match the env-var configuration at
     request time — disabling a component causes the dependent capabilities to
     appear ``false`` in the health response.

Approach
--------
``TestClient`` is used with ``raise_server_exceptions=False`` so that the HTTP
status codes (503, etc.) are inspected directly rather than having the test
client re-raise them as Python exceptions.

The stable ``require_capability`` guards (singleton callables cached in
``capability_guard._GUARDS``) are overridden via
``app.dependency_overrides`` so the env-var simulation approach matches the
approach already used in task 18.2 tests.  However, because this is an
*integration* test of the full routing, we test via env-var patching of
``os.environ`` so the real ``is_capability_available`` path (which reads
``os.environ``) is exercised end-to-end.

We do NOT rely on ``app.dependency_overrides`` for the capability guard here —
instead ``monkeypatch.setenv`` / ``monkeypatch.delenv`` are used to toggle
components, and the real ``_guard()`` function inside
``require_capability`` resolves availability by reading the live environment.
"""

from __future__ import annotations

import uuid
from io import BytesIO
from typing import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image as PILImage
from sqlalchemy.pool import StaticPool

from app.data import Base, RepositoryFactory, User, create_all, create_db_engine, create_session_factory
from app.data.object_store import FilesystemObjectStore, ObjectStore
from app.services.auth import SessionStore, hash_password
from app.services.auth_api import get_db_session, get_session_store, router as auth_router
from app.services.capability_registry import (
    CAPABILITIES,
    get_capability_availability,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_tiny_png() -> bytes:
    """Return bytes of a tiny valid PNG image."""
    img = PILImage.new("RGB", (8, 8), color=(10, 20, 30))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _assert_503(resp, capability: str) -> None:
    """Assert the response is HTTP 503 with the expected structured detail."""
    assert resp.status_code == 503, (
        f"Expected 503 for capability '{capability}', got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    assert "detail" in body, f"Expected 'detail' key in 503 body: {body}"
    detail = body["detail"]
    assert capability in detail, f"Expected '{capability}' in detail: {detail!r}"
    assert "unavailable" in detail, f"Expected 'unavailable' in detail: {detail!r}"


def _assert_not_503(resp, endpoint: str) -> None:
    """Assert the response is NOT 503 (available capabilities must serve)."""
    assert resp.status_code != 503, (
        f"Expected non-503 for available endpoint '{endpoint}', "
        f"but got 503: {resp.text}"
    )


# ---------------------------------------------------------------------------
# Fixtures
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
# Full-app factory (all routers registered)
# ---------------------------------------------------------------------------


def _build_full_app(sf, store: ObjectStore, session_store: SessionStore) -> TestClient:
    """Build a FastAPI app with all capability-guarded routers registered.

    All dependency overrides are applied so the app works with the in-memory
    DB and temp object store.  No capability guards are overridden here —
    availability is determined by ``os.environ`` at request time, which the
    tests manipulate via ``monkeypatch``.
    """
    from app.services.recognition_service import router as recognition_router
    from app.services.correction_service import router as correction_router, get_correction_object_store
    from app.services.calibration_service import router as calibration_router
    from app.services.export_service import router as export_router
    from app.services.images_api import get_object_store, router as images_router
    from app.services.personalization_api import router as personalization_router
    from app.services.capability_registry import get_capability_availability as _gca

    app = FastAPI()

    # --- Auth / DB overrides -----------------------------------------------
    def _db() -> Iterator:
        with sf() as s:
            yield s

    def _ss() -> SessionStore:
        return session_store

    app.dependency_overrides[get_db_session] = _db
    app.dependency_overrides[get_session_store] = _ss
    app.dependency_overrides[get_object_store] = lambda: store
    app.dependency_overrides[get_correction_object_store] = lambda: store

    # --- Health endpoint (reads os.environ at request time via registry) ---
    @app.get("/health")
    def health():
        # Reads os.environ each request — reflects monkeypatched env
        return {"status": "ok", "capabilities": _gca()}

    # --- All guarded routers -----------------------------------------------
    app.include_router(auth_router)
    app.include_router(images_router)
    app.include_router(recognition_router)
    app.include_router(correction_router)
    app.include_router(calibration_router)
    app.include_router(export_router)
    app.include_router(personalization_router)

    return TestClient(app, raise_server_exceptions=False)


def _register_and_login(client: TestClient, sf, username: str = "alice") -> str:
    """Create a user in the DB and return a bearer token."""
    with sf() as s:
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


# ===========================================================================
# 1. Disabling htr_engine: recognition + calibration → 503; others serve
# ===========================================================================


class TestHTREngineDisabled:
    """When HND_HTR_ENGINE_AVAILABLE=false, recognition and calibration are 503;
    correction and export continue to serve (Req 11.5, 11.6)."""

    @pytest.fixture()
    def client_with_env(self, sf, store, session_store, monkeypatch):
        monkeypatch.setenv("HND_HTR_ENGINE_AVAILABLE", "false")
        client = _build_full_app(sf, store, session_store)
        token = _register_and_login(client, sf)
        return client, token

    def test_recognize_returns_503(self, client_with_env):
        """POST /images/{id}/recognize → 503 when htr_engine is off."""
        client, token = client_with_env
        fake_id = uuid.uuid4()
        resp = client.post(f"/images/{fake_id}/recognize", headers=_auth(token))
        _assert_503(resp, "recognition")

    def test_calibration_session_returns_503(self, client_with_env):
        """POST /calibration/session → 503 when htr_engine is off."""
        client, token = client_with_env
        resp = client.post("/calibration/session", headers=_auth(token))
        _assert_503(resp, "calibration")

    def test_export_does_not_return_503(self, client_with_env):
        """POST /export → NOT 503 when only htr_engine is off (storage available)."""
        client, token = client_with_env
        fake_id = uuid.uuid4()
        resp = client.post(
            "/export",
            json={"image_id": str(fake_id), "format": "plain"},
            headers=_auth(token),
        )
        _assert_not_503(resp, "POST /export")

    def test_corrections_do_not_return_503(self, client_with_env):
        """POST /images/{id}/corrections → NOT 503 when only htr_engine is off."""
        client, token = client_with_env
        fake_id = uuid.uuid4()
        resp = client.post(
            f"/images/{fake_id}/corrections",
            json={
                "region": {"x": 0, "y": 0, "width": 10, "height": 10},
                "recognized_text": "hi",
                "corrected_text": "bye",
            },
            headers=_auth(token),
        )
        _assert_not_503(resp, "POST /images/{id}/corrections")

    def test_health_reflects_htr_engine_disabled(self, client_with_env):
        """GET /health correctly reports recognition and calibration as unavailable."""
        client, _ = client_with_env
        body = client.get("/health").json()
        caps = body["capabilities"]
        # htr_engine-dependent capabilities must be False
        assert caps["recognition"] is False, "recognition should be unavailable"
        assert caps["calibration"] is False, "calibration should be unavailable"
        # storage-only capabilities must still be True
        assert caps["correction"] is True, "correction should still be available"
        assert caps["export"] is True, "export should still be available"
        assert caps["image_upload"] is True, "image_upload should still be available"


# ===========================================================================
# 2. Disabling training_worker: personalization unavailable; others serve
# ===========================================================================


class TestTrainingWorkerDisabled:
    """When HND_TRAINING_WORKER_AVAILABLE=false, personalization is unavailable
    but all other capabilities continue to serve (Req 11.5, 11.6)."""

    @pytest.fixture()
    def client_with_env(self, sf, store, session_store, monkeypatch):
        monkeypatch.setenv("HND_TRAINING_WORKER_AVAILABLE", "false")
        client = _build_full_app(sf, store, session_store)
        token = _register_and_login(client, sf)
        return client, token

    def test_health_marks_personalization_unavailable(self, client_with_env):
        """GET /health reports personalization unavailable when training_worker is off."""
        client, _ = client_with_env
        body = client.get("/health").json()
        caps = body["capabilities"]
        assert caps["personalization"] is False, "personalization should be unavailable"

    def test_health_other_capabilities_remain_available(self, client_with_env):
        """GET /health reports all non-personalization capabilities as available."""
        client, _ = client_with_env
        body = client.get("/health").json()
        caps = body["capabilities"]
        for cap in ("image_upload", "recognition", "correction", "export", "calibration"):
            assert caps[cap] is True, f"'{cap}' should remain available when training_worker is off"

    def test_recognition_does_not_return_503(self, client_with_env):
        """POST /images/{id}/recognize → NOT 503 when only training_worker is off."""
        client, token = client_with_env
        fake_id = uuid.uuid4()
        resp = client.post(f"/images/{fake_id}/recognize", headers=_auth(token))
        _assert_not_503(resp, "POST /images/{id}/recognize")

    def test_corrections_do_not_return_503(self, client_with_env):
        """POST /images/{id}/corrections → NOT 503 when only training_worker is off."""
        client, token = client_with_env
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
        _assert_not_503(resp, "POST /images/{id}/corrections")

    def test_export_does_not_return_503(self, client_with_env):
        """POST /export → NOT 503 when only training_worker is off."""
        client, token = client_with_env
        fake_id = uuid.uuid4()
        resp = client.post(
            "/export",
            json={"image_id": str(fake_id), "format": "plain"},
            headers=_auth(token),
        )
        _assert_not_503(resp, "POST /export")

    def test_calibration_does_not_return_503(self, client_with_env):
        """POST /calibration/session → NOT 503 when only training_worker is off."""
        client, token = client_with_env
        resp = client.post("/calibration/session", headers=_auth(token))
        _assert_not_503(resp, "POST /calibration/session")


# ===========================================================================
# 3. Disabling storage: ALL capability endpoints return 503
# ===========================================================================


class TestStorageDisabled:
    """When HND_STORAGE_AVAILABLE=false, every capability endpoint returns 503
    because all capabilities require storage (Req 11.5, 11.6)."""

    @pytest.fixture()
    def client_with_env(self, sf, store, session_store, monkeypatch):
        monkeypatch.setenv("HND_STORAGE_AVAILABLE", "false")
        client = _build_full_app(sf, store, session_store)
        token = _register_and_login(client, sf)
        return client, token

    def test_recognize_returns_503(self, client_with_env):
        """POST /images/{id}/recognize → 503 when storage is off."""
        client, token = client_with_env
        fake_id = uuid.uuid4()
        resp = client.post(f"/images/{fake_id}/recognize", headers=_auth(token))
        _assert_503(resp, "recognition")

    def test_calibration_returns_503(self, client_with_env):
        """POST /calibration/session → 503 when storage is off."""
        client, token = client_with_env
        resp = client.post("/calibration/session", headers=_auth(token))
        _assert_503(resp, "calibration")

    def test_export_returns_503(self, client_with_env):
        """POST /export → 503 when storage is off."""
        client, token = client_with_env
        fake_id = uuid.uuid4()
        resp = client.post(
            "/export",
            json={"image_id": str(fake_id), "format": "plain"},
            headers=_auth(token),
        )
        _assert_503(resp, "export")

    def test_corrections_return_503(self, client_with_env):
        """POST /images/{id}/corrections → 503 when storage is off."""
        client, token = client_with_env
        fake_id = uuid.uuid4()
        resp = client.post(
            f"/images/{fake_id}/corrections",
            json={
                "region": {"x": 0, "y": 0, "width": 10, "height": 10},
                "recognized_text": "hi",
                "corrected_text": "bye",
            },
            headers=_auth(token),
        )
        _assert_503(resp, "correction")

    def test_health_marks_all_capabilities_unavailable(self, client_with_env):
        """GET /health reports every capability as unavailable when storage is off."""
        client, _ = client_with_env
        body = client.get("/health").json()
        caps = body["capabilities"]
        for cap in CAPABILITIES:
            assert caps[cap] is False, (
                f"'{cap}' should be unavailable when storage is off, got {caps[cap]}"
            )


# ===========================================================================
# 4. Health endpoint consistency with routing behaviour
# ===========================================================================


class TestHealthConsistency:
    """GET /health capability booleans must be consistent with actual routing.

    Each test:
    1. Sets up a partial deployment via env-var.
    2. Calls GET /health to get the reported capability availability.
    3. Attempts the corresponding endpoint and verifies the HTTP response code
       matches what /health advertised.
    """

    def test_full_deployment_all_capabilities_available(
        self, sf, store, session_store, monkeypatch
    ):
        """All components enabled → health shows all true, no endpoint returns 503."""
        # Ensure env vars are explicitly set to true values
        monkeypatch.setenv("HND_STORAGE_AVAILABLE", "true")
        monkeypatch.setenv("HND_HTR_ENGINE_AVAILABLE", "true")
        monkeypatch.setenv("HND_TRAINING_WORKER_AVAILABLE", "true")

        client = _build_full_app(sf, store, session_store)
        token = _register_and_login(client, sf)

        body = client.get("/health").json()
        caps = body["capabilities"]

        # All capabilities should be true
        for cap in CAPABILITIES:
            assert caps[cap] is True, f"'{cap}' should be available in full deployment"

        fake_id = uuid.uuid4()

        # Verify guarded endpoints do not return 503
        recognize_resp = client.post(f"/images/{fake_id}/recognize", headers=_auth(token))
        assert recognize_resp.status_code != 503

        calibration_resp = client.post("/calibration/session", headers=_auth(token))
        assert calibration_resp.status_code != 503

        export_resp = client.post(
            "/export",
            json={"image_id": str(fake_id), "format": "plain"},
            headers=_auth(token),
        )
        assert export_resp.status_code != 503

        correction_resp = client.post(
            f"/images/{fake_id}/corrections",
            json={
                "region": {"x": 0, "y": 0, "width": 10, "height": 10},
                "recognized_text": "hi",
                "corrected_text": "bye",
            },
            headers=_auth(token),
        )
        assert correction_resp.status_code != 503

    def test_htr_engine_off_health_matches_routing(
        self, sf, store, session_store, monkeypatch
    ):
        """Health reports recognition=False and calibration=False when htr_engine is off,
        and those endpoints return 503 while others don't."""
        monkeypatch.setenv("HND_HTR_ENGINE_AVAILABLE", "false")

        client = _build_full_app(sf, store, session_store)
        token = _register_and_login(client, sf)
        fake_id = uuid.uuid4()

        caps = client.get("/health").json()["capabilities"]

        # Verify health report
        assert caps["recognition"] is False
        assert caps["calibration"] is False
        assert caps["correction"] is True
        assert caps["export"] is True

        # Verify routing matches health
        rec_resp = client.post(f"/images/{fake_id}/recognize", headers=_auth(token))
        assert rec_resp.status_code == 503, "health says recognition=false → must be 503"

        cal_resp = client.post("/calibration/session", headers=_auth(token))
        assert cal_resp.status_code == 503, "health says calibration=false → must be 503"

        exp_resp = client.post(
            "/export",
            json={"image_id": str(fake_id), "format": "plain"},
            headers=_auth(token),
        )
        assert exp_resp.status_code != 503, "health says export=true → must NOT be 503"

        corr_resp = client.post(
            f"/images/{fake_id}/corrections",
            json={
                "region": {"x": 0, "y": 0, "width": 10, "height": 10},
                "recognized_text": "hi",
                "corrected_text": "bye",
            },
            headers=_auth(token),
        )
        assert corr_resp.status_code != 503, "health says correction=true → must NOT be 503"

    def test_storage_off_health_matches_routing_all_503(
        self, sf, store, session_store, monkeypatch
    ):
        """When storage is off, health reports all capabilities as false and
        every guarded endpoint returns 503."""
        monkeypatch.setenv("HND_STORAGE_AVAILABLE", "false")

        client = _build_full_app(sf, store, session_store)
        token = _register_and_login(client, sf)
        fake_id = uuid.uuid4()

        caps = client.get("/health").json()["capabilities"]
        for cap in CAPABILITIES:
            assert caps[cap] is False, f"storage off → '{cap}' should be false in health"

        # All guarded endpoints must return 503
        for cap, method, url, kwargs in [
            ("recognition", "POST", f"/images/{fake_id}/recognize", {}),
            ("calibration", "POST", "/calibration/session", {}),
            ("export", "POST", "/export",
             {"json": {"image_id": str(fake_id), "format": "plain"}}),
            ("correction", "POST", f"/images/{fake_id}/corrections",
             {"json": {
                 "region": {"x": 0, "y": 0, "width": 10, "height": 10},
                 "recognized_text": "hi",
                 "corrected_text": "bye",
             }}),
        ]:
            resp = client.request(method, url, headers=_auth(token), **kwargs)
            assert resp.status_code == 503, (
                f"storage off + health['{cap}']=false → endpoint {url} must return 503, "
                f"got {resp.status_code}"
            )

    def test_503_body_format_matches_design_spec(
        self, sf, store, session_store, monkeypatch
    ):
        """The 503 body must use the FastAPI HTTPException envelope with the exact
        structured detail string (Req 11.6)."""
        monkeypatch.setenv("HND_HTR_ENGINE_AVAILABLE", "false")

        client = _build_full_app(sf, store, session_store)
        token = _register_and_login(client, sf)
        fake_id = uuid.uuid4()

        resp = client.post(f"/images/{fake_id}/recognize", headers=_auth(token))
        assert resp.status_code == 503
        body = resp.json()
        assert body["detail"] == "capability 'recognition' is unavailable in this deployment"

        resp2 = client.post("/calibration/session", headers=_auth(token))
        assert resp2.status_code == 503
        body2 = resp2.json()
        assert body2["detail"] == "capability 'calibration' is unavailable in this deployment"

from unittest.mock import MagicMock
@pytest.fixture()
def db_session():
    return MagicMock()
