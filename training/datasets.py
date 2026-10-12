"""Validated formula-crop datasets and deterministic InkML rasterization."""

from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
import torch
from PIL import Image, ImageDraw
from torch import Tensor
from torch.utils.data import Dataset

from app.ml.math.tokenizer import LatexTokenizer


@dataclass(frozen=True)
class FormulaRecord:
    sample_id: str
    image: Path | None
    inkml: Path | None
    latex: str
    raw_label: str | None = None
    split: str = "train"
    source_document_id: str | None = None
    page_id: str | None = None
    metadata: dict[str, Any] | None = None

    @property
    def normalized_label(self) -> str:
        """Canonical label name used by MathWriting/HME manifests."""
        return self.latex


def _read_jsonl(manifest: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(manifest.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{manifest}:{line_number}: invalid JSON") from exc
        if not isinstance(item, dict):
            raise ValueError(f"{manifest}:{line_number}: record must be an object")
        records.append(item)
    if not records:
        raise ValueError(f"{manifest}: manifest contains no samples")
    return records


def load_formula_records(
    manifest: str | Path,
    *,
    tokenizer: LatexTokenizer | None = None,
    split: str | None = None,
    require_files: bool = True,
) -> list[FormulaRecord]:
    """Load and validate JSONL records without pairing arbitrary labels/images."""
    path = Path(manifest)
    tokenizer = tokenizer or LatexTokenizer.default()
    raw_records = _read_jsonl(path)

    # Validate the page/document split assignment before applying the optional
    # sample filter.  Checking only the requested split would let a caller
    # accidentally train on a document whose other page appears in dev/test.
    source_splits: dict[str, str] = {}
    for index, raw in enumerate(raw_records):
        record_split = str(raw.get("split", "train")).lower()
        if record_split not in {"train", "dev", "test", "unassigned"}:
            raise ValueError(f"record {index} has unsupported split {record_split!r}")
        source = raw.get("source_document_id") or raw.get("document_id")
        if source is None:
            continue
        source_key = str(source)
        previous = source_splits.get(source_key)
        if previous is not None and previous != record_split:
            raise ValueError(
                f"source document {source_key!r} appears in multiple splits; split pages before cropping"
            )
        source_splits[source_key] = record_split

    records: list[FormulaRecord] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_records):
        sample_id = raw.get("sample_id", f"sample-{index:06d}")
        if not isinstance(sample_id, str) or not sample_id:
            raise ValueError(f"record {index} has an invalid sample_id")
        if sample_id in seen:
            raise ValueError(f"duplicate sample_id: {sample_id}")
        seen.add(sample_id)
        latex = raw.get("normalizedLabel", raw.get("normalized_label", raw.get("latex")))
        if not isinstance(latex, str) or not latex.strip():
            raise ValueError(f"sample {sample_id} has no non-empty normalized LaTeX label")
        # Validation is deliberately strict: an unsupported label cannot be
        # silently converted to a different target vocabulary.
        tokenizer.encode(latex)
        record_split = str(raw.get("split", "train")).lower()
        if split is not None and record_split != split:
            continue
        source = raw.get("source_document_id") or raw.get("document_id")
        if source is not None:
            source = str(source)
        image_value = raw.get("image")
        inkml_value = raw.get("inkml")
        image = (path.parent / str(image_value)).resolve() if image_value else None
        inkml = (path.parent / str(inkml_value)).resolve() if inkml_value else None
        if require_files and image is None and inkml is None:
            raise ValueError(f"sample {sample_id} has neither image nor inkml")
        if require_files and image is not None and not image.is_file() and inkml is None:
            raise FileNotFoundError(f"image for sample {sample_id} was not found: {image}")
        if require_files and inkml is not None and not inkml.is_file() and image is None:
            raise FileNotFoundError(f"InkML for sample {sample_id} was not found: {inkml}")
        raw_label = raw.get("raw_label", raw.get("rawLabel", raw.get("label")))
        records.append(FormulaRecord(
            sample_id=sample_id,
            image=image if image and image.is_file() else None,
            inkml=inkml if inkml and inkml.is_file() else None,
            latex=latex,
            raw_label=str(raw_label) if raw_label is not None else None,
            split=record_split,
            source_document_id=source,
            page_id=str(raw["page_id"]) if raw.get("page_id") is not None else None,
            metadata={key: value for key, value in raw.items() if key not in {"image", "inkml", "latex"}},
        ))
    records.sort(key=lambda record: record.sample_id)
    return records


def _trace_points(value: str) -> list[tuple[float, float]]:
    # InkML commonly separates pairs with commas, but exports also exist with
    # whitespace-only pairs.  Parsing the numeric stream in pairs handles both
    # forms without depending on a vendor-specific delimiter.
    values = re.findall(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", value)
    points: list[tuple[float, float]] = []
    for index in range(0, len(values) - 1, 2):
        try:
            points.append((float(values[index]), float(values[index + 1])))
        except ValueError:
            continue
    return points


def rasterize_inkml(
    inkml: str | Path | bytes,
    *,
    size: tuple[int, int] = (128, 512),
    margin: int = 8,
    stroke_width: int = 2,
) -> Image.Image:
    """Rasterize InkML traces deterministically while retaining aspect ratio."""
    if size[0] <= 0 or size[1] <= 0 or margin < 0 or stroke_width <= 0 or margin * 2 >= min(size):
        raise ValueError("size, margin, and stroke_width must be positive")
    root = ET.fromstring(Path(inkml).read_bytes() if isinstance(inkml, (str, Path)) else inkml)
    traces: list[list[tuple[float, float]]] = []
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] != "trace" or not element.text:
            continue
        points = _trace_points(element.text)
        if len(points) >= 1:
            traces.append(points)
    canvas_h, canvas_w = size
    canvas = Image.new("L", (canvas_w, canvas_h), color=255)
    if not traces:
        return canvas
    all_points = [point for trace in traces for point in trace]
    min_x = min(point[0] for point in all_points)
    max_x = max(point[0] for point in all_points)
    min_y = min(point[1] for point in all_points)
    max_y = max(point[1] for point in all_points)
    source_w = max(max_x - min_x, 1e-6)
    source_h = max(max_y - min_y, 1e-6)
    scale = min((canvas_w - 2 * margin) / source_w, (canvas_h - 2 * margin) / source_h)
    scale = max(scale, 1e-6)
    offset_x = (canvas_w - source_w * scale) / 2.0
    offset_y = (canvas_h - source_h * scale) / 2.0
    draw = ImageDraw.Draw(canvas)
    for trace in traces:
        mapped = [
            (round((x - min_x) * scale + offset_x), round((y - min_y) * scale + offset_y))
            for x, y in trace
        ]
        if len(mapped) == 1:
            x, y = mapped[0]
            draw.ellipse((x - stroke_width, y - stroke_width, x + stroke_width, y + stroke_width), fill=0)
        else:
            draw.line(mapped, fill=0, width=stroke_width, joint="curve")
    return canvas


def _fit_image(image: Image.Image, image_height: int, max_width: int | None = None) -> tuple[Tensor, Tensor]:
    image = image.convert("L")
    array = np.asarray(image, dtype=np.float32) / 255.0
    height, width = array.shape
    scaled_width = max(1, int(round(width * image_height / max(height, 1))))
    if max_width is not None and scaled_width > max_width:
        raise ValueError(f"sample width {scaled_width} exceeds bucket limit {max_width}; use a larger bucket")
    tensor = torch.from_numpy(array).unsqueeze(0).unsqueeze(0)
    tensor = torch.nn.functional.interpolate(tensor, size=(image_height, scaled_width), mode="bilinear", align_corners=False).squeeze(0)
    return tensor, torch.ones(image_height, scaled_width, dtype=torch.bool)


class FormulaCropDataset(Dataset):
    """Dataset used by both pilots and the full compact-model CLI."""

    def __init__(
        self,
        records: Sequence[FormulaRecord],
        tokenizer: LatexTokenizer | None = None,
        *,
        image_height: int = 128,
        max_width: int | None = 512,
    ) -> None:
        self.records = list(records)
        self.tokenizer = tokenizer or LatexTokenizer.default()
        self.image_height = image_height
        self.max_width = max_width

    def __len__(self) -> int:
        return len(self.records)

    def __getitem__(self, index: int) -> dict[str, Any]:
        record = self.records[index]
        if record.image is not None:
            with Image.open(record.image) as opened:
                image = opened.copy()
        elif record.inkml is not None:
            image = rasterize_inkml(record.inkml, size=(self.image_height, self.max_width or 512))
        else:  # validated by the loader; defensive for programmatically made records
            raise FileNotFoundError(f"sample {record.sample_id} has no image source")
        tensor, valid = _fit_image(image, self.image_height, self.max_width)
        return {
            "sample_id": record.sample_id,
            "image": tensor,
            "tokens": torch.tensor(self.tokenizer.encode(record.latex), dtype=torch.long),
            "valid": valid,
            "latex": record.latex,
            "page_id": record.page_id,
        }


def bucket_key(item: dict[str, Any]) -> tuple[int, int]:
    """Stable resolution bucket key used by a sampler or diagnostic."""
    return (int(item["image"].size(-2)), int(item["image"].size(-1)))


def dataset_digest(records: Iterable[FormulaRecord]) -> str:
    hasher = hashlib.sha256()
    for record in sorted(records, key=lambda item: item.sample_id):
        hasher.update(record.sample_id.encode())
        hasher.update(b"\0")
        hasher.update(record.latex.encode())
        hasher.update(b"\0")
        hasher.update(record.split.encode())
        hasher.update(b"\0")
        if record.source_document_id:
            hasher.update(record.source_document_id.encode())
            hasher.update(b"\0")
        if record.image and record.image.is_file():
            hasher.update(hashlib.sha256(record.image.read_bytes()).digest())
        elif record.inkml and record.inkml.is_file():
            hasher.update(hashlib.sha256(record.inkml.read_bytes()).digest())
    return hasher.hexdigest()


__all__ = [
    "FormulaRecord",
    "FormulaCropDataset",
    "load_formula_records",
    "rasterize_inkml",
    "bucket_key",
    "dataset_digest",
]
