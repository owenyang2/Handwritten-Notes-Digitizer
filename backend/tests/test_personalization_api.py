"""Unit tests for GET /personalization/status and GET /personalization/evaluation.

Covers task 19.2 — surfacing personalization state and before/after accuracy
metrics in the API layer (Requirements 5.6, 5.7, 7.3).

Tests use an isolated in-memory SQLite DB with the auth router and
personalization router, following the same fixture pattern as the rest of the
test suite.
"""

from __future__ import annotations

import uuid
from typing import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool

from app.data import (
    Base,
    RepositoryFactory,
    User,
    create_all,
    create_db_engine,
    create_session_factory,
)
from app.config.settings import get_settings
from app.data.enums import (
    AdapterStatus,
    EvaluationAvailability,
    TrainingPairOrigin,
    TrainingRunState,
)
from app.data.models import Adapter, EvaluationResult, TrainingPair, TrainingRun
from app.data.object_store import FilesystemObjectStore
from app.services.auth import AuthService, SessionStore, hash_password
from app.services.auth_api import (
    get_current_user,
    get_db_session,
    get_repositories,
    get_session_store,
    router as auth_router,
)
from app.services.images_api import get_object_store
from app.services.personalization_api import router as personalization_router

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture()
def session_factory():
    engine = create_db_engine(
        "sqlite://",
        poolclass=StaticPool,
    )
    create_all(engine)
    factory = create_session_factory(engine)
    yield factory
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client(db_session, session_factory, tmp_path) -> Iterator[tuple[TestClient, SessionStore]]:
    store = SessionStore(db_session)
    object_store = FilesystemObjectStore(str(tmp_path / "objectstore"))

    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(personalization_router)

    def _override_session():
        factory = session_factory
        s = factory()
        try:
            yield s
        finally:
            s.close()

    def _override_store():
        return store

    app.dependency_overrides[get_db_session] = _override_session
    app.dependency_overrides[get_session_store] = _override_store
    app.dependency_overrides[get_object_store] = lambda: object_store

    with TestClient(app) as c:
        yield c, store, session_factory


def _register_and_login(
    client: TestClient,
    session_factory,
    username: str = "alice",
) -> tuple[str, uuid.UUID]:
    """Create a user and return (token, user_id)."""
    with session_factory() as s:
        repos = RepositoryFactory(s)
        user = User(
            username=username,
            email=f"{username}@example.com",
            password_hash=hash_password("pw"),
        )
        repos.users.add(user)
        s.commit()
        user_id = user.user_id

    resp = client.post("/auth/login", json={"username": username, "password": "pw"})
    assert resp.status_code == 200
    return resp.json()["access_token"], user_id


# --------------------------------------------------------------------------- #
# GET /personalization/status — authentication gate
# --------------------------------------------------------------------------- #


def test_status_requires_auth(client):
    c, _store, _sf = client
    resp = c.get("/personalization/status")
    assert resp.status_code == 401


# --------------------------------------------------------------------------- #
# GET /personalization/status — no training runs
# --------------------------------------------------------------------------- #


def test_status_no_runs(client):
    c, _store, sf = client
    token, _uid = _register_and_login(c, sf, "alice")

    resp = c.get(
        "/personalization/status",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["training_state"] == "none"
    assert data["adapter_active"] is False
    assert data["last_run_outcome"] is None
    assert data["notification"] is None
    assert data["unused_training_pairs"] == 0
    assert data["training_trigger_threshold"] >= 1
    assert data["max_training_epochs"] >= 1


# --------------------------------------------------------------------------- #
# GET /personalization/status — in_progress state (Req 5.6)
# --------------------------------------------------------------------------- #


def test_status_in_progress(client):
    c, _store, sf = client
    token, uid = _register_and_login(c, sf, "alice")

    with sf() as s:
        run = TrainingRun(user_id=uid, state=TrainingRunState.RUNNING)
        s.add(run)
        s.commit()

    resp = c.get(
        "/personalization/status",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["training_state"] == "in_progress"
    assert data["last_run_outcome"] is None
    assert "in progress" in data["notification"].lower()


def test_status_queued_counts_as_in_progress(client):
    c, _store, sf = client
    token, uid = _register_and_login(c, sf, "alice")

    with sf() as s:
        run = TrainingRun(user_id=uid, state=TrainingRunState.QUEUED)
        s.add(run)
        s.commit()

    resp = c.get(
        "/personalization/status",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["training_state"] == "in_progress"


# --------------------------------------------------------------------------- #
# GET /personalization/status — completed/accepted state
# --------------------------------------------------------------------------- #


def test_status_accepted(client):
    c, _store, sf = client
    token, uid = _register_and_login(c, sf, "alice")

    with sf() as s:
        # Create an accepted run and an active adapter.
        run = TrainingRun(user_id=uid, state=TrainingRunState.ACCEPTED)
        s.add(run)
        adapter = Adapter(
            user_id=uid,
            weights_path="/tmp/fake.pt",
            base_model_version="1.0",
            status=AdapterStatus.ACTIVE,
        )
        s.add(adapter)
        s.commit()

    resp = c.get(
        "/personalization/status",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["training_state"] == "completed"
    assert data["adapter_active"] is True
    assert data["last_run_outcome"] == "accepted"
    assert data["reject_reason"] is None


# --------------------------------------------------------------------------- #
# GET /personalization/status — rejected state (Req 5.7)
# --------------------------------------------------------------------------- #


def test_status_rejected_shows_message(client):
    c, _store, sf = client
    token, uid = _register_and_login(c, sf, "alice")

    with sf() as s:
        run = TrainingRun(
            user_id=uid,
            state=TrainingRunState.REJECTED,
            reject_reason="rejected_cer",
        )
        s.add(run)
        s.commit()

    resp = c.get(
        "/personalization/status",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["training_state"] == "completed"
    assert data["adapter_active"] is False
    assert data["last_run_outcome"] == "rejected"
    assert data["reject_reason"] == "rejected_cer"
    # Req 5.7: notification about retained config.
    assert data["notification"] is not None
    assert len(data["notification"]) > 0


# --------------------------------------------------------------------------- #
# GET /personalization/status — failed state (Req 5.7)
# --------------------------------------------------------------------------- #


def test_status_failed_shows_message(client):
    c, _store, sf = client
    token, uid = _register_and_login(c, sf, "alice")

    with sf() as s:
        run = TrainingRun(
            user_id=uid,
            state=TrainingRunState.FAILED,
            reject_reason="Training did not produce a valid adapter.",
        )
        s.add(run)
        s.commit()

    resp = c.get(
        "/personalization/status",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["last_run_outcome"] == "failed"
    assert data["reject_reason"] == "Training did not produce a valid adapter."
    assert data["notification"] is not None


# --------------------------------------------------------------------------- #
# GET /personalization/evaluation — authentication gate
# --------------------------------------------------------------------------- #


def test_evaluation_requires_auth(client):
    c, _store, _sf = client
    resp = c.get("/personalization/evaluation")
    assert resp.status_code == 401


# --------------------------------------------------------------------------- #
# GET /personalization/evaluation — no evaluation yet
# --------------------------------------------------------------------------- #


def test_evaluation_no_result(client):
    c, _store, sf = client
    token, _uid = _register_and_login(c, sf, "alice")

    resp = c.get(
        "/personalization/evaluation",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["availability"] == "unavailable"
    assert data["before_cer"] is None
    assert data["before_wer"] is None
    assert data["after_cer"] is None
    assert data["after_wer"] is None
    assert data["delta_cer"] is None
    assert data["delta_wer"] is None
    assert data["unavailable_reason"] is not None


# --------------------------------------------------------------------------- #
# GET /personalization/evaluation — with a completed evaluation (Req 7.3)
# --------------------------------------------------------------------------- #


def test_evaluation_returns_metrics(client):
    c, _store, sf = client
    token, uid = _register_and_login(c, sf, "alice")

    with sf() as s:
        run = TrainingRun(user_id=uid, state=TrainingRunState.ACCEPTED)
        s.add(run)
        s.flush()
        result = EvaluationResult(
            run_id=run.run_id,
            user_id=uid,
            before_cer=0.20,
            before_wer=0.35,
            after_cer=0.15,
            after_wer=0.28,
            delta_cer=0.05,
            delta_wer=0.07,
            general_cer=0.15,
            general_wer=0.28,
            availability=EvaluationAvailability.AVAILABLE,
        )
        s.add(result)
        s.commit()

    resp = c.get(
        "/personalization/evaluation",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["availability"] == "available"
    assert abs(data["before_cer"] - 0.20) < 1e-6
    assert abs(data["before_wer"] - 0.35) < 1e-6
    assert abs(data["after_cer"] - 0.15) < 1e-6
    assert abs(data["after_wer"] - 0.28) < 1e-6
    assert abs(data["delta_cer"] - 0.05) < 1e-6
    assert abs(data["delta_wer"] - 0.07) < 1e-6
    assert data["unavailable_reason"] is None


# --------------------------------------------------------------------------- #
# GET /personalization/evaluation — unavailable (Req 7.6)
# --------------------------------------------------------------------------- #


def test_evaluation_reports_unavailable(client):
    c, _store, sf = client
    token, uid = _register_and_login(c, sf, "alice")

    with sf() as s:
        run = TrainingRun(user_id=uid, state=TrainingRunState.REJECTED)
        s.add(run)
        s.flush()
        result = EvaluationResult(
            run_id=run.run_id,
            user_id=uid,
            before_cer=None,
            before_wer=None,
            after_cer=None,
            after_wer=None,
            delta_cer=None,
            delta_wer=None,
            general_cer=None,
            general_wer=None,
            availability=EvaluationAvailability.UNAVAILABLE,
        )
        s.add(result)
        s.commit()

    resp = c.get(
        "/personalization/evaluation",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["availability"] == "unavailable"
    assert data["unavailable_reason"] is not None
    # All metrics are null.
    assert data["before_cer"] is None
    assert data["delta_cer"] is None


# --------------------------------------------------------------------------- #
# Cross-user isolation: each user only sees their own data (Req 10.5)
# --------------------------------------------------------------------------- #


def test_status_cross_user_isolation(client):
    c, _store, sf = client
    token_alice, uid_alice = _register_and_login(c, sf, "alice")
    _token_bob, uid_bob = _register_and_login(c, sf, "bob")

    # Give Bob an active adapter and a completed run; Alice has nothing.
    with sf() as s:
        run = TrainingRun(user_id=uid_bob, state=TrainingRunState.ACCEPTED)
        s.add(run)
        adapter = Adapter(
            user_id=uid_bob,
            weights_path="/tmp/bob.pt",
            base_model_version="1.0",
            status=AdapterStatus.ACTIVE,
        )
        s.add(adapter)
        s.commit()

    # Alice should still see "none" state.
    resp = c.get(
        "/personalization/status",
        headers={"Authorization": f"Bearer {token_alice}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["training_state"] == "none"
    assert data["adapter_active"] is False


def test_evaluation_cross_user_isolation(client):
    c, _store, sf = client
    token_alice, uid_alice = _register_and_login(c, sf, "alice")
    _token_bob, uid_bob = _register_and_login(c, sf, "bob")

    # Give Bob a completed evaluation; Alice has none.
    with sf() as s:
        run = TrainingRun(user_id=uid_bob, state=TrainingRunState.ACCEPTED)
        s.add(run)
        s.flush()
        result = EvaluationResult(
            run_id=run.run_id,
            user_id=uid_bob,
            before_cer=0.30,
            before_wer=0.40,
            after_cer=0.20,
            after_wer=0.30,
            delta_cer=0.10,
            delta_wer=0.10,
            general_cer=0.20,
            general_wer=0.30,
            availability=EvaluationAvailability.AVAILABLE,
        )
        s.add(result)
        s.commit()

    # Alice should see "unavailable" (no evaluation for her).
    resp = c.get(
        "/personalization/evaluation",
        headers={"Authorization": f"Bearer {token_alice}"},
    )
    assert resp.status_code == 200
    assert resp.json()["availability"] == "unavailable"


def test_training_pairs_list_returns_examples(client):
    c, _store, sf = client
    token, uid = _register_and_login(c, sf, "alice")

    with sf() as s:
        p1 = TrainingPair(
            user_id=uid,
            image_id=None,
            source_region_key="calibration:session-1:0",
            region_crop_path="crops/alice/p1.png",
            corrected_text="The quick brown fox",
            origin=TrainingPairOrigin.CALIBRATION,
        )
        p2 = TrainingPair(
            user_id=uid,
            image_id=None,
            source_region_key="img-1:line-0:word-0",
            region_crop_path="crops/alice/p2.png",
            corrected_text="corrected",
            origin=TrainingPairOrigin.CORRECTION,
            consumed_by_run_id=uuid.uuid4(),
        )
        s.add_all([p1, p2])
        s.commit()

    resp = c.get(
        "/personalization/training-pairs",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 2
    assert {item["origin"] for item in data["items"]} == {"calibration", "correction"}
    used_flags = {item["corrected_text"]: item["used"] for item in data["items"]}
    assert used_flags["The quick brown fox"] is False
    assert used_flags["corrected"] is True


def test_training_pair_image_returns_crop(client):
    c, _store, sf = client
    token, uid = _register_and_login(c, sf, "alice")

    crop_path = "crops/alice/pair-image.png"
    png_bytes = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
        b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00"
        b"\x00\x00\x0cIDATx\x9cc`\x00\x00\x00\x02\x00\x01"
        b"\xe2!\xbc3\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    with sf() as s:
        object_store = c.app.dependency_overrides[get_object_store]()
        object_store.put(crop_path, png_bytes)
        pair = TrainingPair(
            user_id=uid,
            image_id=None,
            source_region_key="calibration:session-2:0",
            region_crop_path=crop_path,
            corrected_text="sample",
            origin=TrainingPairOrigin.CALIBRATION,
        )
        s.add(pair)
        s.commit()
        pair_id = pair.pair_id

    resp = c.get(
        f"/personalization/training-pairs/{pair_id}/image",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/png")
    assert resp.content == png_bytes


def test_training_pair_label_edit_can_enqueue_training(client):
    c, _store, sf = client
    token, uid = _register_and_login(c, sf, "alice")
    threshold = get_settings().personalization_trigger_threshold

    with sf() as s:
        seed_pairs = [
            TrainingPair(
                user_id=uid,
                image_id=None,
                source_region_key=f"unused-{idx}",
                region_crop_path=f"crops/alice/unused-{idx}.png",
                corrected_text=f"unused {idx}",
                origin=TrainingPairOrigin.CORRECTION,
            )
            for idx in range(threshold - 1)
        ]
        edited_pair = TrainingPair(
            user_id=uid,
            image_id=None,
            source_region_key="used-edit",
            region_crop_path="crops/alice/used-edit.png",
            corrected_text="old label",
            origin=TrainingPairOrigin.CORRECTION,
            consumed_by_run_id=uuid.uuid4(),
        )
        s.add_all([*seed_pairs, edited_pair])
        s.commit()
        pair_id = edited_pair.pair_id

    resp = c.patch(
        f"/personalization/training-pairs/{pair_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"corrected_text": "new label"},
    )

    assert resp.status_code == 200
    assert resp.json()["used"] is False
    with sf() as s:
        repos = RepositoryFactory(s)
        runs = repos.training_runs.list_for_user(uid)
        assert len(runs) == 1
        assert runs[0].state == TrainingRunState.QUEUED
