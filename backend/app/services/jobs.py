"""Durable recognition-job API and a bounded worker.

FastAPI request sessions never cross the worker boundary.  A job stores its
inputs and lifecycle in SQL, while the worker opens a fresh session for every
execution.  The worker is deliberately single-concurrency by default so a
single GPU cannot be oversubscribed; the database state remains useful after a
process restart and allows stale work to be recovered.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config.settings import get_settings
from app.data import RecognitionJobState
from app.data.models import DocumentRegion, RecognitionJob, RecognitionRun, RegionPrediction, User
from app.data.object_store import ObjectStore
from app.data.repositories import RepositoryFactory
from app.services.auth_api import get_current_user, get_repositories
from app.services.images_api import get_object_store

logger = logging.getLogger(__name__)
from app.services.isolation import get_or_404
from app.services.layout import crop_png, detect_page_regions

router = APIRouter(tags=["jobs"])

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hnd-formula-job")
_enqueue_lock = threading.Lock()
_submitted: set[uuid.UUID] = set()


def _deadline_expired(deadline: dt.datetime | None) -> bool:
    """Compare SQLite's naive timestamps and PostgreSQL's aware timestamps safely."""
    if deadline is None:
        return False
    now = dt.datetime.now(dt.timezone.utc)
    if deadline.tzinfo is None:
        return now.replace(tzinfo=None) > deadline
    return now > deadline


class RecognitionJobRequest(BaseModel):
    region_ids: list[uuid.UUID] = Field(default_factory=list, max_length=256)
    auto_layout: bool = False
    mode: Literal["formula", "prose", "figure", "mixed"] = "formula"
    max_attempts: int = Field(default=2, ge=1, le=5)


class RecognitionJobResponse(BaseModel):
    job_id: uuid.UUID
    state: RecognitionJobState
    document_id: uuid.UUID | None = None
    page_id: uuid.UUID | None = None
    image_id: uuid.UUID | None = None
    region_ids: list[uuid.UUID] = Field(default_factory=list)
    progress_current: int = 0
    progress_total: int = 0
    attempt: int = 0
    max_attempts: int = 2
    error_message: str | None = None
    result_ids: list[uuid.UUID] = Field(default_factory=list)
    created_at: dt.datetime | None = None
    started_at: dt.datetime | None = None
    finished_at: dt.datetime | None = None


def _response(job: RecognitionJob) -> RecognitionJobResponse:
    def ids(values: object) -> list[uuid.UUID]:
        result: list[uuid.UUID] = []
        for value in values if isinstance(values, list) else []:
            try:
                result.append(value if isinstance(value, uuid.UUID) else uuid.UUID(str(value)))
            except (TypeError, ValueError):
                continue
        return result

    return RecognitionJobResponse(
        job_id=job.job_id,
        state=job.state,
        document_id=job.document_id,
        page_id=job.page_id,
        image_id=job.image_id,
        region_ids=ids(job.region_ids),
        progress_current=job.progress_current,
        progress_total=job.progress_total,
        attempt=job.attempt,
        max_attempts=job.max_attempts,
        error_message=job.error_message,
        result_ids=ids(job.result_ids),
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


def _session_factory_for(session: Session) -> sessionmaker[Session]:
    """Make a worker factory bound to the same engine, never the request session."""
    return sessionmaker(bind=session.get_bind(), autoflush=False, expire_on_commit=False)


def _create_auto_regions(
    *,
    user_id: uuid.UUID,
    page,
    repos: RepositoryFactory,
    store: ObjectStore,
    content_type: str = "math_display",
) -> list[DocumentRegion]:
    from app.services.page_raster import page_raster
    page_bytes, _ = page_raster(page, user_id, repos, store)
    boxes = detect_page_regions(page_bytes)
    existing_by_key: dict[tuple[int, int, int, int, str], list[DocumentRegion]] = {}
    for region in repos.document_regions.list_for_page(user_id, page.page_id):
        key = (
            region.bbox_x,
            region.bbox_y,
            region.bbox_width,
            region.bbox_height,
            region.content_type,
        )
        existing_by_key.setdefault(key, []).append(region)
    created: list[DocumentRegion] = []
    for index, box in enumerate(boxes):
        key = (box.x, box.y, box.width, box.height, content_type)
        existing = existing_by_key.get(key)
        if existing:
            # Automatic layout is safe to retry: a second request reuses the
            # same stable region instead of appending duplicate active blocks.
            region = existing.pop(0)
            region.order_index = index
            created.append(region)
            continue
        crop = crop_png(page_bytes, box.as_list)
        region = DocumentRegion(
            page_id=page.page_id,
            order_index=index,
            content_type=content_type,
            bbox_x=box.x,
            bbox_y=box.y,
            bbox_width=box.width,
            bbox_height=box.height,
            coordinate_space=page.coordinate_space,
            source_crop_path=store.put_region_crop(str(user_id), crop, suffix=".png"),
            crop_sha256=hashlib.sha256(crop).hexdigest(),
        )
        repos.document_regions.add(region)
        created.append(region)
    return created


def _enqueue(job_id: uuid.UUID, session_factory: sessionmaker[Session], store: ObjectStore) -> None:
    with _enqueue_lock:
        if job_id in _submitted:
            return
        _submitted.add(job_id)
    _executor.submit(_run_job, job_id, session_factory, store)


def _finish(
    session: Session,
    job: RecognitionJob,
    state: RecognitionJobState,
    *,
    error: str | None = None,
) -> None:
    job.state = state
    job.error_message = error
    job.finished_at = dt.datetime.now(dt.timezone.utc)
    job.updated_at = job.finished_at
    _sync_run_envelopes(session, job)
    session.commit()


def _sync_run_envelopes(session: Session, job: RecognitionJob) -> None:
    for run in session.scalars(select(RecognitionRun).where(RecognitionRun.job_id == job.job_id, RecognitionRun.user_id == job.user_id)):
        run.state = {"succeeded": "complete", "no_text": "complete", "timeout": "failed"}.get(job.state.value, job.state.value)
        run.result_ids = list(job.result_ids or [])
        run.error_message = job.error_message
        run.finished_at = job.finished_at
        run.updated_at = job.updated_at


def _queue_retry(
    session: Session,
    job: RecognitionJob,
    error: str,
    *,
    terminal_state: RecognitionJobState = RecognitionJobState.FAILED,
) -> bool:
    """Persist one bounded retry, returning whether the caller should resubmit."""
    if job.attempt < job.max_attempts:
        job.state = RecognitionJobState.QUEUED
        job.error_message = error
        job.deadline_at = None
        job.finished_at = None
        job.updated_at = dt.datetime.now(dt.timezone.utc)
        _sync_run_envelopes(session, job)
        session.commit()
        return True
    _finish(session, job, terminal_state, error=error)
    return False


def _run_job(job_id: uuid.UUID, session_factory: sessionmaker[Session], store: ObjectStore) -> None:
    """Run one job using an independent SQLAlchemy session."""
    retry_requested = False
    try:
        with session_factory() as session:
            repos = RepositoryFactory(session)
            job = session.get(RecognitionJob, job_id)
            if job is None or job.state not in (RecognitionJobState.QUEUED,):
                return
            # Loading a bounded model worker is setup, not inference. Keep the
            # job queued while a cold process loads so restart recovery gets
            # the same inference budget as an already-warm request.
            mode = job.mode
            session.commit()
            _prepare_job_models(mode)
            job = session.get(RecognitionJob, job_id, populate_existing=True)
            if job is None or job.state != RecognitionJobState.QUEUED:
                return
            now = dt.datetime.now(dt.timezone.utc)
            job.state = RecognitionJobState.RUNNING
            job.attempt += 1
            job.started_at = job.started_at or now
            job.deadline_at = now + dt.timedelta(seconds=float(get_settings().recognition_timeout_seconds))
            job.error_message = None
            _sync_run_envelopes(session, job)
            session.commit()

            from app.services.math_regions_api import _crop_png, recognize_formula_crop
            from app.services.prose_engine import recognize_prose_crop
            from app.services.page_raster import page_raster
            run = session.scalars(select(RecognitionRun).where(RecognitionRun.job_id == job_id, RecognitionRun.user_id == job.user_id)).first()
            snapshot = run.source_snapshot[0] if run is not None and run.source_snapshot else None
            frozen_regions = {item["region_id"]: item for item in snapshot.get("regions", [])} if snapshot else {}

            region_ids = [uuid.UUID(str(value)) for value in (job.region_ids or [])]
            if not region_ids:
                _finish(session, job, RecognitionJobState.NO_TEXT, error="No regions were selected.")
                return
            result_ids: list[uuid.UUID] = []
            unavailable: list[str] = []
            recognized_any_text = False
            for index, region_id in enumerate(region_ids, start=1):
                session.refresh(job)
                if _deadline_expired(job.deadline_at):
                    retry_requested = _queue_retry(
                        session,
                        job,
                        "Recognition job exceeded its deadline.",
                        terminal_state=RecognitionJobState.TIMEOUT,
                    )
                    return
                region = repos.document_regions.get(job.user_id, region_id)
                if region is None:
                    _finish(session, job, RecognitionJobState.FAILED, error=f"Region {region_id} was not found.")
                    return
                page = repos.document_pages.get(job.user_id, region.page_id)
                if page is None:
                    _finish(session, job, RecognitionJobState.FAILED, error="The source page was not found.")
                    return
                try:
                    frozen = frozen_regions.get(str(region_id))
                    if frozen is not None:
                        if page.page_revision != snapshot["page_revision"] or region.page_revision != frozen["region_revision"] or region.crop_sha256 != frozen["original_crop_sha256"]:
                            _finish(session, job, RecognitionJobState.FAILED, error="Source changed. Convert the current revision again.")
                            return
                        crop = store.get(frozen["crop_ref"])
                        if hashlib.sha256(crop).hexdigest() != frozen["crop_sha256"]:
                            raise ValueError("Source crop failed its integrity check")
                    else:
                        page_bytes, _ = page_raster(page, job.user_id, repos, store)
                        crop = _crop_png(page_bytes, [region.bbox_x, region.bbox_y, region.bbox_width, region.bbox_height])
                    mode = "prose" if job.mode == "prose" or job.mode == "mixed" and region.content_type == "text" else "formula"
                    result = recognize_prose_crop(crop) if mode == "prose" else recognize_formula_crop(crop)
                    session.refresh(page)
                    session.refresh(region)
                    if frozen is not None and (page.page_revision != snapshot["page_revision"] or region.page_revision != frozen["region_revision"] or region.crop_sha256 != frozen["original_crop_sha256"]):
                        _finish(session, job, RecognitionJobState.FAILED, error="Source changed. Convert the current revision again.")
                        return
                    if _deadline_expired(job.deadline_at):
                        retry_requested = _queue_retry(session, job, "Recognition job exceeded its deadline.", terminal_state=RecognitionJobState.TIMEOUT)
                        return
                    from app.services.model_resolution import resolve_model_assignment, task_for_mode

                    resolved = resolve_model_assignment(
                        repos,
                        job.user_id,
                        task=task_for_mode(mode),
                        engine_id=result.engine_id,
                        engine_version=result.engine_version,
                        provenance=getattr(result, "provenance", None),
                    )
                except Exception as exc:
                    retry_requested = _queue_retry(
                        session,
                        job,
                        f"Region recognition failed: {exc}",
                    )
                    return
                candidate_dicts = [
                    {"text": c.text, "score": c.score, "truncated": c.truncated, "kind": c.kind}
                    for c in result.candidates
                ]
                prediction = RegionPrediction(
                    region_id=region.region_id,
                    model_assignment_id=resolved.assignment.assignment_id,
                    engine_id=result.engine_id,
                    engine_version=result.engine_version,
                    raw_output=result.text,
                    score=result.candidates[0].score if result.candidates else None,
                    truncated=result.truncated,
                    status="ok" if result.available else "unavailable",
                    latency_ms=result.latency_ms,
                    alternatives=candidate_dicts[1:] or None,
                    provenance={
                        **dict(getattr(result, "provenance", None) or {}),
                        "model_assignment_id": str(resolved.assignment.assignment_id),
                        "model_task": resolved.task,
                        "base_model_version": resolved.base_model_version,
                        "adapter_version": resolved.adapter_version,
                        "personalized": resolved.personalized,
                    },
                    settings=getattr(result, "settings", None),
                    source_asset_ref=frozen["crop_ref"] if frozen else region.source_crop_path,
                    source_crop_sha256=hashlib.sha256(crop).hexdigest(),
                    source_bbox=frozen["bbox"] if frozen else [region.bbox_x, region.bbox_y, region.bbox_width, region.bbox_height],
                    source_page_revision=page.page_revision,
                )
                repos.region_predictions.add(prediction)
                result_ids.append(prediction.prediction_id)
                if not result.available:
                    unavailable.append(result.reason or result.engine_id)
                elif result.text.strip():
                    recognized_any_text = True
                job.progress_current = index
                job.progress_total = len(region_ids)
                job.result_ids = [str(value) for value in result_ids]
                job.updated_at = dt.datetime.now(dt.timezone.utc)
                _sync_run_envelopes(session, job)
                session.commit()
            if unavailable and len(unavailable) == len(region_ids):
                _finish(session, job, RecognitionJobState.FAILED, error="; ".join(unavailable))
            elif unavailable:
                _finish(session, job, RecognitionJobState.FAILED, error="; ".join(unavailable))
            elif not recognized_any_text:
                _finish(
                    session,
                    job,
                    RecognitionJobState.NO_TEXT,
                    error="Recognition completed without any text.",
                )
            else:
                from app.services.document_review import create_document_revision

                document = (
                    repos.documents.get(job.user_id, job.document_id)
                    if job.document_id
                    else None
                )
                if document is not None:
                    create_document_revision(job.user_id, document, repos)
                _finish(session, job, RecognitionJobState.SUCCEEDED)
    except Exception:
        # A crash before a normal state transition must not leave an invisible
        # request.  A fresh session records the failure when possible.
        try:
            with session_factory() as session:
                job = session.get(RecognitionJob, job_id)
                if job is not None and job.state in (RecognitionJobState.QUEUED, RecognitionJobState.RUNNING):
                    if job.state == RecognitionJobState.QUEUED:
                        job.attempt += 1
                    retry_requested = _queue_retry(
                        session,
                        job,
                        "Recognition worker crashed.",
                    )
        except Exception:
            pass
    finally:
        with _enqueue_lock:
            _submitted.discard(job_id)
        if retry_requested:
            _enqueue(job_id, session_factory, store)


def recover_stale_jobs(session_factory: sessionmaker[Session]) -> int:
    """Recover expired jobs on startup or from an operator health task."""
    count = 0
    now = dt.datetime.now(dt.timezone.utc)
    with session_factory() as session:
        repos = RepositoryFactory(session)
        for job in repos.recognition_jobs.list_stale(now):
            count += 1
            if job.attempt < job.max_attempts:
                job.state = RecognitionJobState.QUEUED
                job.error_message = "Recovered after the previous worker deadline expired."
                job.deadline_at = None
                _sync_run_envelopes(session, job)
            else:
                _finish(session, job, RecognitionJobState.TIMEOUT, error="Recognition worker deadline expired.")
        session.commit()
    return count


def _prepare_job_models(mode: str) -> None:
    """Warm only the requested task; each runtime has a bounded startup probe."""
    if mode in ("formula", "mixed", "figure"):
        from app.services.math_regions_api import get_formula_engine_status
        get_formula_engine_status()
    if mode in ("prose", "mixed"):
        from app.services.prose_engine import get_prose_engine_status
        get_prose_engine_status()


def recover_orphaned_runs(session_factory: sessionmaker[Session]) -> int:
    """Old request-only envelopes cannot stay queued after installing a worker."""
    with session_factory() as session:
        runs = list(session.scalars(select(RecognitionRun).where(RecognitionRun.job_id.is_(None), RecognitionRun.state.in_(["queued", "running"]))))
        for run in runs:
            run.state = "failed"
            run.error_message = "Conversion was created before worker setup. Retry conversion."
            run.finished_at = dt.datetime.now(dt.timezone.utc)
        session.commit()
        return len(runs)


def resume_queued_jobs(session_factory: sessionmaker[Session], store: ObjectStore) -> int:
    """Submit persisted queued jobs after a process restart.

    The database is the source of truth for job inputs, so a restart should
    not require the browser to resubmit a page.  A snapshot is taken before
    enqueueing so worker submission never holds a database session open.
    """
    with session_factory() as session:
        queued_ids = [
            job.job_id
            for job in RepositoryFactory(session).recognition_jobs.list_for_states(
                (RecognitionJobState.QUEUED,)
            )
        ]
    for job_id in queued_ids:
        _enqueue(job_id, session_factory, store)
    return len(queued_ids)


@router.post(
    "/pages/{page_id}/recognition-jobs",
    response_model=RecognitionJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_page_recognition_job(
    page_id: uuid.UUID,
    body: RecognitionJobRequest,
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
    store: ObjectStore = Depends(get_object_store),
) -> RecognitionJobResponse:
    page = get_or_404(repos.document_pages.get(user.user_id, page_id))
    region_ids = list(dict.fromkeys(body.region_ids))
    if region_ids:
        page_regions = {region.region_id for region in repos.document_regions.list_for_page(user.user_id, page.page_id)}
        if not set(region_ids).issubset(page_regions):
            raise HTTPException(status_code=404, detail="One or more regions were not found.")
    elif body.auto_layout:
        try:
            region_ids = [region.region_id for region in _create_auto_regions(user_id=user.user_id, page=page, repos=repos, store=store, content_type="text" if body.mode == "prose" else "math_display")]
        except Exception as exc:
            repos.session.rollback()
            logger.exception("Automatic layout failed")
            raise HTTPException(status_code=422, detail="Automatic layout failed.") from exc
    else:
        raise HTTPException(status_code=422, detail="Provide region_ids or set auto_layout=true.")

    existing = repos.recognition_jobs.get_active_for_scope(
        user.user_id, page_id=page.page_id, region_ids=[str(value) for value in region_ids], mode=body.mode
    )
    if existing is not None:
        _enqueue(existing.job_id, _session_factory_for(repos.session), store)
        return _response(existing)

    job = RecognitionJob(
        user_id=user.user_id,
        document_id=page.document_id,
        page_id=page.page_id,
        image_id=page.image_id,
        mode=body.mode,
        region_ids=[str(value) for value in region_ids],
        progress_total=len(region_ids),
        max_attempts=body.max_attempts,
    )
    repos.recognition_jobs.add(job)
    repos.session.commit()
    _enqueue(job.job_id, _session_factory_for(repos.session), store)
    return _response(job)


@router.get("/jobs/{job_id}", response_model=RecognitionJobResponse)
def get_job(
    job_id: uuid.UUID,
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
) -> RecognitionJobResponse:
    return _response(get_or_404(repos.recognition_jobs.get(user.user_id, job_id)))


@router.post("/jobs/{job_id}/retry", response_model=RecognitionJobResponse, status_code=status.HTTP_202_ACCEPTED)
def retry_job(
    job_id: uuid.UUID,
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
    store: ObjectStore = Depends(get_object_store),
) -> RecognitionJobResponse:
    job = get_or_404(repos.recognition_jobs.get(user.user_id, job_id))
    if job.state in (RecognitionJobState.QUEUED, RecognitionJobState.RUNNING):
        _enqueue(job.job_id, _session_factory_for(repos.session), store)
        return _response(job)
    job.state = RecognitionJobState.QUEUED
    job.error_message = None
    job.finished_at = None
    repos.session.commit()
    _enqueue(job.job_id, _session_factory_for(repos.session), store)
    return _response(job)


__all__ = [
    "router",
    "recover_stale_jobs",
    "resume_queued_jobs",
    "RecognitionJobRequest",
    "RecognitionJobResponse",
]
