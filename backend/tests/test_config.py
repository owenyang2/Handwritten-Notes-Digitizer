"""Unit tests for the central config module and deterministic seeding."""

import random
from pathlib import Path

from app.config import (
    Settings,
    configure_determinism,
    get_settings,
    seed_everything,
)


def test_settings_have_deterministic_defaults():
    settings = Settings()
    assert settings.random_seed == 1337
    assert settings.deterministic is True
    assert settings.cudnn_benchmark is False
    # Upload limit matches the 10 MB rule used by validation (Req 1.3).
    assert settings.max_upload_bytes == 10 * 1024 * 1024
    # Production extras stay optional so local/demo SQLite + filesystem defaults hold.
    assert settings.database_url == "sqlite:///./hnd.db"
    assert settings.object_store_backend == "filesystem"
    assert settings.redis_url is None
    assert settings.s3_bucket is None
    assert settings.otel_exporter_otlp_endpoint is None
    assert settings.enable_metrics is False


def test_settings_dotenv_path_is_anchored_to_backend_directory():
    expected = Path(__file__).resolve().parents[1] / ".env"
    assert Path(Settings.model_config["env_file"]) == expected


def test_get_settings_is_cached_singleton():
    assert get_settings() is get_settings()


def test_seed_everything_returns_seed_and_is_reproducible():
    seed = seed_everything(42)
    assert seed == 42
    first = [random.random() for _ in range(5)]

    seed_everything(42)
    second = [random.random() for _ in range(5)]

    assert first == second


def test_seed_everything_defaults_to_settings_seed():
    applied = seed_everything()
    assert applied == get_settings().random_seed


def test_configure_determinism_applies_master_seed():
    applied = configure_determinism()
    assert applied == get_settings().random_seed

    # Same seed -> same Python RNG stream after configuration.
    configure_determinism()
    a = [random.random() for _ in range(3)]
    configure_determinism()
    b = [random.random() for _ in range(3)]
    assert a == b
