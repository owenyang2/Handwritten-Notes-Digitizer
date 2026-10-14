"""Lightweight production observability: redacted JSON logs and HTTP counters.

Wired from :mod:`app.main` only when ``settings.environment == production`` or
``HND_ENABLE_METRICS=true``. Counters never include note content, tokens,
LaTeX, or filenames — only method, normalized route, and status.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections import defaultdict
from threading import Lock
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

from app.config.settings import Settings

_BEARER_RE = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9\-._~+/=]+")
_TOKEN_ASSIGN_RE = re.compile(r"(?i)((?:access[_-]?token|refresh[_-]?token|token|api[_-]?key)[=:]\s*)[^\s&,;]+")
_SESSION_RE = re.compile(r"(?i)((?:session|authorization)=)[^;\s]+")
_LATEX_INLINE_RE = re.compile(r"\$[^$]+\$")
_LATEX_BLOCK_RE = re.compile(r"\\\[[\s\S]*?\\\]")
_LATEX_CMD_RE = re.compile(
    r"\\(?:frac|sum|int|begin|end|mathrm|mathbf|mathrm|left|right|sqrt|text)\{[^}]*\}"
)
_FILENAME_RE = re.compile(
    r"(?i)\b[\w./-]+\.(?:png|jpe?g|gif|webp|tif{1,2}|pdf|tex|bin|pt|onnx|heic)\b"
)
_EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_UUID_RE = re.compile(
    r"/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_LONG_HEX_RE = re.compile(r"/[0-9a-fA-F]{16,}")
_NUMERIC_ID_RE = re.compile(r"/\d+")

_SKIP_METRIC_PATHS = frozenset({"/metrics"})


def redact_text(value: str) -> str:
    """Strip tokens, LaTeX, and filenames from a log line."""
    text = _BEARER_RE.sub(r"\1[REDACTED]", value)
    text = _TOKEN_ASSIGN_RE.sub(r"\1[REDACTED]", text)
    text = _SESSION_RE.sub(r"\1[REDACTED]", text)
    text = _LATEX_BLOCK_RE.sub("[LATEX]", text)
    text = _LATEX_INLINE_RE.sub("[LATEX]", text)
    text = _LATEX_CMD_RE.sub("[LATEX]", text)
    text = _FILENAME_RE.sub("[FILE]", text)
    text = _EMAIL_RE.sub("[EMAIL]", text)
    return text


class RedactingFilter(logging.Filter):
    """logging.Filter that redacts secrets and note content in place."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_text(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    key: redact_text(val) if isinstance(val, str) else val
                    for key, val in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    redact_text(arg) if isinstance(arg, str) else arg
                    for arg in record.args
                )
        return True


class JsonLogFormatter(logging.Formatter):
    """Emit one JSON object per log record (no note bodies)."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=True)


def normalize_route(path: str) -> str:
    """Replace resource ids so metric labels never contain note identifiers."""
    path = _UUID_RE.sub("/{id}", path)
    path = _LONG_HEX_RE.sub("/{id}", path)
    path = _NUMERIC_ID_RE.sub("/{id}", path)
    if len(path) > 120:
        path = path[:120]
    return path or "/"


class HttpRequestCounters:
    """Process-local Prometheus counter for HTTP requests."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._counts: dict[tuple[str, str, str], int] = defaultdict(int)

    def inc(self, method: str, route: str, status: int) -> None:
        key = (method.upper(), route, str(status))
        with self._lock:
            self._counts[key] += 1

    def render_prometheus(self) -> str:
        lines = [
            "# HELP hnd_http_requests_total HTTP requests by method, route, and status.",
            "# TYPE hnd_http_requests_total counter",
        ]
        with self._lock:
            items = sorted(self._counts.items())
        for (method, route, status), count in items:
            lines.append(
                f'hnd_http_requests_total{{method="{method}",route="{route}",status="{status}"}} {count}'
            )
        return "\n".join(lines) + "\n"


metrics = HttpRequestCounters()


class MetricsMiddleware(BaseHTTPMiddleware):
    """Count HTTP requests without recording bodies, filenames, or tokens."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        started = time.perf_counter()
        response = await call_next(request)
        _ = started
        path = request.url.path
        if path not in _SKIP_METRIC_PATHS:
            metrics.inc(request.method, normalize_route(path), response.status_code)
        return response


def metrics_endpoint() -> PlainTextResponse:
    return PlainTextResponse(
        metrics.render_prometheus(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


def install_redacting_filter() -> None:
    """Attach the redacting filter even when metrics and JSON logs are off."""
    root = logging.getLogger()
    if not any(isinstance(existing, RedactingFilter) for existing in root.filters):
        root.addFilter(RedactingFilter())
    for handler in root.handlers:
        if not any(isinstance(existing, RedactingFilter) for existing in handler.filters):
            handler.addFilter(RedactingFilter())


def configure_observability(settings: Settings) -> None:
    """Install redacting JSON logs and surface the optional OTLP endpoint."""
    formatter = JsonLogFormatter()
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        root.addHandler(handler)
        root.setLevel(logging.INFO)
    else:
        for handler in root.handlers:
            handler.setFormatter(formatter)
    install_redacting_filter()
    if settings.otel_exporter_otlp_endpoint:
        os.environ.setdefault(
            "OTEL_EXPORTER_OTLP_ENDPOINT", settings.otel_exporter_otlp_endpoint
        )


def metrics_enabled(settings: Settings) -> bool:
    if os.environ.get("HND_ENABLE_METRICS", "").lower() in {"1", "true", "yes", "on"}:
        return True
    return settings.environment.lower() == "production" or bool(settings.enable_metrics)


__all__ = [
    "RedactingFilter",
    "JsonLogFormatter",
    "MetricsMiddleware",
    "configure_observability",
    "install_redacting_filter",
    "metrics",
    "metrics_enabled",
    "metrics_endpoint",
    "normalize_route",
    "redact_text",
]
