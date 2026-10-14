"""Regression tests for the declared request-size envelope."""

from __future__ import annotations

import asyncio

from starlette.requests import Request
from starlette.responses import PlainTextResponse

from app.config.settings import Settings
from app.middleware import request_limits


def _request(content_length: str | None) -> Request:
    headers = []
    if content_length is not None:
        headers.append((b"content-length", content_length.encode("ascii")))
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/images",
        "headers": headers,
        "query_string": b"",
        "server": ("testserver", 80),
        "client": ("testclient", 1234),
        "scheme": "http",
        "http_version": "1.1",
    }
    return Request(scope)


def test_declared_body_above_envelope_is_rejected_before_route(monkeypatch) -> None:
    monkeypatch.setattr(
        request_limits,
        "get_settings",
        lambda: Settings(max_upload_bytes=10),
    )
    middleware = request_limits.RequestSizeLimitMiddleware(object())

    async def call_next(_request):
        return PlainTextResponse("route reached")

    response = asyncio.run(
        middleware.dispatch(
            _request(str(10 + request_limits.REQUEST_ENVELOPE_OVERHEAD_BYTES + 1)),
            call_next,
        )
    )

    assert response.status_code == 413


def test_chunked_body_over_the_envelope_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(request_limits, "max_request_body_bytes", lambda: 8)
    called = False

    async def app(scope, receive, send):
        nonlocal called
        called = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    middleware = request_limits.RequestSizeLimitMiddleware(app)
    messages = [
        {"type": "http.request", "body": b"12345", "more_body": True},
        {"type": "http.request", "body": b"67890", "more_body": False},
    ]

    async def receive():
        if messages:
            return messages.pop(0)
        return {"type": "http.disconnect"}

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/images",
        "headers": [],
        "query_string": b"",
        "server": ("testserver", 80),
        "client": ("testclient", 1234),
        "scheme": "http",
        "http_version": "1.1",
    }
    asyncio.run(middleware(scope, receive, send))
    assert called is False
    assert sent[0]["status"] == 413


def test_missing_or_malformed_content_length_reaches_bounded_route() -> None:
    middleware = request_limits.RequestSizeLimitMiddleware(object())

    async def call_next(_request):
        return PlainTextResponse("route reached")

    for value in (None, "not-a-number"):
        response = asyncio.run(middleware.dispatch(_request(value), call_next))
        assert response.status_code == 200


def test_bounded_body_replays_once_then_preserves_disconnect(monkeypatch) -> None:
    monkeypatch.setattr(request_limits, "max_request_body_bytes", lambda: 8)
    messages = iter([
        {"type": "http.request", "body": b"123", "more_body": True},
        {"type": "http.request", "body": b"45", "more_body": False},
        {"type": "http.disconnect"},
    ])

    async def receive():
        return next(messages)

    async def app(scope, replay, send):
        assert await replay() == {"type": "http.request", "body": b"12345", "more_body": False}
        assert await replay() == {"type": "http.disconnect"}

    asyncio.run(request_limits.RequestSizeLimitMiddleware(app)(_request(None).scope, receive, None))
