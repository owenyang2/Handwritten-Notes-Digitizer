"""Build a *local* IAM split manifest from a real, licensed IAM download.

This is a one-off developer tool, not part of the runtime request path. It
reads the raw ``ascii/forms.txt`` and ``ascii/lines.txt`` files that ship with
a real IAM Handwriting Database download (obtained separately, under IAM's own
license) and produces a manifest JSON in the same shape consumed by
``app.ml.iam_dataset.load_split_manifest`` — but with **real** transcriptions
in place of the tiny fabricated-placeholder set checked into
``app/ml/iam_split_manifest.json``.

Why this isn't checked into the repo
-------------------------------------
IAM's terms do not permit redistributing the real transcriptions (or images)
in a public repository. The checked-in ``iam_split_manifest.json`` therefore
only contains 16 stub entries with made-up sentences so the training/loading
pipeline can be exercised in tests without a licensed copy. This script's
*output* must stay outside version control — write it under the (gitignored)
``datasets/`` directory alongside the raw download, never under ``app/ml/``.

Split strategy
--------------
Lines are grouped by *writer* (from ``forms.txt``) and whole writers are
assigned to train/val/test so the same writer's handwriting never appears in
more than one partition (the "writer-disjoint" property IAM's own Aachen split
also guarantees). Writers are shuffled with a fixed seed and greedily packed
into partitions to approximate the configured fractions by sample count.

Usage
-----
```bash
python -m app.ml.build_real_iam_manifest \\
    --ascii-dir datasets/ascii \\
    --images-dir datasets/lines \\
    --output datasets/iam_real_manifest.json
```
"""

from __future__ import annotations

import argparse
import json
import random
from dataclasses import asdict
from pathlib import Path
from typing import Iterator

from app.ml.htr_config import ALPHABET, DEFAULT_HTR_CONFIG, HTRModelConfig
from app.ml.train_base import DEFAULT_IAM_SPLIT, IAM_DATASET_VERSION, SplitSpec

_ALPHABET_SET = set(ALPHABET)


def _resized_width(image_path: Path, config: HTRModelConfig) -> int:
    """Width the image would have after resizing its height to ``config.image_height``.

    Reads only the header (via ``Image.open`` without ``.load()``), so this is
    cheap even across thousands of files.
    """
    from PIL import Image

    with Image.open(image_path) as img:
        orig_w, orig_h = img.size
    return max(1, round(orig_w * (config.image_height / max(orig_h, 1))))


def _iter_forms_writers(forms_path: Path) -> Iterator[tuple[str, str]]:
    """Yield ``(form_id, writer_id)`` pairs from ``ascii/forms.txt``."""
    with forms_path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not line.strip() or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            form_id, writer_id = parts[0], parts[1]
            yield form_id, writer_id


def _iter_lines(lines_path: Path) -> Iterator[tuple[str, str, str]]:
    """Yield ``(sample_id, form_id, text)`` from ``ascii/lines.txt``.

    The transcription is the last whitespace-separated token, with IAM's
    ``|`` word-separator replaced by spaces.
    """
    with lines_path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not line.strip() or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) < 9:
                continue
            sample_id = parts[0]
            raw_text = parts[-1]
            text = raw_text.replace("|", " ")
            # sample_id looks like "a01-000u-00" -> form_id "a01-000u".
            form_id = "-".join(sample_id.split("-")[:2])
            yield sample_id, form_id, text


def _image_relpath(sample_id: str) -> str:
    """IAM on-disk layout: ``<prefix>/<form_id>/<sample_id>.png``."""
    writer_prefix = sample_id.split("-")[0]
    form_id = "-".join(sample_id.split("-")[:2])
    return f"{writer_prefix}/{form_id}/{sample_id}.png"


def _usable_text(text: str) -> bool:
    """A label is usable if at least one character survives CTC encoding."""
    return any(ch in _ALPHABET_SET for ch in text)


def _assign_splits(
    writer_sample_counts: dict[str, int], split: SplitSpec, seed: int
) -> dict[str, str]:
    """Greedily assign whole writers to train/val/test to hit target fractions."""
    writers = list(writer_sample_counts)
    rng = random.Random(seed)
    rng.shuffle(writers)

    total = sum(writer_sample_counts.values())
    targets = {
        "train": split.train_fraction * total,
        "val": split.val_fraction * total,
        "test": split.test_fraction * total,
    }
    assigned_counts = {"train": 0, "val": 0, "test": 0}
    writer_to_split: dict[str, str] = {}

    for writer in writers:
        # Assign to whichever partition is furthest below its target share.
        deficits = {
            name: targets[name] - assigned_counts[name] for name in targets
        }
        best = max(deficits, key=lambda name: deficits[name])
        writer_to_split[writer] = best
        assigned_counts[best] += writer_sample_counts[writer]

    return writer_to_split


def build_manifest(
    ascii_dir: Path,
    images_dir: Path,
    split: SplitSpec = DEFAULT_IAM_SPLIT,
    seed: int = 1337,
    max_resized_width: int | None = None,
    config: HTRModelConfig = DEFAULT_HTR_CONFIG,
) -> dict:
    """Build the manifest dict (see module docstring for shape/rationale).

    ``max_resized_width`` optionally drops lines whose width (after resizing
    to ``config.image_height``) exceeds the given pixel count. This trims a
    small number of extreme-length outlier lines that would otherwise dominate
    per-batch padding cost (and therefore CPU training time) without losing
    much data or writer diversity.
    """
    form_to_writer = dict(_iter_forms_writers(ascii_dir / "forms.txt"))

    raw_entries: list[tuple[str, str, str]] = []
    for sample_id, form_id, text in _iter_lines(ascii_dir / "lines.txt"):
        if not _usable_text(text):
            continue
        image_rel = _image_relpath(sample_id)
        image_path = images_dir / image_rel
        if not image_path.exists():
            continue
        if max_resized_width is not None and _resized_width(image_path, config) > max_resized_width:
            continue
        writer_id = form_to_writer.get(form_id)
        if writer_id is None:
            continue
        raw_entries.append((sample_id, writer_id, text))

    writer_sample_counts: dict[str, int] = {}
    for _sample_id, writer_id, _text in raw_entries:
        writer_sample_counts[writer_id] = writer_sample_counts.get(writer_id, 0) + 1

    writer_to_split = _assign_splits(writer_sample_counts, split, seed)

    samples = []
    for sample_id, writer_id, text in raw_entries:
        samples.append(
            {
                "sample_id": sample_id,
                "split": writer_to_split[writer_id],
                "text": text,
                "image": _image_relpath(sample_id),
            }
        )
    samples.sort(key=lambda s: s["sample_id"])

    return {
        "name": "iam-line-split-real",
        "dataset_version": IAM_DATASET_VERSION,
        "split": asdict(split),
        "provenance": {
            "source": "local real IAM download (ascii/forms.txt + ascii/lines.txt)",
            "writer_disjoint": True,
            "seed": seed,
            "note": (
                "Generated locally from a licensed IAM download; not derived "
                "from or redistributing any IAM-owned data in this repo."
            ),
        },
        "samples": samples,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build a local IAM split manifest (real transcriptions) from a "
            "licensed IAM download's ascii/ directory."
        )
    )
    parser.add_argument("--ascii-dir", type=Path, required=True)
    parser.add_argument("--images-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=1337)
    parser.add_argument(
        "--max-resized-width",
        type=int,
        default=None,
        help="Drop lines wider than this after resizing to the model's image height.",
    )
    args = parser.parse_args(argv)

    manifest = build_manifest(
        args.ascii_dir,
        args.images_dir,
        seed=args.seed,
        max_resized_width=args.max_resized_width,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    counts = {"train": 0, "val": 0, "test": 0}
    for s in manifest["samples"]:
        counts[s["split"]] += 1
    print(
        f"Wrote {len(manifest['samples'])} samples to {args.output} "
        f"(train={counts['train']}, val={counts['val']}, test={counts['test']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
