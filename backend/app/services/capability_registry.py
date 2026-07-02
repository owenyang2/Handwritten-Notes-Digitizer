"""Capability registry for partial-deployment support (Req 11.5, 11.6).

This module defines the set of capabilities the system exposes, maps each
capability to the components it requires, and checks component availability
based on environment variables.  The result is consumed by the ``GET /health``
endpoint and by route guards that must return "capability unavailable" when a
backing component is absent (task 18.2).

Capabilities
------------
- ``image_upload``       — accepts and stores uploaded images
- ``recognition``        — runs HTR inference on stored images
- ``correction``         — captures inline corrections as Training_Pairs
- ``personalization``    — fine-tunes per-user adapters from Training_Pairs
- ``export``             — serialises the edited text to plain-text/markdown
- ``calibration``        — captures known-label samples for cold-start

Required components
-------------------
A *component* is a logical subsystem.  Availability is determined by a
corresponding boolean environment variable (prefixed ``HND_``).  All
components default to **enabled**; set the variable to ``0``, ``false``, or
``no`` (case-insensitive) to disable it.

+--------------------------+-----------------------------------+
| Component name           | Environment variable              |
+==========================+===================================+
| ``storage``              | ``HND_STORAGE_AVAILABLE``         |
| ``htr_engine``           | ``HND_HTR_ENGINE_AVAILABLE``      |
| ``training_worker``      | ``HND_TRAINING_WORKER_AVAILABLE`` |
+--------------------------+-----------------------------------+

Capability → required component mapping
-----------------------------------------
- ``image_upload``    → [``storage``]
- ``recognition``     → [``storage``, ``htr_engine``]
- ``correction``      → [``storage``]
- ``personalization`` → [``storage``, ``training_worker``]
- ``export``          → [``storage``]
- ``calibration``     → [``storage``, ``htr_engine``]
"""

from __future__ import annotations

import os
from typing import Final

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# All capability names used in the system.
CAPABILITIES: Final[tuple[str, ...]] = (
    "image_upload",
    "recognition",
    "correction",
    "personalization",
    "export",
    "calibration",
)

# All component names and the env-var that controls each.
_COMPONENT_ENV_VARS: Final[dict[str, str]] = {
    "storage": "HND_STORAGE_AVAILABLE",
    "htr_engine": "HND_HTR_ENGINE_AVAILABLE",
    "training_worker": "HND_TRAINING_WORKER_AVAILABLE",
}

# Maps each capability to the set of components it requires.
_CAPABILITY_REQUIREMENTS: Final[dict[str, tuple[str, ...]]] = {
    "image_upload": ("storage",),
    "recognition": ("storage", "htr_engine"),
    "correction": ("storage",),
    "personalization": ("storage", "training_worker"),
    "export": ("storage",),
    "calibration": ("storage", "htr_engine"),
}


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------


def _is_truthy(value: str) -> bool:
    """Return False iff *value* is a recognised falsy string.

    Accepted falsy values (case-insensitive): ``"0"``, ``"false"``, ``"no"``.
    Everything else (including the empty string) is considered truthy so that
    an unset / empty env-var is treated as *enabled*.
    """
    return value.strip().lower() not in {"0", "false", "no"}


def get_component_availability(env: dict[str, str] | None = None) -> dict[str, bool]:
    """Return a mapping of component name → availability flag.

    By default reads ``os.environ``; pass a custom *env* dict for testing.

    All components default to ``True`` (available) unless their environment
    variable is explicitly set to a falsy value.
    """
    source = env if env is not None else os.environ
    return {
        component: _is_truthy(source.get(env_var, "true"))
        for component, env_var in _COMPONENT_ENV_VARS.items()
    }


def get_capability_availability(
    component_availability: dict[str, bool] | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, bool]:
    """Return a mapping of capability name → availability flag.

    A capability is available iff **all** of its required components are
    available.

    Parameters
    ----------
    component_availability:
        Pre-computed component availability dict.  If ``None``, it is derived
        from *env* (or ``os.environ`` when *env* is also ``None``).
    env:
        Explicit environment dict passed through to
        :func:`get_component_availability` when *component_availability* is
        not supplied.
    """
    if component_availability is None:
        component_availability = get_component_availability(env)

    return {
        capability: all(
            component_availability.get(required_component, False)
            for required_component in required_components
        )
        for capability, required_components in _CAPABILITY_REQUIREMENTS.items()
    }


def is_capability_available(
    capability: str,
    component_availability: dict[str, bool] | None = None,
    env: dict[str, str] | None = None,
) -> bool:
    """Return ``True`` iff *capability* is currently available.

    Convenience wrapper around :func:`get_capability_availability`.
    """
    availability = get_capability_availability(component_availability, env)
    return availability.get(capability, False)


__all__ = [
    "CAPABILITIES",
    "get_component_availability",
    "get_capability_availability",
    "is_capability_available",
]
