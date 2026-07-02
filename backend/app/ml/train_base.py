"""Deterministic training procedure for the shared, frozen ``Base_Model`` (task 6.3).

This module implements the documented, reproducible training procedure for the
CNN + BiLSTM + CTC handwriting recognizer (the "CRNN"). Re-running it with the
same dataset and pinned configuration produces a base model whose accuracy on a
fixed evaluation set differs from the previous run by no more than a configured
tolerance (Requirements 11.2, 11.3).

Reproducibility is achieved by:

1. **Fixed seeds** — every RNG (Python, NumPy, PyTorch) is seeded through
   :func:`app.config.settings.configure_determinism` before any stochastic
   operation (weight init, shuffling, augmentation).
2. **Deterministic data ordering** — batching uses a *seeded* PyTorch generator
   and runs the loader in-process (``num_workers=0``) so the sample order is a
   pure function of the seed.
3. **Pinned configuration** — architecture and optimisation hyper-parameters
   come from :mod:`app.ml.htr_config`, never from ad-hoc literals.
4. **Recorded provenance** — the dataset version, split definition, training
   configuration, seed, and framework versions are written next to the weights
   so any artifact is self-describing (see :class:`TrainingProvenance`).

Dataset coupling
----------------
The procedure consumes an abstract :class:`LabeledSample` stream rather than a
concrete loader, so it is independent of any single dataset backend. The IAM
pipeline (task 6.2) supplies such a stream for the real base model; tests supply
a tiny synthetic stream so the procedure can be exercised without downloading
IAM. The canonical dataset version and split used for the *shipped* base model
are documented by :data:`IAM_DATASET_VERSION` and :data:`DEFAULT_IAM_SPLIT`.

Entry points
------------
- :func:`train_base_model` — importable function that runs training and writes
  the frozen artifact; returns a :class:`TrainingResult`.
- :func:`main` — thin CLI wrapper (``python -m app.ml.train_base``).
- :func:`load_base_model` / :func:`freeze_base_model` — load and freeze helpers
  used by the recognition runtime so adapters compose with a frozen base.
"""

from __future__ import annotations

import json
import platform
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence

import torch
from torch import Tensor, nn
from torch.utils.data import DataLoader, Dataset

from app.config.settings import Settings, configure_determinism, get_settings
from app.ml.htr_config import ALPHABET, DEFAULT_HTR_CONFIG, HTRModelConfig
from app.ml.htr_model import CRNN, build_model

# ---------------------------------------------------------------------------
# Documented dataset version and split (provenance for the shipped base model)
# ---------------------------------------------------------------------------

# Pinned public dataset used to train the shared Base_Model (Req 11.2). The IAM
# line-level database is the documented source; the exact release is pinned here
# so the training procedure is reproducible and auditable.
IAM_DATASET_VERSION: str = "IAM-Handwriting-DB-v3.0-lines"

# Default model-artifact filenames written into the output directory.
WEIGHTS_FILENAME: str = "base_model.pt"
PROVENANCE_FILENAME: str = "base_model.meta.json"

# CTC blank occupies index 0; characters map to 1..len(ALPHABET).
_CHAR_TO_INDEX: dict[str, int] = {ch: i + 1 for i, ch in enumerate(ALPHABET)}


@dataclass(frozen=True)
class SplitSpec:
    """A documented, fixed train/validation/test split definition.

    The split is recorded as part of provenance so reruns are auditable. The
    fields describe how the dataset version is partitioned; for the IAM line
    database the standard writer-disjoint split is used so the same writers
    never appear in more than one partition.
    """

    name: str = "iam-aachen-writer-disjoint"
    train_fraction: float = 0.8
    val_fraction: float = 0.1
    test_fraction: float = 0.1
    # Optional explicit counts (filled in when known); None = fraction-defined.
    train_count: int | None = None
    val_count: int | None = None
    test_count: int | None = None

    def __post_init__(self) -> None:
        total = self.train_fraction + self.val_fraction + self.test_fraction
        if abs(total - 1.0) > 1e-6:
            raise ValueError(
                "split fractions must sum to 1.0, got "
                f"{total:.6f} (train={self.train_fraction}, "
                f"val={self.val_fraction}, test={self.test_fraction})"
            )


# The fixed split used for the shipped base model (Req 11.2).
DEFAULT_IAM_SPLIT = SplitSpec()


@dataclass(frozen=True)
class LabeledSample:
    """A single training example: a line image and its ground-truth text.

    ``image`` is a float tensor of shape ``(C, H, W)`` normalised to ``[0, 1]``
    with ``C == config.input_channels`` and ``H == config.image_height``; the
    width ``W`` is variable. ``text`` is the transcription label.
    """

    image: Tensor
    text: str


@dataclass(frozen=True)
class TrainingProvenance:
    """Self-describing record stored alongside a trained artifact.

    Captures everything needed to reproduce or audit the artifact: dataset
    version, split, training/model configuration, seed, and framework versions
    (Req 11.2, 11.3).
    """

    dataset_version: str
    split: dict
    model_config: dict
    training_config: dict
    seed: int
    epochs_run: int
    samples_seen: int
    final_train_loss: float
    framework: dict


@dataclass(frozen=True)
class TrainingResult:
    """Outcome of a base-model training run."""

    model: CRNN
    provenance: TrainingProvenance
    weights_path: Path
    provenance_path: Path
    epoch_losses: list[float] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Text encoding (CTC targets)
# ---------------------------------------------------------------------------


def encode_text(text: str) -> list[int]:
    """Encode a transcription into CTC target indices.

    Characters outside the pinned :data:`app.ml.htr_config.ALPHABET` are dropped
    (the alphabet is the model's full label space). Index 0 is reserved for the
    CTC blank and is never produced here.
    """
    return [_CHAR_TO_INDEX[c] for c in text if c in _CHAR_TO_INDEX]


# ---------------------------------------------------------------------------
# Dataset / batching
# ---------------------------------------------------------------------------


class _SampleDataset(Dataset):
    """Adapts an in-memory sequence of :class:`LabeledSample` to ``Dataset``."""

    def __init__(self, samples: Sequence[LabeledSample]) -> None:
        self._samples = list(samples)

    def __len__(self) -> int:
        return len(self._samples)

    def __getitem__(self, index: int) -> LabeledSample:
        return self._samples[index]


def _collate(batch: list[LabeledSample]) -> tuple[Tensor, list[int], Tensor, Tensor]:
    """Pad images to a common width and build CTC targets.

    Returns ``(images, widths, targets, target_lengths)`` where ``images`` is
    ``(N, C, H, W_max)`` right-padded with zeros, ``widths`` are the original
    per-sample widths (used to derive CTC input lengths), ``targets`` is the
    concatenation of all encoded labels, and ``target_lengths`` gives each
    label's length.
    """
    widths = [s.image.shape[-1] for s in batch]
    max_w = max(widths)
    c = batch[0].image.shape[0]
    h = batch[0].image.shape[1]

    images = torch.zeros(len(batch), c, h, max_w, dtype=batch[0].image.dtype)
    for i, s in enumerate(batch):
        w = s.image.shape[-1]
        images[i, :, :, :w] = s.image

    encoded = [encode_text(s.text) for s in batch]
    target_lengths = torch.tensor([len(e) for e in encoded], dtype=torch.long)
    flat: list[int] = [idx for e in encoded for idx in e]
    targets = torch.tensor(flat, dtype=torch.long)
    return images, widths, targets, target_lengths


class _InputLengthCache:
    """Memoised mapping from input width to CTC timesteps for a given model."""

    def __init__(self, model: CRNN) -> None:
        self._model = model
        self._cache: dict[int, int] = {}

    def of(self, width: int) -> int:
        cached = self._cache.get(width)
        if cached is None:
            cached = self._model.output_length(width)
            self._cache[width] = cached
        return cached


# ---------------------------------------------------------------------------
# Artifact persistence
# ---------------------------------------------------------------------------


def default_model_dir(settings: Settings | None = None) -> Path:
    """Documented filesystem location for the shared base-model artifact.

    The artifact lives under the object store root in a ``models/base_model``
    directory so it is co-located with other model assets and easy to mount in
    the container (Req 11.1, 11.2).
    """
    settings = settings or get_settings()
    return Path(settings.object_store_path) / "models" / "base_model"


def save_base_model_artifact(
    model: CRNN,
    provenance: TrainingProvenance,
    output_dir: Path,
) -> tuple[Path, Path]:
    """Persist frozen weights and the provenance record; return their paths."""
    output_dir.mkdir(parents=True, exist_ok=True)
    weights_path = output_dir / WEIGHTS_FILENAME
    provenance_path = output_dir / PROVENANCE_FILENAME

    torch.save(
        {
            "state_dict": model.state_dict(),
            "model_config": model.config.to_dict(),
            "frozen": True,
        },
        weights_path,
    )
    provenance_path.write_text(
        json.dumps(asdict(provenance), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return weights_path, provenance_path


def freeze_base_model(model: CRNN) -> CRNN:
    """Freeze every parameter and switch to eval mode (the shared base is fixed).

    Personalization composes a small per-user adapter on top of this frozen base
    (Req 5.2); the base parameters themselves never change after training.
    """
    for param in model.parameters():
        param.requires_grad_(False)
    model.eval()
    return model


def load_base_model(
    output_dir: Path | None = None,
    *,
    settings: Settings | None = None,
    map_location: str | torch.device = "cpu",
) -> CRNN:
    """Load a previously trained, frozen base model from its artifact directory."""
    settings = settings or get_settings()
    output_dir = output_dir or default_model_dir(settings)
    weights_path = Path(output_dir) / WEIGHTS_FILENAME
    checkpoint = torch.load(weights_path, map_location=map_location, weights_only=True)
    config = HTRModelConfig.from_dict(checkpoint["model_config"])
    model = build_model(config)
    model.load_state_dict(checkpoint["state_dict"])
    return freeze_base_model(model)


# ---------------------------------------------------------------------------
# Training procedure
# ---------------------------------------------------------------------------


def train_base_model(
    dataset: Iterable[LabeledSample] | Sequence[LabeledSample],
    *,
    config: HTRModelConfig = DEFAULT_HTR_CONFIG,
    output_dir: Path | None = None,
    max_epochs: int | None = None,
    dataset_version: str = IAM_DATASET_VERSION,
    split: SplitSpec | None = None,
    device: str | torch.device = "cpu",
    settings: Settings | None = None,
    save: bool = True,
    on_epoch_end: Callable[[int, float, CRNN], None] | None = None,
) -> TrainingResult:
    """Train the shared base model deterministically and write a frozen artifact.

    Parameters
    ----------
    dataset:
        A sequence (or iterable) of :class:`LabeledSample`. The IAM pipeline
        supplies the real training data; tests supply a tiny synthetic set.
    config:
        Pinned architecture + optimisation configuration (defaults to the
        canonical :data:`app.ml.htr_config.DEFAULT_HTR_CONFIG`).
    output_dir:
        Where the frozen artifact is written. Defaults to
        :func:`default_model_dir`.
    max_epochs:
        Overrides ``config.training.max_epochs`` (used by the smoke test to keep
        runs cheap). ``None`` uses the pinned budget.
    dataset_version / split:
        Documented provenance for the run; recorded next to the weights.
    save:
        When ``False`` the artifact is not written (still returns the in-memory
        model + provenance); useful for fast tests.
    on_epoch_end:
        Optional callback invoked with ``(epoch_index, mean_loss, model)``
        after each epoch — purely for progress observability (e.g. logging or
        periodic checkpointing during a long CPU run); it does not affect
        training. ``model`` is still in ``train()`` mode when passed.

    Returns
    -------
    TrainingResult
        The frozen model, its provenance, and the artifact paths.
    """
    settings = settings or get_settings()
    split = split or DEFAULT_IAM_SPLIT
    output_dir = Path(output_dir) if output_dir is not None else default_model_dir(settings)
    device = torch.device(device)

    # 1. Fix all RNGs and deterministic backends BEFORE building the model so
    #    weight initialisation and shuffling are reproducible (Req 11.3).
    seed = configure_determinism(settings)

    samples = list(dataset)
    if not samples:
        raise ValueError("training dataset is empty")

    # 2. Build the model from the pinned config (deterministic init given seed).
    model = build_model(config).to(device)
    model.train()

    # 3. Deterministic data ordering: a seeded generator drives shuffling and
    #    the loader runs in-process so order is a pure function of the seed.
    generator = torch.Generator()
    generator.manual_seed(seed)
    loader = DataLoader(
        _SampleDataset(samples),
        batch_size=config.training.batch_size,
        shuffle=True,
        generator=generator,
        num_workers=0,
        collate_fn=_collate,
        drop_last=False,
    )

    optimizer = torch.optim.Adam(
        model.parameters(),
        lr=config.training.learning_rate,
        betas=config.training.optimizer_betas,
        weight_decay=config.training.weight_decay,
    )
    ctc_loss = nn.CTCLoss(blank=config.blank_index, zero_infinity=True)
    length_cache = _InputLengthCache(model)

    epochs = max_epochs if max_epochs is not None else config.training.max_epochs

    epoch_losses: list[float] = []
    samples_seen = 0
    final_loss = 0.0

    for _epoch in range(epochs):
        epoch_loss = 0.0
        num_batches = 0
        for images, widths, targets, target_lengths in loader:
            images = images.to(device)
            targets = targets.to(device)

            optimizer.zero_grad()
            log_probs = model(images)  # (T, N, C)
            t_full = log_probs.size(0)
            input_lengths = torch.tensor(
                [min(length_cache.of(w), t_full) for w in widths],
                dtype=torch.long,
            )

            loss = ctc_loss(log_probs, targets, input_lengths, target_lengths)
            loss.backward()
            nn.utils.clip_grad_norm_(
                model.parameters(), config.training.grad_clip_norm
            )
            optimizer.step()

            epoch_loss += float(loss.detach())
            num_batches += 1
            samples_seen += images.size(0)

        mean_loss = epoch_loss / max(num_batches, 1)
        epoch_losses.append(mean_loss)
        final_loss = mean_loss
        if on_epoch_end is not None:
            on_epoch_end(_epoch, mean_loss, model)

    # 4. The shared base is frozen after training; adapters compose on top.
    freeze_base_model(model)

    provenance = TrainingProvenance(
        dataset_version=dataset_version,
        split=asdict(split),
        model_config=config.to_dict(),
        training_config={
            **asdict(config.training),
            "epochs_requested": epochs,
            "alphabet_size": len(ALPHABET),
        },
        seed=seed,
        epochs_run=epochs,
        samples_seen=samples_seen,
        final_train_loss=final_loss,
        framework={
            "torch": torch.__version__,
            "python": platform.python_version(),
        },
    )

    weights_path = output_dir / WEIGHTS_FILENAME
    provenance_path = output_dir / PROVENANCE_FILENAME
    if save:
        weights_path, provenance_path = save_base_model_artifact(
            model, provenance, output_dir
        )

    return TrainingResult(
        model=model,
        provenance=provenance,
        weights_path=weights_path,
        provenance_path=provenance_path,
        epoch_losses=epoch_losses,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point: ``python -m app.ml.train_base``.

    This wrapper wires the real IAM pipeline (task 6.2) to
    :func:`train_base_model`. The dataset loader is imported lazily so this
    module stays importable (and unit-testable) without the heavy dataset
    dependency present.
    """
    import argparse

    parser = argparse.ArgumentParser(description="Train the shared HTR base model.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for the frozen base-model artifact.",
    )
    parser.add_argument(
        "--max-epochs",
        type=int,
        default=None,
        help="Override the pinned epoch budget (mainly for quick runs).",
    )
    parser.add_argument(
        "--device",
        type=str,
        default="cpu",
        help="Torch device to train on (e.g. cpu, cuda).",
    )
    args = parser.parse_args(argv)

    try:
        # The IAM pipeline (task 6.2) is expected to expose a function that
        # yields LabeledSample for the training split. Imported lazily so the
        # absence of the dataset module does not break importing train_base.
        from app.ml.iam_dataset import load_training_samples  # type: ignore
    except ImportError as exc:  # pragma: no cover - depends on task 6.2 wiring
        parser.error(
            "IAM dataset pipeline is unavailable: "
            f"{exc}. Provide app.ml.iam_dataset.load_training_samples()."
        )
        return 2

    samples = load_training_samples(split=DEFAULT_IAM_SPLIT)  # pragma: no cover
    result = train_base_model(  # pragma: no cover
        samples,
        output_dir=args.output_dir,
        max_epochs=args.max_epochs,
        device=args.device,
    )
    print(  # pragma: no cover
        f"Saved frozen base model to {result.weights_path} "
        f"(epochs={result.provenance.epochs_run}, "
        f"final_loss={result.provenance.final_train_loss:.4f})"
    )
    return 0  # pragma: no cover


__all__ = [
    "IAM_DATASET_VERSION",
    "DEFAULT_IAM_SPLIT",
    "SplitSpec",
    "LabeledSample",
    "TrainingProvenance",
    "TrainingResult",
    "encode_text",
    "default_model_dir",
    "save_base_model_artifact",
    "freeze_base_model",
    "load_base_model",
    "train_base_model",
    "main",
]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
