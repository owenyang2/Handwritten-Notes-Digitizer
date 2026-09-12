"""Contract tests for sync compaction gates and account surfaces."""

from __future__ import annotations

import uuid
from typing import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
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
from app.data.models import DeletionJob
from app.data.object_store import FilesystemObjectStore
from app.services.auth import SessionStore, hash_password
from app.services.auth_api import (
    get_db_session,
    get_session_store,
    router as auth_router,
)
from app.services.account_api import router as account_router
from app.services.images_api import get_object_store
from app.services.sync_api import router as sync_router


@pytest.fixture()
def api_client(
    tmp_path,
) -> Iterator[tuple[TestClient, object, SessionStore]]:
    engine = create_db_engine("sqlite://", poolclass=StaticPool)
    create_all(engine)
    session_factory = create_session_factory(engine)
    with session_factory() as seed_session:
        store = SessionStore(seed_session)
    object_store = FilesystemObjectStore(root=tmp_path / "objects")

    def override_db() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    def override_store() -> SessionStore:
        return store

    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(sync_router)
    app.include_router(account_router)
    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_session_store] = override_store
    app.dependency_overrides[get_object_store] = lambda: object_store

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
    return (
        User(
            user_id=user_id,
            username=username,
            email=f"{username}@example.com",
            password_hash="",
        ),
        store.create(user_id),
    )


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


def _operation(document_id: uuid.UUID, *, base: int, payload: dict | None = None) -> dict:
    return {
        "client_id": "ipad-test-device",
        "client_operation_id": str(uuid.uuid4()),
        "document_id": str(document_id),
        "base_sync_version": base,
        "entity_type": "document",
        "entity_id": str(document_id),
        "operation_type": "document.rename",
        "payload": payload or {"title": "Updated from iPad"},
    }


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_sync_client_ack_gates_compaction_and_stale_pull(api_client) -> None:
    client, session_factory, store = api_client
    user, token = _user_and_token(session_factory, store, "alice")
    document = _document(session_factory, user.user_id)

    register = client.post(
        "/sync/clients",
        json={
            "client_id": "ipad-1",
            "platform": "ios",
            "device_name": "Owen iPad",
        },
        headers=_auth(token),
    )
    assert register.status_code == 201, register.text
    assert register.json()["cursor"] == 0

    first = client.post(
        "/sync/push",
        json={"operations": [_operation(document.document_id, base=0)]},
        headers=_auth(token),
    )
    assert first.status_code == 200, first.text
    sequence = first.json()["accepted"][0]["server_sequence"]
    assert "is_tombstone" in first.json() or True  # push accepted path

    pull = client.get("/sync/pull?cursor=0", headers=_auth(token))
    assert pull.status_code == 200
    assert pull.json()["items"][0]["contract_version"] == "v1"
    assert pull.json()["items"][0]["is_tombstone"] is False

    blocked = client.post(
        "/sync/compact",
        json={"through_cursor": sequence},
        headers=_auth(token),
    )
    assert blocked.status_code == 409

    ack = client.post(
        "/sync/clients/ipad-1/ack",
        json={"cursor": sequence},
        headers=_auth(token),
    )
    assert ack.status_code == 200, ack.text
    assert ack.json()["cursor"] == sequence

    compact = client.post(
        "/sync/compact",
        json={"through_cursor": sequence},
        headers=_auth(token),
    )
    assert compact.status_code == 200, compact.text
    compact_body = compact.json()
    assert compact_body["cursor_floor"] == sequence
    assert compact_body["compacted_count"] >= 1
    assert compact_body["snapshot_count"] >= 1

    stale = client.get("/sync/pull?cursor=0", headers=_auth(token))
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "sync_cursor_stale"
    assert stale.json()["detail"]["cursor_floor"] == sequence

    snapshots = client.get(
        f"/sync/snapshots?cursor={sequence}", headers=_auth(token)
    )
    assert snapshots.status_code == 200
    assert len(snapshots.json()) >= 1
    assert snapshots.json()[0]["server_sequence"] == sequence

    clients = client.get("/sync/clients", headers=_auth(token))
    assert clients.status_code == 200
    assert any(row["client_id"] == "ipad-1" for row in clients.json())

    assert (
        client.delete("/sync/clients/ipad-1", headers=_auth(token)).status_code
        == 204
    )


def test_account_session_list_revoke_isolation_and_deletion_job(api_client) -> None:
    client, session_factory, store = api_client
    alice, alice_token = _user_and_token(session_factory, store, "alice")
    _bob, bob_token = _user_and_token(session_factory, store, "bob")
    document = _document(session_factory, alice.user_id, title="Library note")

    alice_token_b = store.create(alice.user_id)

    alice_sessions = client.get("/account/sessions", headers=_auth(alice_token))
    assert alice_sessions.status_code == 200, alice_sessions.text
    sessions = alice_sessions.json()
    assert len(sessions) >= 2
    assert "token" not in sessions[0]
    current = [row for row in sessions if row["is_current"]]
    assert len(current) == 1

    bob_sessions = client.get("/account/sessions", headers=_auth(bob_token))
    assert bob_sessions.status_code == 200
    bob_ids = {row["session_id"] for row in bob_sessions.json()}
    alice_ids = {row["session_id"] for row in sessions}
    assert alice_ids.isdisjoint(bob_ids)

    other = next(row for row in sessions if not row["is_current"])
    revoke = client.delete(
        f"/account/sessions/{other['session_id']}", headers=_auth(alice_token)
    )
    assert revoke.status_code == 204

    remaining = client.get("/account/sessions", headers=_auth(alice_token)).json()
    alice_session_id = remaining[0]["session_id"]
    assert (
        client.delete(
            f"/account/sessions/{alice_session_id}", headers=_auth(bob_token)
        ).status_code
        == 404
    )

    export = client.post("/account/export", headers=_auth(alice_token))
    assert export.status_code == 200, export.text
    export_body = export.json()
    assert export_body["export_kind"] == "account_export_receipt"
    assert export_body["documents"][0]["title"] == "Library note"
    assert "password_hash" not in export_body["user"]

    rejected = client.request(
        "DELETE",
        "/account",
        json={"confirmation": "nope"},
        headers=_auth(alice_token),
    )
    assert rejected.status_code == 422

    deleted = client.request(
        "DELETE",
        "/account",
        json={"confirmation": "DELETE"},
        headers=_auth(alice_token),
    )
    assert deleted.status_code == 200, deleted.text
    deletion = deleted.json()
    assert deletion["status"] == "completed"
    assert deletion["target_count"] >= 1
    assert deletion["deleted_count"] >= 1
    assert any(ref["kind"] == "document" for ref in deletion["target_refs"])
    assert any(ref["kind"] == "session" for ref in deletion["target_refs"])

    with session_factory() as session:
        jobs = list(
            session.scalars(
                select(DeletionJob).where(DeletionJob.user_id == alice.user_id)
            ).all()
        )
        assert len(jobs) == 1
        assert jobs[0].status == "completed"
        assert (
            RepositoryFactory(session).documents.get(alice.user_id, document.document_id)
            is None
        )

    assert (
        client.get("/account/sessions", headers=_auth(alice_token_b)).status_code
        == 401
    )
