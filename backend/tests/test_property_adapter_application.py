"""Property test for adapter application in the HTR recognition engine (task 7.5).

Design property under test:

    Property 14: Recognition applies the adapter exactly when one exists

    *For any* recognition request, the engine composes the user's adapter with
    the base model if and only if that user has an active adapter; otherwise it
    uses the base model alone.

**Validates: Requirements 5.4, 5.5**

This is a single Hypothesis property test running ≥100 iterations (the project's
``default`` profile; see ``tests/conftest.py`` and ``docs/development/testing.md``).

Strategy
--------
We test the property at the level of ``_compose_model`` (the internal
composition gate in ``app.ml.recognition``) and ``apply_adapter`` (the adapter
weight substitution), which are the exact mechanisms that enforce Req 5.4 and
5.5. Using these functions directly – rather than the full ``recognize()``
pipeline – lets us run 100+ iterations cheaply without loading or running
images through the full CNN.

For each generated example we:

* Build a **tiny fake CRNN** from a minimal ``HTRModelConfig``
  (tiny image height, minimal channels, one LSTM layer) so model construction
  is fast and cheap.
* Either provide **no adapter** (``None``) or a **fake adapter** constructed
  from a known perturbation of the base model's classifier weights.
* Assert the two halves of the property:
  - When adapter is None  → ``_compose_model`` returns *the same object* as the
    base (Req 5.5: base model alone, no copy).
  - When adapter is given  → ``_compose_model`` returns a *different* object
    whose classifier weight matches the adapter's weights exactly, while the
    base model's classifier weight is *unchanged* (Req 5.4: adapter composed,
    base frozen).

Small fake models
-----------------
``TINY_CONFIG`` uses the smallest architecture that still exercises the full
CNN → BiLSTM → classifier path:
  * ``image_height=8``  — 3 pooling stages bring height to 1
  * ``cnn_channels=(4, 4, 4)`` — just enough channels for shape correctness
  * ``lstm_hidden_size=8`` — minimal BiLSTM
  * ``lstm_num_layers=1`` — no dropout path (faster)

This keeps ``build_model()`` well under 1 ms per call on CPU.
"""

from __future__ import annotations

import io
import tempfile
import os

import torch
from hypothesis import given, settings
from hypothesis import strategies as st

from app.ml.htr_config import HTRModelConfig, TrainingConfig, AugmentationConfig
from app.ml.htr_model import CRNN, build_model
from app.ml.recognition import apply_adapter, _compose_model


# ---------------------------------------------------------------------------
# Tiny fake CRNN configuration — cheap for 100+ iterations
# ---------------------------------------------------------------------------

TINY_CONFIG = HTRModelConfig(
    image_height=8,
    input_channels=1,
    cnn_channels=(4, 4, 4),
    lstm_hidden_size=8,
    lstm_num_layers=1,
    lstm_dropout=0.0,
    bidirectional=True,
    # num_classes left at default (full alphabet + blank) so the classifier
    # layer is the same shape as in production – the property is about
    # weight substitution, so this must match.
    training=TrainingConfig(),
    augmentation=AugmentationConfig(enabled=False),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_tiny_model() -> CRNN:
    """Return a freshly initialised CRNN using the tiny fake config."""
    return build_model(TINY_CONFIG)


def _make_adapter_bytes(model: CRNN, weight_offset: float = 0.0) -> bytes:
    """Serialise classifier weights (+ optional offset) as an adapter payload.

    The ``weight_offset`` lets callers create a distinctly different adapter
    so the property test can distinguish base weights from adapter weights.
    """
    adapter_state = {
        "classifier.weight": model.classifier.weight.detach().clone() + weight_offset,
        "classifier.bias": model.classifier.bias.detach().clone(),
    }
    buf = io.BytesIO()
    torch.save(adapter_state, buf)
    return buf.getvalue()


class _FileAdapter:
    """Minimal object that looks like the ORM ``Adapter`` from ``_compose_model``'s perspective."""

    def __init__(self, weights_path: str) -> None:
        self.weights_path = weights_path


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

# Draw a float offset for the adapter's classifier.weight so that we can
# verify the adapter's weights are applied and are distinct from the base.
# Exclude zero so that the test can always tell apart base and adapter weights.
nonzero_float_strategy = st.floats(
    min_value=0.5,
    max_value=10.0,
    allow_nan=False,
    allow_infinity=False,
)

# Strategy for "has adapter" vs "no adapter" scenarios
adapter_present_strategy = st.booleans()


# ---------------------------------------------------------------------------
# Property 14: Recognition applies the adapter exactly when one exists
# ---------------------------------------------------------------------------

# Feature: handwriting-notes-digitizer, Property 14: Recognition applies the adapter exactly when one exists
@given(
    has_adapter=adapter_present_strategy,
    weight_offset=nonzero_float_strategy,
)
def test_property_adapter_application(
    has_adapter: bool,
    weight_offset: float,
) -> None:
    """For any recognition request, the engine composes the user's adapter with
    the base model if and only if that user has an active adapter; otherwise it
    uses the base model alone.

    **Validates: Requirements 5.4, 5.5**
    """
    base = _build_tiny_model()
    base_weight_before = base.classifier.weight.detach().clone()
    base_bias_before = base.classifier.bias.detach().clone()

    if not has_adapter:
        # --- Req 5.5: no adapter → base model is used unchanged ---------------
        result = _compose_model(base, None)

        # The exact same object must be returned (no unnecessary copy).
        assert result is base, (
            "When no adapter is present, _compose_model must return the base "
            "model unchanged (Req 5.5)."
        )
        # The base model's weights must be completely unmodified.
        assert torch.equal(result.classifier.weight, base_weight_before), (
            "Base model classifier weight was mutated even though no adapter was applied."
        )
        assert torch.equal(result.classifier.bias, base_bias_before), (
            "Base model classifier bias was mutated even though no adapter was applied."
        )

    else:
        # --- Req 5.4: adapter present → adapter weights composed in ----------
        adapter_bytes = _make_adapter_bytes(base, weight_offset=weight_offset)

        with tempfile.NamedTemporaryFile(suffix=".pt", delete=False) as fh:
            fh.write(adapter_bytes)
            tmp_path = fh.name

        try:
            fake_adapter = _FileAdapter(tmp_path)
            result = _compose_model(base, fake_adapter)

            # Must return a *different* object (the base is never mutated in place).
            assert result is not base, (
                "When an adapter is provided, _compose_model must return a new "
                "composed model, not the original base (Req 5.4)."
            )

            # The composed model's classifier weight must equal the adapter's weight
            # (base weight + weight_offset), not the base weight.
            expected_weight = base_weight_before + weight_offset
            assert torch.allclose(result.classifier.weight, expected_weight), (
                "Composed model's classifier weight does not match the adapter's "
                "weight. The adapter was not applied correctly (Req 5.4)."
            )

            # The base model's classifier weight must remain unchanged.
            assert torch.equal(base.classifier.weight, base_weight_before), (
                "The base model's classifier weight was mutated by adapter "
                "composition (violates Req 5.2 / 5.4 contract)."
            )
            assert torch.equal(base.classifier.bias, base_bias_before), (
                "The base model's classifier bias was mutated by adapter composition."
            )

            # Non-classifier parameters (CNN, LSTM) in the composed model must
            # equal those of the base (only the last layer is adapted).
            for name, base_param in base.cnn.named_parameters():
                adapted_param = dict(result.cnn.named_parameters())[name]
                assert torch.equal(base_param, adapted_param), (
                    f"CNN parameter '{name}' changed after adapter composition; "
                    "only the classifier layer should differ (Req 5.4)."
                )
            for name, base_param in base.lstm.named_parameters():
                adapted_param = dict(result.lstm.named_parameters())[name]
                assert torch.equal(base_param, adapted_param), (
                    f"LSTM parameter '{name}' changed after adapter composition; "
                    "only the classifier layer should differ (Req 5.4)."
                )
        finally:
            os.unlink(tmp_path)
