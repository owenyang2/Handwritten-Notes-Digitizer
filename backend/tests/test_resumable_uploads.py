"""Contract tests for resumable multi-part uploads."""

from __future__ import annotations

import hashlib
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
    DocumentPage,
    InkBundle,
    RepositoryFactory,
    User,
    create_all,
    create_db_engine,
    create_session_factory,
)
from app.data.object_store import FilesystemObjectStore
from app.ml.ink_v1 import finalize
from app.services.auth import SessionStore, hash_password
from app.services.auth_api import (
    get_db_session,
    get_session_store,
    router as auth_router,
)
from app.services.images_api import get_object_store
from app.services.assets_api import router as assets_router
from app.services.uploads_api import router as uploads_router


@pytest.fixture()
def upload_client(
    tmp_path,
) -> Iterator[tuple[TestClient, object, SessionStore, FilesystemObjectStore]]:
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
    app.include_router(uploads_router)
    app.include_router(assets_router)
    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_session_store] = override_store
    app.dependency_overrides[get_object_store] = lambda: object_store

    with TestClient(app) as client:
        yield client, session_factory, store, object_store

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


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _png_bytes() -> bytes:
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (2, 2), (10, 20, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_resumable_upload_create_part_complete_and_idempotency(upload_client) -> None:
    client, session_factory, store, object_store = upload_client
    user, token = _user_and_token(session_factory, store, "alice")

    payload = _png_bytes()
    part_a = payload[:8]
    part_b = payload[8:]
    expected_sha = _sha256(payload)
    idempotency_key = str(uuid.uuid4())

    create = client.post(
        "/uploads",
        json={
            "filename": "note.png",
            "content_type": "image/png",
            "expected_size": len(payload),
            "expected_sha256": expected_sha,
            "part_size": 256 * 1024,
            "idempotency_key": idempotency_key,
        },
        headers=_auth(token),
    )
    assert create.status_code == 201, create.text
    body = create.json()
    upload_id = body["upload_id"]
    assert body["status"] == "open"
    assert body["part_size"] == 256 * 1024
    assert body["received_size"] == 0

    declared_oversize = client.put(
        f"/uploads/{upload_id}/parts/1",
        content=b"x",
        headers={
            **_auth(token),
            "X-Part-SHA256": _sha256(b"x"),
            "Content-Length": str(256 * 1024 + 1),
        },
    )
    assert declared_oversize.status_code == 413
    assert declared_oversize.json()["detail"]["code"] == "upload_too_large"

    replay = client.post(
        "/uploads",
        json={
            "filename": "note.png",
            "content_type": "image/png",
            "expected_size": len(payload),
            "expected_sha256": expected_sha,
            "part_size": 256 * 1024,
            "idempotency_key": idempotency_key,
        },
        headers=_auth(token),
    )
    assert replay.status_code == 201
    assert replay.json()["upload_id"] == upload_id

    reused_for_different_payload = client.post(
        "/uploads",
        json={
            "filename": "note.png",
            "content_type": "image/png",
            "expected_size": len(payload) + 1,
            "expected_sha256": _sha256(payload + b"!"),
            "part_size": 256 * 1024,
            "idempotency_key": idempotency_key,
        },
        headers=_auth(token),
    )
    assert reused_for_different_payload.status_code == 409
    assert reused_for_different_payload.json()["detail"]["code"] == "upload_conflict"

    first_part = client.put(
        f"/uploads/{upload_id}/parts/1",
        content=part_a,
        headers={**_auth(token), "X-Part-SHA256": _sha256(part_a)},
    )
    assert first_part.status_code == 200, first_part.text
    assert first_part.json()["received_size"] == len(part_a)

    first_part_again = client.put(
        f"/uploads/{upload_id}/parts/1",
        content=part_a,
        headers={**_auth(token), "X-Part-SHA256": _sha256(part_a)},
    )
    assert first_part_again.status_code == 200
    assert first_part_again.json()["received_size"] == len(part_a)
    assert len(first_part_again.json()["parts"]) == 1

    conflict = client.put(
        f"/uploads/{upload_id}/parts/1",
        content=b"HELLO ",
        headers={**_auth(token), "X-Part-SHA256": _sha256(b"HELLO ")},
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "upload_conflict"

    second_part = client.put(
        f"/uploads/{upload_id}/parts/2",
        content=part_b,
        headers={**_auth(token), "X-Part-SHA256": _sha256(part_b)},
    )
    assert second_part.status_code == 200, second_part.text

    status = client.get(f"/uploads/{upload_id}", headers=_auth(token))
    assert status.status_code == 200
    status_body = status.json()
    assert status_body["received_size"] == len(payload)
    assert [part["part_number"] for part in status_body["parts"]] == [1, 2]

    complete = client.post(f"/uploads/{upload_id}/complete", headers=_auth(token))
    assert complete.status_code == 200, complete.text
    complete_body = complete.json()
    assert complete_body["status"] == "completed"
    assert complete_body["assembled_ref"]
    assert object_store.get(complete_body["assembled_ref"]) == payload

    complete_again = client.post(
        f"/uploads/{upload_id}/complete", headers=_auth(token)
    )
    assert complete_again.status_code == 200
    assert complete_again.json()["assembled_ref"] == complete_body["assembled_ref"]

    with session_factory() as session:
        stored = RepositoryFactory(session).uploads.get(user.user_id, upload_id)
        assert stored is not None
        assert stored.parts_manifest is not None


def test_upload_part_hash_mismatch_and_owner_isolation(upload_client) -> None:
    client, session_factory, store, _object_store = upload_client
    alice, alice_token = _user_and_token(session_factory, store, "alice")
    _bob, bob_token = _user_and_token(session_factory, store, "bob")

    payload = b"abcdef"
    create = client.post(
        "/uploads",
        json={
            "filename": "page.png",
            "content_type": "image/png",
            "expected_size": len(payload),
            "expected_sha256": _sha256(payload),
            "part_size": 256 * 1024,
        },
        headers=_auth(alice_token),
    )
    assert create.status_code == 201, create.text
    upload_id = create.json()["upload_id"]

    mismatch = client.put(
        f"/uploads/{upload_id}/parts/1",
        content=payload,
        headers={**_auth(alice_token), "X-Part-SHA256": _sha256(b"other!!")},
    )
    assert mismatch.status_code in {400, 422}
    assert mismatch.json()["detail"]["code"] == "upload_hash_mismatch"

    assert (
        client.get(f"/uploads/{upload_id}", headers=_auth(bob_token)).status_code
        == 404
    )
    assert (
        client.put(
            f"/uploads/{upload_id}/parts/1",
            content=payload,
            headers={**_auth(bob_token), "X-Part-SHA256": _sha256(payload)},
        ).status_code
        == 404
    )

    too_large = client.post(
        "/uploads",
        json={
            "filename": "huge.bin",
            "expected_size": 50 * 1024 * 1024,
            "expected_sha256": "0" * 64,
        },
        headers=_auth(alice_token),
    )
    assert too_large.status_code == 413
    assert too_large.json()["detail"]["code"] == "upload_too_large"

    with session_factory() as session:
        assert (
            RepositoryFactory(session).uploads.get(alice.user_id, upload_id) is not None
        )


def test_resumable_ink_upload_publishes_one_immutable_revision(upload_client) -> None:
    client, session_factory, store, object_store = upload_client
    alice, alice_token = _user_and_token(session_factory, store, "ink-alice")
    _bob, bob_token = _user_and_token(session_factory, store, "ink-bob")

    with session_factory() as session:
        document = Document(
            user_id=alice.user_id,
            title="Resumable notebook",
            source_kind="notebook",
        )
        session.add(document)
        session.flush()
        page = DocumentPage(
            document_id=document.document_id,
            page_index=0,
            raster_path="native-ink://resumable-page",
            width=612,
            height=792,
            dpi=72,
            coordinate_space="ink-points-v1",
            order_key="00000000000000000000",
        )
        session.add(page)
        session.commit()
        page_id = page.page_id

    payload = finalize(
        {
            "schema": "ink_v1",
            "schema_version": 1,
            "page_id": str(page_id),
            "coordinate_space": {
                "width": 612,
                "height": 792,
                "unit": "points",
                "origin": "top_left",
            },
            "strokes": [
                {
                    "stroke_id": str(uuid.uuid4()),
                    "points": [
                        {"x": 20, "y": 30, "time_offset": 0},
                        {"x": 40, "y": 50, "time_offset": 0.1},
                    ],
                    "tool": "pen",
                    "color": {"red": 0, "green": 0, "blue": 0, "alpha": 1},
                    "width": 1.5,
                }
            ],
            "capture": {
                "pressure": False,
                "azimuth": False,
                "altitude": False,
                "timing": True,
                "source": "apple_pencil",
            },
        }
    )
    import json

    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    first = encoded[: max(1, len(encoded) // 2)]
    second = encoded[len(first) :]
    idempotency_key = str(uuid.uuid4())
    create = client.post(
        "/uploads",
        json={
            "purpose": "ink_v1",
            "filename": "page.ink.json",
            "content_type": "application/vnd.hnd.ink-v1+json",
            "expected_size": len(encoded),
            "expected_sha256": _sha256(encoded),
            "part_size": 256 * 1024,
            "idempotency_key": idempotency_key,
            "page_id": str(page_id),
        },
        headers=_auth(alice_token),
    )
    assert create.status_code == 201, create.text
    session_body = create.json()
    upload_id = session_body["upload_id"]
    assert session_body["purpose"] == "ink_v1"
    assert session_body["page_id"] == str(page_id)
    assert session_body["document_id"]

    assert (
        client.get(f"/uploads/{upload_id}", headers=_auth(bob_token)).status_code == 404
    )
    assert (
        client.put(
            f"/uploads/{upload_id}/parts/1",
            content=first,
            headers={**_auth(bob_token), "X-Part-SHA256": _sha256(first)},
        ).status_code
        == 404
    )

    first_part = client.put(
        f"/uploads/{upload_id}/parts/1",
        content=first,
        headers={**_auth(alice_token), "X-Part-SHA256": _sha256(first)},
    )
    assert first_part.status_code == 200, first_part.text
    assert first_part.json()["received_size"] == len(first)
    first_again = client.put(
        f"/uploads/{upload_id}/parts/1",
        content=first,
        headers={**_auth(alice_token), "X-Part-SHA256": _sha256(first)},
    )
    assert first_again.status_code == 200
    assert first_again.json()["idempotent"] is True

    second_part = client.put(
        f"/uploads/{upload_id}/parts/2",
        content=second,
        headers={**_auth(alice_token), "X-Part-SHA256": _sha256(second)},
    )
    assert second_part.status_code == 200, second_part.text

    completed = client.post(
        f"/uploads/{upload_id}/complete", headers=_auth(alice_token)
    )
    assert completed.status_code == 200, completed.text
    completed_body = completed.json()
    assert completed_body["status"] == "completed"
    assert completed_body["ink_bundle_id"]
    assert completed_body["assembled_ref"]
    assert object_store.get(completed_body["assembled_ref"]) != b""

    completed_again = client.post(
        f"/uploads/{upload_id}/complete", headers=_auth(alice_token)
    )
    assert completed_again.status_code == 200
    assert completed_again.json()["ink_bundle_id"] == completed_body["ink_bundle_id"]

    manifest = client.get(
        f"/pages/{page_id}/ink-bundles", headers=_auth(alice_token)
    )
    assert manifest.status_code == 200, manifest.text
    revisions = manifest.json()["revisions"]
    assert len(revisions) == 1
    assert revisions[0]["bundle_id"] == completed_body["ink_bundle_id"]
    assert revisions[0]["active"] is True

    with session_factory() as session:
        bundle = session.get(InkBundle, uuid.UUID(completed_body["ink_bundle_id"]))
        assert bundle is not None
        assert bundle.page_id == page_id
        assert bundle.revision_number == 1


def test_asset_completion_rejects_bytes_that_do_not_match_a_real_image(upload_client) -> None:
    client, session_factory, store, _object_store = upload_client
    _user, token = _user_and_token(session_factory, store, "spoof")
    payload = b"not-a-png"
    created = client.post(
        "/uploads",
        json={
            "filename": "note.png",
            "content_type": "image/png",
            "expected_size": len(payload),
            "expected_sha256": _sha256(payload),
            "part_size": 256 * 1024,
        },
        headers=_auth(token),
    )
    assert created.status_code == 201, created.text
    upload_id = created.json()["upload_id"]
    uploaded = client.put(
        f"/uploads/{upload_id}/parts/1",
        content=payload,
        headers={**_auth(token), "X-Part-SHA256": _sha256(payload)},
    )
    assert uploaded.status_code == 200, uploaded.text
    completed = client.post(f"/uploads/{upload_id}/complete", headers=_auth(token))
    assert completed.status_code == 422
    assert completed.json()["detail"]["code"] == "invalid_image"
