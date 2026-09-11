"""Acceptance coverage for the first personalized-platform foundations slice."""

from __future__ import annotations

import io
import uuid
from typing import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image as PILImage
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.data import (
    Adapter,
    AdapterStatus,
    Base,
    Document,
    DocumentPage,
    DocumentRegion,
    ModelTask,
    RegionPrediction,
    RepositoryFactory,
    User,
    create_all,
    create_db_engine,
    create_session_factory,
)
from app.ml.backends.base import EngineCandidate, EngineResult
from app.services import math_regions_api
from app.services.auth import SessionStore, hash_password
from app.services.auth_api import (
    get_db_session,
    get_session_store,
    router as auth_router,
)
from app.services.documents_api import router as documents_router
from app.services.personalization_api import router as personalization_router
from app.services.model_resolution import (
    base_model_version,
    resolve_model_assignment,
    task_for_mode,
)
from app.data.object_store import FilesystemObjectStore


@pytest.fixture()
def session_factory():
    engine = create_db_engine("sqlite://", poolclass=StaticPool)
    create_all(engine)
    factory = create_session_factory(engine)
    yield factory
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client(session_factory) -> Iterator[tuple[TestClient, object]]:
    with session_factory() as session:
        store = SessionStore(session)

        def override_db() -> Iterator[Session]:
            yield session

        app = FastAPI()
        app.include_router(auth_router)
        app.include_router(documents_router)
        app.include_router(personalization_router)
        app.dependency_overrides[get_db_session] = override_db
        app.dependency_overrides[get_session_store] = lambda: store
        with TestClient(app) as test_client:
            yield test_client, session_factory


def _register(client: TestClient, session_factory, username: str) -> str:
    with session_factory() as session:
        session.add(
            User(
                username=username,
                email=f"{username}@example.com",
                password_hash=hash_password("hunter2"),
            )
        )
        session.commit()
    response = client.post(
        "/auth/login", json={"username": username, "password": "hunter2"}
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_document_library_lifecycle_is_reversible_and_versioned(client) -> None:
    test_client, session_factory = client
    token = _register(test_client, session_factory, "alice")

    created = test_client.post(
        "/documents",
        json={
            "title": "  Linear Algebra  ",
            "default_language": " en ",
            "course_context": "MATH 221",
            "source_metadata": {"source": "goodnotes-export"},
        },
        headers=_auth(token),
    )
    assert created.status_code == 201, created.text
    document = created.json()
    assert document["title"] == "Linear Algebra"
    assert document["default_language"] == "en"
    assert document["course_context"] == "MATH 221"
    assert document["sync_version"] == 0
    assert document["archived_at"] is None
    assert document["trashed_at"] is None

    archived = test_client.patch(
        f"/documents/{document['document_id']}",
        json={"title": "Linear Algebra - Week 1", "archived": True},
        headers=_auth(token),
    )
    assert archived.status_code == 200, archived.text
    assert archived.json()["sync_version"] == 1
    assert archived.json()["archived_at"] is not None

    # The default library is the active view; archived items are opt-in and
    # title search remains owner-scoped.
    assert test_client.get("/documents", headers=_auth(token)).json() == []
    archived_list = test_client.get(
        "/documents?include_archived=true&query=week%201",
        headers=_auth(token),
    )
    assert [item["document_id"] for item in archived_list.json()] == [
        document["document_id"]
    ]

    trashed = test_client.delete(
        f"/documents/{document['document_id']}", headers=_auth(token)
    )
    assert trashed.status_code == 200, trashed.text
    assert trashed.json()["trashed_at"] is not None
    assert trashed.json()["sync_version"] == 2
    assert test_client.get("/documents", headers=_auth(token)).json() == []
    assert test_client.get(
        "/documents?include_archived=true&include_trashed=true",
        headers=_auth(token),
    ).json()[0]["trashed_at"] is not None

    restored = test_client.post(
        f"/documents/{document['document_id']}/restore", headers=_auth(token)
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["trashed_at"] is None
    assert restored.json()["sync_version"] == 3

    unarchived = test_client.patch(
        f"/documents/{document['document_id']}",
        json={"archived": False},
        headers=_auth(token),
    )
    assert unarchived.status_code == 200, unarchived.text
    assert unarchived.json()["sync_version"] == 4
    assert [item["title"] for item in test_client.get(
        "/documents", headers=_auth(token)
    ).json()] == ["Linear Algebra - Week 1"]


def test_document_mutations_do_not_cross_user_boundaries(client) -> None:
    test_client, session_factory = client
    alice = _register(test_client, session_factory, "alice")
    bob = _register(test_client, session_factory, "bob")
    created = test_client.post(
        "/documents", json={"title": "Private"}, headers=_auth(alice)
    )
    document_id = created.json()["document_id"]

    for method, path, payload in (
        ("patch", f"/documents/{document_id}", {"title": "Stolen"}),
        ("delete", f"/documents/{document_id}", None),
        ("post", f"/documents/{document_id}/restore", None),
    ):
        response = getattr(test_client, method)(
            path, json=payload, headers=_auth(bob)
        ) if payload is not None else getattr(test_client, method)(
            path, headers=_auth(bob)
        )
        assert response.status_code == 404

    assert test_client.get("/documents", headers=_auth(alice)).json()[0]["title"] == "Private"


def test_document_list_pagination_headers_are_deterministic(client) -> None:
    test_client, session_factory = client
    token = _register(test_client, session_factory, "alice")
    for title in ("One", "Two", "Three"):
        response = test_client.post(
            "/documents", json={"title": title}, headers=_auth(token)
        )
        assert response.status_code == 201

    first = test_client.get("/documents?limit=2", headers=_auth(token))
    exact_final = test_client.get(
        "/documents?limit=2&offset=1", headers=_auth(token)
    )
    second = test_client.get("/documents?limit=2&offset=2", headers=_auth(token))
    assert first.status_code == second.status_code == 200
    assert len(first.json()) == 2
    assert len(second.json()) == 1
    assert first.headers["X-Document-Has-More"] == "true"
    assert exact_final.headers["X-Document-Has-More"] == "false"
    assert exact_final.headers["X-Document-Total"] == "3"
    assert second.headers["X-Document-Has-More"] == "false"


def test_model_assignment_is_stable_versioned_and_task_scoped(session_factory) -> None:
    with session_factory() as session:
        alice = User(
            username="alice",
            email="alice@example.com",
            password_hash="hashed",
        )
        bob = User(
            username="bob",
            email="bob@example.com",
            password_hash="hashed",
        )
        session.add_all([alice, bob])
        session.commit()
        repos = RepositoryFactory(session)

        first = resolve_model_assignment(
            repos,
            alice.user_id,
            task=ModelTask.MATH_RASTER,
            engine_id="compact-math",
            engine_version="v1",
            provenance={
                "model_id": "owned-compact",
                "weight_sha256": "a" * 64,
                "checkpoint": "/private/path/that-must-not-be-stored",
            },
        )
        repeated = resolve_model_assignment(
            repos,
            alice.user_id,
            task=ModelTask.MATH_RASTER,
            engine_id="compact-math",
            engine_version="v1",
            provenance={"model_id": "owned-compact", "weight_sha256": "a" * 64},
        )
        assert repeated.assignment.assignment_id == first.assignment.assignment_id
        assert first.assignment.active is True
        assert "/private/path" not in first.base_model_version
        assert "checkpoint" not in (first.assignment.metrics or {})

        changed = resolve_model_assignment(
            repos,
            alice.user_id,
            task=ModelTask.MATH_RASTER,
            engine_id="compact-math",
            engine_version="v2",
            provenance={"model_id": "owned-compact", "weight_sha256": "b" * 64},
        )
        assert changed.assignment.assignment_id != first.assignment.assignment_id
        assert first.assignment.active is False
        assert first.assignment.retired_at is not None
        assert changed.assignment.active is True
        assert repos.model_assignments.get_active(
            alice.user_id, ModelTask.MATH_RASTER.value
        ).assignment_id == changed.assignment.assignment_id

        other_user = resolve_model_assignment(
            repos,
            bob.user_id,
            task=ModelTask.MATH_RASTER,
            engine_id="compact-math",
            engine_version="v2",
            provenance={"model_id": "owned-compact", "weight_sha256": "b" * 64},
        )
        assert other_user.assignment.assignment_id != changed.assignment.assignment_id
        assert task_for_mode("formula") == ModelTask.MATH_RASTER
        assert task_for_mode("prose") == ModelTask.PROSE_RASTER


def test_math_assignment_does_not_apply_legacy_prose_adapter(session_factory) -> None:
    with session_factory() as session:
        user = User(username="alice", email="alice@example.com", password_hash="hashed")
        session.add(user)
        session.flush()
        matching_base = base_model_version(
            "crnn", "v1", {"model_id": "prose-base", "weight_sha256": "c" * 64}
        )
        adapter = Adapter(
            user_id=user.user_id,
            weights_path="adapters/alice/adapter.pt",
            base_model_version=matching_base,
            status=AdapterStatus.ACTIVE,
        )
        session.add(adapter)
        session.commit()
        repos = RepositoryFactory(session)

        prose = resolve_model_assignment(
            repos,
            user.user_id,
            task=ModelTask.PROSE_RASTER,
            engine_id="crnn",
            engine_version="v1",
            provenance={"model_id": "prose-base", "weight_sha256": "c" * 64},
        )
        math = resolve_model_assignment(
            repos,
            user.user_id,
            task=ModelTask.MATH_RASTER,
            engine_id="crnn",
            engine_version="v1",
            provenance={"model_id": "prose-base", "weight_sha256": "c" * 64},
        )
        assert prose.personalized is True
        assert prose.assignment.adapter_id == adapter.adapter_id
        assert math.personalized is False
        assert math.assignment.adapter_id is None


def test_formula_prediction_records_model_assignment(session_factory, tmp_path, monkeypatch) -> None:
    with session_factory() as session:
        user = User(username="alice", email="alice@example.com", password_hash="hashed")
        document = Document(user=user, title="Math")
        session.add_all([user, document])
        session.flush()
        store = FilesystemObjectStore(tmp_path / "objects")
        image_bytes = io.BytesIO()
        PILImage.new("RGB", (32, 32), "white").save(image_bytes, format="PNG")
        page_ref = store.put_page_raster(str(user.user_id), image_bytes.getvalue())
        page = DocumentPage(
            document_id=document.document_id,
            page_index=0,
            raster_path=page_ref,
            width=32,
            height=32,
            dpi=200,
        )
        session.add(page)
        session.flush()
        region = DocumentRegion(
            page_id=page.page_id,
            order_index=0,
            content_type="math_display",
            bbox_x=0,
            bbox_y=0,
            bbox_width=32,
            bbox_height=32,
            coordinate_space="page-raster-v1",
            source_crop_path=page_ref,
            crop_sha256="d" * 64,
        )
        session.add(region)
        session.commit()
        repos = RepositoryFactory(session)

        monkeypatch.setattr(
            math_regions_api,
            "recognize_formula_crop",
            lambda _crop: EngineResult(
                engine_id="compact-math",
                engine_version="v1",
                candidates=[EngineCandidate(text=r"x^2", kind="latex", score=0.8)],
                provenance={
                    "model_id": "owned-compact",
                    "weight_sha256": "e" * 64,
                    "checkpoint": "/tmp/hidden-checkpoint.pt",
                },
            ),
        )

        response = math_regions_api.recognize_region(region.region_id, user, repos, store)
        assert response.model_assignment_id is not None
        assert response.model_task == ModelTask.MATH_RASTER.value
        assert response.personalized is False
        prediction = session.get(RegionPrediction, response.prediction_id)
        assert prediction is not None
        assert prediction.model_assignment_id == response.model_assignment_id
        assert prediction.model_assignment is not None
        assert prediction.model_assignment.task == ModelTask.MATH_RASTER.value
        assert "/tmp/hidden-checkpoint" not in prediction.provenance["base_model_version"]


def test_assignment_endpoint_is_owner_scoped_and_hides_raw_metrics(client) -> None:
    test_client, session_factory = client
    alice_token = _register(test_client, session_factory, "alice")
    bob_token = _register(test_client, session_factory, "bob")
    with session_factory() as session:
        alice = session.query(User).filter_by(username="alice").one()
        repos = RepositoryFactory(session)
        resolve_model_assignment(
            repos,
            alice.user_id,
            task=ModelTask.MATH_RASTER,
            engine_id="compact-math",
            engine_version="v1",
            provenance={"model_id": "demo", "weight_sha256": "f" * 64},
        )
        session.commit()

    alice_response = test_client.get(
        "/personalization/assignments", headers=_auth(alice_token)
    )
    assert alice_response.status_code == 200, alice_response.text
    assert len(alice_response.json()) == 1
    assert alice_response.json()[0]["task"] == ModelTask.MATH_RASTER.value
    assert "metrics" not in alice_response.json()[0]
    assert test_client.get(
        "/personalization/assignments", headers=_auth(bob_token)
    ).json() == []
