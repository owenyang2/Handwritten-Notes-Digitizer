"""Standalone property-based test for Property 12: Base model parameters are unchanged by training.

# Feature: handwriting-notes-digitizer, Property 12: Base model parameters are unchanged by training

For any adapter training run with any combination of adapter initialization
config and training step count, the shared base model parameters must be
bit-for-bit identical before and after training.

**Validates: Requirements 5.2**

Background
----------
Requirement 5.2 states that the Personalization_Service SHALL train each
Adapter using parameter-efficient fine-tuning that updates only the Adapter
parameters while keeping the Base_Model parameters unchanged.

This property exercises the core invariant: ``train_adapter`` deep-copies the
classifier head into an independent trainable layer and never touches
``base_model`` itself. We verify this holds across:

* Different random seeds (affecting weight init copy and data shuffling).
* Different numbers of training pairs and held-out pairs.
* Different epoch counts (1..5 to keep iterations fast).
* Different learning rates.
* Different batch sizes.

Strategy
--------
We use a **tiny fake CRNN** (minimal channels, height, LSTM size) so that
building and training a model takes well under a millisecond. A single
training call is therefore fast enough for 100+ Hypothesis iterations.

The config used here — ``TINY_CONFIG`` — mirrors the one in
``test_property_adapter_application.py``:

* ``image_height=16``, ``input_channels=1``
* ``cnn_channels=(8, 16, 16, 32)`` — 4 pooling stages collapse height to 1
* ``lstm_hidden_size=16``, ``lstm_num_layers=1`` (no dropout path)
* ``bidirectional=True``

This is the smallest CRNN that exercises the full CNN → BiLSTM → CTC path.

Assertion
---------
After every ``train_adapter`` call we compare every named parameter in the
base model (by name and value) with a snapshot taken before training:

1. **Values**: ``torch.equal(before, after)`` — bit-for-bit identical.
2. **Gradient flag**: ``requires_grad`` must match the pre-training value
   (all False for a frozen base model).
"""

from __future__ import annotations

import torch
from hypothesis import given, settings
from hypothesis import strategies as st

from app.ml.adapter_trainer import train_adapter
from app.ml.htr_config import AugmentationConfig, HTRModelConfig, TrainingConfig
from app.ml.htr_model import CRNN, build_model
from app.ml.train_base import LabeledSample, freeze_base_model


# ---------------------------------------------------------------------------
# Tiny fake CRNN configuration — cheap for 100+ iterations
# ---------------------------------------------------------------------------

TINY_CONFIG = HTRModelConfig(
    image_height=16,
    input_channels=1,
    cnn_channels=(8, 16, 16, 32),
    lstm_hidden_size=16,
    lstm_num_layers=1,
    lstm_dropout=0.0,
    bidirectional=True,
    training=TrainingConfig(max_epochs=5, batch_size=4),
    augmentation=AugmentationConfig(enabled=False),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_fake_base_model() -> CRNN:
    """Build a tiny frozen CRNN that acts as the shared base model."""
    model = build_model(TINY_CONFIG)
    return freeze_base_model(model)


def _make_sample(width: int = 32) -> LabeledSample:
    """Return a synthetic LabeledSample with a random grayscale image."""
    image = torch.rand(
        TINY_CONFIG.input_channels,
        TINY_CONFIG.image_height,
        width,
    )
    return LabeledSample(image=image, text="hi")


def _make_pairs(n: int, width_offset: int = 0) -> list[LabeledSample]:
    """Generate *n* synthetic training samples with slightly varied widths."""
    return [_make_sample(width=32 + width_offset + i) for i in range(n)]


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

# Seed controls Python / NumPy / PyTorch RNGs inside train_adapter.
seed_strategy = st.integers(min_value=0, max_value=2**31 - 1)

# Training-set sizes: at least 1, at most 6 (fast training).
n_train_strategy = st.integers(min_value=1, max_value=6)

# Held-out set sizes: at least 1, at most 4.
n_held_strategy = st.integers(min_value=1, max_value=4)

# Epoch counts: 1–5, so each generated iteration stays under ~1 s.
max_epochs_strategy = st.integers(min_value=1, max_value=5)

# Learning rates: small positive floats — exercises different update magnitudes.
learning_rate_strategy = st.floats(
    min_value=1e-5,
    max_value=1e-1,
    allow_nan=False,
    allow_infinity=False,
)

# Batch sizes: 1..4 (capped at len(training_pairs) inside train_adapter).
batch_size_strategy = st.integers(min_value=1, max_value=4)


# ---------------------------------------------------------------------------
# Property 12: Base model parameters are unchanged by training
# ---------------------------------------------------------------------------

# Feature: handwriting-notes-digitizer, Property 12: Base model parameters are unchanged by training
@given(
    seed=seed_strategy,
    n_train=n_train_strategy,
    n_held=n_held_strategy,
    max_epochs=max_epochs_strategy,
    learning_rate=learning_rate_strategy,
    batch_size=batch_size_strategy,
)
@settings(max_examples=25, deadline=None)
def test_property_12_base_model_parameters_unchanged_by_training(
    seed: int,
    n_train: int,
    n_held: int,
    max_epochs: int,
    learning_rate: float,
    batch_size: int,
) -> None:
    """Property 12: Base model parameters are unchanged by training.

    For any training run — regardless of the random seed, training-set size,
    held-out size, epoch count, learning rate, or batch size — the shared
    base model's named parameters must be **bit-for-bit identical** before
    and after ``train_adapter`` returns.

    Additionally, the ``requires_grad`` flag on every base parameter must
    remain exactly as it was before training (all False for a frozen base).

    **Validates: Requirements 5.2**
    """
    base_model = _build_fake_base_model()

    # ------------------------------------------------------------------
    # 1. Snapshot every base parameter before training.
    # ------------------------------------------------------------------
    before_values: dict[str, torch.Tensor] = {
        name: param.detach().clone()
        for name, param in base_model.named_parameters()
    }
    before_requires_grad: dict[str, bool] = {
        name: param.requires_grad
        for name, param in base_model.named_parameters()
    }

    # ------------------------------------------------------------------
    # 2. Build training and held-out pairs with different width offsets
    #    so the two sets produce distinct samples.
    # ------------------------------------------------------------------
    training_pairs = _make_pairs(n=n_train, width_offset=0)
    held_out_pairs = _make_pairs(n=n_held, width_offset=20)

    # ------------------------------------------------------------------
    # 3. Run the adapter trainer (the core operation under test).
    # ------------------------------------------------------------------
    train_adapter(
        base_model,
        training_pairs=training_pairs,
        held_out_pairs=held_out_pairs,
        seed=seed,
        max_epochs=max_epochs,
        learning_rate=learning_rate,
        batch_size=batch_size,
        patience=100,  # disable early stopping — we want max_epochs to run
    )

    # ------------------------------------------------------------------
    # 4. Assert: every base parameter is bit-for-bit identical.
    # ------------------------------------------------------------------
    for name, before_val in before_values.items():
        after_val = dict(base_model.named_parameters())[name].detach()
        assert torch.equal(before_val, after_val), (
            f"[Property 12] Base model parameter '{name}' was mutated by "
            f"train_adapter (seed={seed}, n_train={n_train}, n_held={n_held}, "
            f"max_epochs={max_epochs}, lr={learning_rate:.2e}, "
            f"batch_size={batch_size}). "
            f"Requirement 5.2 violated: the Base_Model parameters must not "
            f"change during adapter training."
        )

    # ------------------------------------------------------------------
    # 5. Assert: requires_grad flags are unchanged.
    # ------------------------------------------------------------------
    for name, was_frozen in before_requires_grad.items():
        after_grad = dict(base_model.named_parameters())[name].requires_grad
        assert after_grad == was_frozen, (
            f"[Property 12] Base model parameter '{name}' had requires_grad "
            f"changed from {was_frozen} to {after_grad} by train_adapter. "
            f"The base model must remain frozen throughout training "
            f"(Requirement 5.2)."
        )
