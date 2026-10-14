"""Application-side request-size guard for upload endpoints and proxies.

The reverse proxy rejects bodies above its configured envelope, while route
handlers enforce the decoded file/part limit. This middleware closes the gap
for clients that bypass the proxy: it rejects a declared Content-Length above
the envelope and also counts bytes of a chunked body before the route reads it.
"""

from __future__ import annotations

from fastapi.responses import JSONResponse
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


def _too_large(limit: int) -> JSONResponse:
    return JSONResponse(
        status_code=413,
        content={
            "detail": {
                "code": "upload_too_large",
                "message": f"Request body exceeds the {limit} byte limit.",
            }
        },
    )


class RequestSizeLimitMiddleware:
    """Reject request bodies above the configured application envelope.

    ``dispatch`` preserves the content-length behavior covered by unit tests.
    The ASGI entry point also counts a chunked body that omits Content-Length.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def dispatch(self, request: Request, call_next) -> Response:
        declared = _declared_content_length(request)
        limit = max_request_body_bytes()
        if declared is not None and declared > limit:
            return _too_large(limit)
        return await call_next(request)

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive)
        limit = max_request_body_bytes()
        declared = _declared_content_length(request)
        if declared is not None and declared > limit:
            await _too_large(limit)(scope, receive, send)
            return

        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                continue
            chunk = message.get("body", b"") or b""
            body.extend(chunk)
            if len(body) > limit:
                await _too_large(limit)(scope, receive, send)
                return
            if not message.get("more_body", False):
                break

        payload = bytes(body)
        sent = False

        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": payload, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


__all__ = [
    "REQUEST_ENVELOPE_OVERHEAD_BYTES",
    "RequestSizeLimitMiddleware",
    "max_request_body_bytes",
]
