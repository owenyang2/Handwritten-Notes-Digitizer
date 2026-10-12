"""Portable checkpoint helpers for uninterrupted/resumed experiments."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import random

import numpy as np

import torch


def save_checkpoint(path: str | Path, payload: dict[str, Any]) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # A schema marker makes old, incomplete pilot checkpoints distinguishable
    # from resumable training artifacts.
    payload = {"checkpoint_schema_version": payload.get("checkpoint_schema_version", 2), **payload}
    torch.save(payload, destination)
    return destination


def load_checkpoint(path: str | Path, map_location: str = "cpu") -> dict[str, Any]:
    payload = torch.load(Path(path), map_location=map_location, weights_only=False)
    if not isinstance(payload, dict):
        raise ValueError("checkpoint must contain a mapping")
    return payload


def capture_rng_state() -> dict[str, Any]:
    """Capture Python, NumPy, Torch CPU, and available CUDA RNG states."""
    state: dict[str, Any] = {
        "python": random.getstate(),
        "numpy": np.random.get_state(),
        "torch": torch.get_rng_state(),
    }
    if torch.cuda.is_available():
        state["cuda"] = torch.cuda.get_rng_state_all()
    return state


def restore_rng_state(state: dict[str, Any] | None) -> None:
    """Restore a state captured by :func:`capture_rng_state`, if present."""
    if not isinstance(state, dict):
        return
    if state.get("python") is not None:
        random.setstate(state["python"])
    if state.get("numpy") is not None:
        np.random.set_state(state["numpy"])
    if state.get("torch") is not None:
        torch.set_rng_state(state["torch"])
    if torch.cuda.is_available() and state.get("cuda") is not None:
        torch.cuda.set_rng_state_all(state["cuda"])
