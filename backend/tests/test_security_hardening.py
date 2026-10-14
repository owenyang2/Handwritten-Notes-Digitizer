"""Regression coverage for the security-hardening pass.

These tests lock the account-takeover and data-retention fixes: reset tokens
are not returned by default, confirming a reset revokes sessions, production
refuses known credentials, and deleting an account removes every user-scoped
row plus the owner's object-store prefixes.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import timedelta
from typing import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config.settings import Settings, assert_production_safe
from app.data import (
    Base,
    User,
    create_all,
    create_db_engine,
    create_session_factory,
)
from app.data.base import utcnow
from app.data.enums import ImageFormat, TrainingPairOrigin
from app.data.models import (
    Adapter,
    Asset,
    ConsentEvent,
    DeletionJob,
    Document,
    DocumentConflict,
    DocumentOperation,
    DocumentPage,
    DocumentRegion,
    DocumentRevision,
    DocumentSyncSnapshot,
    EvaluationResult,
    Image,
    ImageLineReview,
    InkBundle,
    ModelAssignment,
    PasswordResetToken,
    RecognitionJob,
    RecognitionRun,
    RecognizedWord,
    RegionPrediction,
    RegionReview,
    ReviewRevision,
    SyncClient,
    SyncCursorState,
    TrainingExample,
    TrainingPair,
    TrainingRun,
    TrainingRunPair,
    UploadPart,
    UploadSession,
    UserSession,
    UserVocabularyEntry,
)
from app.data.object_store import FilesystemObjectStore
from app.middleware import rate_limiter
from app.middleware.rate_limiter import login_lock_remaining, note_login_failure, note_login_success
from app.observability import redact_text
from app.services.account_api import router as account_router
from app.services.auth import SessionStore, hash_password
from app.services.auth_api import get_db_session, get_session_store, router as auth_router
from app.services.consent_api import account_recovery_router
from app.services.images_api import get_object_store


def test_environment_rejects_unknown_names() -> None:
    with pytest.raises(ValidationError):
        Settings(environment="prod")


def test_production_refuses_default_credentials_and_reset_tokens() -> None:
    insecure = Settings(
        environment="production",
        database_url="postgresql+psycopg://hnd:hnd@postgres:5432/hnd",
        object_store_backend="s3",
        s3_bucket="hnd-objects",
        s3_access_key="minioadmin",
        s3_secret_key="minioadmin",
    )
    with pytest.raises(RuntimeError, match="known default"):
        assert_production_safe(insecure)

    exposed = Settings(
        environment="production",
        expose_reset_tokens=True,
        database_url="sqlite://",
    )
    with pytest.raises(RuntimeError, match="HND_EXPOSE_RESET_TOKENS"):
        assert_production_safe(exposed)


def test_redactor_strips_email_addresses() -> None:
    assert redact_text("reset for ada@example.com failed") == "reset for [EMAIL] failed"


def test_login_lockout_backs_off_after_repeated_failures(monkeypatch) -> None:
    monkeypatch.setenv("HND_ENVIRONMENT", "development")
    monkeypatch.setattr(rate_limiter, "_failures", rate_limiter._FailureTracker())
    monkeypatch.setattr(rate_limiter, "_get_redis", lambda: None)
    monkeypatch.setattr(rate_limiter, "_redis_client", None)
    monkeypatch.setattr(rate_limiter, "_redis_init_attempted", True)

    assert login_lock_remaining("Alice") is None
    for _ in range(4):
        note_login_failure("Alice")
    assert login_lock_remaining("alice") is None
    note_login_failure("alice")
    assert login_lock_remaining("ALICE") is not None
    note_login_success("alice")
    assert login_lock_remaining("alice") is None


@pytest.fixture()
def api_client(tmp_path) -> Iterator[tuple[TestClient, object, SessionStore, FilesystemObjectStore]]:
    engine = create_db_engine("sqlite://", poolclass=StaticPool)
    create_all(engine)
    session_factory = create_session_factory(engine)
    with session_factory() as seed_session:
        sessions = SessionStore(seed_session)
    object_store = FilesystemObjectStore(root=tmp_path / "objects")

    def override_db() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(account_recovery_router)
    app.include_router(account_router)
    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_session_store] = lambda: sessions
    app.dependency_overrides[get_object_store] = lambda: object_store

    with TestClient(app) as client:
        yield client, session_factory, sessions, object_store

    Base.metadata.drop_all(engine)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_password_reset_does_not_reveal_tokens_and_revokes_sessions(api_client) -> None:
    client, session_factory, sessions, _store = api_client
    with session_factory() as session:
        user = User(
            username="ada",
            email="Ada@Example.com",
            password_hash=hash_password("password-123"),
        )
        session.add(user)
        session.commit()
        user_id = user.user_id
    token = sessions.create(user_id)
    older = secrets_token(session_factory, user_id)

    hidden = client.post("/auth/password-reset/request", json={"email": "ada@example.com"})
    missing = client.post("/auth/password-reset/request", json={"email": "missing@example.com"})
    assert hidden.status_code == 202
    assert missing.status_code == 202
    assert hidden.json() == {"status": "accepted"}
    assert missing.json() == {"status": "accepted"}

    confirm = client.post(
        "/auth/password-reset/confirm",
        json={"token": older[0], "new_password": "replacement-123"},
    )
    assert confirm.status_code == 200, confirm.text
    assert client.get("/auth/me", headers=_auth(token)).status_code == 401
    login = client.post(
        "/auth/login",
        json={"username": "ada", "password": "replacement-123"},
    )
    assert login.status_code == 200, login.text

    with session_factory() as session:
        rows = list(
            session.scalars(
                select(PasswordResetToken).where(PasswordResetToken.user_id == user_id)
            ).all()
        )
        assert rows
        assert all(row.used_at is not None for row in rows)
        live = list(
            session.scalars(
                select(UserSession).where(
                    UserSession.user_id == user_id,
                    UserSession.revoked_at.is_(None),
                )
            ).all()
        )
        # The successful login above opened one new session.
        assert len(live) == 1
        assert sessions.resolve(token) is None


def secrets_token(session_factory, user_id: uuid.UUID) -> tuple[str, str]:
    """Insert two reset tokens and return the raw value of the one tests confirm."""
    raw = "confirm-this-reset-token-0123456789"
    other = "older-reset-token-should-be-spent"
    with session_factory() as session:
        session.add_all(
            [
                PasswordResetToken(
                    token_hash=hashlib.sha256(raw.encode()).hexdigest(),
                    user_id=user_id,
                    expires_at=utcnow() + timedelta(hours=1),
                ),
                PasswordResetToken(
                    token_hash=hashlib.sha256(other.encode()).hexdigest(),
                    user_id=user_id,
                    expires_at=utcnow() + timedelta(hours=1),
                ),
            ]
        )
        session.commit()
    return raw, other


def test_account_deletion_purges_user_owned_data(api_client) -> None:
    client, session_factory, sessions, store = api_client
    with session_factory() as session:
        alice = User(
            username="alice",
            email="alice@example.com",
            password_hash=hash_password("password-123"),
        )
        bob = User(
            username="bob",
            email="bob@example.com",
            password_hash=hash_password("password-123"),
        )
        session.add_all([alice, bob])
        session.commit()
        alice_id = alice.user_id
        bob_id = bob.user_id

        alice_image = store.put_image_bytes(str(alice_id), b"alice-image", suffix=".bin")
        alice_crop = store.put_region_crop(str(alice_id), b"crop", suffix=".png")
        alice_adapter = store.put_adapter_weights(str(alice_id), b"weights", suffix=".pt")
        alice_orphan = store.put_image_bytes(str(alice_id), b"orphan", suffix=".bin")
        bob_image = store.put_image_bytes(str(bob_id), b"bob-image", suffix=".bin")

        document = Document(
            user_id=alice_id,
            title="Lecture",
            original_path=store.put_document_bytes(str(alice_id), b"%PDF-1.4", suffix=".pdf"),
        )
        bob_document = Document(user_id=bob_id, title="Bob's notes")
        session.add_all([document, bob_document])
        session.flush()
        image = Image(
            user_id=alice_id,
            document_id=document.document_id,
            storage_path=alice_image,
            format=ImageFormat.PNG,
            size_bytes=11,
        )
        session.add(image)
        session.flush()
        page = DocumentPage(
            document_id=document.document_id,
            page_index=0,
            raster_path=store.put_image_bytes(str(alice_id), b"raster", suffix=".png"),
            width=10,
            height=10,
        )
        session.add(page)
        session.flush()
        region = DocumentRegion(
            page_id=page.page_id,
            bbox_x=0,
            bbox_y=0,
            bbox_width=4,
            bbox_height=4,
            source_crop_path=alice_crop,
        )
        session.add(region)
        session.flush()
        review = RegionReview(
            region_id=region.region_id,
            reviewed_text="x",
            author_user_id=alice_id,
        )
        revision = ReviewRevision(
            region_id=region.region_id,
            reviewed_text="x",
            author_user_id=alice_id,
        )
        prediction = RegionPrediction(region_id=region.region_id, engine_id="compact")
        session.add_all([review, revision, prediction])
        session.flush()
        session.add(
            TrainingExample(
                user_id=alice_id,
                region_id=region.region_id,
                review_revision_id=revision.review_revision_id,
                crop_sha256="a" * 64,
            )
        )
        session.add(
            DocumentRevision(
                document_id=document.document_id,
                revision_number=1,
                author_user_id=alice_id,
            )
        )
        session.add(
            DocumentOperation(
                user_id=alice_id,
                document_id=document.document_id,
                client_id="ipad",
                client_operation_id=uuid.uuid4(),
                base_sync_version=0,
                document_sync_version=1,
                entity_type="document",
                operation_type="document.create",
            )
        )
        session.add(
            DocumentConflict(
                user_id=alice_id,
                document_id=document.document_id,
                client_id="ipad",
                client_operation_id=uuid.uuid4(),
                base_sync_version=0,
                current_sync_version=1,
                entity_type="document",
                operation_type="document.update",
            )
        )
        session.add(SyncClient(user_id=alice_id, client_id="ipad"))
        session.add(SyncCursorState(user_id=alice_id))
        session.add(
            DocumentSyncSnapshot(
                user_id=alice_id,
                document_id=document.document_id,
                server_sequence=1,
                document_sync_version=1,
            )
        )
        session.add(RecognizedWord(
            image_id=image.image_id,
            line_index=0,
            word_index=0,
            text="hi",
            region_x=0,
            region_y=0,
            region_width=1,
            region_height=1,
        ))
        session.add(ImageLineReview(image_id=image.image_id, line_index=0))
        upload = UploadSession(
            user_id=alice_id,
            expected_size=4,
            expected_sha256="b" * 64,
            expires_at=utcnow() + timedelta(hours=1),
            assembled_ref=alice_orphan,
        )
        session.add(upload)
        session.flush()
        session.add(
            UploadPart(
                upload_id=upload.upload_id,
                part_number=1,
                size_bytes=4,
                sha256="c" * 64,
                storage_path=store.put_upload_part(str(alice_id), b"part", suffix=".part"),
            )
        )
        run = TrainingRun(user_id=alice_id)
        pair = TrainingPair(
            user_id=alice_id,
            source_region_key="region-1",
            region_crop_path=alice_crop,
            corrected_text="x",
            origin=TrainingPairOrigin.CORRECTION,
        )
        session.add_all([run, pair])
        session.flush()
        session.add(TrainingRunPair(run_id=run.run_id, pair_id=pair.pair_id))
        session.add(EvaluationResult(user_id=alice_id, run_id=run.run_id))
        adapter = Adapter(
            user_id=alice_id,
            weights_path=alice_adapter,
            base_model_version="base",
        )
        session.add(adapter)
        session.flush()
        session.add(
            ModelAssignment(
                user_id=alice_id,
                task="prose",
                assignment_key="prose:base",
                base_model_version="base",
                adapter_id=adapter.adapter_id,
            )
        )
        asset = Asset(
            user_id=alice_id,
            content_hash="d" * 64,
            media_type="application/json",
            byte_size=2,
            object_ref=store.put_asset_bytes(str(alice_id), b"{}", suffix=".json"),
        )
        session.add(asset)
        session.flush()
        session.add(
            InkBundle(
                user_id=alice_id,
                page_id=page.page_id,
                asset_id=asset.asset_id,
                content_hash="e" * 64,
            )
        )
        session.add(
            ConsentEvent(
                user_id=alice_id,
                policy_version="v1",
                scope="personal_model",
                action="grant",
                document_id=document.document_id,
            )
        )
        session.add(
            UserVocabularyEntry(user_id=alice_id, token="2", preferred_form="z")
        )
        session.add(
            PasswordResetToken(
                token_hash="f" * 64,
                user_id=alice_id,
                expires_at=utcnow() + timedelta(hours=1),
            )
        )
        session.add(
            RecognitionJob(user_id=alice_id, document_id=document.document_id, page_id=page.page_id)
        )
        session.add(
            RecognitionRun(
                user_id=alice_id,
                document_id=document.document_id,
                page_id=page.page_id,
                scope="page",
                mode="formula",
            )
        )
        session.add(
            UserSession(
                user_id=alice_id,
                token_hash="1" * 64,
                expires_at=utcnow() + timedelta(days=1),
            )
        )
        session.commit()
        seeded = _user_scoped_tables(session)
        page_id = page.page_id
        region_id = region.region_id
        image_id = image.image_id

    token = sessions.create(alice_id)
    with session_factory() as session:
        session.execute(text("PRAGMA foreign_keys=ON"))
    deleted = client.request(
        "DELETE",
        "/account",
        json={"confirmation": "DELETE"},
        headers=_auth(token),
    )
    assert deleted.status_code == 200, deleted.text
    body = deleted.json()
    assert body["status"] == "completed"
    assert client.get("/auth/me", headers=_auth(token)).status_code == 401
    assert (
        client.post("/auth/login", json={"username": "alice", "password": "password-123"}).status_code
        == 401
    )
    bob_login = client.post(
        "/auth/login", json={"username": "bob", "password": "password-123"}
    )
    assert bob_login.status_code == 200, bob_login.text

    with session_factory() as session:
        assert session.get(User, alice_id) is None
        assert session.get(User, bob_id) is not None
        assert session.get(Document, bob_document.document_id) is not None
        job = session.get(DeletionJob, uuid.UUID(body["job_id"]))
        assert job is not None
        assert job.user_id is None
        assert job.status == "completed"
        for table_name, column_name in seeded:
            table = Base.metadata.tables[table_name]
            count = session.scalar(
                select(func.count()).select_from(table).where(table.c[column_name] == alice_id)
            )
            assert count == 0, f"{table_name}.{column_name} still has alice's rows"
        assert session.get(DocumentPage, page_id) is None
        assert session.get(DocumentRegion, region_id) is None
        assert session.get(RegionPrediction, prediction.prediction_id) is None
        assert session.get(Image, image_id) is None

    assert store.exists(alice_image) is False
    assert store.exists(alice_crop) is False
    assert store.exists(alice_adapter) is False
    assert store.exists(alice_orphan) is False
    assert store.exists(bob_image) is True
    with session_factory() as session:
        session.execute(text("PRAGMA foreign_keys=OFF"))


def _user_scoped_tables(session: Session) -> list[tuple[str, str]]:
    """Every user/author column except the anonymized deletion receipt."""
    found: list[tuple[str, str]] = []
    for table in Base.metadata.tables.values():
        if table.name == "deletion_jobs":
            continue
        for column in table.columns:
            if column.name in {"user_id", "author_user_id"}:
                count = session.scalar(
                    select(func.count()).select_from(table).where(column.is_not(None))
                )
                assert count, f"security test did not seed {table.name}.{column.name}"
                found.append((table.name, column.name))
    return found


@pytest.mark.parametrize("failure", ["delete", "delete_owner"])
def test_failed_storage_purge_keeps_account_disabled_and_data_retryable(api_client, monkeypatch, failure) -> None:
    client, session_factory, sessions, store = api_client
    with session_factory() as session:
        user = User(username="purge-failure", email="purge@example.com", password_hash=hash_password("password-123"))
        session.add(user)
        session.flush()
        user_id = user.user_id
        ref = store.put_image_bytes(str(user_id), b"retained", suffix=".bin")
        session.add(Image(user_id=user_id, storage_path=ref, format=ImageFormat.PNG, size_bytes=8))
        session.commit()
    token = sessions.create(user_id)

    def unavailable(*args):
        raise OSError("storage unavailable")

    monkeypatch.setattr(store, failure, unavailable)
    response = client.request("DELETE", "/account", json={"confirmation": "DELETE"}, headers=_auth(token))
    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert client.get("/auth/me", headers=_auth(token)).status_code == 401
    assert client.post("/auth/login", json={"username": "purge-failure", "password": "password-123"}).status_code == 401
    with session_factory() as session:
        assert session.get(User, user_id).disabled_at is not None
        assert session.scalar(select(Image).where(Image.user_id == user_id)) is not None
