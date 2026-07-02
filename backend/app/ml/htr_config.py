"""Pinned configuration for the HTR_Engine base model (CNN + BiLSTM + CTC).

This module is the single, version-controlled source of truth for the base
model's architecture and training hyper-parameters. Pinning these values here
(rather than scattering literals through the training code) is what makes the
documented training procedure reproducible: re-running with the same config and
seed yields a base model whose accuracy is within tolerance of the previous run
(Requirements 11.2, 11.3).

The configuration is expressed as frozen dataclasses so values are immutable at
runtime, and a single canonical default (``DEFAULT_HTR_CONFIG``) is provided.
Use :func:`HTRModelConfig.to_dict` / :func:`HTRModelConfig.from_dict` to
serialise the config alongside a trained artifact for provenance.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Tuple


# The CTC alphabet for English handwriting recognition. Index 0 is reserved for
# the CTC "blank" symbol and is therefore NOT part of this string. The model's
# output class count is ``len(ALPHABET) + 1`` (characters + blank).
ALPHABET: str = (
    " !\"#&'()*+,-./0123456789:;?"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz"
)

# CTC blank occupies index 0 of the output distribution.
BLANK_INDEX: int = 0


@dataclass(frozen=True)
class AugmentationConfig:
    """Pinned data-augmentation settings used during base-model training.

    Augmentation is applied only at training time to improve robustness of the
    handwriting recognizer; it never alters evaluation inputs. Probabilities are
    in ``[0, 1]`` and magnitudes are in input-image units (degrees / pixels /
    ratios) so the configuration is self-describing.
    """

    enabled: bool = True
    # Small affine rotation to mimic natural slant variation.
    rotation_degrees: float = 3.0
    # Random horizontal/vertical translation as a fraction of image size.
    translate_fraction: float = 0.03
    # Random scale range (min, max) applied uniformly.
    scale_range: Tuple[float, float] = (0.9, 1.1)
    # Random shear in degrees to emulate cursive slant.
    shear_degrees: float = 5.0
    # Additive Gaussian noise standard deviation (on a 0..1 normalized image).
    gaussian_noise_std: float = 0.02
    # Probability that elastic distortion is applied to a sample.
    elastic_distortion_prob: float = 0.2


@dataclass(frozen=True)
class TrainingConfig:
    """Pinned optimisation hyper-parameters for base-model training.

    These values drive the deterministic training script (task 6.3) and the
    early-stopping guardrails are enforced separately by the personalization
    service (Req 6.5). ``max_epochs`` here is the base-model budget, distinct
    from the per-user adapter epoch cap.
    """

    learning_rate: float = 3e-4
    batch_size: int = 32
    max_epochs: int = 80
    weight_decay: float = 1e-5
    grad_clip_norm: float = 5.0
    # Adam-family optimiser betas; pinned for reproducibility.
    optimizer_betas: Tuple[float, float] = (0.9, 0.999)
    # Linear warmup steps before the main schedule.
    warmup_steps: int = 500


@dataclass(frozen=True)
class HTRModelConfig:
    """Pinned architecture configuration for the CNN + BiLSTM + CTC network.

    The CNN feature extractor reduces a fixed-height grayscale line image to a
    sequence of feature vectors (one per horizontal step); the BiLSTM models the
    sequence context; a linear CTC head projects each step to a distribution
    over the alphabet plus the blank symbol.
    """

    # --- Input ---------------------------------------------------------------
    # Line images are normalised to this height (pixels); width is variable.
    image_height: int = 32
    input_channels: int = 1  # grayscale

    # --- CNN feature extractor ----------------------------------------------
    # Output channels for each successive convolutional stage.
    cnn_channels: Tuple[int, ...] = (64, 128, 256, 256, 512, 512, 512)

    # --- BiLSTM sequence model ----------------------------------------------
    lstm_hidden_size: int = 256
    lstm_num_layers: int = 2
    lstm_dropout: float = 0.1
    bidirectional: bool = True

    # --- CTC head ------------------------------------------------------------
    # Number of label classes EXCLUDING the blank symbol.
    num_classes: int = len(ALPHABET)
    blank_index: int = BLANK_INDEX

    # --- Sub-configs ---------------------------------------------------------
    training: TrainingConfig = field(default_factory=TrainingConfig)
    augmentation: AugmentationConfig = field(default_factory=AugmentationConfig)

    @property
    def num_classes_with_blank(self) -> int:
        """Total size of the CTC output distribution (alphabet + blank)."""
        return self.num_classes + 1

    def to_dict(self) -> dict[str, Any]:
        """Serialise the full configuration to a plain dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HTRModelConfig":
        """Reconstruct a config from a dictionary produced by :meth:`to_dict`."""
        data = dict(data)
        training = data.pop("training", None)
        augmentation = data.pop("augmentation", None)
        kwargs: dict[str, Any] = dict(data)
        # Tuples survive a JSON round-trip as lists; normalise back to tuples.
        if "cnn_channels" in kwargs and kwargs["cnn_channels"] is not None:
            kwargs["cnn_channels"] = tuple(kwargs["cnn_channels"])
        if training is not None:
            training = dict(training)
            if "optimizer_betas" in training:
                training["optimizer_betas"] = tuple(training["optimizer_betas"])
            kwargs["training"] = TrainingConfig(**training)
        if augmentation is not None:
            augmentation = dict(augmentation)
            if "scale_range" in augmentation:
                augmentation["scale_range"] = tuple(augmentation["scale_range"])
            kwargs["augmentation"] = AugmentationConfig(**augmentation)
        return cls(**kwargs)


# The canonical, pinned default configuration for the shared Base_Model.
DEFAULT_HTR_CONFIG = HTRModelConfig()


__all__ = [
    "ALPHABET",
    "BLANK_INDEX",
    "AugmentationConfig",
    "TrainingConfig",
    "HTRModelConfig",
    "DEFAULT_HTR_CONFIG",
]
