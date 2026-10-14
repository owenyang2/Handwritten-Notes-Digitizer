"""Rate limiting middleware for the Handwriting Notes Digitizer.

Implements per-client quotas on abuse-prone endpoints:

* ``/auth/login``, ``/auth/register``, and ``/auth/password-reset/*`` — keyed
  by client IP. A second bucket, keyed by the submitted username or email, is
  enforced in those handlers. Presented bearer tokens are ignored so a fresh
  ``Authorization`` header cannot mint a new quota.
* ``/images`` (POST) — disk-fill via bulk image uploads.
* ``/images/{id}/recognize`` (POST) — CPU exhaustion via inference spam.
* ``/documents/import`` and document exports — bulk ingest and export spam.
* ``/uploads`` — resumable upload create/part traffic.
* ``/pages/{id}/recognition`` — page-level recognition jobs.
* ``/sync/push`` — sync-operation floods.
* ``/export``, calibration, corrections, personalization runs, and job retry.

The default limiter is in-memory (single Uvicorn process, local/demo). When
``settings.redis_url`` is set, buckets use Redis ``INCR`` + ``EXPIRE``. In
production, auth routes fail closed if that Redis dependency is configured
but unreachable. Other routes, and non-production processes, fall back to the
in-memory store so lean CI and local tests never require Redis.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from typing import Any, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)

SESSION_COOKIE_NAME = "session"


class RateLimitUnavailable(RuntimeError):
    """Production auth limiting could not reach its configured Redis."""

# Auth routes never trust a presented credential as the bucket identity.
_IP_ONLY_PATHS = frozenset(
    {
        "/auth/login",
        "/auth/register",
        "/auth/password-reset/request",
        "/auth/password-reset/confirm",
    }
)

# Progressive lockout after repeated credential failures: (threshold, seconds).
_LOCK_STEPS = ((15, 15 * 60), (10, 5 * 60), (5, 60))

# ---------------------------------------------------------------------------
# Token-bucket implementation (in-memory fallback)
# ---------------------------------------------------------------------------


class _Bucket:
    """A single token-bucket for one (identity, route) pair."""

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
    """Thread-safe store of per-(identity, route-key) token buckets."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buckets: dict[tuple[str, str], _Bucket] = {}
        self._last_cleanup = time.monotonic()

    def consume(
        self, identity: str, route_key: str, capacity: float, refill_rate: float
    ) -> bool:
        key = (identity, route_key)
        with self._lock:
            now = time.monotonic()
            if now - self._last_cleanup >= 300.0:
                self._cleanup_stale_locked(now, max_idle_seconds=3600.0)
                self._last_cleanup = now
            if key not in self._buckets:
                self._buckets[key] = _Bucket(capacity, refill_rate)
            bucket = self._buckets[key]
            return bucket.consume()

    def cleanup_stale(self, max_idle_seconds: float = 3600.0) -> None:
        """Remove buckets that have been idle for ``max_idle_seconds``.

        Call periodically to prevent unbounded memory growth.
        """
        now = time.monotonic()
        with self._lock:
            self._cleanup_stale_locked(now, max_idle_seconds)
            self._last_cleanup = now

    def _cleanup_stale_locked(self, now: float, max_idle_seconds: float) -> None:
        stale = [
            key
            for key, bucket in self._buckets.items()
            if now - bucket.last_refill > max_idle_seconds
        ]
        for key in stale:
            del self._buckets[key]


class _FailureTracker:
    """In-memory progressive lockout used when Redis is not configured."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: dict[str, int] = {}
        self._locked_until: dict[str, float] = {}

    def remaining(self, key: str) -> int | None:
        with self._lock:
            until = self._locked_until.get(key)
            if until is None:
                return None
            left = until - time.monotonic()
            if left <= 0:
                self._locked_until.pop(key, None)
                return None
            return max(1, int(left))

    def record_failure(self, key: str) -> int | None:
        with self._lock:
            count = self._counts.get(key, 0) + 1
            self._counts[key] = count
            delay = 0
            for threshold, seconds in _LOCK_STEPS:
                if count >= threshold:
                    delay = seconds
                    break
            if delay:
                self._locked_until[key] = time.monotonic() + delay
                return delay
            return None

    def clear(self, key: str) -> None:
        with self._lock:
            self._counts.pop(key, None)
            self._locked_until.pop(key, None)


# ---------------------------------------------------------------------------
# Route → (capacity, refill_rate) configuration
# ---------------------------------------------------------------------------

# Each entry is (method, stable_route_key): (burst_capacity, sustained_rps)
# capacity  = max burst requests allowed before throttling starts
# refill_rate = steady-state requests per second after burst is exhausted
_RATE_LIMIT_RULES: list[tuple[str, str, float, float]] = [
    # Auth endpoints — tight to prevent brute force / account spam.
    # Identity is client IP; handlers add a second bucket on the account name.
    ("POST", "/auth/login",    5,  0.2),  # 5 burst, then 1 req / 5s
    ("POST", "/auth/register", 3,  0.05), # 3 burst, then 1 req / 20s
    ("POST", "/auth/password-reset/request", 5, 0.1),  # 5 burst, then 1 req / 10s
    ("POST", "/auth/password-reset/confirm", 10, 0.2),
    # Image upload — prevent disk-fill; allow reasonable bursts for multi-page docs
    ("POST", "/images",       10,  0.5),  # 10 burst, then 1 req / 2s
    # Recognition — each triggers ML inference; expensive
    ("POST", "/recognize",     5,  0.33), # 5 burst, then ~1 req / 3s (path suffix match)
    ("POST", "/corrections",  10,  0.5),
    ("POST", "/documents/import", 5, 0.2),
    ("POST", "/documents/*/exports", 5, 0.2),
    ("POST", "/uploads", 10, 0.5),
    ("PUT", "/uploads", 20, 1.0),
    ("POST", "/pages/*/recognition", 5, 0.33),
    ("POST", "/sync/push", 20, 1.0),
    ("POST", "/export", 10, 0.5),
    ("POST", "/personalization/runs", 2, 0.05),
    ("POST", "/calibration", 5, 0.2),
    ("POST", "/jobs/*/retry", 5, 0.2),
]

_store = _TokenBucketStore()
_failures = _FailureTracker()
_redis_client: Any | None = None
_redis_init_attempted = False
_redis_unavailable = False


def _client_ip(request: Request) -> str:
    """Extract the address supplied by nginx, falling back to the peer address.

    nginx overwrites ``X-Real-IP`` with ``$remote_addr``. In contrast,
    ``X-Forwarded-For`` deliberately preserves client-supplied values and must
    not be used as an unvalidated rate-limit identity.
    """
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else "unknown"


def _hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


def _client_identity(request: Request) -> str:
    """Prefer a hashed account credential; otherwise fall back to client IP.

    Login, registration, and password reset always use the client IP. A random
    bearer token or session cookie must not open a fresh bucket.
    """
    if request.url.path in _IP_ONLY_PATHS:
        return f"ip:{_client_ip(request)}"
    authorization = request.headers.get("Authorization")
    if authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() == "bearer" and token.strip():
            return f"acct:{_hash_secret(token.strip())}"
    cookie = request.cookies.get(SESSION_COOKIE_NAME)
    if cookie:
        return f"acct:{_hash_secret(cookie)}"
    return f"ip:{_client_ip(request)}"


def _wildcard_match(pattern: str, path: str) -> bool:
    pattern_parts = [segment for segment in pattern.split("/") if segment]
    path_parts = [segment for segment in path.split("/") if segment]
    if len(pattern_parts) != len(path_parts):
        return False
    return all(
        pattern_part == "*" or pattern_part == path_part
        for pattern_part, path_part in zip(pattern_parts, path_parts)
    )


def _path_matches(rule_prefix: str, path: str) -> bool:
    if rule_prefix == "/recognize":
        return path.endswith("/recognize")
    if rule_prefix == "/corrections":
        return path.endswith("/corrections")
    if rule_prefix == "/calibration":
        return path == "/calibration" or path.startswith("/calibration/")
    if rule_prefix == "/uploads":
        return path == "/uploads" or path.startswith("/uploads/")
    if rule_prefix == "/pages/*/recognition":
        parts = [segment for segment in path.split("/") if segment]
        return (
            len(parts) >= 3
            and parts[0] == "pages"
            and parts[2].startswith("recognition")
        )
    if "*" in rule_prefix:
        return _wildcard_match(rule_prefix, path)
    return path == rule_prefix


def _find_rule(method: str, path: str) -> tuple[str, float, float] | None:
    """Return the matched route key, capacity, and refill rate, if any."""
    method_upper = method.upper()
    for rule_method, rule_prefix, capacity, rate in _RATE_LIMIT_RULES:
        if method_upper != rule_method:
            continue
        if _path_matches(rule_prefix, path):
            return rule_prefix, capacity, rate
    return None


def _window_seconds(capacity: float, refill_rate: float) -> int:
    if refill_rate <= 0:
        return 60
    return max(1, int(round(capacity / refill_rate)))


def _retry_after(refill_rate: float) -> int:
    return int(1.0 / refill_rate) if refill_rate > 0 else 60


def _environment_name() -> str:
    env = os.environ.get("HND_ENVIRONMENT", "").strip().lower()
    if env:
        return env
    try:
        from app.config.settings import get_settings

        return get_settings().environment
    except Exception:
        return ""


def _redis_url_configured() -> bool:
    try:
        from app.config.settings import get_settings

        return bool(get_settings().redis_url)
    except Exception:
        return False


def _should_fail_closed(route_key: str) -> bool:
    """Auth quotas fail closed only when production Redis was configured."""
    if _environment_name() != "production":
        return False
    if not route_key.startswith("POST:/auth/"):
        return False
    return _redis_unavailable or _redis_url_configured()


def _get_redis() -> Any | None:
    """Return a Redis client when configured and reachable; otherwise None."""
    global _redis_client, _redis_init_attempted, _redis_unavailable
    if _redis_init_attempted:
        return _redis_client
    _redis_init_attempted = True
    try:
        from app.config.settings import get_settings

        url = get_settings().redis_url
    except Exception:
        return None
    if not url:
        return None
    try:
        import redis as redis_lib
    except ImportError:
        logger.warning(
            "redis_url is set but the redis package is missing; using in-memory rate limits"
        )
        _redis_unavailable = True
        return None
    try:
        client = redis_lib.Redis.from_url(
            url,
            socket_connect_timeout=0.2,
            socket_timeout=0.2,
            decode_responses=True,
        )
        client.ping()
    except Exception as exc:
        _redis_unavailable = True
        logger.warning(
            "Redis rate limiter unavailable (%s); using in-memory buckets", exc
        )
        return None
    _redis_client = client
    return _redis_client


def _redis_consume(
    client: Any, identity: str, route_key: str, capacity: float, refill_rate: float
) -> bool:
    window = _window_seconds(capacity, refill_rate)
    slot = int(time.time()) // window
    key = f"hnd:rl:{identity}:{route_key}:{slot}"
    count = int(client.incr(key))
    if count == 1:
        client.expire(key, window)
    return count <= int(capacity)


def _consume_status(
    identity: str, route_key: str, capacity: float, refill_rate: float
) -> str:
    """Return ``allowed``, ``limited``, or ``unavailable``."""
    client = _get_redis()
    if client is not None:
        try:
            allowed = _redis_consume(client, identity, route_key, capacity, refill_rate)
            return "allowed" if allowed else "limited"
        except Exception as exc:
            if _should_fail_closed(route_key):
                logger.warning("Redis INCR failed (%s); refusing auth traffic", exc)
                return "unavailable"
            logger.warning(
                "Redis INCR failed (%s); falling back to in-memory rate limits", exc
            )
    elif _should_fail_closed(route_key):
        logger.warning("Redis rate limiter is unavailable; refusing auth traffic")
        return "unavailable"
    allowed = _store.consume(identity, route_key, capacity, refill_rate)
    return "allowed" if allowed else "limited"


def _consume(identity: str, route_key: str, capacity: float, refill_rate: float) -> bool:
    return _consume_status(identity, route_key, capacity, refill_rate) == "allowed"


def _testing() -> bool:
    return _environment_name() == "test"


def _subject_key(subject: str) -> str:
    return _hash_secret(subject.strip().lower())


def consume_subject_limit(
    subject: str, route_key: str, capacity: float, refill_rate: float
) -> str:
    """Consume the account-name bucket for an auth route.

    Returns ``allowed``, ``limited``, or ``unavailable``. Tests skip the check
    so suites can register and log in freely.
    """
    if _testing():
        return "allowed"
    normalized = subject.strip().lower()
    if not normalized:
        return "allowed"
    return _consume_status(
        f"subj:{_subject_key(normalized)}", route_key, capacity, refill_rate
    )


def _lock_delay(count: int) -> int:
    for threshold, seconds in _LOCK_STEPS:
        if count >= threshold:
            return seconds
    return 0


def login_lock_remaining(username: str) -> int | None:
    """Seconds remaining on a progressive lockout, if the account is locked."""
    if _testing():
        return None
    key = _subject_key(username)
    client = _get_redis()
    if client is not None:
        try:
            ttl = int(client.ttl(f"hnd:lock:{key}"))
        except Exception as exc:
            if _should_fail_closed("POST:/auth/login"):
                raise RateLimitUnavailable(str(exc)) from exc
            ttl = -1
        if ttl > 0:
            return ttl
        return _failures.remaining(key)
    return _failures.remaining(key)


def note_login_failure(username: str) -> None:
    """Record a failed login and arm the next lockout step when due."""
    if _testing():
        return
    key = _subject_key(username)
    client = _get_redis()
    if client is not None:
        try:
            count = int(client.incr(f"hnd:fail:{key}"))
            if count == 1:
                client.expire(f"hnd:fail:{key}", 15 * 60)
            delay = _lock_delay(count)
            if delay:
                client.setex(f"hnd:lock:{key}", delay, "1")
            return
        except Exception as exc:
            if _should_fail_closed("POST:/auth/login"):
                raise RateLimitUnavailable(str(exc)) from exc
    _failures.record_failure(key)


def note_login_success(username: str) -> None:
    """Clear failure counters after a successful login."""
    if _testing():
        return
    key = _subject_key(username)
    client = _get_redis()
    if client is not None:
        try:
            client.delete(f"hnd:fail:{key}", f"hnd:lock:{key}")
        except Exception as exc:
            if _should_fail_closed("POST:/auth/login"):
                raise RateLimitUnavailable(str(exc)) from exc
    _failures.clear(key)


def _limited_response(status: str, refill_rate: float) -> JSONResponse:
    if status == "unavailable":
        return JSONResponse(
            status_code=503,
            content={"detail": "Authentication is temporarily unavailable."},
            headers={"Retry-After": "5"},
        )
    retry_after = _retry_after(refill_rate)
    return JSONResponse(
        status_code=429,
        content={
            "detail": "Too many requests. Please slow down.",
            "retry_after_seconds": retry_after,
        },
        headers={"Retry-After": str(retry_after)},
    )


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
        if _testing():
            return await call_next(request)

        rule = _find_rule(request.method, request.url.path)
        if rule is not None:
            rule_key, capacity, refill_rate = rule
            identity = _client_identity(request)
            route_key = f"{request.method}:{rule_key}"
            status = _consume_status(identity, route_key, capacity, refill_rate)
            if status != "allowed":
                logger.warning(
                    "Rate limit %s: identity=%s path=%s",
                    status,
                    identity,
                    request.url.path,
                )
                return _limited_response(status, refill_rate)
        return await call_next(request)


__all__ = [
    "RateLimitMiddleware",
    "_find_rule",
    "_TokenBucketStore",
    "_FailureTracker",
    "consume_subject_limit",
    "login_lock_remaining",
    "note_login_failure",
    "note_login_success",
    "RateLimitUnavailable",
]
