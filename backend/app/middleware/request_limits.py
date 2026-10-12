"""Application-side request-size guard for upload endpoints and proxies.

The reverse proxy rejects bodies above its configured envelope, while route
handlers enforce the decoded file/part limit. This middleware closes the gap
for clients that bypass the proxy and for requests that declare an oversized
body before Starlette parses a multipart form. Chunked bodies still go through
the bounded readers owned by the upload routes.
"""

from __future__ import annotations

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.config.settings import get_settings

# Multipart framing and headers are allowed above the decoded upload limit.
# nginx.conf mirrors the default 10 MiB payload + 1 MiB envelope.
REQUEST_ENVELOPE_OVERHEAD_BYTES = 1024 * 1024


def _declared_content_length(request: Request) -> int | None:
    value = request.headers.get("content-length")
    if value is None:
        return None
    try:
        length = int(value)
    except (TypeError, ValueError):
        return None
    return length if length >= 0 else None


def max_request_body_bytes() -> int:
    """Return the application envelope matching the reverse-proxy budget."""
    return get_settings().max_upload_bytes + REQUEST_ENVELOPE_OVERHEAD_BYTES


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject declared request bodies above the configured application envelope."""

    async def dispatch(self, request: Request, call_next) -> Response:
        declared = _declared_content_length(request)
        limit = max_request_body_bytes()
        if declared is not None and declared > limit:
            return JSONResponse(
                status_code=413,
                content={
                    "detail": {
                        "code": "upload_too_large",
                        "message": f"Request body exceeds the {limit} byte limit.",
                    }
                },
            )
        return await call_next(request)


__all__ = [
    "REQUEST_ENVELOPE_OVERHEAD_BYTES",
    "RequestSizeLimitMiddleware",
    "max_request_body_bytes",
]
