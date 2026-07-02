"""Tests for the deterministic base-model training procedure (task 6.3).

These exercise the full documented procedure on a *tiny synthetic* dataset so it
runs in seconds and never downloads IAM. They verify that:

- training runs and writes a frozen artifact to the documented path;
- the saved provenance documents dataset version, split, and training config;
- the produced base model is frozen (no trainable parameters);
- a reload reconstructs an equivalent frozen model;
- the procedure is reproducible: same seed/config/data => identical weights
  (Req 11.3);
- text encoding maps to the CTC label space (blank index reserved).
"""

from __future__ import annotations

import json

import torch

from app.config.settings import Settings
from app.ml.htr_config import ALPHABET, HTRModelConfig, TrainingConfig
from app.ml.train_base import (
    DEFAULT_IAM_SPLIT,
    IAM_DATASET_VERSION,
    LabeledSample,
    SplitSpec,
    default_model_dir,
    encode_text,
    load_base_model,
    train_base_model,
)


def _tiny_config() -> HTRModelConfig:
    """A small but architecture-faithful config so the smoke test is fast."""
    # Four CNN stages collapse the 16px height to 1 (16->8->4->2->1) while the
    # width (CTC time axis) is preserved after the first two stages.
    return HTRModelConfig(
        image_height=16,
        cnn_channels=(8, 16, 32, 32),
        lstm_hidden_size=16,
        lstm_num_layers=1,
        training=TrainingConfig(batch_size=2, max_epochs=80),
    )


def _synthetic_dataset(config: HTRModelConfig, n: int = 6) -> list[LabeledSample]:
    """Build a tiny deterministic synthetic dataset of line images + labels."""
    torch.manual_seed(0)
    words = ["cat", "dog", "Hi", "note", "the", "a"]
    samples: list[LabeledSample] = []
    for i in range(n):
        width = 48 + (i % 3) * 16
        image = torch.rand(config.input_channels, config.image_height, width)
        samples.append(LabeledSample(image=image, text=words[i % len(words)]))
    return samples


def _test_settings(tmp_path) -> Settings:
    return Settings(object_store_path=str(tmp_path / "objectstore"))


def test_encode_text_maps_to_ctc_label_space():
    # Blank is index 0; characters map to 1..len(ALPHABET).
    indices = encode_text("abc")
    assert all(1 <= idx <= len(ALPHABET) for idx in indices)
    assert 0 not in indices
    # Characters outside the alphabet are dropped (no crash, no blank emitted).
    assert encode_text("a\tb") == encode_text("ab")
    assert encode_text("") == []


def test_split_spec_validates_fractions():
    # Fractions must sum to 1.0.
    SplitSpec(train_fraction=0.7, val_fraction=0.2, test_fraction=0.1)
    try:
        SplitSpec(train_fraction=0.5, val_fraction=0.2, test_fraction=0.2)
    except ValueError:
        pass
    else:  # pragma: no cover - defensive
        raise AssertionError("expected ValueError for non-unit split fractions")


def test_train_base_model_writes_frozen_artifact(tmp_path):
    config = _tiny_config()
    settings = _test_settings(tmp_path)
    dataset = _synthetic_dataset(config)

    result = train_base_model(
        dataset,
        config=config,
        max_epochs=2,
        settings=settings,
    )

    # Artifact written to the documented path under the object store root.
    expected_dir = default_model_dir(settings)
    assert result.weights_path == expected_dir / "base_model.pt"
    assert result.weights_path.exists()
    assert result.provenance_path.exists()

    # The produced base model is frozen (Req 5.2 — base never trains further).
    assert all(not p.requires_grad for p in result.model.parameters())
    assert not result.model.training  # eval mode

    # Training actually ran for the requested epochs and produced loss values.
    assert len(result.epoch_losses) == 2
    assert all(torch.isfinite(torch.tensor(l)) for l in result.epoch_losses)


def test_provenance_documents_dataset_split_and_config(tmp_path):
    config = _tiny_config()
    settings = _test_settings(tmp_path)
    dataset = _synthetic_dataset(config)

    result = train_base_model(
        dataset,
        config=config,
        max_epochs=1,
        settings=settings,
    )

    meta = json.loads(result.provenance_path.read_text(encoding="utf-8"))
    # Dataset version (Req 11.2).
    assert meta["dataset_version"] == IAM_DATASET_VERSION
    # Split definition (Req 11.2).
    assert meta["split"]["name"] == DEFAULT_IAM_SPLIT.name
    assert abs(
        meta["split"]["train_fraction"]
        + meta["split"]["val_fraction"]
        + meta["split"]["test_fraction"]
        - 1.0
    ) < 1e-6
    # Training configuration (Req 11.2).
    assert meta["training_config"]["learning_rate"] == config.training.learning_rate
    assert meta["training_config"]["batch_size"] == config.training.batch_size
    # Seed and framework recorded for reproducibility provenance (Req 11.3).
    assert meta["seed"] == settings.random_seed
    assert "torch" in meta["framework"]
    # Model config round-trips back to an equivalent config.
    assert HTRModelConfig.from_dict(meta["model_config"]) == config


def test_loaded_model_matches_trained_model(tmp_path):
    config = _tiny_config()
    settings = _test_settings(tmp_path)
    dataset = _synthetic_dataset(config)

    result = train_base_model(
        dataset, config=config, max_epochs=1, settings=settings
    )
    reloaded = load_base_model(settings=settings)

    # Frozen on load, and weights match exactly.
    assert all(not p.requires_grad for p in reloaded.parameters())
    trained_state = result.model.state_dict()
    for key, value in reloaded.state_dict().items():
        assert torch.equal(value, trained_state[key])


def test_training_is_reproducible_with_fixed_seed(tmp_path):
    # Re-running with the same seed/config/data yields identical weights (Req 11.3).
    config = _tiny_config()
    dataset = _synthetic_dataset(config)

    settings_a = _test_settings(tmp_path / "run_a")
    settings_b = _test_settings(tmp_path / "run_b")

    result_a = train_base_model(
        dataset, config=config, max_epochs=2, settings=settings_a
    )
    result_b = train_base_model(
        dataset, config=config, max_epochs=2, settings=settings_b
    )

    state_a = result_a.model.state_dict()
    state_b = result_b.model.state_dict()
    assert state_a.keys() == state_b.keys()
    for key in state_a:
        assert torch.equal(state_a[key], state_b[key]), f"mismatch in {key}"
    # Loss trajectory is identical too.
    assert result_a.epoch_losses == result_b.epoch_losses


def test_empty_dataset_raises(tmp_path):
    config = _tiny_config()
    settings = _test_settings(tmp_path)
    try:
        train_base_model([], config=config, max_epochs=1, settings=settings)
    except ValueError:
        pass
    else:  # pragma: no cover - defensive
        raise AssertionError("expected ValueError for empty dataset")
