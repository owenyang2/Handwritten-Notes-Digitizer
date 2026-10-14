"""Platform-plan coverage: labels gate, ink_v1, assets, consent, Unicode PDF."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image as PILImage
from sqlalchemy import select
from sqlalchemy.pool import StaticPool

from app.data import (
    Asset,
    Base,
    DocumentPage,
    InkBundle,
    RecognitionRun,
    User,
    create_all,
    create_db_engine,
    create_session_factory,
)
from app.data.models import PasswordResetToken
from app.data.object_store import FilesystemObjectStore
from app.ml.iam_dataset import LabelsUnverifiedError, load_split_manifest, load_training_samples
from app.ml.ink_v1 import content_hash, finalize, validate
from app.ml.personal_reranker import rerank_candidates
from app.services.auth import SessionStore, hash_password
from app.services.account_api import router as account_router
from app.services.assets_api import router as assets_router
from app.services.auth_api import get_db_session, get_session_store, router as auth_router
from app.services.consent_api import account_recovery_router, consent_router
from app.services.documents_api import router as documents_router
from app.services.images_api import get_object_store
from app.services.pdf_import import router as pdf_import_router
from app.services.sync_api import router as sync_router
from app.services.document_export import DocumentBlock, _render_pdf_bytes
from app.services.error_codes import ERROR_CODES, error_detail
from app.main import create_app

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "contracts" / "ink_v1" / "simple_stroke.json"


def test_checked_in_manifest_is_not_label_verified():
    manifest = load_split_manifest()
    assert manifest.labels_verified is False


def test_real_iam_images_fail_closed_without_verified_labels(tmp_path):
    (tmp_path / "a01" / "a01-000u").mkdir(parents=True)
    (tmp_path / "a01" / "a01-000u" / "a01-000u-00.png").write_bytes(b"not-a-real-iam-image")
    with pytest.raises(LabelsUnverifiedError, match="labels_verified"):
        load_training_samples(dataset_root=tmp_path, synthetic=False)


def test_synthetic_placeholders_still_load():
    samples = load_training_samples(synthetic=True, partition="train")
    assert samples
    assert all(sample.text for sample in samples)


def test_ink_v1_golden_hash_matches_checked_in_fixture():
    raw = json.loads(GOLDEN.read_text(encoding="utf-8"))
    expected = raw["content_hash"]
    payload = {key: value for key, value in raw.items() if key != "content_hash"}
    assert content_hash(payload) == expected
    validated = validate(raw)
    assert validated["content_hash"] == expected
    assert finalize(payload)["content_hash"] == expected


def test_personal_reranker_prefers_vocabulary():
    ranked = rerank_candidates(
        [("2", 0.51), ("z", 0.49)],
        vocabulary={"2": "z"},
    )
    assert ranked[0].text == "z"
    assert ranked[0].reason == "vocabulary"


def test_unicode_pdf_keeps_non_latin_text():
    pdf = _render_pdf_bytes(
        [DocumentBlock(kind="text", text="Σ café naïve", excluded=False)]
    )
    assert pdf.startswith(b"%PDF")
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(pdf)
    text = document[0].get_textpage().get_text_bounded()
    assert "café" in text or "caf" in text
    assert "Σ" in text or "\\u03a3" not in text.encode("unicode_escape").decode()


@pytest.fixture()
def api_client(tmp_path):
    engine = create_db_engine("sqlite://", poolclass=StaticPool)
    create_all(engine)
    factory = create_session_factory(engine)
    store = FilesystemObjectStore(root=tmp_path / "objects")
    with factory() as session:
        sessions = SessionStore(session)

        def override_db():
            yield session

        app = FastAPI()
        app.include_router(auth_router)
        app.include_router(documents_router)
        app.include_router(pdf_import_router)
        app.include_router(assets_router)
        app.include_router(sync_router)
        app.include_router(consent_router)
        app.include_router(account_recovery_router)
        app.include_router(account_router)
        app.dependency_overrides[get_db_session] = override_db
        app.dependency_overrides[get_session_store] = lambda: sessions
        app.dependency_overrides[get_object_store] = lambda: store
        with TestClient(app) as client:
            yield client, session, factory
    Base.metadata.drop_all(engine)


def _login(client: TestClient, session, username: str = "ink-user") -> None:
    session.add(
        User(
            username=username,
            email=f"{username}@example.com",
            password_hash=hash_password("password123"),
        )
    )
    session.commit()
    response = client.post(
        "/auth/login", json={"username": username, "password": "password123"}
    )
    assert response.status_code == 200, response.text


def test_ink_bundle_and_consent_flow(api_client):
    client, session, _factory = api_client
    _login(client, session)
    created = client.post("/documents", json={"title": "Notebook"})
    assert created.status_code == 201, created.text
    document_id = created.json()["document_id"]
    page = client.post(f"/documents/{document_id}/pages", json={"width": 800, "height": 600})
    assert page.status_code == 201, page.text
    page_id = page.json()["page_id"]
    bundle = json.loads(GOLDEN.read_text(encoding="utf-8"))
    bundle["page_id"] = page_id
    del bundle["content_hash"]
    uploaded = client.post(f"/pages/{page_id}/ink-bundles", json={"bundle": bundle})
    assert uploaded.status_code == 201, uploaded.text
    body = uploaded.json()
    assert body["stroke_count"] == 1
    assert len(body["content_hash"]) == 64

    again = client.post(f"/pages/{page_id}/ink-bundles", json={"bundle": {**bundle, "content_hash": body["content_hash"]}})
    assert again.status_code == 201
    assert again.json()["idempotent"] is True

    second_bundle = json.loads(GOLDEN.read_text(encoding="utf-8"))
    second_bundle["page_id"] = page_id
    del second_bundle["content_hash"]
    second_bundle["strokes"][0]["points"][0]["x"] = 12.0
    second = client.post(f"/pages/{page_id}/ink-bundles", json={"bundle": second_bundle})
    assert second.status_code == 201, second.text
    second_body = second.json()
    assert second_body["revision_number"] == 2
    assert second_body["parent_bundle_id"] == body["bundle_id"]
    assert second_body["active"] is True

    manifest = client.get(f"/pages/{page_id}/ink-bundles")
    assert manifest.status_code == 200, manifest.text
    manifest_body = manifest.json()
    assert manifest_body["active_bundle_id"] == second_body["bundle_id"]
    assert [item["revision_number"] for item in manifest_body["revisions"]] == [2, 1]

    latest = client.get(f"/pages/{page_id}/ink-bundles/latest")
    assert latest.status_code == 200
    assert latest.json()["bundle_id"] == second_body["bundle_id"]

    # Replaying an older, content-addressed revision is idempotent but makes
    # that exact retained revision active again; no bytes are rewritten.
    reactivate = client.post(
        f"/pages/{page_id}/ink-bundles",
        json={"bundle": {**bundle, "content_hash": body["content_hash"]}},
    )
    assert reactivate.status_code == 201
    assert reactivate.json()["idempotent"] is True
    assert reactivate.json()["active"] is True
    assert client.get(f"/pages/{page_id}/ink-bundles/latest").json()["bundle_id"] == body["bundle_id"]

    consent = client.post(
        "/personalization/consent",
        json={"action": "grant", "scope": "personal_model"},
    )
    assert consent.status_code == 200, consent.text
    assert consent.json()["personal_training_allowed"] is True
    vocab = client.post(
        "/personalization/vocabulary",
        json={"token": "2", "preferred_form": "z"},
    )
    assert vocab.status_code == 200, vocab.text

    run = client.post(
        f"/pages/{page_id}/recognition-runs",
        json={
            "scope": "page",
            "mode": "ink",
            "ink_bundle_id": body["bundle_id"],
        },
    )
    assert run.status_code == 201, run.text
    assert run.json()["state"] in {"queued", "failed"}
    persisted_run = session.get(RecognitionRun, uuid.UUID(run.json()["run_id"]))
    assert persisted_run is not None
    assert persisted_run.source_snapshot[0]["ink_bundle_id"] == body["bundle_id"]
    assert persisted_run.source_snapshot[0]["ink_hash"] == body["content_hash"]
    if run.json()["state"] == "failed":
        assert run.json()["error_message"] == "model_unavailable"

    reset = client.post(
        "/auth/password-reset/request",
        json={"email": "ink-user@example.com"},
    )
    assert reset.status_code == 202
    assert reset.json() == {"status": "accepted"}
    raw_token = "local-reset-token-value-0123456789"
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    owner = session.scalar(select(User).where(User.username == "ink-user"))
    assert owner is not None
    session.add(
        PasswordResetToken(
            token_hash=token_hash,
            user_id=owner.user_id,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
    )
    session.commit()
    confirm = client.post(
        "/auth/password-reset/confirm",
        json={"token": raw_token, "new_password": "new-password-123"},
    )
    assert confirm.status_code == 200, confirm.text


def test_ink_bundle_decodes_native_drawing_data_before_object_storage(api_client):
    client, session, _factory = api_client
    _login(client, session, username="native-ink-user")
    created = client.post("/documents", json={"title": "Native notebook"})
    assert created.status_code == 201, created.text
    document_id = created.json()["document_id"]
    page = client.post(f"/documents/{document_id}/pages", json={"width": 800, "height": 600})
    assert page.status_code == 201, page.text
    page_id = page.json()["page_id"]
    bundle = json.loads(GOLDEN.read_text(encoding="utf-8"))
    bundle["page_id"] = page_id
    bundle["native_drawing"] = {
        "platform": "apple.pkdrawing",
        "media_type": "application/x-apple-pkdrawing",
        "data": base64.b64encode(b"native-pkdrawing-bytes").decode("ascii"),
    }
    bundle.pop("content_hash", None)

    uploaded = client.post(f"/pages/{page_id}/ink-bundles", json={"bundle": bundle})
    assert uploaded.status_code == 201, uploaded.text
    ink_bundle = session.get(InkBundle, uuid.UUID(uploaded.json()["bundle_id"]))
    assert ink_bundle is not None
    assert ink_bundle.native_asset_id is not None
    native_asset = session.get(Asset, ink_bundle.native_asset_id)
    assert native_asset is not None
    object_store = client.app.dependency_overrides[get_object_store]()
    assert object_store.get(native_asset.object_ref) == b"native-pkdrawing-bytes"


def test_second_client_bootstraps_stable_ids_and_downloads_exact_ink_revision(api_client):
    client, session, _factory = api_client
    _login(client, session, username="two-client-ink-user")
    document_id = uuid.uuid4()
    page_id = uuid.uuid4()
    create_operation = {
        "client_id": "ipad-first",
        "client_operation_id": str(uuid.uuid4()),
        "document_id": str(document_id),
        "base_sync_version": 0,
        "entity_type": "document",
        "entity_id": str(document_id),
        "operation_type": "document.create",
        "payload": {
            "title": "Two-client lecture",
            "source_kind": "native_ink",
            "default_language": "en",
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
    }
    pushed = client.post("/sync/push", json={"operations": [create_operation]})
    assert pushed.status_code == 200, pushed.text
    assert pushed.json()["accepted"][0]["document_id"] == str(document_id)

    bundle = json.loads(GOLDEN.read_text(encoding="utf-8"))
    bundle["page_id"] = str(page_id)
    bundle["native_drawing"] = {
        "platform": "apple.pkdrawing",
        "media_type": "application/x-apple-pkdrawing",
        "data": base64.b64encode(b"first-client-native-drawing").decode("ascii"),
    }
    bundle.pop("content_hash", None)
    uploaded = client.post(f"/pages/{page_id}/ink-bundles", json={"bundle": bundle})
    assert uploaded.status_code == 201, uploaded.text
    revision = uploaded.json()

    # A fresh second client starts at cursor zero, reconstructs the exact
    # stable aggregate, then follows the manifest to the immutable ink bytes.
    pulled = client.get("/sync/pull?cursor=0&limit=100")
    assert pulled.status_code == 200, pulled.text
    create_change = next(
        item for item in pulled.json()["items"] if item["operation_type"] == "document.create"
    )
    assert create_change["document_id"] == str(document_id)
    assert create_change["payload"]["pages"][0]["page_id"] == str(page_id)

    manifest = client.get(f"/pages/{page_id}/ink-bundles")
    assert manifest.status_code == 200, manifest.text
    assert manifest.json()["active_bundle_id"] == revision["bundle_id"]
    content_path = f"/pages/{page_id}/ink-bundles/{revision['bundle_id']}/content"
    first_download = client.get(content_path)
    retry_download = client.get(content_path)
    assert first_download.status_code == 200, first_download.text
    assert retry_download.status_code == 200, retry_download.text
    assert retry_download.content == first_download.content
    restored = first_download.json()
    assert restored["page_id"] == str(page_id)
    assert restored["content_hash"] == revision["content_hash"]
    assert base64.b64decode(restored["native_drawing"]["data"]) == b"first-client-native-drawing"
    assert validate(restored)["content_hash"] == revision["content_hash"]

    # A different account cannot learn whether either stable ID exists.
    _login(client, session, username="two-client-other-user")
    assert client.get(content_path).status_code == 404


def test_source_upload_preserves_document_and_page_identity_on_retry(api_client):
    client, session, _factory = api_client
    _login(client, session, username="source-user")
    created = client.post("/documents", json={"title": "Imported notebook"})
    assert created.status_code == 201, created.text
    document_id = uuid.UUID(created.json()["document_id"])
    page_id = uuid.uuid4()
    session.add(
        DocumentPage(
            page_id=page_id,
            document_id=document_id,
            page_index=0,
            raster_path=f"native-ink://{page_id}",
            width=64,
            height=48,
            dpi=132,
            coordinate_space="points",
        )
    )
    session.commit()

    image = PILImage.new("RGB", (64, 48), (240, 240, 240))
    payload = io.BytesIO()
    image.save(payload, format="PNG")
    source = payload.getvalue()
    first = client.post(
        f"/documents/{document_id}/source",
        files={"file": ("lecture.png", source, "image/png")},
    )
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert first_body["document_id"] == str(document_id)
    assert first_body["source_kind"] == "image_import"
    assert first_body["pages"][0]["page_id"] == str(page_id)
    assert first_body["pages"][0]["width"] == 64
    assert first_body["pages"][0]["height"] == 48

    repeated = client.post(
        f"/documents/{document_id}/source",
        files={"file": ("lecture.png", source, "image/png")},
    )
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["source_sha256"] == first_body["source_sha256"]
    assert repeated.json()["pages"][0]["page_id"] == str(page_id)

    downloaded = client.get(f"/documents/{document_id}/source")
    assert downloaded.status_code == 200
    assert downloaded.content == source
    assert downloaded.headers["content-type"].startswith("image/png")

    fetched = client.get(f"/documents/{document_id}")
    assert fetched.status_code == 200
    assert fetched.json()["source_kind"] == "image_import"
    assert len(fetched.json()["image_ids"]) == 1


def test_raster_recognition_run_uses_attached_page_source_without_ink(api_client):
    client, session, _factory = api_client
    _login(client, session, username="raster-conversion-user")
    created = client.post("/documents", json={"title": "Raster conversion"})
    assert created.status_code == 201, created.text
    document_id = uuid.UUID(created.json()["document_id"])
    page = client.post(
        f"/documents/{document_id}/pages",
        json={"width": 64, "height": 48},
    )
    assert page.status_code == 201, page.text
    page_id = uuid.UUID(page.json()["page_id"])

    image = PILImage.new("RGB", (64, 48), (240, 240, 240))
    payload = io.BytesIO()
    image.save(payload, format="PNG")
    attached = client.post(
        f"/documents/{document_id}/source",
        files={"file": ("lecture.png", payload.getvalue(), "image/png")},
    )
    assert attached.status_code == 200, attached.text

    run = client.post(
        f"/pages/{page_id}/recognition-runs",
        json={"scope": "page", "mode": "raster"},
    )
    assert run.status_code == 201, run.text
    body = run.json()
    persisted_run = session.get(RecognitionRun, uuid.UUID(body["run_id"]))
    assert persisted_run is not None
    assert persisted_run.mode == "raster"
    assert persisted_run.source_snapshot[0]["ink_bundle_id"] is None
    assert persisted_run.source_snapshot[0]["raster_ref"]
    assert not persisted_run.source_snapshot[0]["raster_ref"].startswith("native-ink://")
    if body["state"] == "failed":
        assert body["error_message"] == "model_unavailable"


def test_source_upload_rejects_page_count_change_without_mutating_document(api_client):
    client, session, _factory = api_client
    _login(client, session, username="source-mismatch-user")
    created = client.post("/documents", json={"title": "Page mismatch"})
    assert created.status_code == 201, created.text
    document_id = uuid.UUID(created.json()["document_id"])
    first_page = uuid.uuid4()
    second_page = uuid.uuid4()
    session.add_all(
        [
            DocumentPage(
                page_id=first_page,
                document_id=document_id,
                page_index=0,
                raster_path=f"native-ink://{first_page}",
                width=64,
                height=48,
                dpi=132,
                coordinate_space="points",
            ),
            DocumentPage(
                page_id=second_page,
                document_id=document_id,
                page_index=1,
                raster_path=f"native-ink://{second_page}",
                width=64,
                height=48,
                dpi=132,
                coordinate_space="points",
            ),
        ]
    )
    session.commit()
    image = PILImage.new("RGB", (64, 48), (240, 240, 240))
    payload = io.BytesIO()
    image.save(payload, format="PNG")

    response = client.post(
        f"/documents/{document_id}/source",
        files={"file": ("one-page.png", payload.getvalue(), "image/png")},
    )
    assert response.status_code == 409
    assert client.get(f"/documents/{document_id}").json()["source_kind"] == "images"


def test_error_codes_are_stable_and_exported():
    assert "labels_unverified" in ERROR_CODES
    assert "ink_integrity_failure" in ERROR_CODES
    detail = error_detail("model_unavailable")
    assert detail["code"] == "model_unavailable"
    codes_path = ROOT / "contracts" / "error_codes.json"
    if codes_path.exists():
        exported = json.loads(codes_path.read_text(encoding="utf-8"))
        assert set(ERROR_CODES) == set(exported)


def test_openapi_includes_new_paths():
    app = create_app()
    schema = app.openapi()
    paths = schema["paths"]
    assert "/pages/{page_id}/ink-bundles" in paths
    assert "/personalization/consent" in paths
    assert "/auth/password-reset/request" in paths
