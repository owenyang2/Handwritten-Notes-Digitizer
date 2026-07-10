"""Integration coverage for the authenticated document-history API."""

from __future__ import annotations

from typing import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.data import Base, RepositoryFactory, User, create_all, create_db_engine, create_session_factory
from app.services import auth_api
from app.services.auth import SessionStore, hash_password
from app.services.auth_api import get_db_session, get_session_store, router as auth_router
from app.services.documents_api import router as documents_router


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
        app.dependency_overrides[get_db_session] = override_db
        app.dependency_overrides[get_session_store] = lambda: store
        with TestClient(app) as test_client:
            yield test_client, session_factory


def register(client: TestClient, session_factory, username: str) -> str:
    with session_factory() as session:
        session.add(
            User(
                username=username,
                email=f"{username}@example.com",
                password_hash=hash_password("hunter2"),
            )
        )
        session.commit()
    response = client.post("/auth/login", json={"username": username, "password": "hunter2"})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_create_list_and_fetch_document_are_scoped_to_the_current_user(client):
    test_client, session_factory = client
    alice_token = register(test_client, session_factory, "alice")
    bob_token = register(test_client, session_factory, "bob")

    create = test_client.post("/documents", json={"title": "  Physics notes  "}, headers=auth(alice_token))
    assert create.status_code == 201, create.text
    document = create.json()
    assert document["title"] == "Physics notes"
    assert document["image_ids"] == []

    listed = test_client.get("/documents", headers=auth(alice_token))
    assert listed.status_code == 200
    assert [item["document_id"] for item in listed.json()] == [document["document_id"]]

    denied = test_client.get(f"/documents/{document['document_id']}", headers=auth(bob_token))
    assert denied.status_code == 404
    assert denied.json()["detail"] == "Document not found"


def test_create_document_rejects_a_whitespace_only_title(client):
    test_client, session_factory = client
    token = register(test_client, session_factory, "alice")

    response = test_client.post("/documents", json={"title": "   "}, headers=auth(token))

    assert response.status_code == 422
    assert response.json()["detail"] == "Title is required"
