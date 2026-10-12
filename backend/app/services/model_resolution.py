"""Resolve and persist the model assignment used for one recognition result.

The assignment registry is intentionally separate from the recognizer.  An
engine can remain a pure, testable function while the request/worker boundary
records which task, base artifact, and compatible personal adapter were used.
That provenance is required before model experiments or personalized math
recognition can be evaluated honestly.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from app.data.enums import AdapterStatus, ModelTask
from app.data.models import Adapter, ModelAssignment
from app.data.repositories import RepositoryFactory


@dataclass(frozen=True)
class ResolvedModel:
    """The assignment selected for one inference operation."""

    assignment: ModelAssignment
    task: str
    base_model_version: str
    adapter_version: str
    personalized: bool


def task_for_mode(mode: str) -> ModelTask:
    """Map an API recognition mode to the stable registry task vocabulary."""
    return {
        "prose": ModelTask.PROSE_RASTER,
        "math": ModelTask.MATH_RASTER,
        "formula": ModelTask.MATH_RASTER,
        "ink": ModelTask.MATH_INK,
        "mixed": ModelTask.MIXED_RASTER,
        "figure": ModelTask.FIGURE,
    }.get(mode, ModelTask.MATH_RASTER)


def base_model_version(
    engine_id: str,
    engine_version: str,
    provenance: dict[str, Any] | None = None,
) -> str:
    """Build a stable, content-based model identity without leaking paths.

    Absolute checkpoint paths are useful in local logs but are not a model
    identity and should not be copied into user-visible prediction metadata.
    Prefer an upstream model id/revision and weight digest when available; the
    engine/version pair remains a useful fallback for unavailable/test engines.
    """
    safe_provenance = provenance or {}
    parts = [str(engine_id or "unknown"), str(engine_version or "unknown")]
    for key in ("model_id", "revision", "weight_sha256"):
        value = safe_provenance.get(key)
        if value:
            parts.append(f"{key}={value}")
    identity = ":".join(parts)
    if len(identity) <= 255:
        return identity
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return f"{engine_id or 'unknown'}:{engine_version or 'unknown'}:sha256={digest}"


def _compatible_adapter(
    repos: RepositoryFactory,
    user_id,
    task: str,
    base_version: str,
) -> tuple[Adapter | None, str]:
    """Return a personal adapter only for a compatible task/base pair.

    The current adapter implementation is a legacy prose adapter.  It is not
    silently applied to math raster recognition; a future math adapter must
    explicitly resolve through this same compatibility check.
    """
    if task != ModelTask.PROSE_RASTER.value:
        return None, "base_default"
    adapter = repos.adapters.get_active_for_user(user_id)
    if adapter is None or adapter.status != AdapterStatus.ACTIVE:
        return None, "base_default"
    if adapter.base_model_version != base_version:
        return None, "base_adapter_incompatible"
    return adapter, "personal_adapter"


def resolve_model_assignment(
    repos: RepositoryFactory,
    user_id,
    *,
    task: ModelTask | str,
    engine_id: str,
    engine_version: str,
    provenance: dict[str, Any] | None = None,
) -> ResolvedModel:
    """Resolve the exact assignment and persist it in the user's registry.

    Repeated inference with the same model combination reuses one immutable
    assignment row.  Selecting a new combination retires the former active
    assignment for that task, while old predictions continue to reference the
    historical row.
    """
    task_value = task.value if isinstance(task, ModelTask) else str(task)
    base_version = base_model_version(engine_id, engine_version, provenance)
    adapter, activation_reason = _compatible_adapter(
        repos, user_id, task_value, base_version
    )
    adapter_id = str(adapter.adapter_id) if adapter is not None else "base"
    adapter_version = adapter_id
    assignment_key = (
        f"{task_value}|base={base_version}|adapter={adapter_id}|"
        f"adapter_version={adapter_version}"
    )
    existing = repos.model_assignments.get_by_key(
        user_id, task_value, assignment_key
    )
    if existing is None:
        # Retire before inserting because the database has a partial unique
        # index enforcing one active assignment per user/task.
        repos.model_assignments.retire_active(user_id, task_value)
        safe_provenance = provenance or {}
        metrics = {
            "engine_id": str(engine_id),
            "engine_version": str(engine_version),
        }
        for key in ("model_id", "revision", "weight_sha256"):
            value = safe_provenance.get(key)
            if value is not None:
                metrics[key] = str(value)
        existing = repos.model_assignments.add(
            ModelAssignment(
                user_id=user_id,
                task=task_value,
                assignment_key=assignment_key,
                base_model_version=base_version,
                adapter_id=adapter.adapter_id if adapter is not None else None,
                adapter_version=adapter_version,
                activation_reason=activation_reason,
                metrics=metrics,
                active=True,
            )
        )
    # This also handles a newly inserted assignment.  Activating after the
    # insert retires any previous combination for the same user/task while
    # keeping historical rows available to old predictions.
    repos.model_assignments.activate(existing)

    return ResolvedModel(
        assignment=existing,
        task=task_value,
        base_model_version=base_version,
        adapter_version=adapter_version,
        personalized=adapter is not None,
    )


__all__ = [
    "ResolvedModel",
    "base_model_version",
    "resolve_model_assignment",
    "task_for_mode",
]
