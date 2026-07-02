"""Optional ONNX export of the shared, frozen ``Base_Model`` (task 6.5).

The CRNN base model (CNN + BiLSTM + CTC) is trained and frozen by
:mod:`app.ml.train_base`. For *portable, faster* inference it may optionally be
exported to the ONNX interchange format (Req 11.2). ONNX export is a
convenience for deployment targets that prefer a runtime-agnostic graph; the
canonical artifact remains the PyTorch checkpoint.

Why this module is optional
---------------------------
The ``onnx`` / ``onnxruntime`` packages are **not** required to use the rest of
the ML package. ``torch.onnx.export`` ships inside PyTorch, so producing an
``.onnx`` file needs no extra dependency. The optional ``onnx`` (graph checker)
and ``onnxruntime`` (inference verification) imports are guarded so their
absence never breaks importing this module or the wider package. Availability
is exposed via :data:`ONNX_AVAILABLE` and :data:`ONNXRUNTIME_AVAILABLE`.

Exported signature
------------------
The exported graph mirrors :meth:`app.ml.htr_model.CRNN.forward`:

- **Input** ``"image"``: float tensor ``(N, C, H, W)`` — a batch of normalised
  grayscale line images. ``C`` and ``H`` are fixed by the pinned config
  (``input_channels`` and ``image_height``); ``N`` (batch) and ``W`` (width)
  are *dynamic*.
- **Output** ``"log_probs"``: float tensor ``(T, N, num_classes_with_blank)`` —
  per-timestep CTC log-probabilities. ``T`` (the CTC time axis, derived from the
  input width) and ``N`` are *dynamic*.

The width ``W`` and the resulting time axis ``T`` are declared as dynamic axes
so a single exported graph handles variable-width inputs, exactly as the
PyTorch model does.

Adapters and the frozen base
-----------------------------
Personalization composes a small per-user adapter on top of the **frozen** base
at inference time (design: "Frozen base + pluggable adapter"; Req 5.4, 5.5).
ONNX export captures the **base model only**: the exported graph is the shared
frozen base, identical for every user. Adapters are *not* baked into the ONNX
graph. This keeps the "one shared base + one small adapter per user" model
intact:

- The ONNX graph is a base-only, adapter-free inference path (use it when no
  personalization is needed, or as a fast general-purpose recognizer).
- Per-user adapter composition continues to happen on the PyTorch base at
  recognition time (the HTR_Engine path), so adapters still compose with the
  frozen base at load time exactly as before.

Because the export reads weights from the frozen base checkpoint (which
training never mutates further), re-exporting after an adapter is trained yields
the same base graph — adapters never alter the exported base.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import torch

from app.config.settings import Settings, get_settings
from app.ml.htr_model import CRNN
from app.ml.train_base import default_model_dir, load_base_model

# ---------------------------------------------------------------------------
# Optional-dependency probing (never raises at import time)
# ---------------------------------------------------------------------------


def _module_available(name: str) -> bool:
    """True if *name* can be imported, without importing it eagerly."""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):  # pragma: no cover - defensive
        return False


# ``onnx`` provides graph validation (onnx.checker); ``onnxruntime`` provides a
# runtime to numerically verify the exported graph. Both are OPTIONAL — their
# absence simply disables the corresponding verification step.
ONNX_AVAILABLE: bool = _module_available("onnx")
ONNXRUNTIME_AVAILABLE: bool = _module_available("onnxruntime")

# Default output filename for the exported base-model graph, written next to the
# PyTorch artifact so deployment assets are co-located.
ONNX_FILENAME: str = "base_model.onnx"

# Stable I/O names for the exported graph (documented signature above).
ONNX_INPUT_NAME: str = "image"
ONNX_OUTPUT_NAME: str = "log_probs"

# ONNX opset: 14 supports LSTM and dynamic shapes used by the CRNN and is widely
# supported by onnxruntime releases.
DEFAULT_OPSET: int = 14


def onnx_export_supported() -> bool:
    """Whether ONNX export can run at all.

    Export uses ``torch.onnx`` (bundled with PyTorch) to trace the model, but
    PyTorch's exporter serialises the traced graph through the ``onnx`` package,
    so a working export additionally requires ``onnx`` to be installed. Runtime
    numerical *verification* further needs :data:`ONNXRUNTIME_AVAILABLE`.
    """
    return _module_available("torch.onnx") and ONNX_AVAILABLE


def default_onnx_path(
    output_dir: Path | None = None, *, settings: Settings | None = None
) -> Path:
    """Documented path for the exported base-model ONNX file."""
    settings = settings or get_settings()
    output_dir = output_dir or default_model_dir(settings)
    return Path(output_dir) / ONNX_FILENAME


def _example_input(model: CRNN, *, batch_size: int = 1, width: int = 128) -> torch.Tensor:
    """Build a representative tracing input matching the model's fixed dims."""
    cfg = model.config
    # Width must be large enough for the CNN pooling plan to leave a non-empty
    # time axis; 128px comfortably exceeds the height-collapse budget.
    return torch.zeros(batch_size, cfg.input_channels, cfg.image_height, max(width, 32))


def export_base_model_to_onnx(
    model: CRNN,
    output_path: Path | str,
    *,
    opset: int = DEFAULT_OPSET,
    example_width: int = 128,
) -> Path:
    """Export a (frozen) base ``CRNN`` to ONNX with dynamic width/time axes.

    Parameters
    ----------
    model:
        The base model to export. It is switched to eval mode for a
        deterministic, dropout-free graph. Only the base is exported — adapters
        are applied separately on the PyTorch path (see module docstring).
    output_path:
        Destination ``.onnx`` file. Parent directories are created.
    opset:
        ONNX opset version (defaults to :data:`DEFAULT_OPSET`).
    example_width:
        Width of the tracing input. The width axis is exported as dynamic, so
        this value does not constrain inference; it only needs to be large
        enough to produce a valid trace.

    Returns
    -------
    Path
        The path to the written ONNX file.
    """
    if not onnx_export_supported():
        raise RuntimeError(
            "ONNX export is unavailable: torch.onnx requires the optional "
            "'onnx' package to serialise the graph. Install onnx to enable "
            "export (see requirements.txt optional ONNX section)."
        )

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    model.eval()
    example = _example_input(model, width=example_width)

    # Dynamic axes let one graph serve variable-width inputs: the input width
    # (axis 3) and the derived CTC time axis on the output (axis 0), plus the
    # batch dimension on both tensors.
    dynamic_axes = {
        ONNX_INPUT_NAME: {0: "batch", 3: "width"},
        ONNX_OUTPUT_NAME: {0: "time", 1: "batch"},
    }

    with torch.no_grad():
        torch.onnx.export(
            model,
            example,
            str(output_path),
            input_names=[ONNX_INPUT_NAME],
            output_names=[ONNX_OUTPUT_NAME],
            dynamic_axes=dynamic_axes,
            opset_version=opset,
            do_constant_folding=True,
        )

    return output_path


def export_base_model_artifact_to_onnx(
    artifact_dir: Path | None = None,
    output_path: Path | str | None = None,
    *,
    settings: Settings | None = None,
    opset: int = DEFAULT_OPSET,
    example_width: int = 128,
) -> Path:
    """Load the frozen base-model artifact and export it to ONNX.

    Convenience wrapper that reads the trained base model from its artifact
    directory (defaults to :func:`app.ml.train_base.default_model_dir`) and
    writes the ONNX graph beside it (defaults to :func:`default_onnx_path`).
    """
    settings = settings or get_settings()
    model = load_base_model(artifact_dir, settings=settings)
    if output_path is None:
        output_path = default_onnx_path(artifact_dir, settings=settings)
    return export_base_model_to_onnx(
        model, output_path, opset=opset, example_width=example_width
    )


def verify_onnx_model(onnx_path: Path | str) -> bool:
    """Best-effort validation of an exported ONNX graph.

    Runs ``onnx.checker.check_model`` when ``onnx`` is installed. Returns
    ``True`` when the graph is structurally valid, ``False`` when ``onnx`` is
    unavailable (verification skipped). Raises if the graph is present but
    invalid.
    """
    if not ONNX_AVAILABLE:
        return False
    import onnx  # local import keeps the dependency optional

    onnx_model = onnx.load(str(onnx_path))
    onnx.checker.check_model(onnx_model)
    return True


def run_onnx_inference(
    onnx_path: Path | str, image: torch.Tensor
) -> "torch.Tensor | None":
    """Run the exported graph through onnxruntime, if available.

    Returns the ``log_probs`` output as a tensor, or ``None`` when
    ``onnxruntime`` is not installed (so callers can skip numerical
    verification gracefully).
    """
    if not ONNXRUNTIME_AVAILABLE:
        return None
    import numpy as np  # noqa: WPS433 - optional path
    import onnxruntime as ort  # local import keeps the dependency optional

    session = ort.InferenceSession(
        str(onnx_path), providers=["CPUExecutionProvider"]
    )
    outputs = session.run(
        [ONNX_OUTPUT_NAME],
        {ONNX_INPUT_NAME: image.detach().cpu().numpy().astype(np.float32)},
    )
    return torch.from_numpy(outputs[0])


__all__ = [
    "ONNX_AVAILABLE",
    "ONNXRUNTIME_AVAILABLE",
    "ONNX_FILENAME",
    "ONNX_INPUT_NAME",
    "ONNX_OUTPUT_NAME",
    "DEFAULT_OPSET",
    "onnx_export_supported",
    "default_onnx_path",
    "export_base_model_to_onnx",
    "export_base_model_artifact_to_onnx",
    "verify_onnx_model",
    "run_onnx_inference",
]
