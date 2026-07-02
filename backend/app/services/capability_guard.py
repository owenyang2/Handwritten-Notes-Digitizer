"""Capability guard: FastAPI dependency factory for partial-deployment protection.

This module implements the "capability unavailable" response required by
Req 11.5 and 11.6.  Endpoints whose backing component is unavailable must
return HTTP 503 with a structured JSON body rather than a 404 or an
internal-server-error, so that:

* Available capabilities continue serving normally (Req 11.5).
* Unavailable capabilities return a clear, machine-readable reason (Req 11.6).

Usage
-----
Each guarded endpoint declares a FastAPI dependency created by
:func:`require_capability`:

.. code-block:: python

    from app.services.capability_guard import require_capability

    @router.post("/images/{image_id}/recognize")
    def recognize_image(
        image_id: uuid.UUID,
        _: None = Depends(require_capability("recognition")),
        ...
    ):
        ...

When the named capability is unavailable the dependency raises an
``HTTPException(503)`` before the handler body runs.  When it is available the
dependency returns ``None`` and execution continues normally.

The guard reads component availability from ``os.environ`` at *request time*,
not at startup, so toggling an env-var (e.g. in tests) takes effect
immediately without restarting the process.

503 response body
-----------------
::

    {"detail": "capability '<name>' is unavailable in this deployment"}

This is the standard FastAPI ``HTTPException`` JSON envelope, which matches the
format specified in the task.

Singleton guards
----------------
``require_capability`` returns the **same callable object** for a given
capability name (cached in ``_GUARDS``).  This is critical for FastAPI's
``app.dependency_overrides`` to work in tests: the override key must be the
identical object that is used as the ``Depends(...)`` argument.
"""

from __future__ import annotations

from fastapi import HTTPException, status

from app.services.capability_registry import is_capability_available

# Cache of capability-name → guard callable, so the same object is always
# returned for the same capability name (required for dependency_overrides).
_GUARDS: dict[str, object] = {}


def require_capability(capability: str):
    """Return a stable FastAPI dependency callable that guards *capability*.

    The function is cached: calling ``require_capability("recognition")`` twice
    returns the **same object**, which allows ``app.dependency_overrides`` to
    work correctly in tests.

    Parameters
    ----------
    capability:
        A capability name from :data:`~app.services.capability_registry.CAPABILITIES`
        (e.g. ``"recognition"``, ``"correction"``, ``"calibration"``,
        ``"personalization"``, ``"export"``).

    Returns
    -------
    Callable
        A zero-argument callable suitable for use with ``Depends()``.
        Raises ``HTTP 503`` when the capability is unavailable; returns
        ``None`` when it is available.
    """
    if capability in _GUARDS:
        return _GUARDS[capability]

    # Build a named function (not a lambda) so pytest and FastAPI give useful
    # names in tracebacks and the OpenAPI schema.
    def _guard() -> None:
        """Inner dependency: check capability availability at request time."""
        if not is_capability_available(capability):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    f"capability '{capability}' is unavailable in this deployment"
                ),
            )

    # Give the function a unique name so FastAPI can distinguish multiple guards
    # in the same route's dependency tree.
    _guard.__name__ = f"_guard_{capability}"
    _guard.__qualname__ = f"require_capability.<locals>._guard_{capability}"

    _GUARDS[capability] = _guard
    return _guard


__all__ = ["require_capability"]
