"""Request-id and structured log helpers with note-content redaction."""

from __future__ import annotations

import logging
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

logger = logging.getLogger("hnd.http")

_REDACT_KEYS = frozenset(
    {
        "password",
        "token",
        "access_token",
        "refresh_token",
        "reviewed_text",
        "prediction_text",
        "latex",
        "text",
        "filename",
    }
)


def redact(payload: dict) -> dict:
    """Return a shallow copy with sensitive keys replaced."""
    return {
        key: "[redacted]" if key.lower() in _REDACT_KEYS else value
        for key, value in payload.items()
    }


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Attach ``X-Request-ID`` without logging note contents or tokens."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "request",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
            },
        )
        return response
