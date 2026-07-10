"""FastAPI application entry point for the Handwriting Notes Digitizer backend.

This module wires up the application instance and applies deterministic runtime
settings at startup (Req 11.1, 11.3). Capability routers (recognition,
correction, calibration, personalization, evaluation, export) are registered in
later tasks.

Middleware registered here (outermost first):
* RateLimitMiddleware — token-bucket rate limiting on auth/upload/recognize.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import configure_determinism, get_settings
from app.middleware.rate_limiter import RateLimitMiddleware
from app.services.auth_api import router as auth_router
from app.services.documents_api import router as documents_router
from app.services.calibration_service import router as calibration_router
from app.services.correction_service import router as correction_router
from app.services.export_service import router as export_router
from app.services.images_api import router as images_router
from app.services.isolation import register_isolation_handlers
from app.services.recognition_service import router as recognition_router
from app.services.personalization_api import router as personalization_router
from app.services.capability_registry import get_component_availability, get_capability_availability


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

    if get_component_availability().get("training_worker", True):
        from app.services.images_api import get_object_store
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
        - ``model_status``: whether the base model is trained or randomly
          initialized (untrained fallback). Clients should warn the user when
          ``model_status`` is ``"untrained"`` since recognition output will be
          nonsensical until a real trained artifact is present.
        """
        model_status = _get_model_status()
        return {
            "status": "ok",
            "capabilities": get_capability_availability(),
            "model_status": model_status,
        }

    # Auth + Session layer: /auth/login and the auth gate used by protected
    # capability routers (Req 10.1, 10.4).
    app.include_router(auth_router)

    # Image upload: POST /images (Req 1.1, 1.6, 10.4).
    app.include_router(images_router)

    # Documents management: /documents (Req 6.1–6.5).
    app.include_router(documents_router)

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

    # Cross-user isolation: map framework-agnostic CrossUserAccessError to a 404
    # (not 403) so another user's resource existence is never disclosed
    # (Req 10.5). Route handlers may also raise isolation.ResourceNotFound
    # directly, which FastAPI already renders as a 404.
    register_isolation_handlers(app)

    # Outermost middleware runs last-registered first in Starlette's chain,
    # so rate limiting is applied before any route handler or dependency.
    app.add_middleware(RateLimitMiddleware)

    return app


def _get_model_status() -> str:
    """Return 'trained' if a real base-model artifact is present, else 'untrained'.

    Checks whether the model weights file exists on disk. The in-memory cache
    cannot reliably tell trained vs. random-fallback state, so we check the
    file directly. This is an O(1) filesystem stat on every health call —
    acceptable because health is polled infrequently and the file rarely moves.
    """
    try:
        from app.ml.train_base import default_model_dir

        weights_path = default_model_dir() / "base_model.pt"
        return "trained" if weights_path.exists() else "untrained"
    except Exception:
        return "unknown"


app = create_app()
