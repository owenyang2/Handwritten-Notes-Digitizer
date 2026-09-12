"""The general English evaluation set used by the personalization guardrails.

This module defines and loads a *fixed, versioned* evaluation subset of general
English handwriting samples. After a per-user adapter is trained, the
Evaluation_Service measures the adapter's CER and WER on this set so the
Personalization_Service can reject adapters that regress general accuracy
(catastrophic forgetting) beyond the configured tolerances (Requirement 6.2,
and the guardrails in Req 6.3/6.4).

Design intent
-------------
* **Fixed and held aside.** The samples here are reserved exclusively for
  guardrail evaluation. They are never used to train the shared base model
  (task 6.3) nor any per-user adapter, so measurements on this set are an
  unbiased estimate of general recognition quality.
* **Versioned and provenanced.** The set is described by a checked-in manifest
  (``general_eval_manifest.json``) carrying a semantic ``version`` and a
  ``provenance`` block (base dataset, source split, selection method, licensing
  note). A content digest (:func:`general_eval_set_digest`) lets callers pin the
  exact set a measurement was computed against.
* **Deterministic.** :func:`load_general_eval_set` returns the samples in a
  stable order (sorted by ``sample_id``) regardless of manifest ordering, so two
  runs always evaluate the same references in the same sequence.

The reference transcriptions in the manifest are original general-English
sentences authored for this harness rather than verbatim corpus transcriptions;
sample ids follow the IAM line-id convention so the corresponding handwriting
line images can be located when a licensed copy of the dataset is present. See
``GENERAL_EVAL_SET.md`` for full documentation.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Tuple

# The manifest is checked in next to this module so the evaluation set travels
# with the code and requires no external download to be well-defined.
MANIFEST_PATH: Path = Path(__file__).with_name("general_eval_manifest.json")
EVAL_FIXTURES_DIR: Path = Path(__file__).with_name("eval_fixtures")

# Pinned semantic version of the general English evaluation set. Bump this
# whenever the manifest's sample membership or references change so that stored
# evaluation results remain traceable to the exact set they were measured on.
GENERAL_EVAL_SET_VERSION: str = "1.0.0"


@dataclass(frozen=True)
class EvalSample:
    """A single held-aside general English evaluation sample.

    Attributes:
        sample_id: Stable identifier following the IAM line-id convention
            (``form-line``, e.g. ``"a01-000u-00"``). Used to locate the
            corresponding handwriting line image when the licensed dataset is
            present.
        text: The reference transcription used as ground truth when computing
            CER/WER for the guardrails.
        source_split: The dataset split this sample is held aside from
            (always the test split for this set).
    """

    sample_id: str
    text: str
    source_split: str
    image: str | None = None
    image_sha256: str | None = None


@dataclass(frozen=True)
class GeneralEvalSet:
    """The fully loaded, immutable general English evaluation set.

    Combines the version + provenance metadata with the deterministically
    ordered samples and a content digest for pinning.
    """

    name: str
    version: str
    provenance: Mapping[str, Any]
    samples: Tuple[EvalSample, ...]
    digest: str
    labels_verified: bool = False

    def __len__(self) -> int:
        return len(self.samples)

    @property
    def sample_ids(self) -> Tuple[str, ...]:
        """The sample ids in deterministic (loaded) order."""
        return tuple(s.sample_id for s in self.samples)

    def references(self) -> Tuple[str, ...]:
        """The reference transcriptions in deterministic (loaded) order."""
        return tuple(s.text for s in self.samples)


def _read_manifest(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):  # pragma: no cover - defensive
        raise ValueError("general eval manifest must be a JSON object")
    return data


def _parse_samples(raw_samples: Any) -> Tuple[EvalSample, ...]:
    if not isinstance(raw_samples, list) or not raw_samples:
        raise ValueError("general eval manifest must contain a non-empty 'samples' list")

    seen: set[str] = set()
    parsed: list[EvalSample] = []
    for entry in raw_samples:
        if not isinstance(entry, Mapping):  # pragma: no cover - defensive
            raise ValueError(f"each sample must be an object, got: {entry!r}")
        sample_id = entry.get("sample_id")
        text = entry.get("text")
        source_split = entry.get("source_split", "test")
        image = entry.get("image")
        image_sha256 = entry.get("image_sha256") or entry.get("sha256")
        if not isinstance(sample_id, str) or not sample_id:
            raise ValueError(f"sample is missing a valid 'sample_id': {entry!r}")
        if not isinstance(text, str):
            raise ValueError(f"sample {sample_id!r} is missing a string 'text'")
        if sample_id in seen:
            raise ValueError(f"duplicate sample_id in manifest: {sample_id!r}")
        seen.add(sample_id)
        parsed.append(
            EvalSample(
                sample_id=sample_id,
                text=text,
                source_split=str(source_split),
                image=str(image) if image else None,
                image_sha256=str(image_sha256).lower() if image_sha256 else None,
            )
        )

    # Deterministic order independent of how the manifest happens to be written.
    parsed.sort(key=lambda s: s.sample_id)
    return tuple(parsed)


def _compute_digest(version: str, samples: Tuple[EvalSample, ...]) -> str:
    """Stable SHA-256 over (version + ordered sample id/text/split tuples).

    The digest pins the exact membership and references of the set so a stored
    evaluation result can be tied to the precise evaluation set it used.
    """
    hasher = hashlib.sha256()
    hasher.update(version.encode("utf-8"))
    for sample in samples:
        hasher.update(b"\x00")
        hasher.update(sample.sample_id.encode("utf-8"))
        hasher.update(b"\x01")
        hasher.update(sample.text.encode("utf-8"))
        hasher.update(b"\x02")
        hasher.update(sample.source_split.encode("utf-8"))
        hasher.update(b"\x03")
        hasher.update((sample.image or "").encode("utf-8"))
        hasher.update(b"\x04")
        hasher.update((sample.image_sha256 or "").encode("utf-8"))
    return hasher.hexdigest()


@lru_cache(maxsize=1)
def load_general_eval_set(manifest_path: str | None = None) -> GeneralEvalSet:
    """Load the fixed, versioned general English evaluation set.

    The result is cached so repeated guardrail evaluations reuse the same
    immutable set without re-reading the manifest. Loading is deterministic:
    samples are always returned sorted by ``sample_id`` and the manifest
    ``version`` is verified against :data:`GENERAL_EVAL_SET_VERSION`.

    Args:
        manifest_path: Optional override path to a manifest file (primarily for
            testing). Defaults to the checked-in :data:`MANIFEST_PATH`.

    Returns:
        An immutable :class:`GeneralEvalSet`.

    Raises:
        FileNotFoundError: if the manifest file does not exist.
        ValueError: if the manifest is malformed or its declared version does
            not match the pinned :data:`GENERAL_EVAL_SET_VERSION`.
    """
    path = Path(manifest_path) if manifest_path else MANIFEST_PATH
    if not path.exists():
        raise FileNotFoundError(f"general eval manifest not found: {path}")

    data = _read_manifest(path)

    name = str(data.get("name", "general-english-eval"))
    version = str(data.get("version", ""))
    if version != GENERAL_EVAL_SET_VERSION:
        raise ValueError(
            "general eval manifest version "
            f"{version!r} does not match pinned GENERAL_EVAL_SET_VERSION "
            f"{GENERAL_EVAL_SET_VERSION!r}; bump the constant deliberately when "
            "the set changes."
        )

    provenance = data.get("provenance", {})
    if not isinstance(provenance, Mapping):  # pragma: no cover - defensive
        raise ValueError("manifest 'provenance' must be an object")

    samples = _parse_samples(data.get("samples"))
    digest = _compute_digest(version, samples)
    labels_verified = bool(data.get("labels_verified", False))

    return GeneralEvalSet(
        name=name,
        version=version,
        provenance=dict(provenance),
        samples=samples,
        digest=digest,
        labels_verified=labels_verified,
    )


def load_verified_eval_pairs(
    *,
    allow_synthetic: bool = False,
    fixtures_dir: Path | None = None,
    iam_root: Path | None = None,
) -> list[tuple[bytes, str]]:
    """Load verified (image bytes, reference text) pairs for guardrail eval.

    Looks for real handwriting images in checked-in fixtures at
    ``eval_fixtures/<sample_id>.png``.  The checked-in general-evaluation
    manifest intentionally contains reference sentences authored for the
    harness, not the original IAM transcriptions, so an arbitrary IAM image
    must never be paired with those references.  ``iam_root`` is retained as a
    compatibility argument but is deliberately ignored until a separately
    verified image/label manifest is supplied.

    Synthetic noise images are used only when ``allow_synthetic`` is explicitly
    true (test mode). An empty result means promotion must be refused.
    """
    from app.ml.iam_dataset import LabelsUnverifiedError

    eval_set = load_general_eval_set()
    if iam_root is not None and not eval_set.labels_verified:
        raise LabelsUnverifiedError(
            "real IAM images cannot be paired with the authored general-eval "
            "references; supply a labels_verified fixture manifest instead"
        )
    fixture_root = Path(fixtures_dir) if fixtures_dir else EVAL_FIXTURES_DIR
    pairs: list[tuple[bytes, str]] = []
    for sample in eval_set.samples:
        image_bytes = _read_eval_image(sample, fixture_root)
        if image_bytes is not None:
            pairs.append((image_bytes, sample.text))

    if pairs:
        return pairs

    if allow_synthetic:
        return [
            (_synthetic_eval_crop_bytes(sample.sample_id, sample.text), sample.text)
            for sample in eval_set.samples
        ]
    return []


def _read_eval_image(
    sample: EvalSample,
    fixture_root: Path,
) -> bytes | None:
    # A fixture without a manifest-declared digest is not a verified pair.
    # This prevents a random file with a familiar id from being paired with a
    # hand-authored reference transcription.
    if not sample.image_sha256:
        return None
    fixture = fixture_root / (sample.image or f"{sample.sample_id}.png")
    if fixture.is_file() and fixture.stat().st_size > 0:
        data = fixture.read_bytes()
        if data and hashlib.sha256(data).hexdigest().lower() == sample.image_sha256.lower():
            return data
    return None


def _synthetic_eval_crop_bytes(sample_id: str, text: str) -> bytes:
    """Deterministic synthetic crop used only in explicit test mode."""
    import io

    from PIL import Image as PILImage

    from app.config.settings import get_settings
    from app.ml.htr_config import DEFAULT_HTR_CONFIG
    from app.ml.iam_dataset import IamSampleRef, synthetic_image

    ref = IamSampleRef(sample_id=sample_id, split="test", text=text, image="")
    tensor = synthetic_image(ref, config=DEFAULT_HTR_CONFIG, base_seed=get_settings().random_seed)
    array = (tensor.squeeze(0).clamp(0, 1).numpy() * 255).astype("uint8")
    buf = io.BytesIO()
    PILImage.fromarray(array, mode="L").save(buf, format="PNG")
    return buf.getvalue()


def general_eval_set_digest() -> str:
    """Return the content digest of the current general English evaluation set."""
    return load_general_eval_set().digest


__all__ = [
    "MANIFEST_PATH",
    "EVAL_FIXTURES_DIR",
    "GENERAL_EVAL_SET_VERSION",
    "EvalSample",
    "GeneralEvalSet",
    "load_general_eval_set",
    "load_verified_eval_pairs",
    "general_eval_set_digest",
]
