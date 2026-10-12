"""Contract tests for the server-sequenced document sync protocol."""

from __future__ import annotations

import uuid
from typing import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.data import (
    Base,
    Document,
    RepositoryFactory,
    User,
    create_all,
    create_db_engine,
    create_session_factory,
)
from app.services.auth import SessionStore, hash_password
from app.services.auth_api import (
    get_db_session,
    get_session_store,
    router as auth_router,
)
from app.services.sync_api import router as sync_router


@pytest.fixture()
def sync_client() -> Iterator[tuple[TestClient, object, object]]:
    engine = create_db_engine("sqlite://", poolclass=StaticPool)
    create_all(engine)
    session_factory = create_session_factory(engine)
    with session_factory() as seed_session:
        store = SessionStore(seed_session)

    def override_db() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    def override_store() -> SessionStore:
        return store

    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(sync_router)
    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_session_store] = override_store

    with TestClient(app) as client:
        yield client, session_factory, store

    Base.metadata.drop_all(engine)


def _user_and_token(session_factory, store: SessionStore, username: str) -> tuple[User, str]:
    with session_factory() as session:
        user = User(
            username=username,
            email=f"{username}@example.com",
            password_hash=hash_password("password-123"),
        )
        session.add(user)
        session.commit()
        user_id = user.user_id
    return User(user_id=user_id, username=username, email=f"{username}@example.com", password_hash=""), store.create(user_id)


def _document(session_factory, user_id: uuid.UUID, title: str = "Sync note") -> Document:
    with session_factory() as session:
        document = RepositoryFactory(session).documents.create(user_id, title)
        session.commit()
        return Document(
            document_id=document.document_id,
            user_id=user_id,
            title=title,
            created_at=document.created_at,
            updated_at=document.updated_at,
            sync_version=document.sync_version,
            default_language=document.default_language,
            source_kind=document.source_kind,
        )


def _operation(
    document_id: uuid.UUID,
    *,
    base: int,
    operation_id: uuid.UUID | None = None,
    client_id: str = "ipad-test-device",
    operation_type: str = "document.rename",
    payload: dict | None = None,
) -> dict:
    return {
        "client_id": client_id,
        "client_operation_id": str(operation_id or uuid.uuid4()),
        "document_id": str(document_id),
        "base_sync_version": base,
        "entity_type": "document",
        "entity_id": str(document_id),
        "operation_type": operation_type,
        "payload": payload or {"title": "Updated from iPad"},
    }


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_push_is_idempotent_and_pull_uses_server_cursor(sync_client) -> None:
    client, session_factory, store = sync_client
    user, token = _user_and_token(session_factory, store, "alice")
    document = _document(session_factory, user.user_id)
    operation = _operation(document.document_id, base=0)

    first = client.post("/sync/push", json={"operations": [operation]}, headers=_auth(token))
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert len(first_body["accepted"]) == 1
    assert first_body["accepted"][0]["idempotent"] is False
    assert first_body["accepted"][0]["sync_version"] == 1
    assert first_body["current_versions"][str(document.document_id)] == 1

    repeated = client.post("/sync/push", json={"operations": [operation]}, headers=_auth(token))
    assert repeated.status_code == 200, repeated.text
    repeated_body = repeated.json()
    assert repeated_body["accepted"][0]["idempotent"] is True
    assert repeated_body["accepted"][0]["server_sequence"] == first_body["accepted"][0]["server_sequence"]
    assert repeated_body["current_versions"][str(document.document_id)] == 1

    different_payload = dict(operation)
    different_payload["payload"] = {"title": "Not the original retry"}
    mismatched_retry = client.post(
        "/sync/push",
        json={"operations": [different_payload]},
        headers=_auth(token),
    )
    assert mismatched_retry.status_code == 409

    pulled = client.get("/sync/pull?cursor=0&limit=1", headers=_auth(token))
    assert pulled.status_code == 200, pulled.text
    pulled_body = pulled.json()
    assert len(pulled_body["items"]) == 1
    assert pulled_body["items"][0]["payload"] == operation["payload"]
    assert pulled_body["next_cursor"] == first_body["accepted"][0]["server_sequence"]
    assert pulled_body["has_more"] is False

    empty = client.get(
        f"/sync/pull?cursor={pulled_body['next_cursor']}", headers=_auth(token)
    )
    assert empty.status_code == 200
    assert empty.json() == {"items": [], "next_cursor": pulled_body["next_cursor"], "has_more": False}

    second = _operation(document.document_id, base=1, payload={"title": "Second edit"})
    second_response = client.post(
        "/sync/push", json={"operations": [second]}, headers=_auth(token)
    )
    assert second_response.status_code == 200, second_response.text
    assert second_response.json()["accepted"][0]["sync_version"] == 2

    retry_after_later_change = client.post(
        "/sync/push", json={"operations": [operation]}, headers=_auth(token)
    )
    assert retry_after_later_change.status_code == 200
    assert retry_after_later_change.json()["current_versions"][str(document.document_id)] == 2

    page = client.get("/sync/pull?cursor=0&limit=1", headers=_auth(token)).json()
    assert page["has_more"] is True

    with session_factory() as session:
        stored = session.get(Document, document.document_id)
        assert stored is not None
        assert stored.title == "Second edit"
        assert stored.sync_version == 2
        assert len(RepositoryFactory(session).sync.list_operations(user.user_id)) == 2


def test_favorite_operation_projects_and_pulls_as_a_document_change(sync_client) -> None:
    client, session_factory, store = sync_client
    user, token = _user_and_token(session_factory, store, "favorite-alice")
    document = _document(session_factory, user.user_id)
    operation = _operation(
        document.document_id,
        base=0,
        operation_type="document.favorite",
        payload={"favorite": True},
    )

    response = client.post(
        "/sync/push", json={"operations": [operation]}, headers=_auth(token)
    )
    assert response.status_code == 200, response.text
    assert response.json()["accepted"][0]["sync_version"] == 1

    pulled = client.get("/sync/pull?cursor=0", headers=_auth(token))
    assert pulled.status_code == 200
    assert pulled.json()["items"][0]["operation_type"] == "document.favorite"
    assert pulled.json()["items"][0]["payload"] == {"favorite": True}

    with session_factory() as session:
        stored = session.get(Document, document.document_id)
        assert stored is not None
        assert stored.is_favorite is True


def test_organization_operations_project_and_sync_metadata(sync_client) -> None:
    client, session_factory, store = sync_client
    user, token = _user_and_token(session_factory, store, "organization-alice")
    document = _document(session_factory, user.user_id)

    tags = _operation(
        document.document_id,
        base=0,
        operation_type="document.tags",
        payload={"tags": ["math", "exam", "math"]},
    )
    tags_response = client.post(
        "/sync/push", json={"operations": [tags]}, headers=_auth(token)
    )
    assert tags_response.status_code == 200, tags_response.text

    move = _operation(
        document.document_id,
        base=1,
        operation_type="document.move",
        payload={"folder_id": "semester-1"},
    )
    move_response = client.post(
        "/sync/push", json={"operations": [move]}, headers=_auth(token)
    )
    assert move_response.status_code == 200, move_response.text

    with session_factory() as session:
        stored = session.get(Document, document.document_id)
        assert stored is not None
        assert stored.source_metadata == {
            "tags": ["math", "exam"],
            "folder_id": "semester-1",
        }

    pulled = client.get("/sync/pull?cursor=0", headers=_auth(token))
    assert pulled.status_code == 200
    assert [item["operation_type"] for item in pulled.json()["items"]] == [
        "document.tags",
        "document.move",
    ]


def test_stale_operations_are_retained_as_owner_scoped_conflicts(sync_client) -> None:
    client, session_factory, store = sync_client
    alice, alice_token = _user_and_token(session_factory, store, "alice")
    bob, bob_token = _user_and_token(session_factory, store, "bob")
    document = _document(session_factory, alice.user_id)

    current = _operation(document.document_id, base=0)
    assert client.post("/sync/push", json={"operations": [current]}, headers=_auth(alice_token)).status_code == 200

    stale = _operation(
        document.document_id,
        base=0,
        operation_type="document.reorder",
        payload={"page_id": "page-1", "position": 0},
    )
    conflict_response = client.post(
        "/sync/push", json={"operations": [stale]}, headers=_auth(alice_token)
    )
    assert conflict_response.status_code == 200, conflict_response.text
    conflict = conflict_response.json()["conflicts"][0]
    assert conflict["current_sync_version"] == 1
    assert conflict["status"] == "pending"

    repeated_conflict = client.post(
        "/sync/push", json={"operations": [stale]}, headers=_auth(alice_token)
    )
    assert repeated_conflict.status_code == 200
    assert repeated_conflict.json()["conflicts"][0]["conflict_id"] == conflict["conflict_id"]

    assert client.get("/sync/conflicts", headers=_auth(alice_token)).json()[0]["conflict_id"] == conflict["conflict_id"]
    resolved = client.post(
        f"/sync/conflicts/{conflict['conflict_id']}/resolve",
        json={"decision": "keep_server"},
        headers=_auth(alice_token),
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["status"] == "resolved"
    assert client.get("/sync/conflicts", headers=_auth(alice_token)).json() == []
    assert len(client.get("/sync/conflicts?include_resolved=true", headers=_auth(alice_token)).json()) == 1

    # Both the document target and the conflict record are owner-scoped.
    bob_push = client.post(
        "/sync/push",
        json={"operations": [_operation(document.document_id, base=1)]},
        headers=_auth(bob_token),
    )
    assert bob_push.status_code == 404
    bob_conflicts = client.get("/sync/conflicts", headers=_auth(bob_token))
    assert bob_conflicts.status_code == 200
    assert bob_conflicts.json() == []
    assert client.post(
        f"/sync/conflicts/{conflict['conflict_id']}/resolve",
        json={"decision": "keep_server"},
        headers=_auth(bob_token),
    ).status_code == 404


def test_batch_versions_are_checked_in_order_and_payload_is_bounded(sync_client) -> None:
    client, session_factory, store = sync_client
    user, token = _user_and_token(session_factory, store, "alice")
    document = _document(session_factory, user.user_id)
    first = _operation(document.document_id, base=0)
    second = _operation(document.document_id, base=0, payload={"title": "Conflicting edit"})

    response = client.post(
        "/sync/push", json={"operations": [first, second]}, headers=_auth(token)
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["accepted"]) == 1
    assert len(body["conflicts"]) == 1
    assert body["current_versions"][str(document.document_id)] == 1

    too_many = client.post(
        "/sync/push",
        json={"operations": [
            _operation(document.document_id, base=1) for _ in range(51)
        ]},
        headers=_auth(token),
    )
    assert too_many.status_code == 422


def test_offline_document_create_assigns_stable_id_and_bootstraps_first_page(sync_client) -> None:
    client, session_factory, store = sync_client
    user, token = _user_and_token(session_factory, store, "offline-alice")
    document_id = uuid.uuid4()
    page_id = uuid.uuid4()
    operation = _operation(
        document_id,
        base=0,
        operation_type="document.create",
        payload={
            "title": "Offline class notes",
            "default_language": "en",
            "source_kind": "native_ink",
            "source_metadata": {"original_filename": "class-notes.pdf"},
            "pages": [
                {
                    "page_id": str(page_id),
                    "page_index": 0,
                    "width": 768,
                    "height": 1024,
                    "dpi": 132,
                    "coordinate_space": "points",
                }
            ],
        },
    )

    first = client.post("/sync/push", json={"operations": [operation]}, headers=_auth(token))
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["accepted"][0]["document_id"] == str(document_id)
    assert body["accepted"][0]["sync_version"] == 1

    repeated = client.post("/sync/push", json={"operations": [operation]}, headers=_auth(token))
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["accepted"][0]["idempotent"] is True

    with session_factory() as session:
        stored = session.get(Document, document_id)
        assert stored is not None
        assert stored.title == "Offline class notes"
        assert stored.source_metadata == {"original_filename": "class-notes.pdf"}
        assert stored.sync_version == 1
        assert len(stored.pages) == 1
        assert stored.pages[0].page_id == page_id

    pulled = client.get("/sync/pull?cursor=0", headers=_auth(token))
    assert pulled.status_code == 200, pulled.text
    assert pulled.json()["items"][0]["operation_type"] == "document.create"


def test_page_operations_keep_normalized_order_and_reject_last_page_delete(sync_client) -> None:
    client, session_factory, store = sync_client
    user, token = _user_and_token(session_factory, store, "page-alice")
    document = _document(session_factory, user.user_id)
    first_page = uuid.uuid4()
    second_page = uuid.uuid4()

    def push(base: int, operation_type: str, payload: dict) -> dict:
        response = client.post(
            "/sync/push",
            json={"operations": [_operation(
                document.document_id,
                base=base,
                operation_type=operation_type,
                payload=payload,
            )]},
            headers=_auth(token),
        )
        assert response.status_code == 200, response.text
        return response.json()

    first = push(
        0,
        "page.add",
        {"page": {
            "page_id": str(first_page),
            "page_index": 0,
            "width": 768,
            "height": 1024,
            "dpi": 132,
            "coordinate_space": "points",
        }, "page_index": 0},
    )
    assert first["accepted"][0]["sync_version"] == 1
    second = push(
        1,
        "page.add",
        {"page": {
            "page_id": str(second_page),
            "page_index": 1,
            "width": 768,
            "height": 1024,
            "dpi": 132,
            "coordinate_space": "points",
        }, "page_index": 1},
    )
    assert second["accepted"][0]["sync_version"] == 2
    reordered = push(
        2,
        "page.reorder",
        {"page_id": str(second_page), "page_index": 0},
    )
    assert reordered["accepted"][0]["sync_version"] == 3

    with session_factory() as session:
        stored = session.get(Document, document.document_id)
        assert stored is not None
        assert [page.page_id for page in stored.pages] == [second_page, first_page]
        assert [page.page_index for page in stored.pages] == [0, 1]

    deleted = push(
        3,
        "page.delete",
        {"page_id": str(first_page)},
    )
    assert deleted["accepted"][0]["sync_version"] == 4

    last_delete = client.post(
        "/sync/push",
        json={"operations": [_operation(
            document.document_id,
            base=4,
            operation_type="page.delete",
            payload={"page_id": str(second_page)},
        )]},
        headers=_auth(token),
    )
    assert last_delete.status_code == 422, last_delete.text


def test_page_template_operation_round_trips_and_validates(sync_client) -> None:
    client, session_factory, store = sync_client
    user, token = _user_and_token(session_factory, store, "page-template-alice")
    document = _document(session_factory, user.user_id)
    page_id = uuid.uuid4()

    create = client.post(
        "/sync/push",
        json={"operations": [_operation(
            document.document_id,
            base=0,
            operation_type="page.add",
            payload={"page": {
                "page_id": str(page_id),
                "page_index": 0,
                "title": "Formula sheet",
                "width": 768,
                "height": 1024,
                "dpi": 132,
                "coordinate_space": "points",
                "paper_template": "grid",
            }, "page_index": 0},
        )]},
        headers=_auth(token),
    )
    assert create.status_code == 200, create.text

    update = client.post(
        "/sync/push",
        json={"operations": [_operation(
            document.document_id,
            base=1,
            operation_type="page.template",
            payload={"page_id": str(page_id), "paper_template": "cornell"},
        )]},
        headers=_auth(token),
    )
    assert update.status_code == 200, update.text

    with session_factory() as session:
        stored = session.get(Document, document.document_id)
        assert stored is not None
        assert stored.pages[0].paper_template == "cornell"

    rename = client.post(
        "/sync/push",
        json={"operations": [_operation(
            document.document_id,
            base=2,
            operation_type="page.rename",
            payload={"page_id": str(page_id), "title": "Chapter 1"},
        )]},
        headers=_auth(token),
    )
    assert rename.status_code == 200, rename.text
    with session_factory() as session:
        stored = session.get(Document, document.document_id)
        assert stored is not None
        assert stored.pages[0].title == "Chapter 1"

    resize = client.post(
        "/sync/push",
        json={"operations": [_operation(
            document.document_id,
            base=3,
            operation_type="page.resize",
            payload={"page_id": str(page_id), "width": 595, "height": 842},
        )]},
        headers=_auth(token),
    )
    assert resize.status_code == 200, resize.text
    with session_factory() as session:
        stored = session.get(Document, document.document_id)
        assert stored is not None
        assert stored.pages[0].width == 595
        assert stored.pages[0].height == 842

    invalid = client.post(
        "/sync/push",
        json={"operations": [_operation(
            document.document_id,
            base=4,
            operation_type="page.template",
            payload={"page_id": str(page_id), "paper_template": "unknown"},
        )]},
        headers=_auth(token),
    )
    assert invalid.status_code == 422, invalid.text


def test_permanent_delete_requires_trash_and_emits_tombstone(sync_client) -> None:
    client, session_factory, store = sync_client
    user, token = _user_and_token(session_factory, store, "permanent-delete-alice")
    document = _document(session_factory, user.user_id)

    not_trashed = client.post(
        "/sync/push",
        json={"operations": [_operation(
            document.document_id,
            base=0,
            operation_type="document.delete",
            payload={},
        )]},
        headers=_auth(token),
    )
    assert not_trashed.status_code == 422, not_trashed.text

    trash = client.post(
        "/sync/push",
        json={"operations": [_operation(
            document.document_id,
            base=0,
            operation_type="document.trash",
            payload={},
        )]},
        headers=_auth(token),
    )
    assert trash.status_code == 200, trash.text

    delete = client.post(
        "/sync/push",
        json={"operations": [_operation(
            document.document_id,
            base=1,
            operation_type="document.delete",
            payload={},
        )]},
        headers=_auth(token),
    )
    assert delete.status_code == 200, delete.text
    assert delete.json()["accepted"][0]["sync_version"] == 2

    with session_factory() as session:
        stored = session.get(Document, document.document_id)
        assert stored is not None
        assert stored.deleted_at is not None

    with session_factory() as session:
        assert RepositoryFactory(session).documents.get(user.user_id, document.document_id) is None

    pulled = client.get("/sync/pull?cursor=0&limit=20", headers=_auth(token))
    assert pulled.status_code == 200, pulled.text
    delete_change = next(
        item for item in pulled.json()["items"] if item["operation_type"] == "document.delete"
    )
    assert delete_change["is_tombstone"] is True
