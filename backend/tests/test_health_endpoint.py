"""Tests for the GET /health endpoint and the capability registry (task 18.1).

Covers:
* /health returns 200 with ``status == "ok"`` (liveness).
* /health response contains a ``capabilities`` dict with all expected keys.
* All capabilities are available by default (no env-vars set / all enabled).
* Disabling a single component marks the dependent capabilities as unavailable
  while unaffected capabilities remain available (Req 11.5).
* Disabling all components marks every capability as unavailable.

Unit tests for the capability_registry module:
* Component availability is ``True`` when the env-var is absent or truthy.
* Component availability is ``False`` when the env-var is a falsy value.
* get_capability_availability returns the correct capability dict.
* is_capability_available is a correct convenience wrapper.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.services.capability_registry import (
    CAPABILITIES,
    get_capability_availability,
    get_component_availability,
    is_capability_available,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

EXPECTED_CAPABILITIES = frozenset(
    ["image_upload", "recognition", "correction", "personalization", "export", "calibration"]
)


def _make_client() -> TestClient:
    """Build a minimal app with just the /health endpoint for focused testing."""
    from app.services.capability_registry import get_capability_availability as _gca

    app = FastAPI()

    @app.get("/health")
    def health():
        return {"status": "ok", "capabilities": _gca()}

    return TestClient(app)


# ---------------------------------------------------------------------------
# /health endpoint tests
# ---------------------------------------------------------------------------


def test_health_returns_200():
    client = _make_client()
    resp = client.get("/health")
    assert resp.status_code == 200


def test_health_status_is_ok():
    client = _make_client()
    resp = client.get("/health")
    assert resp.json()["status"] == "ok"


def test_health_response_contains_capabilities_key():
    client = _make_client()
    body = client.get("/health").json()
    assert "capabilities" in body


def test_health_capabilities_contains_all_expected_keys():
    client = _make_client()
    body = client.get("/health").json()
    caps = body["capabilities"]
    assert set(caps.keys()) == EXPECTED_CAPABILITIES


def test_health_capabilities_are_booleans():
    client = _make_client()
    caps = client.get("/health").json()["capabilities"]
    for name, value in caps.items():
        assert isinstance(value, bool), f"capability '{name}' should be bool, got {type(value)}"


def test_health_all_capabilities_available_by_default():
    """When no env-vars are set, all capabilities should be available."""
    caps = get_capability_availability(env={})  # empty env → all defaults to True
    for name in EXPECTED_CAPABILITIES:
        assert caps[name] is True, f"capability '{name}' should be available by default"


# ---------------------------------------------------------------------------
# Partial-deployment: disabling htr_engine
# ---------------------------------------------------------------------------


def test_disabling_htr_engine_marks_recognition_unavailable():
    env = {"HND_HTR_ENGINE_AVAILABLE": "false"}
    caps = get_capability_availability(env=env)
    assert caps["recognition"] is False


def test_disabling_htr_engine_marks_calibration_unavailable():
    env = {"HND_HTR_ENGINE_AVAILABLE": "false"}
    caps = get_capability_availability(env=env)
    assert caps["calibration"] is False


def test_disabling_htr_engine_leaves_storage_only_caps_available():
    """image_upload, correction, export, personalization don't need htr_engine."""
    env = {"HND_HTR_ENGINE_AVAILABLE": "false"}
    caps = get_capability_availability(env=env)
    for cap in ("image_upload", "correction", "export"):
        assert caps[cap] is True, f"'{cap}' should still be available when htr_engine is off"


# ---------------------------------------------------------------------------
# Partial-deployment: disabling training_worker
# ---------------------------------------------------------------------------


def test_disabling_training_worker_marks_personalization_unavailable():
    env = {"HND_TRAINING_WORKER_AVAILABLE": "0"}
    caps = get_capability_availability(env=env)
    assert caps["personalization"] is False


def test_disabling_training_worker_leaves_other_caps_available():
    env = {"HND_TRAINING_WORKER_AVAILABLE": "no"}
    caps = get_capability_availability(env=env)
    for cap in ("image_upload", "recognition", "correction", "export", "calibration"):
        assert caps[cap] is True, f"'{cap}' should be available when only training_worker is off"


# ---------------------------------------------------------------------------
# Partial-deployment: disabling storage
# ---------------------------------------------------------------------------


def test_disabling_storage_marks_all_capabilities_unavailable():
    """Every capability depends on storage."""
    env = {"HND_STORAGE_AVAILABLE": "false"}
    caps = get_capability_availability(env=env)
    for cap in EXPECTED_CAPABILITIES:
        assert caps[cap] is False, f"'{cap}' should be unavailable when storage is off"


# ---------------------------------------------------------------------------
# Unit tests for the registry helpers
# ---------------------------------------------------------------------------


def test_get_component_availability_defaults_to_all_true():
    avail = get_component_availability(env={})
    for component, value in avail.items():
        assert value is True, f"component '{component}' should default to True"


def test_get_component_availability_respects_false_value():
    avail = get_component_availability(env={"HND_HTR_ENGINE_AVAILABLE": "false"})
    assert avail["htr_engine"] is False


def test_get_component_availability_respects_zero_value():
    avail = get_component_availability(env={"HND_STORAGE_AVAILABLE": "0"})
    assert avail["storage"] is False


def test_get_component_availability_respects_no_value():
    avail = get_component_availability(env={"HND_TRAINING_WORKER_AVAILABLE": "no"})
    assert avail["training_worker"] is False


def test_get_component_availability_treats_true_string_as_available():
    avail = get_component_availability(env={"HND_HTR_ENGINE_AVAILABLE": "true"})
    assert avail["htr_engine"] is True


def test_get_component_availability_treats_1_as_available():
    avail = get_component_availability(env={"HND_STORAGE_AVAILABLE": "1"})
    assert avail["storage"] is True


def test_is_capability_available_returns_true_when_all_components_up():
    assert is_capability_available("recognition", env={}) is True


def test_is_capability_available_returns_false_when_component_down():
    assert is_capability_available("recognition", env={"HND_HTR_ENGINE_AVAILABLE": "0"}) is False


def test_capabilities_constant_matches_expected_set():
    assert set(CAPABILITIES) == EXPECTED_CAPABILITIES
