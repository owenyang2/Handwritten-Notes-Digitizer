"""Unit tests for the Calibration_Service — task 12.1.

Covers:
* ``create_calibration_session`` business logic:
  - Returns between MIN_PROMPTS and MAX_PROMPTS prompts.
  - Each prompt is between 1 and MAX_PROMPT_LENGTH characters.
  - Session is stored in the provided session_store.
  - No duplicate prompts in a single session.
  - Custom num_prompts works across boundary values.
  - Out-of-range num_prompts raises ValueError.
  - Each call produces a distinct session_id.
* ``get_session_prompts``:
  - Returns prompts for a known session.
  - Returns None for an unknown session_id.
* ``POST /calibration/session`` HTTP endpoint:
  - Authenticated request returns 201 with session_id + prompts.
  - Each prompt is 1–200 chars, count 3–10.
  - Unauthenticated request returns 401 (Req 9.1 — auth still required).
  - Multiple calls produce different session_ids.

Requirements validated: 9.1, 9.2
"""

from __future__ import annotations

import uuid
from typing import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.data import Base, RepositoryFactory, User, create_all, create_db_engine, create_session_factory
from app.services.auth import SessionStore, hash_password
from app.services import auth_api
from app.services.auth_api import (
    AUTH_REQUIRED_MESSAGE,
    get_current_user,
    get_db_session,
    get_session_store,
    router as auth_router,
)
from app.services.calibration_service import (
    MAX_PROMPT_LENGTH,
    MAX_PROMPTS,
    MIN_PROMPTS,
    CalibrationSession,
    create_calibration_session,
    get_session_prompts,
    router as calibration_router,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fresh_store() -> dict:
    """Return a fresh in-memory session_store for test isolation."""
    return {}


# ---------------------------------------------------------------------------
# Unit tests: create_calibration_session
# ---------------------------------------------------------------------------


def test_create_session_returns_calibration_session_instance() -> None:
    store: dict = _fresh_store()
    result = create_calibration_session(session_store=store)
    assert isinstance(result, CalibrationSession)


def test_create_session_prompt_count_within_bounds() -> None:
    store: dict = _fresh_store()
    result = create_calibration_session(session_store=store)
    assert MIN_PROMPTS <= len(result.prompts) <= MAX_PROMPTS


def test_create_session_each_prompt_within_length_bounds() -> None:
    store: dict = _fresh_store()
    result = create_calibration_session(session_store=store)
    for prompt in result.prompts:
        assert 1 <= len(prompt) <= MAX_PROMPT_LENGTH, (
            f"Prompt out of length bounds: {prompt!r}"
        )


def test_create_session_no_duplicate_prompts_in_session() -> None:
    store: dict = _fresh_store()
    result = create_calibration_session(session_store=store)
    assert len(result.prompts) == len(set(result.prompts))


def test_create_session_stored_under_returned_session_id() -> None:
    store: dict = _fresh_store()
    result = create_calibration_session(session_store=store)
    assert result.session_id in store
    assert store[result.session_id] == result.prompts


def test_create_session_distinct_session_ids_across_calls() -> None:
    store: dict = _fresh_store()
    ids = {create_calibration_session(session_store=store).session_id for _ in range(20)}
    assert len(ids) == 20


def test_create_session_custom_num_prompts_min() -> None:
    store: dict = _fresh_store()
    result = create_calibration_session(num_prompts=MIN_PROMPTS, session_store=store)
    assert len(result.prompts) == MIN_PROMPTS


def test_create_session_custom_num_prompts_max() -> None:
    store: dict = _fresh_store()
    result = create_calibration_session(num_prompts=MAX_PROMPTS, session_store=store)
    assert len(result.prompts) == MAX_PROMPTS


def test_create_session_custom_num_prompts_mid() -> None:
    store: dict = _fresh_store()
    for n in range(MIN_PROMPTS, MAX_PROMPTS + 1):
        result = create_calibration_session(num_prompts=n, session_store=store)
        assert len(result.prompts) == n


def test_create_session_raises_for_too_few_prompts() -> None:
    store: dict = _fresh_store()
    with pytest.raises(ValueError, match="num_prompts"):
        create_calibration_session(num_prompts=MIN_PROMPTS - 1, session_store=store)


def test_create_session_raises_for_too_many_prompts() -> None:
    store: dict = _fresh_store()
    with pytest.raises(ValueError, match="num_prompts"):
        create_calibration_session(num_prompts=MAX_PROMPTS + 1, session_store=store)


def test_create_session_raises_for_zero_prompts() -> None:
    store: dict = _fresh_store()
    with pytest.raises(ValueError):
        create_calibration_session(num_prompts=0, session_store=store)


# ---------------------------------------------------------------------------
# Unit tests: get_session_prompts
# ---------------------------------------------------------------------------


def test_get_session_prompts_returns_prompts_for_known_session() -> None:
    store: dict = _fresh_store()
    result = create_calibration_session(session_store=store)
    prompts = get_session_prompts(result.session_id, session_store=store)
    assert prompts == result.prompts


def test_get_session_prompts_returns_none_for_unknown_session() -> None:
    store: dict = _fresh_store()
    prompts = get_session_prompts(uuid.uuid4(), session_store=store)
    assert prompts is None


def test_get_session_prompts_returns_none_after_fresh_store() -> None:
    store_a: dict = _fresh_store()
    result = create_calibration_session(session_store=store_a)
    # A different store doesn't have the session.
    store_b: dict = _fresh_store()
    assert get_session_prompts(result.session_id, session_store=store_b) is None


# ---------------------------------------------------------------------------
# HTTP endpoint tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def session_factory():
    """Shared in-memory SQLite engine (StaticPool for cross-thread sharing)."""
    engine = create_db_engine("sqlite://", poolclass=StaticPool)
    create_all(engine)
    factory = create_session_factory(engine)
    yield factory
    Base.metadata.drop_all(engine)


def _make_user(session: Session, username: str, password: str = "testpass") -> User:
    user = User(
        username=username,
        email=f"{username}@example.com",
        password_hash=hash_password(password),
    )
    session.add(user)
    session.commit()
    return user


@pytest.fixture()
def client(session_factory) -> Iterator[tuple[TestClient, SessionStore]]:
    """Build a FastAPI test app wired with auth + calibration routers."""
    store = SessionStore()

    def _override_db_session() -> Iterator[Session]:
        with session_factory() as s:
            yield s

    def _override_store() -> SessionStore:
        return store

    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(calibration_router)

    app.dependency_overrides[get_db_session] = _override_db_session
    app.dependency_overrides[get_session_store] = _override_store

    with TestClient(app) as c:
        yield c, store


def _login(client: TestClient, session_factory, username: str = "alice", password: str = "testpass") -> str:
    """Register and log in a user; return the bearer token."""
    with session_factory() as s:
        _make_user(s, username, password)
    resp = client.post("/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200
    return resp.json()["access_token"]


def test_endpoint_authenticated_returns_201(client, session_factory) -> None:
    c, store = client
    token = _login(c, session_factory)
    resp = c.post("/calibration/session", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 201


def test_endpoint_response_contains_session_id_and_prompts(client, session_factory) -> None:
    c, store = client
    token = _login(c, session_factory)
    resp = c.post("/calibration/session", headers={"Authorization": f"Bearer {token}"})
    body = resp.json()
    assert "session_id" in body
    assert "prompts" in body
    # session_id is a valid UUID string
    uuid.UUID(body["session_id"])


def test_endpoint_prompt_count_within_bounds(client, session_factory) -> None:
    c, store = client
    token = _login(c, session_factory)
    resp = c.post("/calibration/session", headers={"Authorization": f"Bearer {token}"})
    prompts = resp.json()["prompts"]
    assert MIN_PROMPTS <= len(prompts) <= MAX_PROMPTS


def test_endpoint_each_prompt_within_length_bounds(client, session_factory) -> None:
    c, store = client
    token = _login(c, session_factory)
    resp = c.post("/calibration/session", headers={"Authorization": f"Bearer {token}"})
    for prompt in resp.json()["prompts"]:
        assert 1 <= len(prompt) <= MAX_PROMPT_LENGTH, (
            f"Prompt out of length bounds: {prompt!r}"
        )


def test_endpoint_unauthenticated_returns_401(client, session_factory) -> None:
    """Calibration is optional (Req 9.1) but still requires auth (Req 10.1)."""
    c, store = client
    resp = c.post("/calibration/session")
    assert resp.status_code == 401
    assert resp.json()["detail"] == AUTH_REQUIRED_MESSAGE


def test_endpoint_invalid_token_returns_401(client, session_factory) -> None:
    c, store = client
    resp = c.post(
        "/calibration/session",
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert resp.status_code == 401


def test_endpoint_multiple_calls_produce_distinct_session_ids(client, session_factory) -> None:
    c, store = client
    token = _login(c, session_factory)
    ids = set()
    for _ in range(5):
        resp = c.post("/calibration/session", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 201
        ids.add(resp.json()["session_id"])
    assert len(ids) == 5


def test_endpoint_prompts_are_non_empty_strings(client, session_factory) -> None:
    c, store = client
    token = _login(c, session_factory)
    resp = c.post("/calibration/session", headers={"Authorization": f"Bearer {token}"})
    for prompt in resp.json()["prompts"]:
        assert isinstance(prompt, str)
        assert len(prompt) >= 1


# =============================================================================
# Task 12.2: Calibration sample submission tests
# Validates: Requirements 9.3, 9.4, 9.5
# =============================================================================

import tempfile
from io import BytesIO
from typing import Any

import numpy as np
from PIL import Image as PILImage
from sqlalchemy.pool import StaticPool

from app.data import Base, RepositoryFactory, User, TrainingPair, TrainingPairOrigin
from app.data import create_all, create_db_engine, create_session_factory
from app.data.object_store import FilesystemObjectStore
from app.services.calibration_service import (
    CALIBRATION_MAX_BYTES,
    CalibrationAccepted,
    CalibrationRejection,
    CalibrationRejectReason,
    CalibrationStorageError,
    CalibrationSampleResponse,
    submit_calibration_sample,
    get_calibration_object_store,
    router as calibration_router,
)


# ---------------------------------------------------------------------------
# Image byte helpers
# ---------------------------------------------------------------------------


def _make_png_bytes(size: tuple = (32, 32), color: tuple = (255, 255, 255)) -> bytes:
    """Create a minimal valid PNG in-memory."""
    buf = BytesIO()
    PILImage.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _make_jpeg_bytes(size: tuple = (32, 32), color: tuple = (255, 255, 255)) -> bytes:
    """Create a minimal valid JPEG in-memory."""
    buf = BytesIO()
    PILImage.new("RGB", size, color).save(buf, format="JPEG")
    return buf.getvalue()


def _make_handwriting_png_bytes() -> bytes:
    """Create a PNG with black-on-white ink blobs that the segmenter can detect.

    We draw horizontal bands of black pixels across a white image so the
    projection-profile segmenter finds line bands and produces at least one
    word region (``recognized_any_region`` == True).
    """
    width, height = 200, 100
    # White background.
    arr = np.ones((height, width), dtype=np.uint8) * 255
    # Two horizontal ink bands (dark pixels) to simulate two lines of text.
    arr[20:35, 10:190] = 20   # line 1
    arr[55:70, 10:190] = 20   # line 2
    # Within each band, add some column gaps to simulate word boundaries.
    arr[20:35, 90:100] = 255  # word gap in line 1
    arr[55:70, 90:100] = 255  # word gap in line 2
    img = PILImage.fromarray(arr, mode="L")
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Fixtures for unit-level tests (business logic without HTTP)
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_session_factory():
    engine = create_db_engine("sqlite://", poolclass=StaticPool)
    create_all(engine)
    factory = create_session_factory(engine)
    yield factory
    Base.metadata.drop_all(engine)


@pytest.fixture()
def repos_and_store(db_session_factory, tmp_path):
    """Yield (session, repos, object_store, user_id) for a fresh alice account."""
    store = FilesystemObjectStore(root=tmp_path / "objects")
    session = db_session_factory()
    from app.services.auth import hash_password
    user = User(
        username="alice_12_2",
        email="alice_12_2@example.com",
        password_hash=hash_password("pw"),
    )
    session.add(user)
    session.commit()
    repos = RepositoryFactory(session)
    yield session, repos, store, user.user_id
    session.close()


def _make_session_with_prompts(n_prompts: int = 3):
    """Create an in-memory session store with a known session."""
    session_id = uuid.uuid4()
    prompts = [f"Prompt text {i}" for i in range(n_prompts)]
    store = {session_id: prompts}
    return session_id, prompts, store


# ---------------------------------------------------------------------------
# Unit tests: submit_calibration_sample business logic
# ---------------------------------------------------------------------------


def test_valid_sample_creates_calibration_pair(repos_and_store) -> None:
    """Valid submission creates a Training_Pair with origin=CALIBRATION (Req 9.3)."""
    _, repos, store, user_id = repos_and_store
    session_id, prompts, session_store = _make_session_with_prompts(3)
    image_bytes = _make_handwriting_png_bytes()

    result = submit_calibration_sample(
        user_id=user_id,
        session_id=session_id,
        prompt_index=0,
        image_bytes=image_bytes,
        repos=repos,
        object_store=store,
        session_store=session_store,
    )

    assert isinstance(result, CalibrationAccepted), f"Expected CalibrationAccepted, got {result!r}"
    # Verify the pair exists in the DB.
    pair = repos.training_pairs.get(user_id, result.pair_id)
    assert pair is not None
    assert pair.origin == TrainingPairOrigin.CALIBRATION
    assert pair.corrected_text == prompts[0]


def test_valid_sample_uses_known_prompt_text(repos_and_store) -> None:
    """Training_Pair corrected_text equals the known prompt text (Req 9.3)."""
    _, repos, store, user_id = repos_and_store
    session_id, prompts, session_store = _make_session_with_prompts(5)
    image_bytes = _make_handwriting_png_bytes()

    result = submit_calibration_sample(
        user_id=user_id,
        session_id=session_id,
        prompt_index=2,
        image_bytes=image_bytes,
        repos=repos,
        object_store=store,
        session_store=session_store,
    )

    assert isinstance(result, CalibrationAccepted)
    pair = repos.training_pairs.get(user_id, result.pair_id)
    assert pair.corrected_text == prompts[2]


def test_valid_sample_pair_origin_is_calibration(repos_and_store) -> None:
    """Training_Pair origin is CALIBRATION, not CORRECTION (Req 9.5)."""
    _, repos, store, user_id = repos_and_store
    session_id, _, session_store = _make_session_with_prompts(3)
    image_bytes = _make_handwriting_png_bytes()

    result = submit_calibration_sample(
        user_id=user_id,
        session_id=session_id,
        prompt_index=0,
        image_bytes=image_bytes,
        repos=repos,
        object_store=store,
        session_store=session_store,
    )

    assert isinstance(result, CalibrationAccepted)
    pair = repos.training_pairs.get(user_id, result.pair_id)
    assert pair.origin == TrainingPairOrigin.CALIBRATION


def test_valid_sample_stores_crop_path(repos_and_store) -> None:
    """Training_Pair records a non-empty crop path (Req 9.5 — same store)."""
    _, repos, store, user_id = repos_and_store
    session_id, _, session_store = _make_session_with_prompts(3)
    image_bytes = _make_handwriting_png_bytes()

    result = submit_calibration_sample(
        user_id=user_id,
        session_id=session_id,
        prompt_index=0,
        image_bytes=image_bytes,
        repos=repos,
        object_store=store,
        session_store=session_store,
    )

    assert isinstance(result, CalibrationAccepted)
    pair = repos.training_pairs.get(user_id, result.pair_id)
    assert pair.region_crop_path
    assert store.exists(pair.region_crop_path)


def test_resubmission_of_same_prompt_upserts(repos_and_store) -> None:
    """Re-submitting the same prompt/session is an upsert — no duplicates."""
    _, repos, store, user_id = repos_and_store
    session_id, prompts, session_store = _make_session_with_prompts(3)
    image_bytes = _make_handwriting_png_bytes()

    result1 = submit_calibration_sample(
        user_id=user_id,
        session_id=session_id,
        prompt_index=0,
        image_bytes=image_bytes,
        repos=repos,
        object_store=store,
        session_store=session_store,
    )
    result2 = submit_calibration_sample(
        user_id=user_id,
        session_id=session_id,
        prompt_index=0,
        image_bytes=image_bytes,
        repos=repos,
        object_store=store,
        session_store=session_store,
    )

    assert isinstance(result1, CalibrationAccepted)
    assert isinstance(result2, CalibrationAccepted)
    assert result1.pair_id == result2.pair_id

    all_pairs = repos.training_pairs.list_for_user(user_id)
    calibration_pairs = [p for p in all_pairs if p.origin == TrainingPairOrigin.CALIBRATION]
    assert len(calibration_pairs) == 1


# ---------------------------------------------------------------------------
# Rejection tests (Req 9.4): invalid submissions create no pair
# ---------------------------------------------------------------------------


def test_empty_image_is_rejected(repos_and_store) -> None:
    """Empty image bytes → CalibrationRejection, no pair created (Req 9.4)."""
    _, repos, store, user_id = repos_and_store
    session_id, _, session_store = _make_session_with_prompts(3)

    result = submit_calibration_sample(
        user_id=user_id,
        session_id=session_id,
        prompt_index=0,
        image_bytes=b"",
        repos=repos,
        object_store=store,
        session_store=session_store,
    )

    assert isinstance(result, CalibrationRejection)
    assert repos.training_pairs.list_for_user(user_id) == []


def test_oversized_image_is_rejected(repos_and_store) -> None:
    """Image > 10 MB → CalibrationRejection with size_limit reason (Req 9.4)."""
    _, repos, store, user_id = repos_and_store
    session_id, _, session_store = _make_session_with_prompts(3)

    oversized = b"X" * (CALIBRATION_MAX_BYTES + 1)
    result = submit_calibration_sample(
        user_id=user_id,
        session_id=session_id,
        prompt_index=0,
        image_bytes=oversized,
        repos=repos,
        object_store=store,
        session_store=session_store,
    )

    assert isinstance(result, CalibrationRejection)
    assert result.reason == CalibrationRejectReason.SIZE_LIMIT
    assert repos.training_pairs.list_for_user(user_id) == []


def test_invalid_image_bytes_are_rejected(repos_and_store) -> None:
    """Undecodable bytes → CalibrationRejection with invalid_image reason (Req 9.4)."""
    _, repos, store, user_id = repos_and_store
    session_id, _, session_store = _make_session_with_prompts(3)

    result = submit_calibration_sample(
        user_id=user_id,
        session_id=session_id,
        prompt_index=0,
        image_bytes=b"this is not an image at all",
        repos=repos,
        object_store=store,
        session_store=session_store,
    )

    assert isinstance(result, CalibrationRejection)
    assert result.reason == CalibrationRejectReason.INVALID_IMAGE
    assert repos.training_pairs.list_for_user(user_id) == []


def test_image_with_no_handwriting_is_rejected(repos_and_store) -> None:
    """Blank/white image (no ink) → rejected for no detectable handwriting (Req 9.4)."""
    _, repos, store, user_id = repos_and_store
    session_id, _, session_store = _make_session_with_prompts(3)

    # A pure white image has no ink → segmenter detects no region.
    blank_image = _make_png_bytes(size=(64, 64), color=(255, 255, 255))
    result = submit_calibration_sample(
        user_id=user_id,
        session_id=session_id,
        prompt_index=0,
        image_bytes=blank_image,
        repos=repos,
        object_store=store,
        session_store=session_store,
    )

    assert isinstance(result, CalibrationRejection)
    assert result.reason == CalibrationRejectReason.NO_HANDWRITING
    assert repos.training_pairs.list_for_user(user_id) == []


def test_rejected_sample_creates_no_pair(repos_and_store) -> None:
    """Any rejected submission creates absolutely no Training_Pair (Req 9.4)."""
    _, repos, store, user_id = repos_and_store
    session_id, _, session_store = _make_session_with_prompts(3)

    before = repos.training_pairs.list_for_user(user_id)

    # Empty image — guaranteed rejection.
    submit_calibration_sample(
        user_id=user_id,
        session_id=session_id,
        prompt_index=0,
        image_bytes=b"",
        repos=repos,
        object_store=store,
        session_store=session_store,
    )

    after = repos.training_pairs.list_for_user(user_id)
    assert len(before) == len(after)


def test_unknown_session_is_rejected(repos_and_store) -> None:
    """Unknown session_id → CalibrationRejection."""
    _, repos, store, user_id = repos_and_store
    unknown_session_id = uuid.uuid4()
    image_bytes = _make_handwriting_png_bytes()
    empty_store: dict = {}

    result = submit_calibration_sample(
        user_id=user_id,
        session_id=unknown_session_id,
        prompt_index=0,
        image_bytes=image_bytes,
        repos=repos,
        object_store=store,
        session_store=empty_store,
    )

    assert isinstance(result, CalibrationRejection)
    assert repos.training_pairs.list_for_user(user_id) == []


def test_out_of_range_prompt_index_is_rejected(repos_and_store) -> None:
    """prompt_index beyond session range → CalibrationRejection."""
    _, repos, store, user_id = repos_and_store
    session_id, _, session_store = _make_session_with_prompts(3)
    image_bytes = _make_handwriting_png_bytes()

    result = submit_calibration_sample(
        user_id=user_id,
        session_id=session_id,
        prompt_index=99,  # out of range for a 3-prompt session
        image_bytes=image_bytes,
        repos=repos,
        object_store=store,
        session_store=session_store,
    )

    assert isinstance(result, CalibrationRejection)
    assert repos.training_pairs.list_for_user(user_id) == []


# ---------------------------------------------------------------------------
# HTTP endpoint tests: POST /calibration/{session_id}/samples
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_client(session_factory, tmp_path) -> Iterator[tuple[TestClient, Any, Any, str]]:
    """TestClient for session + samples endpoints, yielding (client, in_memory_store, token)."""
    store = SessionStore()
    obj_store = FilesystemObjectStore(root=tmp_path / "obj_store")
    calibration_session_store: dict = {}

    def _override_db_session() -> Iterator[Session]:
        with session_factory() as s:
            yield s

    def _override_store() -> SessionStore:
        return store

    def _override_obj_store() -> FilesystemObjectStore:
        return obj_store

    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(calibration_router)

    app.dependency_overrides[get_db_session] = _override_db_session
    app.dependency_overrides[get_session_store] = _override_store
    app.dependency_overrides[get_calibration_object_store] = _override_obj_store

    with TestClient(app) as c:
        yield c, calibration_session_store, obj_store, store


def _login_sample_client(
    client: TestClient, session_factory, username: str = "bob_sample"
) -> str:
    """Register bob_sample and return bearer token."""
    with session_factory() as s:
        _make_user(s, username)
    resp = client.post("/auth/login", json={"username": username, "password": "testpass"})
    assert resp.status_code == 200
    return resp.json()["access_token"]


def test_http_valid_submission_returns_201(sample_client, session_factory) -> None:
    """Valid calibration submission returns 201 with pair_id (Req 9.3)."""
    c, _, _, _ = sample_client
    token = _login_sample_client(c, session_factory)

    # Create a session first.
    session_resp = c.post("/calibration/session", headers={"Authorization": f"Bearer {token}"})
    assert session_resp.status_code == 201
    session_id = session_resp.json()["session_id"]

    image_bytes = _make_handwriting_png_bytes()
    resp = c.post(
        f"/calibration/{session_id}/samples",
        headers={"Authorization": f"Bearer {token}"},
        data={"prompt_index": "0"},
        files={"file": ("sample.png", image_bytes, "image/png")},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "accepted"
    assert "pair_id" in body
    uuid.UUID(body["pair_id"])


def test_http_valid_submission_pair_id_is_uuid(sample_client, session_factory) -> None:
    """pair_id in the response is a valid UUID."""
    c, _, _, _ = sample_client
    token = _login_sample_client(c, session_factory, "bob_uuid")

    session_resp = c.post("/calibration/session", headers={"Authorization": f"Bearer {token}"})
    session_id = session_resp.json()["session_id"]

    image_bytes = _make_handwriting_png_bytes()
    resp = c.post(
        f"/calibration/{session_id}/samples",
        headers={"Authorization": f"Bearer {token}"},
        data={"prompt_index": "0"},
        files={"file": ("sample.png", image_bytes, "image/png")},
    )
    assert resp.status_code == 201
    uuid.UUID(resp.json()["pair_id"])


def test_http_unauthenticated_returns_401(sample_client, session_factory) -> None:
    """Unauthenticated submission returns 401 (Req 10.1)."""
    c, _, _, _ = sample_client
    # Create a session as authenticated user to get a valid session_id.
    token = _login_sample_client(c, session_factory, "bob_unauth")
    session_resp = c.post("/calibration/session", headers={"Authorization": f"Bearer {token}"})
    session_id = session_resp.json()["session_id"]

    image_bytes = _make_handwriting_png_bytes()
    # Truly unauthenticated: no Authorization header AND no session cookie.
    # (Login now sets a session cookie, which TestClient persists, so we must
    # clear it to simulate an unauthenticated client.)
    c.cookies.clear()
    resp = c.post(
        f"/calibration/{session_id}/samples",
        # No auth header.
        data={"prompt_index": "0"},
        files={"file": ("sample.png", image_bytes, "image/png")},
    )
    assert resp.status_code == 401


def test_http_empty_image_returns_422(sample_client, session_factory) -> None:
    """Empty image upload returns 422 with reason (Req 9.4)."""
    c, _, _, _ = sample_client
    token = _login_sample_client(c, session_factory, "bob_empty")

    session_resp = c.post("/calibration/session", headers={"Authorization": f"Bearer {token}"})
    session_id = session_resp.json()["session_id"]

    resp = c.post(
        f"/calibration/{session_id}/samples",
        headers={"Authorization": f"Bearer {token}"},
        data={"prompt_index": "0"},
        files={"file": ("empty.png", b"", "image/png")},
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert "reason" in detail


def test_http_blank_image_returns_422_no_handwriting(sample_client, session_factory) -> None:
    """Blank white image returns 422 with no_handwriting reason (Req 9.4)."""
    c, _, _, _ = sample_client
    token = _login_sample_client(c, session_factory, "bob_blank")

    session_resp = c.post("/calibration/session", headers={"Authorization": f"Bearer {token}"})
    session_id = session_resp.json()["session_id"]

    blank_image = _make_png_bytes(size=(64, 64), color=(255, 255, 255))
    resp = c.post(
        f"/calibration/{session_id}/samples",
        headers={"Authorization": f"Bearer {token}"},
        data={"prompt_index": "0"},
        files={"file": ("blank.png", blank_image, "image/png")},
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["reason"] == CalibrationRejectReason.NO_HANDWRITING


def test_http_invalid_image_bytes_returns_422(sample_client, session_factory) -> None:
    """Non-image bytes return 422 with invalid_image reason (Req 9.4)."""
    c, _, _, _ = sample_client
    token = _login_sample_client(c, session_factory, "bob_invalid")

    session_resp = c.post("/calibration/session", headers={"Authorization": f"Bearer {token}"})
    session_id = session_resp.json()["session_id"]

    resp = c.post(
        f"/calibration/{session_id}/samples",
        headers={"Authorization": f"Bearer {token}"},
        data={"prompt_index": "0"},
        files={"file": ("bad.png", b"this is not an image", "image/png")},
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["reason"] == CalibrationRejectReason.INVALID_IMAGE


def test_http_jpeg_image_accepted(sample_client, session_factory) -> None:
    """JPEG calibration images are also accepted (PNG/JPEG both supported)."""
    c, _, _, _ = sample_client
    token = _login_sample_client(c, session_factory, "bob_jpeg")

    session_resp = c.post("/calibration/session", headers={"Authorization": f"Bearer {token}"})
    session_id = session_resp.json()["session_id"]
    num_prompts = len(session_resp.json()["prompts"])

    # Build a JPEG with ink blobs.
    width, height = 200, 100
    arr = np.ones((height, width), dtype=np.uint8) * 255
    arr[20:35, 10:190] = 20
    arr[55:70, 10:190] = 20
    arr[20:35, 90:100] = 255
    arr[55:70, 90:100] = 255
    img = PILImage.fromarray(arr, mode="L").convert("RGB")
    buf = BytesIO()
    img.save(buf, format="JPEG")
    jpeg_bytes = buf.getvalue()

    resp = c.post(
        f"/calibration/{session_id}/samples",
        headers={"Authorization": f"Bearer {token}"},
        data={"prompt_index": "0"},
        files={"file": ("sample.jpg", jpeg_bytes, "image/jpeg")},
    )
    assert resp.status_code == 201


def test_http_calibration_pair_in_same_store_as_corrections(
    sample_client, session_factory
) -> None:
    """Calibration Training_Pairs are stored in the same training_pairs table as corrections (Req 9.5)."""
    c, _, _, _ = sample_client
    token = _login_sample_client(c, session_factory, "bob_store_check")

    session_resp = c.post("/calibration/session", headers={"Authorization": f"Bearer {token}"})
    session_id = session_resp.json()["session_id"]

    image_bytes = _make_handwriting_png_bytes()
    resp = c.post(
        f"/calibration/{session_id}/samples",
        headers={"Authorization": f"Bearer {token}"},
        data={"prompt_index": "0"},
        files={"file": ("sample.png", image_bytes, "image/png")},
    )
    assert resp.status_code == 201
    pair_id = uuid.UUID(resp.json()["pair_id"])

    # Verify via the training_pairs repository that the pair is queryable.
    with session_factory() as session:
        from sqlalchemy import select
        pair = session.get(TrainingPair, pair_id)
        assert pair is not None, "Calibration pair should be in the training_pairs table"
        assert pair.origin == TrainingPairOrigin.CALIBRATION
