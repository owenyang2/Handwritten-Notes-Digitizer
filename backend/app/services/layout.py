"""Deterministic, conservative page-layout helpers.

The first release deliberately keeps layout detection simple.  It finds
ink-bearing horizontal bands and returns crops that a reviewer can correct.
It never claims that a page is completely understood: an empty result is
reported to the caller and every generated region remains editable/deletable.
This makes segmentation recall measurable before a learned layout model is
introduced.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import numpy as np
from PIL import Image


@dataclass(frozen=True)
class LayoutBox:
    x: int
    y: int
    width: int
    height: int
    content_type: str = "math_display"

    @property
    def as_list(self) -> list[int]:
        return [self.x, self.y, self.width, self.height]


def detect_page_regions(
    png_bytes: bytes,
    *,
    min_ink_fraction: float = 0.002,
    row_gap: int = 12,
    padding: int = 4,
) -> list[LayoutBox]:
    """Return conservative ink-band boxes for a page raster.

    This is a baseline layout proposal, not ground truth.  It is intentionally
    deterministic and bounded: it decodes one image, uses projections rather
    than recursive connected-component growth, and caps the number of bands.
    """
    if not 0 < min_ink_fraction < 1:
        raise ValueError("min_ink_fraction must be between 0 and 1")
    if row_gap < 0 or padding < 0:
        raise ValueError("row_gap and padding must be non-negative")
    with Image.open(io.BytesIO(png_bytes)) as source:
        image = source.convert("L")
        pixels = np.asarray(image, dtype=np.uint8)
    height, width = pixels.shape
    # A modest threshold handles both white paper and light gray scans.  The
    # border is not removed because handwritten marks at a page edge matter.
    ink = pixels < 220
    row_counts = ink.sum(axis=1)
    active = row_counts >= max(1, int(width * min_ink_fraction))
    bands: list[tuple[int, int]] = []
    start: int | None = None
    gap = 0
    for row, is_active in enumerate(active.tolist() + [False] * (row_gap + 1)):
        if is_active:
            if start is None:
                start = row
            gap = 0
            continue
        if start is None:
            continue
        gap += 1
        if gap > row_gap:
            end = row - gap + 1
            if end > start:
                bands.append((start, end))
            start = None
            gap = 0

    boxes: list[LayoutBox] = []
    for top, bottom in bands[:256]:
        band = ink[top:bottom]
        columns = np.flatnonzero(band.any(axis=0))
        if columns.size == 0:
            continue
        left = max(0, int(columns[0]) - padding)
        right = min(width, int(columns[-1]) + 1 + padding)
        y = max(0, top - padding)
        lower = min(height, bottom + padding)
        if right - left > 2 and lower - y > 2:
            boxes.append(LayoutBox(left, y, right - left, lower - y))
    return boxes


def crop_png(png_bytes: bytes, box: LayoutBox | tuple[int, int, int, int]) -> bytes:
    """Crop a validated page box and return a normalized RGB PNG."""
    values = box.as_list if isinstance(box, LayoutBox) else list(box)
    x, y, width, height = (int(value) for value in values)
    if width <= 0 or height <= 0 or x < 0 or y < 0:
        raise ValueError("crop must have a positive, non-negative box")
    with Image.open(io.BytesIO(png_bytes)) as source:
        if x + width > source.width or y + height > source.height:
            raise ValueError("crop is outside the page raster")
        result = source.convert("RGB").crop((x, y, x + width, y + height))
        output = io.BytesIO()
        result.save(output, format="PNG")
        return output.getvalue()


__all__ = ["LayoutBox", "detect_page_regions", "crop_png"]
