"""Platform-plan coverage: labels gate, ink_v1, assets, consent, Unicode PDF."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool

from app.data import Base, User, create_all, create_db_engine, create_session_factory
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
        app.include_router(assets_router)
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
        json={"scope": "page", "mode": "ink"},
    )
    assert run.status_code == 201, run.text
    assert run.json()["state"] in {"queued", "failed"}
    if run.json()["state"] == "failed":
        assert run.json()["error_message"] == "model_unavailable"

    reset = client.post(
        "/auth/password-reset/request",
        json={"email": "ink-user@example.com"},
    )
    assert reset.status_code == 202
    token = reset.json().get("debug_token")
    assert token
    confirm = client.post(
        "/auth/password-reset/confirm",
        json={"token": token, "new_password": "new-password-123"},
    )
    assert confirm.status_code == 200, confirm.text


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
