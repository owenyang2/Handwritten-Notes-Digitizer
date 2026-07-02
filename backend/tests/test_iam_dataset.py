"""Tests for the IAM dataset pipeline and fixed split (task 6.2).

These verify the deterministic split definition and loader without requiring a
licensed copy of IAM:

- the checked-in manifest pins the documented dataset version;
- the train/validation/test split partitions the lines (disjoint + covering);
- loading is deterministic in order and content (sorted by sample id);
- synthetic placeholder images are reproducible and correctly shaped;
- the loader feeds the documented base-model training procedure end-to-end.
"""

from __future__ import annotations

import json

import pytest
import torch

from app.config.settings import Settings
from app.ml.htr_config import HTRModelConfig, TrainingConfig
from app.ml.iam_dataset import (
    DATASET_ROOT_ENV,
    IAM_SPLIT_MANIFEST_PATH,
    PARTITIONS,
    IamSplitManifest,
    load_split_manifest,
    load_training_samples,
    synthetic_image,
)
from app.ml.train_base import (
    DEFAULT_IAM_SPLIT,
    IAM_DATASET_VERSION,
    LabeledSample,
    train_base_model,
)


def test_manifest_pins_documented_dataset_version():
    manifest = load_split_manifest()
    assert isinstance(manifest, IamSplitManifest)
    assert manifest.dataset_version == IAM_DATASET_VERSION
    # The split definition is reused from train_base (SplitSpec).
    assert manifest.split.name == DEFAULT_IAM_SPLIT.name


def test_split_partitions_lines_disjointly_and_completely():
    manifest = load_split_manifest()
    all_ids = set(manifest.sample_ids)
    assert len(all_ids) == len(manifest.sample_ids)  # ids are unique

    partition_id_sets = {p: set(s.sample_id for s in manifest.for_partition(p)) for p in PARTITIONS}

    # Pairwise disjoint: no line leaks across partitions.
    for a in PARTITIONS:
        for b in PARTITIONS:
            if a < b:
                assert partition_id_sets[a].isdisjoint(partition_id_sets[b])

    # Covering: the partitions together account for exactly all lines.
    union = set().union(*partition_id_sets.values())
    assert union == all_ids

    # Counts add up and every partition is non-empty.
    counts = manifest.partition_counts()
    assert sum(counts.values()) == len(manifest)
    assert all(counts[p] > 0 for p in PARTITIONS)


def test_split_is_writer_disjoint():
    # The IAM Aachen convention keeps each writer (form) within one partition.
    manifest = load_split_manifest()

    def form_of(sample_id: str) -> str:
        # sample id is "form-line" -> take everything before the final segment.
        return sample_id.rsplit("-", 1)[0]

    forms_by_partition = {
        p: {form_of(s.sample_id) for s in manifest.for_partition(p)} for p in PARTITIONS
    }
    for a in PARTITIONS:
        for b in PARTITIONS:
            if a < b:
                assert forms_by_partition[a].isdisjoint(forms_by_partition[b])


def test_loading_is_deterministic_in_order_and_content():
    a = load_training_samples(partition="train")
    b = load_training_samples(partition="train")
    assert [s.text for s in a] == [s.text for s in b]
    # Synthetic images are reproducible across loads (same pixels).
    for sa, sb in zip(a, b):
        assert torch.equal(sa.image, sb.image)


def test_samples_are_sorted_by_id():
    manifest = load_split_manifest()
    train_ids = [s.sample_id for s in manifest.for_partition("train")]
    assert train_ids == sorted(train_ids)


def test_default_partition_is_train_and_split_kwarg_accepted():
    # train_base.main calls load_training_samples(split=DEFAULT_IAM_SPLIT).
    via_default = load_training_samples(split=DEFAULT_IAM_SPLIT)
    via_explicit = load_training_samples(split=DEFAULT_IAM_SPLIT, partition="train")
    assert len(via_default) == len(via_explicit)
    assert [s.text for s in via_default] == [s.text for s in via_explicit]


def test_loaded_samples_have_expected_tensor_shape():
    config = HTRModelConfig(image_height=24, input_channels=1)
    samples = load_training_samples(partition="val", config=config)
    assert samples, "validation partition should be non-empty"
    for sample in samples:
        assert isinstance(sample, LabeledSample)
        assert sample.image.ndim == 3  # (C, H, W)
        c, h, _w = sample.image.shape
        assert c == config.input_channels
        assert h == config.image_height
        assert sample.text


def test_synthetic_image_is_reproducible_for_same_seed():
    manifest = load_split_manifest()
    ref = manifest.for_partition("train")[0]
    img1 = synthetic_image(ref, base_seed=1337)
    img2 = synthetic_image(ref, base_seed=1337)
    img3 = synthetic_image(ref, base_seed=42)
    assert torch.equal(img1, img2)
    # A different master seed yields different placeholder pixels.
    assert not torch.equal(img1, img3)


def test_unknown_partition_is_rejected():
    with pytest.raises(ValueError):
        load_training_samples(partition="holdout")


def test_real_images_required_without_root_raises(monkeypatch):
    monkeypatch.delenv(DATASET_ROOT_ENV, raising=False)
    with pytest.raises(FileNotFoundError):
        load_training_samples(partition="train", synthetic=False)


def test_dataset_version_mismatch_is_rejected(tmp_path):
    manifest = {
        "name": "iam-line-split",
        "dataset_version": "IAM-Handwriting-DB-v9.9-lines",
        "split": {"name": "x", "train_fraction": 0.8, "val_fraction": 0.1, "test_fraction": 0.1},
        "provenance": {},
        "samples": [
            {"sample_id": "a01-000-00", "split": "train", "image": "a.png", "text": "hello"}
        ],
    }
    path = tmp_path / "bad_version.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError):
        load_split_manifest(str(path))


def test_invalid_split_label_is_rejected(tmp_path):
    manifest = {
        "name": "iam-line-split",
        "dataset_version": IAM_DATASET_VERSION,
        "split": {"name": "x", "train_fraction": 0.8, "val_fraction": 0.1, "test_fraction": 0.1},
        "provenance": {},
        "samples": [
            {"sample_id": "a01-000-00", "split": "nope", "image": "a.png", "text": "hello"}
        ],
    }
    path = tmp_path / "bad_split.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError):
        load_split_manifest(str(path))


def test_pipeline_feeds_base_model_training(tmp_path):
    # End-to-end: the synthetic IAM stream drives the documented training
    # procedure without any download (a tiny config keeps it fast).
    config = HTRModelConfig(
        image_height=16,
        cnn_channels=(8, 16, 32, 32),
        lstm_hidden_size=16,
        lstm_num_layers=1,
        training=TrainingConfig(batch_size=2, max_epochs=80),
    )
    settings = Settings(object_store_path=str(tmp_path / "objectstore"))
    samples = load_training_samples(
        split=DEFAULT_IAM_SPLIT, partition="train", config=config, settings=settings
    )
    assert samples

    result = train_base_model(
        samples, config=config, max_epochs=1, settings=settings
    )
    assert result.weights_path.exists()
    assert result.provenance.dataset_version == IAM_DATASET_VERSION
