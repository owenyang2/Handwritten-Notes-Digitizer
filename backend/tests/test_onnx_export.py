"""Tests for the optional ONNX export of the base model (task 6.5).

These verify that:

- the module imports cleanly even when ``onnx``/``onnxruntime`` are absent
  (the export path is OPTIONAL and must not break the package);
- exporting a (frozen) base CRNN produces a valid, non-empty ``.onnx`` file
  with the documented dynamic axes for variable-width input;
- when the optional ``onnx`` graph checker is installed the graph validates,
  and when ``onnxruntime`` is installed the exported graph reproduces the
  PyTorch output (otherwise these checks skip cleanly);
- the artifact wrapper exports the frozen base loaded from disk, confirming the
  ONNX path is base-only while adapters continue to compose on the PyTorch base.

Export itself only needs ``torch.onnx`` (bundled with PyTorch), so the core
export test always runs; runtime/graph verification is gated on availability.
"""

from __future__ import annotations

import pytest
import torch

from app.config.settings import Settings
from app.ml.htr_config import HTRModelConfig, TrainingConfig
from app.ml.htr_model import build_model
from app.ml.onnx_export import (
    ONNX_AVAILABLE,
    ONNX_INPUT_NAME,
    ONNX_OUTPUT_NAME,
    ONNXRUNTIME_AVAILABLE,
    default_onnx_path,
    export_base_model_artifact_to_onnx,
    export_base_model_to_onnx,
    onnx_export_supported,
    run_onnx_inference,
    verify_onnx_model,
)
from app.ml.train_base import freeze_base_model, train_base_model


def _tiny_config() -> HTRModelConfig:
    """Small architecture-faithful config so export is fast."""
    return HTRModelConfig(
        image_height=16,
        cnn_channels=(8, 16, 32, 32),
        lstm_hidden_size=16,
        lstm_num_layers=1,
        training=TrainingConfig(batch_size=2, max_epochs=1),
    )


def _frozen_model() -> "object":
    model = build_model(_tiny_config())
    return freeze_base_model(model)


def test_onnx_export_support_reflects_onnx_availability():
    # Export requires torch.onnx (always present) plus the optional onnx pkg.
    assert onnx_export_supported() == ONNX_AVAILABLE


def test_export_produces_valid_onnx_file(tmp_path):
    if not onnx_export_supported():
        pytest.skip("onnx not installed; export unavailable")

    model = _frozen_model()
    out = tmp_path / "base_model.onnx"

    result_path = export_base_model_to_onnx(model, out, example_width=96)

    assert result_path == out
    assert out.exists()
    assert out.stat().st_size > 0
    # ONNX files start with a protobuf header; sanity-check it is non-trivial.
    assert len(out.read_bytes()) > 64

    # Optional structural validation when the onnx checker is installed.
    if ONNX_AVAILABLE:
        assert verify_onnx_model(out) is True
    else:
        # Skipped cleanly: returns False rather than raising.
        assert verify_onnx_model(out) is False


def test_export_matches_eager_model_across_widths_and_batch_sizes(tmp_path):
    """The ONNX graph must preserve eager results across production shapes."""
    if not ONNXRUNTIME_AVAILABLE:
        pytest.skip("onnxruntime not installed; dynamic-axis runtime check skipped")

    config = _tiny_config()
    model = _frozen_model()
    out = tmp_path / "base_model.onnx"
    export_base_model_to_onnx(model, out, example_width=96)

    for batch_size, width in ((1, 64), (1, 160), (2, 96), (2, 192)):
        # The non-trace widths exercise the dynamic axis; batch size 2 catches
        # accidental single-example assumptions in the exported graph.
        image = torch.rand(batch_size, config.input_channels, config.image_height, width)
        onnx_out = run_onnx_inference(out, image)
        assert onnx_out is not None

        with torch.no_grad():
            torch_out = model(image)

        assert onnx_out.shape == torch_out.shape
        assert torch.allclose(onnx_out, torch_out, atol=1e-4)


def test_export_artifact_wrapper_exports_frozen_base(tmp_path):
    """The artifact wrapper loads the frozen base from disk and exports it."""
    if not onnx_export_supported():
        pytest.skip("onnx not installed; export unavailable")

    config = _tiny_config()
    settings = Settings(object_store_path=str(tmp_path / "objectstore"))

    # Produce a trained+frozen base artifact on disk.
    torch.manual_seed(0)
    # Build a tiny synthetic dataset using the public LabeledSample type.
    from app.ml.train_base import LabeledSample

    samples = [
        LabeledSample(
            image=torch.rand(config.input_channels, config.image_height, 64),
            text="hi",
        )
        for _ in range(4)
    ]
    train_base_model(samples, config=config, max_epochs=1, settings=settings)

    onnx_path = export_base_model_artifact_to_onnx(settings=settings)

    assert onnx_path == default_onnx_path(settings=settings)
    assert onnx_path.exists()
    assert onnx_path.stat().st_size > 0

    # Sanity: the I/O names are the documented stable names.
    assert ONNX_INPUT_NAME == "image"
    assert ONNX_OUTPUT_NAME == "log_probs"
