"""Cross-user resource isolation helpers (Req 10.5).

This module implements the design's *strict per-user isolation* guarantee at the
service/route boundary. The per-user-scoped repositories in
``app.data.repositories`` already refuse to return rows owned by another user:
an ownership miss surfaces as ``None`` (for single-resource lookups) or an empty
collection (for listings). This module turns that "ownership miss" signal into
the **correct HTTP behavior**: a request for a resource owned by another user
must return **404 Not Found**, *not* 403 Forbidden, so the system never discloses
that the resource exists at all (design "API Surface": *a request for a resource
owned by another user returns 404 (not 403) so existence is not disclosed*).

Why 404 instead of 403
----------------------
A 403 response tells the caller "this exists but you may not have it", which
leaks the existence of another user's training pairs, recognized text, or
adapter. A 404 is indistinguishable from "there is no such resource", so an
attacker cannot use the status code as an existence oracle. The same generic
message is used for a genuinely missing resource and for a resource owned by
someone else.

How to use it
-------------
Service and route code should funnel every owned-resource lookup through these
helpers instead of branching on ``None`` ad hoc:

* :func:`get_or_404` - wrap a repository lookup that returns ``Optional[T]``;
  it returns the value when present and raises :class:`ResourceNotFound`
  (HTTP 404) when the lookup missed (whether the row never existed or belongs to
  another user - the two are deliberately indistinguishable).
* :func:`require_owned` - alias of :func:`get_or_404` that reads naturally at
  ownership-enforcement call sites.

:class:`ResourceNotFound` subclasses FastAPI's :class:`~fastapi.HTTPException`
with status code 404, so it integrates with FastAPI's default error handling
without any extra wiring. :func:`register_isolation_handlers` is provided for the
case where non-HTTP layers raise the framework-agnostic
:class:`CrossUserAccessError`; it installs an exception handler mapping that to
the same 404 response so a leaked domain error can never become a 403/500 that
discloses existence.
"""

from __future__ import annotations

from typing import Optional, TypeVar

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse

T = TypeVar("T")

# Single generic message used for *both* "no such resource" and "owned by
# another user". Keeping them identical is what prevents the response from
# disclosing the existence of another user's data (Req 10.5).
RESOURCE_NOT_FOUND_MESSAGE = "Resource not found"


class CrossUserAccessError(Exception):
    """Framework-agnostic signal that a resource is not accessible to the caller.

    Raised by service-layer logic that has no business importing FastAPI but
    still needs to express "this resource does not belong to the requesting
    user". :func:`register_isolation_handlers` maps it to a 404 response so it
    is reported identically to a genuinely missing resource. Carrying no
    resource identifier or owner information by design, it cannot leak details
    about the other user's data.
    """

    def __init__(self, message: str = RESOURCE_NOT_FOUND_MESSAGE) -> None:
        super().__init__(message)
        self.message = message


class ResourceNotFound(HTTPException):
    """HTTP 404 raised when an owned-resource lookup misses.

    Used for both a genuinely absent resource and a resource owned by another
    user. Because it is a 404 (never a 403) with a generic message, the caller
    cannot tell the two apart and the other user's data existence stays hidden
    (Req 10.5).
    """

    def __init__(self, detail: str = RESOURCE_NOT_FOUND_MESSAGE) -> None:
        super().__init__(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


def get_or_404(value: Optional[T], detail: str = RESOURCE_NOT_FOUND_MESSAGE) -> T:
    """Return ``value`` if present, else raise :class:`ResourceNotFound` (404).

    Wrap any per-user-scoped repository lookup that returns ``Optional[T]`` with
    this helper. The scoped repositories return ``None`` when the resource does
    not exist *or* when it exists but belongs to another user; mapping both to a
    single 404 ensures cross-user access is denied without disclosing existence
    (Req 10.5).

    Example::

        image = get_or_404(repos.images.get(user.user_id, image_id))
    """
    if value is None:
        raise ResourceNotFound(detail)
    return value


def require_owned(value: Optional[T], detail: str = RESOURCE_NOT_FOUND_MESSAGE) -> T:
    """Ownership-enforcing alias of :func:`get_or_404`.

    Reads naturally where the intent is specifically to enforce that the
    authenticated user owns the looked-up resource, e.g.::

        pair = require_owned(repos.training_pairs.get(user.user_id, pair_id))
        adapter = require_owned(repos.adapters.get_for_user(user.user_id))
    """
    return get_or_404(value, detail)


def register_isolation_handlers(app: FastAPI) -> None:
    """Install an exception handler mapping :class:`CrossUserAccessError` to 404.

    Route handlers can simply raise :class:`ResourceNotFound` (already a 404),
    but service-layer code that stays framework-agnostic may raise
    :class:`CrossUserAccessError` instead. This handler guarantees that such a
    domain error is reported as a 404 with the generic not-found message rather
    than surfacing as a 403/500 that could disclose existence (Req 10.5).
    """

    @app.exception_handler(CrossUserAccessError)
    async def _handle_cross_user_access(  # pragma: no cover - thin adapter
        _request: Request, exc: CrossUserAccessError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": exc.message or RESOURCE_NOT_FOUND_MESSAGE},
        )


__all__ = [
    "RESOURCE_NOT_FOUND_MESSAGE",
    "CrossUserAccessError",
    "ResourceNotFound",
    "get_or_404",
    "require_owned",
    "register_isolation_handlers",
]
