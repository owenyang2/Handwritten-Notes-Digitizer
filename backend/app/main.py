"""FastAPI application entry point for the Handwriting Notes Digitizer backend.

This module wires up the application instance and applies deterministic runtime
settings at startup (Req 11.1, 11.3). Capability routers (recognition,
correction, calibration, personalization, evaluation, export) are registered in
later tasks.

Middleware registered here (outermost first):
* MetricsMiddleware — Prometheus counters and JSON logs (production or HND_ENABLE_METRICS).
* RateLimitMiddleware — token-bucket rate limiting on auth/upload/recognize/sync.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import configure_determinism, get_settings
from app.middleware.rate_limiter import RateLimitMiddleware
from app.middleware.request_limits import RequestSizeLimitMiddleware
from app.observability import (
    MetricsMiddleware,
    configure_observability,
    metrics_enabled,
    metrics_endpoint,
)
from app.services.auth_api import router as auth_router
from app.services.documents_api import router as documents_router
from app.services.pdf_import import router as pdf_import_router
from app.services.document_review import router as document_review_router
from app.services.math_regions_api import router as math_regions_router
from app.services.document_export import router as document_export_router
from app.services.jobs import router as jobs_router, recover_stale_jobs, resume_queued_jobs
from app.services.math_regions_api import get_formula_engine_status
from app.services.calibration_service import router as calibration_router
from app.services.correction_service import router as correction_router
from app.services.export_service import router as export_router
from app.services.images_api import router as images_router
from app.services.isolation import register_isolation_handlers
from app.services.recognition_service import router as recognition_router
from app.services.personalization_api import router as personalization_router
from app.services.sync_api import router as sync_router
from app.services.uploads_api import router as uploads_router
from app.services.account_api import router as account_router
from app.services.assets_api import router as assets_router
from app.services.consent_api import (
    account_recovery_router,
    consent_router,
)
from app.ml.recognition import get_loaded_model_status
from app.services.prose_engine import get_prose_engine_status
from app.services.capability_registry import get_component_availability, get_capability_availability
from app.services.observability import RequestIdMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Apply deterministic settings and start background services on boot.

    Startup:
    * Fixed seeds / deterministic backends (Req 11.1, 11.3).
    * Upgrade the database schema before serving any route, including partial
      deployments that intentionally disable personalization training.
    * The personalization Async Training Worker (design's Component Map) is
      started here so QUEUED training runs — created by the
      Correction_Pipeline/Calibration_Service once a user crosses the
      unused-pair threshold (Req 5.1) — are actually picked up, trained, and
      finalised, rather than sitting QUEUED forever. Only started when the
      ``training_worker`` component is enabled (Req 11.5, 11.6 — partial
      deployment can disable it via ``HND_TRAINING_WORKER_AVAILABLE=false``).

    Shutdown: stop the worker thread cleanly.
    """
    settings = get_settings()
    configure_determinism(settings)
    from app.services.auth_api import _default_session_factory

    # Initializing the shared factory applies pending Alembic migrations. This
    # must not depend on the optional training worker being enabled.
    session_factory = _default_session_factory()

    # Recover jobs whose previous worker process stopped at a deadline before
    # serving requests, then resume persisted queued jobs.  The recognition
    # worker is independent of the optional personalization worker.
    # A real SQLAlchemy sessionmaker is callable.  Keeping the guard makes the
    # lifespan independently testable with a lightweight initialization stub
    # and avoids invoking worker recovery before the persistence layer exists.
    if callable(session_factory):
        from app.services.jobs import recover_orphaned_runs
        recover_orphaned_runs(session_factory)
        recover_stale_jobs(session_factory)
        from app.services.images_api import get_object_store
        resume_queued_jobs(session_factory, get_object_store())

    if get_component_availability().get("training_worker", True):
        from app.services.training_worker import start_background_worker, stop_background_worker

        start_background_worker(session_factory, get_object_store())
        try:
            yield
        finally:
            stop_background_worker()
    else:
        yield


def create_app() -> FastAPI:
    """Application factory so tests can build isolated app instances."""
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        lifespan=lifespan,
    )

    @app.get("/health", tags=["health"])
    def health() -> dict:
        """Capability-aware health probe (Req 11.5).

        Returns HTTP 200 with a JSON object containing:
        - ``status``: always ``"ok"`` (the endpoint itself is always up)
        - ``capabilities``: a dict mapping each capability name to a boolean
          indicating whether all its required components are currently available.
        - ``model_status``: ``loaded`` when a trained artifact passed its
          inference check, otherwise ``unavailable``. Recognition never
          silently falls back to random weights.
        """
        model_status = get_loaded_model_status()
        formula_status = get_formula_engine_status()
        capabilities = dict(get_capability_availability())
        if not model_status.loaded:
            capabilities["recognition"] = False
            capabilities["calibration"] = False
        return {
            "status": "ok",
            "capabilities": capabilities,
            "model_status": model_status.health_label,
            "model": {
                "backend": model_status.backend,
                "loaded": model_status.loaded,
                "artifact_path": model_status.artifact_path,
                "probe_ok": model_status.probe_ok,
                "reason": model_status.reason,
            },
            "formula_model": formula_status,
            "prose_model": get_prose_engine_status(),
        }

    # Auth + Session layer: /auth/login and the auth gate used by protected
    # capability routers (Req 10.1, 10.4).
    app.include_router(auth_router)

    # Image upload: POST /images (Req 1.1, 1.6, 10.4).
    app.include_router(images_router)

    # Documents management: /documents (Req 6.1–6.5).
    app.include_router(documents_router)
    app.include_router(pdf_import_router)
    app.include_router(document_review_router)
    app.include_router(math_regions_router)
    app.include_router(document_export_router)
    app.include_router(jobs_router)

    # Recognition: POST /images/{image_id}/recognize, GET /images/{image_id}/result
    # (Req 2.3–2.7, 3.1–3.4, 10.3).
    app.include_router(recognition_router)

    # Correction pipeline: POST /images/{image_id}/corrections (Req 4.3–4.8).
    app.include_router(correction_router)

    # Export: POST /export (Req 8.1–8.6).
    app.include_router(export_router)

    # Calibration (optional): POST /calibration/session (Req 9.1, 9.2).
    app.include_router(calibration_router)

    # Personalization status and evaluation:
    # GET /personalization/status (Req 5.6, 5.7)
    # GET /personalization/evaluation (Req 7.3)
    app.include_router(personalization_router)

    # Cross-device document operation log and conflict protocol.
    app.include_router(sync_router)

    # Resumable uploads and account session/export/deletion surfaces.
    app.include_router(uploads_router)
    app.include_router(account_router)
    app.include_router(assets_router)
    app.include_router(consent_router)
    app.include_router(account_recovery_router)

    # Cross-user isolation: map framework-agnostic CrossUserAccessError to a 404
    # (not 403) so another user's resource existence is never disclosed
    # (Req 10.5). Route handlers may also raise isolation.ResourceNotFound
    # directly, which FastAPI already renders as a 404.
    register_isolation_handlers(app)

    # Outermost middleware runs last-registered first in Starlette's chain,
    # so rate limiting is applied before any route handler or dependency.
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(RequestSizeLimitMiddleware)
    app.add_middleware(RequestIdMiddleware)

    if metrics_enabled(settings):
        configure_observability(settings)
        app.add_middleware(MetricsMiddleware)
        app.add_api_route(
            "/metrics",
            metrics_endpoint,
            methods=["GET"],
            include_in_schema=False,
            tags=["health"],
        )

    return app


app = create_app()
