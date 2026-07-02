"""Deterministic IAM dataset pipeline and fixed split (task 6.2).

This module supplies the training data for the shared, frozen ``Base_Model``
(task 6.3). It is deliberately decoupled from any heavy download step: the
*split definition* — which line belongs to the train, validation, or test
partition — is checked in as a manifest (``iam_split_manifest.json``) so the
partitioning is reproducible and auditable without re-deriving it. The actual
handwriting line images are resolved **at load time** relative to a configurable
dataset root, and, when that root is absent, the loader can synthesise
deterministic placeholder images so the documented training procedure can still
be exercised end-to-end (e.g. in tests) without a licensed copy of IAM.

Reproducibility (Req 11.2)
--------------------------
* **Pinned dataset version.** The manifest pins the dataset to
  :data:`app.ml.train_base.IAM_DATASET_VERSION`
  (``"IAM-Handwriting-DB-v3.0-lines"``); :func:`load_split_manifest` rejects any
  manifest whose declared version disagrees.
* **Checked-in split.** Each line carries an explicit ``split`` label
  (``train`` / ``val`` / ``test``) in the manifest, following the writer-disjoint
  IAM Aachen convention. This *is* the fixed split definition reused via
  :class:`app.ml.train_base.SplitSpec` / :data:`app.ml.train_base.DEFAULT_IAM_SPLIT`.
* **Deterministic ordering.** :func:`load_training_samples` always returns
  samples sorted by ``sample_id``, so two runs feed the trainer the same
  examples in the same sequence. Synthetic placeholder images are seeded by a
  stable hash of the ``sample_id`` (mixed with the configured master seed) so
  they too are reproducible across runs and processes.

Resolution strategy
--------------------
``load_training_samples`` resolves each line's pixels by, in order:

1. a real image file under ``dataset_root`` (or ``$HND_IAM_DATASET_ROOT``) when
   present and ``synthetic`` is not forced; otherwise
2. a deterministic synthetic placeholder, when ``synthetic`` is ``True`` or when
   ``synthetic`` is ``None`` (auto) and no usable dataset root is configured.

In all cases each returned :class:`app.ml.train_base.LabeledSample` carries a
float image tensor of shape ``(C, H, W)`` with ``C == config.input_channels`` and
``H == config.image_height`` (variable width ``W``), matching what the base-model
trainer and the CTC collate function expect.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence

import torch
from torch import Tensor

from app.config.settings import Settings, get_settings
from app.ml.htr_config import DEFAULT_HTR_CONFIG, HTRModelConfig
from app.ml.train_base import (
    DEFAULT_IAM_SPLIT,
    IAM_DATASET_VERSION,
    LabeledSample,
    SplitSpec,
)

# The split manifest is checked in next to this module so the split definition
# travels with the code and is well-defined without any external download.
IAM_SPLIT_MANIFEST_PATH: Path = Path(__file__).with_name("iam_split_manifest.json")

# The valid partitions of the fixed split, in canonical order.
PARTITIONS: tuple[str, ...] = ("train", "val", "test")

# Environment variable used to point the loader at a licensed copy of the IAM
# line images when one is present.
DATASET_ROOT_ENV: str = "HND_IAM_DATASET_ROOT"

# Synthetic placeholder geometry: width grows with the label length so a
# placeholder line is always wide enough for its transcription's CTC targets.
_SYNTHETIC_MIN_WIDTH: int = 32
_SYNTHETIC_PX_PER_CHAR: int = 12


@dataclass(frozen=True)
class IamSampleRef:
    """A single line reference from the checked-in split manifest.

    Attributes:
        sample_id: Stable IAM line id (``form-line``, e.g. ``"a01-000u-00"``).
        split: The partition this line belongs to (one of :data:`PARTITIONS`).
        text: The reference transcription used as the CTC training label.
        image: Path to the line image **relative to the dataset root**, following
            the IAM on-disk layout.
    """

    sample_id: str
    split: str
    text: str
    image: str


@dataclass(frozen=True)
class IamSplitManifest:
    """The fully parsed, immutable IAM split definition.

    Combines the pinned dataset version and split metadata with the
    deterministically ordered (sorted-by-id) line references.
    """

    name: str
    dataset_version: str
    split: SplitSpec
    provenance: Mapping[str, Any]
    samples: tuple[IamSampleRef, ...]

    def __len__(self) -> int:
        return len(self.samples)

    @property
    def sample_ids(self) -> tuple[str, ...]:
        """All line ids in deterministic (loaded) order."""
        return tuple(s.sample_id for s in self.samples)

    def for_partition(self, partition: str) -> tuple[IamSampleRef, ...]:
        """Return the line references for ``partition`` in deterministic order."""
        _require_valid_partition(partition)
        return tuple(s for s in self.samples if s.split == partition)

    def partition_counts(self) -> dict[str, int]:
        """Map each partition name to the number of lines assigned to it."""
        counts = {p: 0 for p in PARTITIONS}
        for sample in self.samples:
            counts[sample.split] += 1
        return counts


def _require_valid_partition(partition: str) -> None:
    if partition not in PARTITIONS:
        raise ValueError(
            f"unknown partition {partition!r}; expected one of {PARTITIONS}"
        )


def _read_manifest(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):  # pragma: no cover - defensive
        raise ValueError("IAM split manifest must be a JSON object")
    return data


def _parse_split_spec(raw: Any, fallback: SplitSpec) -> SplitSpec:
    if not isinstance(raw, Mapping):
        return fallback
    kwargs: dict[str, Any] = {}
    for key in (
        "name",
        "train_fraction",
        "val_fraction",
        "test_fraction",
        "train_count",
        "val_count",
        "test_count",
    ):
        if key in raw:
            kwargs[key] = raw[key]
    return SplitSpec(**kwargs)


def _parse_samples(raw_samples: Any) -> tuple[IamSampleRef, ...]:
    if not isinstance(raw_samples, list) or not raw_samples:
        raise ValueError("IAM split manifest must contain a non-empty 'samples' list")

    seen: set[str] = set()
    parsed: list[IamSampleRef] = []
    for entry in raw_samples:
        if not isinstance(entry, Mapping):  # pragma: no cover - defensive
            raise ValueError(f"each sample must be an object, got: {entry!r}")
        sample_id = entry.get("sample_id")
        text = entry.get("text")
        split = entry.get("split")
        image = entry.get("image")
        if not isinstance(sample_id, str) or not sample_id:
            raise ValueError(f"sample is missing a valid 'sample_id': {entry!r}")
        if not isinstance(text, str):
            raise ValueError(f"sample {sample_id!r} is missing a string 'text'")
        if not isinstance(split, str) or split not in PARTITIONS:
            raise ValueError(
                f"sample {sample_id!r} has an invalid 'split': {split!r}; "
                f"expected one of {PARTITIONS}"
            )
        if not isinstance(image, str) or not image:
            raise ValueError(f"sample {sample_id!r} is missing a string 'image' path")
        if sample_id in seen:
            raise ValueError(f"duplicate sample_id in manifest: {sample_id!r}")
        seen.add(sample_id)
        parsed.append(
            IamSampleRef(sample_id=sample_id, split=split, text=text, image=image)
        )

    # Deterministic order independent of how the manifest happens to be written.
    parsed.sort(key=lambda s: s.sample_id)
    return tuple(parsed)


@lru_cache(maxsize=None)
def _load_manifest_cached(path_str: str) -> IamSplitManifest:
    path = Path(path_str)
    if not path.exists():
        raise FileNotFoundError(f"IAM split manifest not found: {path}")

    data = _read_manifest(path)

    declared_version = str(data.get("dataset_version", ""))
    if declared_version != IAM_DATASET_VERSION:
        raise ValueError(
            "IAM split manifest dataset_version "
            f"{declared_version!r} does not match the pinned IAM_DATASET_VERSION "
            f"{IAM_DATASET_VERSION!r}; bump the constant deliberately when the "
            "dataset version changes."
        )

    split = _parse_split_spec(data.get("split"), DEFAULT_IAM_SPLIT)
    provenance = data.get("provenance", {})
    if not isinstance(provenance, Mapping):  # pragma: no cover - defensive
        raise ValueError("manifest 'provenance' must be an object")

    samples = _parse_samples(data.get("samples"))
    name = str(data.get("name", "iam-line-split"))

    return IamSplitManifest(
        name=name,
        dataset_version=declared_version,
        split=split,
        provenance=dict(provenance),
        samples=samples,
    )


def load_split_manifest(manifest_path: str | os.PathLike[str] | None = None) -> IamSplitManifest:
    """Load and validate the checked-in IAM split definition.

    Loading is deterministic: line references are always returned sorted by
    ``sample_id`` and the manifest's declared dataset version is verified against
    the pinned :data:`app.ml.train_base.IAM_DATASET_VERSION`.

    Args:
        manifest_path: Optional override path (primarily for testing). Defaults
            to the checked-in :data:`IAM_SPLIT_MANIFEST_PATH`.

    Returns:
        An immutable :class:`IamSplitManifest`.

    Raises:
        FileNotFoundError: if the manifest file does not exist.
        ValueError: if the manifest is malformed or its declared dataset version
            does not match the pinned constant.
    """
    path = Path(manifest_path) if manifest_path is not None else IAM_SPLIT_MANIFEST_PATH
    return _load_manifest_cached(str(path))


def _stable_synthetic_seed(sample_id: str, base_seed: int) -> int:
    """A reproducible, process-independent seed derived from a sample id.

    Python's built-in ``hash`` is salted per process, so we use a stable SHA-256
    over the master seed and the sample id to keep synthetic placeholders
    identical across runs and processes (Req 11.2).
    """
    digest = hashlib.sha256(f"{base_seed}:{sample_id}".encode("utf-8")).digest()
    # Keep within int64 range accepted by torch.Generator.manual_seed.
    return int.from_bytes(digest[:8], "big") % (2**63 - 1)


def _synthetic_width(text: str) -> int:
    """Placeholder width wide enough to host the label's CTC targets."""
    return max(_SYNTHETIC_MIN_WIDTH, len(text) * _SYNTHETIC_PX_PER_CHAR)


def synthetic_image(
    sample: IamSampleRef,
    *,
    config: HTRModelConfig = DEFAULT_HTR_CONFIG,
    base_seed: int = 0,
) -> Tensor:
    """Generate a deterministic placeholder line image for ``sample``.

    The image has shape ``(C, H, W)`` with ``C == config.input_channels`` and
    ``H == config.image_height``; the width scales with the label length. Pixel
    values are reproducible for a given ``(sample_id, base_seed)`` pair.
    """
    generator = torch.Generator()
    generator.manual_seed(_stable_synthetic_seed(sample.sample_id, base_seed))
    width = _synthetic_width(sample.text)
    return torch.rand(
        config.input_channels,
        config.image_height,
        width,
        generator=generator,
    )


def _load_real_image(
    sample: IamSampleRef,
    dataset_root: Path,
    config: HTRModelConfig,
) -> Tensor:
    """Decode a real IAM line image into a normalised ``(C, H, W)`` tensor.

    The image is converted to grayscale (single channel), resized so its height
    equals ``config.image_height`` while preserving aspect ratio, and scaled to
    ``[0, 1]``. Pillow and torchvision are imported lazily so this module stays
    importable in environments that only use the synthetic path.
    """
    from PIL import Image  # local import: only needed for the real-image path

    image_path = dataset_root / sample.image
    if not image_path.exists():
        raise FileNotFoundError(
            f"IAM line image for {sample.sample_id!r} not found at {image_path}"
        )

    with Image.open(image_path) as img:
        gray = img.convert("L")
        orig_w, orig_h = gray.size
        target_h = config.image_height
        target_w = max(1, round(orig_w * (target_h / max(orig_h, 1))))
        resized = gray.resize((target_w, target_h))
        values = list(resized.getdata())

    tensor = torch.tensor(values, dtype=torch.float32).reshape(target_h, target_w)
    tensor = tensor / 255.0
    # Shape (C, H, W); replicate across channels if the config expects > 1.
    tensor = tensor.unsqueeze(0)
    if config.input_channels > 1:
        tensor = tensor.repeat(config.input_channels, 1, 1)
    return tensor


def _resolve_dataset_root(
    dataset_root: str | os.PathLike[str] | None,
) -> Path | None:
    if dataset_root is not None:
        return Path(dataset_root)
    env_root = os.environ.get(DATASET_ROOT_ENV)
    if env_root:
        return Path(env_root)
    return None


def load_training_samples(
    split: SplitSpec = DEFAULT_IAM_SPLIT,
    *,
    partition: str = "train",
    dataset_root: str | os.PathLike[str] | None = None,
    manifest_path: str | os.PathLike[str] | None = None,
    config: HTRModelConfig = DEFAULT_HTR_CONFIG,
    synthetic: bool | None = None,
    settings: Settings | None = None,
) -> list[LabeledSample]:
    """Load the IAM line samples for a partition in deterministic order.

    This is the entry point consumed by the base-model training script
    (:func:`app.ml.train_base.main` calls ``load_training_samples(split=...)``).
    The returned list is ordered deterministically (sorted by ``sample_id``) so
    the trainer always sees the same examples in the same sequence (Req 11.2).

    Args:
        split: The fixed split definition (a :class:`app.ml.train_base.SplitSpec`).
            Recorded for provenance; the authoritative per-line partition comes
            from the checked-in manifest. Defaults to
            :data:`app.ml.train_base.DEFAULT_IAM_SPLIT`.
        partition: Which partition to load — ``"train"`` (default), ``"val"``,
            or ``"test"``.
        dataset_root: Filesystem root under which the IAM line images live. When
            ``None``, the ``HND_IAM_DATASET_ROOT`` environment variable is used
            if set.
        manifest_path: Optional override for the split manifest (testing).
        config: Pinned model config that fixes the image height and channels.
        synthetic: Tri-state image-resolution strategy:
            ``True`` always synthesise deterministic placeholder images;
            ``False`` always load real images (raising if a root/file is
            missing); ``None`` (default) auto-selects — real images when a usable
            dataset root is configured, otherwise synthetic placeholders so the
            pipeline runs without a licensed IAM copy.
        settings: Optional settings override (provides the master seed used to
            seed synthetic placeholders).

    Returns:
        A list of :class:`app.ml.train_base.LabeledSample`, one per line in the
        requested partition, ordered by ``sample_id``.

    Raises:
        ValueError: for an unknown ``partition`` or a malformed manifest.
        FileNotFoundError: when real images are required but cannot be located.
    """
    _require_valid_partition(partition)
    settings = settings or get_settings()
    manifest = load_split_manifest(manifest_path)
    refs = manifest.for_partition(partition)

    root = _resolve_dataset_root(dataset_root)

    # Resolve the image-resolution strategy.
    if synthetic is None:
        use_synthetic = root is None
    else:
        use_synthetic = synthetic

    if not use_synthetic and root is None:
        raise FileNotFoundError(
            "real IAM images requested (synthetic=False) but no dataset root is "
            f"configured; pass dataset_root=... or set ${DATASET_ROOT_ENV}."
        )

    samples: list[LabeledSample] = []
    for ref in refs:
        if use_synthetic:
            image = synthetic_image(
                ref, config=config, base_seed=settings.random_seed
            )
        else:
            assert root is not None  # narrowed above
            image = _load_real_image(ref, root, config)
        samples.append(LabeledSample(image=image, text=ref.text))
    return samples


__all__ = [
    "IAM_SPLIT_MANIFEST_PATH",
    "PARTITIONS",
    "DATASET_ROOT_ENV",
    "IamSampleRef",
    "IamSplitManifest",
    "load_split_manifest",
    "load_training_samples",
    "synthetic_image",
]
