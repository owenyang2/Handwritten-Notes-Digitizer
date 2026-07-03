"""Rate limiting middleware for the Handwriting Notes Digitizer.

Implements a simple in-process token-bucket rate limiter per client IP for
the three most abuse-prone endpoints:

* ``/auth/login`` and ``/auth/register`` — brute-force and account-creation abuse.
* ``/images`` (POST) — disk-fill via bulk image uploads.
* ``/images/{id}/recognize`` (POST) — CPU exhaustion via inference spam.

The limiter is intentionally simple (in-memory, per-process) because the demo
runs as a single Uvicorn process behind nginx. For multi-worker or distributed
deployments, replace ``_TokenBucketStore`` with a Redis-backed implementation
(e.g. via ``slowapi`` or a custom Redis script).

Configuration is via ``Settings``; see ``config/settings.py`` for the knobs.

Usage
-----
Register the middleware on the FastAPI app::

    from app.middleware.rate_limiter import RateLimitMiddleware
    app.add_middleware(RateLimitMiddleware)
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Token-bucket implementation
# ---------------------------------------------------------------------------


class _Bucket:
    """A single token-bucket for one (IP, route) pair."""

    __slots__ = ("tokens", "last_refill", "capacity", "refill_rate")

    def __init__(self, capacity: float, refill_rate: float) -> None:
        self.capacity = capacity        # max tokens
        self.refill_rate = refill_rate  # tokens added per second
        self.tokens = capacity          # start full
        self.last_refill = time.monotonic()

    def consume(self) -> bool:
        """Try to consume one token. Returns True if allowed, False if rate-limited."""
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False


class _TokenBucketStore:
    """Thread-safe store of per-(IP, route-key) token buckets."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buckets: dict[tuple[str, str], _Bucket] = defaultdict(
            lambda: _Bucket(0, 0)  # replaced on first get_or_create call
        )

    def get_or_create(
        self, ip: str, route_key: str, capacity: float, refill_rate: float
    ) -> _Bucket:
        key = (ip, route_key)
        with self._lock:
            if key not in self._buckets:
                self._buckets[key] = _Bucket(capacity, refill_rate)
            return self._buckets[key]

    def consume(
        self, ip: str, route_key: str, capacity: float, refill_rate: float
    ) -> bool:
        bucket = self.get_or_create(ip, route_key, capacity, refill_rate)
        with self._lock:
            return bucket.consume()

    def cleanup_stale(self, max_idle_seconds: float = 3600.0) -> None:
        """Remove buckets that have been idle for ``max_idle_seconds``.

        Call periodically to prevent unbounded memory growth.
        """
        now = time.monotonic()
        with self._lock:
            stale = [
                k
                for k, b in self._buckets.items()
                if (now - b.last_refill) > max_idle_seconds
            ]
            for k in stale:
                del self._buckets[k]


# ---------------------------------------------------------------------------
# Route → (capacity, refill_rate) configuration
# ---------------------------------------------------------------------------

# Each entry is (method, path_prefix): (burst_capacity, sustained_rps)
# capacity  = max burst requests allowed before throttling starts
# refill_rate = steady-state requests per second after burst is exhausted
_RATE_LIMIT_RULES: list[tuple[str, str, float, float]] = [
    # Auth endpoints — tight to prevent brute force / account spam
    ("POST", "/auth/login",    5,  0.2),  # 5 burst, then 1 req / 5s
    ("POST", "/auth/register", 3,  0.05), # 3 burst, then 1 req / 20s
    # Image upload — prevent disk-fill; allow reasonable bursts for multi-page docs
    ("POST", "/images",       10,  0.5),  # 10 burst, then 1 req / 2s
    # Recognition — each triggers ML inference; expensive
    ("POST", "/recognize",     5,  0.33), # 5 burst, then ~1 req / 3s (path suffix match)
]

_store = _TokenBucketStore()


def _client_ip(request: Request) -> str:
    """Extract the real client IP, respecting X-Forwarded-For from nginx."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _find_rule(
    method: str, path: str
) -> tuple[float, float] | None:
    """Return (capacity, refill_rate) for the first matching rule, or None."""
    for rule_method, rule_prefix, capacity, rate in _RATE_LIMIT_RULES:
        if method.upper() == rule_method and rule_prefix in path:
            return capacity, rate
    return None


# ---------------------------------------------------------------------------
# Starlette middleware
# ---------------------------------------------------------------------------


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Starlette BaseHTTPMiddleware that enforces token-bucket rate limits.

    Clients exceeding their quota receive ``429 Too Many Requests`` with a
    ``Retry-After`` header indicating when they may try again (conservatively
    estimated as 1 / refill_rate seconds).
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Skip rate limiting in test environments to avoid interfering with
        # test suites that make many rapid requests from the same fake IP.
        import os
        if os.environ.get("HND_ENVIRONMENT", "").lower() == "test":
            return await call_next(request)

        rule = _find_rule(request.method, request.url.path)
        if rule is not None:
            capacity, refill_rate = rule
            ip = _client_ip(request)
            route_key = f"{request.method}:{request.url.path}"
            allowed = _store.consume(ip, route_key, capacity, refill_rate)
            if not allowed:
                retry_after = int(1.0 / refill_rate) if refill_rate > 0 else 60
                logger.warning(
                    "Rate limit exceeded: ip=%s path=%s", ip, request.url.path
                )
                return JSONResponse(
                    status_code=429,
                    content={
                        "detail": "Too many requests. Please slow down.",
                        "retry_after_seconds": retry_after,
                    },
                    headers={"Retry-After": str(retry_after)},
                )
        return await call_next(request)


__all__ = ["RateLimitMiddleware"]
