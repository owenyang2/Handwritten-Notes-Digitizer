"""Regressions for cross-client metadata and source replacement during conversion."""
import io
import uuid

from PIL import Image, ImageDraw
import pytest
from sqlalchemy import select

from app.data.models import DocumentPage, Image as StoredImage, RecognitionRun, User
from app.data.repositories import RepositoryFactory
from app.services import assets_api, jobs, pdf_import
from tests.test_conversion_runs import execute, setup
from tests.test_platform_plan_remaining import api_client, _login


def png(color="white", *, repeated_lines=False):
    image = Image.new("RGB", (120, 120), color)
    if repeated_lines:
        draw = ImageDraw.Draw(image)
        draw.rectangle((20, 20, 80, 32), fill="black")
        draw.rectangle((20, 75, 80, 87), fill="black")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def imported_page(api_client, monkeypatch):
    client, session, factory = api_client
    _login(client, session)
    user = session.scalars(select(User)).one()
    store = client.app.dependency_overrides[assets_api.get_object_store]()
    document = pdf_import.import_image_document(
        user_id=user.user_id, title="Repeated source", image_bytes=png(repeated_lines=True),
        repos=RepositoryFactory(session), store=store,
    )
    page = session.scalars(select(DocumentPage).where(DocumentPage.document_id == document.document_id)).one()
    monkeypatch.setattr(assets_api, "get_formula_engine_status", lambda: {"loaded": True})
    monkeypatch.setattr(jobs, "_enqueue", lambda *args: None)
    return client, session, factory, document, page, store


def attach(client, document_id, source):
    return client.post(f"/documents/{document_id}/source", files={"file": ("source.png", source, "image/png")})


def test_source_replacements_keep_current_rasters_and_idempotent_revision(api_client, monkeypatch):
    client, session, _, document, page, store = imported_page(api_client, monkeypatch)
    original_revision = page.page_revision
    old_path = page.raster_path
    for index, color in enumerate(("gray", "black"), 1):
        response = attach(client, document.document_id, png(color))
        assert response.status_code == 200, response.text
        session.refresh(page)
        assert page.page_revision == original_revision + index
        assert page.raster_path != old_path and store.exists(page.raster_path)
        assert store.exists(session.get(StoredImage, page.image_id).storage_path)
        assert not store.exists(old_path)
        old_path = page.raster_path
    response = attach(client, document.document_id, png("black"))
    assert response.status_code == 200
    session.refresh(page)
    assert page.page_revision == original_revision + 2 and page.raster_path == old_path


def test_failed_source_replacement_preserves_previous_bytes(api_client, monkeypatch):
    client, session, _, document, page, store = imported_page(api_client, monkeypatch)
    # Publish a source first, so the regression covers keys used by attachment.
    assert attach(client, document.document_id, png("gray")).status_code == 200
    old_path, old_bytes, old_revision = page.raster_path, store.get(page.raster_path), page.page_revision
    def fail_commit():
        raise RuntimeError("Injected transaction failure")
    monkeypatch.setattr(session, "commit", fail_commit)
    with pytest.raises(RuntimeError, match="Injected transaction failure"):
        attach(client, document.document_id, png("black"))
    session.refresh(page)
    assert page.raster_path == old_path and page.page_revision == old_revision
    assert store.get(old_path) == old_bytes


def test_identical_lines_keep_distinct_positions_and_reuse_on_retry(api_client, monkeypatch):
    client, session, _, _, page, _ = imported_page(api_client, monkeypatch)
    runs = []
    for _ in range(2):
        response = client.post(f"/pages/{page.page_id}/recognition-runs", json={"mode": "formula"})
        assert response.status_code == 201, response.text
        runs.append(session.get(RecognitionRun, uuid.UUID(response.json()["run_id"])))
    first, retry = [run.source_snapshot[0]["regions"] for run in runs]
    assert len(first) == 2 and len({region["region_id"] for region in first}) == 2
    assert first[0]["bbox"][1] != first[1]["bbox"][1]
    assert [region["region_id"] for region in retry] == [region["region_id"] for region in first]


def test_source_replacement_marks_completed_results_stale_and_rejects_queued_run(api_client, monkeypatch):
    client, session, factory, page_data, _ = setup(api_client, monkeypatch)
    page_id, document_id = page_data["page_id"], page_data["document_id"]
    first = client.post(f"/pages/{page_id}/recognition-runs", json={"mode": "formula"})
    complete, _ = execute(client, session, factory, first, monkeypatch)
    results = client.get(f"/recognition-runs/{complete.run_id}/results").json()
    assert results and not results[0]["source_stale"]
    review = client.post(f"/regions/{results[0]['region_id']}/review", json={
        "reviewed_text": "kept history", "prediction_id": results[0]["prediction_id"],
    })
    assert review.status_code == 200
    queued = client.post(f"/pages/{page_id}/recognition-runs", json={"mode": "formula"})
    # The replacement keeps the page size used by the overlay ink.
    replacement = io.BytesIO()
    Image.new("RGB", (64, 48), "gray").save(replacement, format="PNG")
    assert attach(client, document_id, replacement.getvalue()).status_code == 200
    stale = client.get(f"/recognition-runs/{complete.run_id}/results").json()
    assert stale[0]["source_stale"] and stale[0]["reviewed_text"] == "kept history"
    assert client.get(f"/recognition-runs/{complete.run_id}/source").status_code == 200
    rejected = client.post(f"/regions/{results[0]['region_id']}/review", json={
        "reviewed_text": "incorrect new review", "prediction_id": results[0]["prediction_id"],
    })
    assert rejected.status_code == 409
    queued_run, observed = execute(client, session, factory, queued, monkeypatch)
    assert not observed
    failed = client.get(f"/recognition-runs/{queued_run.run_id}").json()
    assert failed["state"] == "failed" and "Source changed" in failed["error_message"]
    fresh = client.post(f"/pages/{page_id}/recognition-runs", json={
        "mode": "formula", "scope": "region", "region_ids": [results[0]["region_id"]],
    })
    fresh_run, observed = execute(client, session, factory, fresh, monkeypatch)
    assert observed and client.get(f"/recognition-runs/{fresh_run.run_id}").json()["state"] == "complete"
    assert all(not result["source_stale"] for result in client.get(f"/recognition-runs/{fresh_run.run_id}/results").json())


def test_rest_metadata_trash_and_restore_publish_owner_scoped_sync_events(api_client):
    client, session, _ = api_client
    _login(client, session)
    document_id = client.post("/documents", json={"title": "Old"}).json()["document_id"]
    patch = client.patch(f"/documents/{document_id}", json={
        "title": "New", "archived": True, "is_favorite": True,
        "default_language": "fr", "course_context": "Physics", "expected_sync_version": 0,
    })
    assert patch.status_code == 200
    assert client.delete(f"/documents/{document_id}").status_code == 200
    assert client.delete(f"/documents/{document_id}").status_code == 200  # no duplicate tombstone
    assert client.post(f"/documents/{document_id}/restore").status_code == 200
    assert client.post(f"/documents/{document_id}/restore").status_code == 200
    items = client.get("/sync/pull").json()["items"]
    assert [item["operation_type"] for item in items] == ["document.metadata", "document.trash", "document.restore"]
    assert [item["sync_version"] for item in items] == [1, 2, 3]
    assert [item["base_sync_version"] for item in items] == [0, 1, 2]
    assert items[0]["payload"]["title"] == "New" and items[0]["payload"]["archived"]
    cursor = items[-1]["server_sequence"]
    # A client that pulled the REST changes can mutate with the resulting version.
    push = client.post("/sync/push", json={"operations": [{
        "document_id": document_id, "client_id": "ipad", "client_operation_id": str(uuid.uuid4()),
        "base_sync_version": 3, "entity_type": "document", "operation_type": "document.archive",
        "payload": {"archived": False},
    }]})
    assert push.status_code == 200 and len(push.json()["accepted"]) == 1
    assert len(client.get(f"/sync/pull?cursor={cursor}").json()["items"]) == 1
    _login(client, session, username="other-user")
    assert client.get("/sync/pull").json()["items"] == []
