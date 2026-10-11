"""Live worker boundaries for native ink, immutable sources and task selection."""
import io
import uuid
import datetime as dt

from PIL import Image
import pytest

from tests.test_platform_plan_remaining import api_client, _login
from app.data.models import RecognitionRun, DocumentPage, RegionPrediction
from app.ml.backends.base import EngineCandidate, EngineResult
from app.services import assets_api, jobs, math_regions_api, prose_engine
from app.services.document_review import router as review_router
from app.services.page_raster import rasterize_ink


def ink(page_id, y=20):
    return {"schema": "ink_v1", "schema_version": 1, "page_id": str(page_id),
            "coordinate_space": {"width": 64, "height": 48}, "capture": {},
            "strokes": [{"stroke_id": str(uuid.uuid4()), "points": [
                {"x": 8, "y": y, "time_offset": 0}, {"x": 56, "y": y, "time_offset": 1}],
                "width": 3}]}


def setup(api_client, monkeypatch):
    client, session, factory = api_client
    client.app.include_router(math_regions_api.router)
    client.app.include_router(review_router)
    _login(client, session)
    doc = client.post("/documents", json={"title": "Conversion"}).json()
    page = client.post(f"/documents/{doc['document_id']}/pages", json={"width": 64, "height": 48}).json()
    bundle = client.post(f"/pages/{page['page_id']}/ink-bundles", json={"bundle": ink(page['page_id'])})
    assert bundle.status_code == 201, bundle.text
    monkeypatch.setattr(assets_api, "get_formula_engine_status", lambda: {"loaded": True})
    monkeypatch.setattr(jobs, "_enqueue", lambda *args: None)
    return client, session, factory, page, bundle.json()


def execute(client, session, factory, response, monkeypatch):
    run = session.get(RecognitionRun, uuid.UUID(response.json()["run_id"]))
    observed = []
    def infer(crop):
        with Image.open(io.BytesIO(crop)) as image:
            assert image.width > 2 and image.height > 2
        observed.append(crop)
        return EngineResult(engine_id="deterministic-formula-fixture", engine_version="test", candidates=[EngineCandidate("x = 2", kind="latex")])
    monkeypatch.setattr(math_regions_api, "recognize_formula_crop", infer)
    store = client.app.dependency_overrides[assets_api.get_object_store]()
    jobs._run_job(run.job_id, factory, store)
    session.expire_all()
    return run, observed


def test_native_run_completes_with_immutable_source_and_saved_results(api_client, monkeypatch):
    client, session, factory, page, bundle = setup(api_client, monkeypatch)
    response = client.post(f"/pages/{page['page_id']}/recognition-runs", json={"mode": "formula", "ink_bundle_id": bundle["bundle_id"]})
    assert response.status_code == 201, response.text
    run, observed = execute(client, session, factory, response, monkeypatch)
    result = client.get(f"/recognition-runs/{run.run_id}").json()
    assert result["state"] == "complete" and len(observed) == 1
    assert result["progress_current"] == result["progress_total"] == 1
    details = client.get(f"/recognition-runs/{run.run_id}/results").json()
    assert details[0]["prediction_text"] == "x = 2" and not details[0]["source_stale"]
    source = client.get(f"/recognition-runs/{run.run_id}/source")
    assert source.headers["x-ink-hash"] == bundle["content_hash"]
    with Image.open(io.BytesIO(source.content)) as image:
        assert image.size == (64, 48) and image.getpixel((20, 20))[0] < 30
    review = client.post(f"/regions/{details[0]['region_id']}/review", json={"reviewed_text": "x = 3", "prediction_id": details[0]["prediction_id"]})
    assert review.status_code == 200, review.text
    assert client.get(f"/recognition-runs/{run.run_id}/results").json()[0]["reviewed_text"] == "x = 3"


def test_cold_model_setup_precedes_inference_deadline(api_client, monkeypatch):
    client, session, factory, page, _ = setup(api_client, monkeypatch)
    response = client.post(f"/pages/{page['page_id']}/recognition-runs", json={"mode": "formula"})
    warm_finished = []
    def warm(mode):
        assert mode == "formula"
        run = session.get(RecognitionRun, uuid.UUID(response.json()["run_id"]))
        from app.data.models import RecognitionJob
        pending = session.get(RecognitionJob, run.job_id)
        assert pending.state.value == "queued" and pending.deadline_at is None
        warm_finished.append(dt.datetime.now(dt.timezone.utc))
    monkeypatch.setattr(jobs, "_prepare_job_models", warm)
    run, _ = execute(client, session, factory, response, monkeypatch)
    from app.data.models import RecognitionJob
    job = session.get(RecognitionJob, run.job_id)
    assert job.state.value == "succeeded"
    started = job.started_at.replace(tzinfo=dt.timezone.utc)
    assert started >= warm_finished[0]


def test_restart_recovery_synchronizes_native_run(api_client, monkeypatch):
    client, session, factory, page, _ = setup(api_client, monkeypatch)
    response = client.post(f"/pages/{page['page_id']}/recognition-runs", json={"mode": "formula"})
    run = session.get(RecognitionRun, uuid.UUID(response.json()["run_id"]))
    from app.data.models import RecognitionJob
    from app.data.enums import RecognitionJobState
    job = session.get(RecognitionJob, run.job_id)
    job.state = RecognitionJobState.RUNNING
    job.attempt = 1
    job.deadline_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=1)
    run.state = "running"
    session.commit()
    assert jobs.recover_stale_jobs(factory) == 1
    session.expire_all()
    assert session.get(RecognitionRun, run.run_id).state == "queued"
    submitted = []
    monkeypatch.setattr(jobs, "_enqueue", lambda job_id, *args: submitted.append(job_id))
    assert jobs.resume_queued_jobs(factory, object()) == 1
    assert submitted == [run.job_id]
    execute(client, session, factory, response, monkeypatch)
    assert client.get(f"/recognition-runs/{run.run_id}").json()["state"] == "complete"


def test_changed_ink_fails_without_overwriting_results(api_client, monkeypatch):
    client, session, factory, page, _ = setup(api_client, monkeypatch)
    response = client.post(f"/pages/{page['page_id']}/recognition-runs", json={"mode": "formula"})
    client.post(f"/pages/{page['page_id']}/ink-bundles", json={"bundle": ink(page['page_id'], y=30)})
    run, observed = execute(client, session, factory, response, monkeypatch)
    result = client.get(f"/recognition-runs/{run.run_id}").json()
    assert result["state"] == "failed" and "Source changed" in result["error_message"]
    assert not observed and not result["result_ids"]


def test_run_retry_reuses_source_crops_and_idempotency(api_client, monkeypatch):
    client, session, factory, page, _ = setup(api_client, monkeypatch)
    key = str(uuid.uuid4())
    body = {"mode": "formula", "idempotency_key": key}
    first = client.post(f"/pages/{page['page_id']}/recognition-runs", json=body)
    second = client.post(f"/pages/{page['page_id']}/recognition-runs", json=body)
    assert second.json()["idempotent"] and first.json()["run_id"] == second.json()["run_id"]
    third = client.post(f"/pages/{page['page_id']}/recognition-runs", json={"mode": "formula"})
    a = session.get(RecognitionRun, uuid.UUID(first.json()["run_id"]))
    b = session.get(RecognitionRun, uuid.UUID(third.json()["run_id"]))
    assert a.source_snapshot[0]["region_ids"] == b.source_snapshot[0]["region_ids"]


def test_formula_model_cannot_enable_prose_task(api_client, monkeypatch):
    client, session, factory, page, _ = setup(api_client, monkeypatch)
    monkeypatch.setattr(prose_engine, "get_prose_engine_status", lambda: {"loaded": False})
    response = client.post(f"/pages/{page['page_id']}/recognition-runs", json={"mode": "prose"})
    assert response.json()["state"] == "failed" and response.json()["error_message"] == "model_unavailable"
    run = session.get(RecognitionRun, uuid.UUID(response.json()["run_id"]))
    assert run.job_id is None


def test_other_account_cannot_read_conversion_or_source(api_client, monkeypatch):
    client, session, factory, page, _ = setup(api_client, monkeypatch)
    response = client.post(f"/pages/{page['page_id']}/recognition-runs", json={"mode": "formula"})
    run_id = response.json()["run_id"]
    _login(client, session, username="other-conversion-user")
    for suffix in ("", "/source", "/results"):
        assert client.get(f"/recognition-runs/{run_id}{suffix}").status_code == 404
    assert client.get(f"/pages/{page['page_id']}/raster").status_code == 404


def test_rasterizer_rejects_unbounded_dimensions_and_nonfinite_points():
    payload = ink(uuid.uuid4())
    from app.ml.ink_v1 import finalize
    payload = finalize(payload)
    with pytest.raises(ValueError, match="pixel limit"):
        rasterize_ink(payload, width=100_000, height=100_000)
    payload["strokes"][0]["points"][0]["x"] = float("nan")
    with pytest.raises(ValueError, match="point"):
        rasterize_ink(payload, width=64, height=48)


def test_legacy_website_import_advertises_source_for_native_bootstrap(api_client):
    client, session, _ = api_client
    _login(client, session, username="legacy-import-user")
    from sqlalchemy import select
    from app.data.models import User
    user = session.scalars(select(User).where(User.username == "legacy-import-user")).one()
    from app.services.pdf_import import import_image_document
    from app.data.repositories import RepositoryFactory
    image = Image.new("RGB", (64, 48), "white")
    source = io.BytesIO()
    image.save(source, format="PNG")
    store = client.app.dependency_overrides[assets_api.get_object_store]()
    # The historical import path intentionally has no source metadata.
    document = import_image_document(user_id=user.user_id, title="Website import", image_bytes=source.getvalue(), repos=RepositoryFactory(session), store=store)
    assert document.source_metadata is None
    fetched = client.get(f"/documents/{document.document_id}")
    assert fetched.status_code == 200
    assert fetched.json()["source_metadata"]["source_attached"] is True
    assert fetched.json()["source_metadata"]["original_filename"].endswith(".png")
    downloaded = client.get(f"/documents/{document.document_id}/source")
    assert downloaded.status_code == 200 and downloaded.content == source.getvalue()
    assert downloaded.headers["content-type"] == "image/png"
