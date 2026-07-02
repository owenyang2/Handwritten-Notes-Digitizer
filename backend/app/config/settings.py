"""Central application configuration and deterministic-runtime settings.

This module is the single source of truth for configuration shared across the
recognition and training subsystems. It pins random seeds and deterministic
backend settings so that, given the same dataset and configuration, the system
produces reproducible results (Requirements 11.1 and 11.3).

Import ``get_settings()`` to read configuration, and call ``seed_everything()``
(typically via ``configure_determinism()``) before any stochastic operation
such as data shuffling, weight initialisation, or augmentation.
"""

from __future__ import annotations

import os
import random
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings.

    Values may be overridden via environment variables (prefixed ``HND_``) or a
    ``.env`` file, but every field has a deterministic, documented default so a
    clean checkout is reproducible without manual edits (Req 11.1).
    """

    model_config = SettingsConfigDict(
        env_prefix="HND_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Application metadata -------------------------------------------------
    app_name: str = "Handwriting Notes Digitizer"
    app_version: str = "0.1.0"
    environment: str = Field(
        default="development",
        description="Deployment environment: development | test | production.",
    )

    # --- Reproducibility / determinism ---------------------------------------
    random_seed: int = Field(
        default=1337,
        description="Master seed applied to Python, NumPy, and PyTorch RNGs.",
    )
    deterministic: bool = Field(
        default=True,
        description=(
            "When true, configure NumPy/PyTorch for deterministic execution so "
            "re-running the documented procedure reproduces results (Req 11.3)."
        ),
    )
    cudnn_benchmark: bool = Field(
        default=False,
        description="Must stay False for deterministic convolution selection.",
    )

    # --- Storage / persistence ------------------------------------------------
    database_url: str = Field(
        default="sqlite:///./hnd.db",
        description="SQLAlchemy database URL for relational records.",
    )
    object_store_path: str = Field(
        default="./var/objectstore",
        description="Filesystem root for image bytes, region crops, and adapter weights.",
    )

    # --- Recognition / upload limits -----------------------------------------
    max_upload_bytes: int = Field(
        default=10 * 1024 * 1024,
        description="Maximum accepted upload size in bytes (10 MB, Req 1.3).",
    )
    recognition_timeout_seconds: float = Field(
        default=10.0,
        description="Recognition time budget in seconds (Req 2.3, 2.6).",
    )

    # --- Personalization ------------------------------------------------------
    personalization_trigger_threshold: int = Field(
        default=20,
        description="Unused training-pair count that triggers adapter training (Req 5.1).",
    )
    held_out_fraction: float = Field(
        default=0.2,
        description="Fraction of a user's pairs reserved for held-out evaluation (Req 6.1).",
    )
    held_out_min_pairs: int = Field(
        default=5,
        description="Minimum held-out pairs regardless of fraction (Req 6.1).",
    )
    max_training_epochs: int = Field(
        default=50,
        description="Hard cap on adapter training epochs (Req 6.5).",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached application settings singleton."""
    return Settings()


def seed_everything(seed: int | None = None) -> int:
    """Seed all relevant RNGs (Python, NumPy, PyTorch) with a fixed value.

    Returns the seed that was applied so callers can record it for provenance.
    PyTorch and NumPy are imported lazily so the config module stays importable
    in lightweight contexts (e.g. tooling) without the full ML stack installed.
    """
    if seed is None:
        seed = get_settings().random_seed

    os.environ["PYTHONHASHSEED"] = str(seed)
    random.seed(seed)

    try:
        import numpy as np

        np.random.seed(seed)
    except ImportError:  # pragma: no cover - numpy is a pinned dependency
        pass

    try:
        import torch

        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
    except ImportError:  # pragma: no cover - torch is a pinned dependency
        pass

    return seed


def configure_determinism(settings: Settings | None = None) -> int:
    """Apply seeds and deterministic backend settings, returning the seed used.

    This is the entry point recognition and training should call at startup.
    """
    if settings is None:
        settings = get_settings()

    applied_seed = seed_everything(settings.random_seed)

    if settings.deterministic:
        try:
            import torch

            torch.backends.cudnn.deterministic = True
            torch.backends.cudnn.benchmark = settings.cudnn_benchmark
            # Best-effort: not all ops have deterministic implementations.
            torch.use_deterministic_algorithms(True, warn_only=True)
        except ImportError:  # pragma: no cover - torch is a pinned dependency
            pass

    return applied_seed
