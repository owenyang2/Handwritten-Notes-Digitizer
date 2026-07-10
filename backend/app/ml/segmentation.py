"""Layout analysis: line and word segmentation for the HTR_Engine (task 7.1).

This module implements the second stage of the engine pipeline (HTR_Engine →
Layout/segmentation). Given a :class:`~app.ml.preprocessing.PreprocessedImage`
(or raw image bytes), it detects text *lines* ordered top-to-bottom and, within
each line, *words* ordered left-to-right, producing a crop and an
in-bounds bounding region ``{x, y, width, height}`` for each word
(Requirements 2.1, 2.2, and the coordinate guarantee behind Property 4).

Method
------
The segmenter uses classic projection profiles, which are robust and cheap and
make the ordering guarantees explicit:

* **Lines** — sum the ink mask across each row. Contiguous runs of rows whose
  ink exceeds a threshold are line bands; runs are produced top-to-bottom.
* **Words** — within a line band, sum the ink mask across each column.
  Contiguous runs of ink columns are character/word fragments; fragments
  separated by a horizontal gap smaller than ``word_gap`` are merged into one
  word (inter-character gaps are smaller than inter-word gaps). Runs are
  produced left-to-right.

Each word's region is tightened to the bounding box of its ink within the band,
so reported coordinates are minimal and always lie inside the source image.

Data structures
---------------
:class:`Region`, :class:`WordSegment`, :class:`LineSegment`, and
:class:`SegmentationResult` form the typed output consumed by the recognition /
assembly stage (task 7.2). ``Region`` matches the ``{x, y, width, height}``
shape used by the ``RecognizedWord`` data model (design Data Models).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from app.ml.preprocessing import (
    PreprocessConfig,
    PreprocessedImage,
    preprocess,
)


@dataclass(frozen=True)
class Region:
    """An axis-aligned bounding box in image pixel coordinates.

    Mirrors the ``{x, y, width, height}`` value object from the design. ``x`` /
    ``y`` are the top-left corner; ``width`` / ``height`` are strictly positive
    for any non-empty region.
    """

    x: int
    y: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    def is_within(self, image_width: int, image_height: int) -> bool:
        """True if the whole region lies inside an ``image_width × image_height``."""
        return (
            self.x >= 0
            and self.y >= 0
            and self.width > 0
            and self.height > 0
            and self.right <= image_width
            and self.bottom <= image_height
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
        }


@dataclass(frozen=True)
class WordSegment:
    """A segmented word: its reading-order index, region, and pixel crop."""

    index: int
    region: Region
    # Normalised float32 crop in [0, 1] of shape (region.height, region.width).
    crop: np.ndarray = field(repr=False)


@dataclass(frozen=True)
class LineSegment:
    """A segmented text line: its index, bounding region, and ordered words."""

    index: int
    region: Region
    words: list[WordSegment]


@dataclass(frozen=True)
class SegmentationResult:
    """The full layout result for one image.

    ``lines`` are ordered top-to-bottom and each line's ``words`` are ordered
    left-to-right. ``image_width`` / ``image_height`` define the coordinate
    space that every region is guaranteed to fall within.
    """

    lines: list[LineSegment]
    image_width: int
    image_height: int

    @property
    def recognized_any_region(self) -> bool:
        """True if at least one word region was detected."""
        return any(line.words for line in self.lines)


@dataclass(frozen=True)
class SegmentationConfig:
    """Tunable thresholds for projection-profile segmentation.

    The fractional thresholds adapt to image size, while ``min_*`` floors keep
    behaviour sane on tiny inputs. ``word_gap_*`` controls when adjacent ink
    fragments are merged into a single word.
    """

    # A row is part of a line if its ink-pixel count is at least this fraction
    # of the image width (floored by ``min_row_ink``).
    row_ink_fraction: float = 0.005
    min_row_ink: int = 1
    # A column is part of a word if its ink-pixel count within the line band is
    # at least this fraction of the band height (floored by ``min_col_ink``).
    col_ink_fraction: float = 0.05
    min_col_ink: int = 1
    # Adjacent ink column-runs separated by a gap smaller than
    # ``max(word_gap_fraction × band_height, word_gap_min_px)`` belong to the
    # same word. Inter-word gaps are wider than this; inter-character gaps are
    # narrower.
    word_gap_fraction: float = 0.6
    word_gap_min_px: int = 3
    # Ignore projection bands that are both narrow and contain very little ink.
    # This suppresses punctuation specks and detached pen marks without
    # discarding a narrow but substantial line such as a handwritten "I".
    min_line_width_px: int = 12
    min_line_ink_pixels: int = 36


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Return ``[start, end)`` index runs of consecutive ``True`` values.

    Runs are returned in increasing index order, which gives the top-to-bottom
    (rows) and left-to-right (columns) ordering guarantees directly.
    """
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for i, value in enumerate(mask):
        if value and start is None:
            start = i
        elif not value and start is not None:
            runs.append((start, i))
            start = None
    if start is not None:
        runs.append((start, len(mask)))
    return runs


def _merge_runs_by_gap(
    runs: list[tuple[int, int]], min_gap: int
) -> list[tuple[int, int]]:
    """Merge adjacent runs whose separating gap is smaller than ``min_gap``."""
    if not runs:
        return []
    merged: list[tuple[int, int]] = [runs[0]]
    for start, end in runs[1:]:
        prev_start, prev_end = merged[-1]
        if start - prev_end < min_gap:
            merged[-1] = (prev_start, end)
        else:
            merged.append((start, end))
    return merged


def _segment_words_in_band(
    ink_mask: np.ndarray,
    normalized: np.ndarray,
    band_top: int,
    band_bottom: int,
    config: SegmentationConfig,
) -> list[WordSegment]:
    """Segment a single line band into left-to-right word segments."""
    band_height = band_bottom - band_top
    band_mask = ink_mask[band_top:band_bottom, :]

    col_ink = band_mask.sum(axis=0)
    col_threshold = max(
        config.min_col_ink, int(round(config.col_ink_fraction * band_height))
    )
    is_ink_col = col_ink >= col_threshold

    word_gap = max(
        config.word_gap_min_px,
        int(round(config.word_gap_fraction * band_height)),
    )
    col_runs = _merge_runs_by_gap(_runs(is_ink_col), word_gap)

    words: list[WordSegment] = []
    for word_index, (x0, x1) in enumerate(col_runs):
        # Tighten the vertical extent to the ink rows of this word so the region
        # is the minimal bounding box (and never larger than the band).
        sub = band_mask[:, x0:x1]
        ink_rows = np.where(sub.any(axis=1))[0]
        if ink_rows.size == 0:
            continue
        top = band_top + int(ink_rows[0])
        bottom = band_top + int(ink_rows[-1]) + 1
        region = Region(x=x0, y=top, width=x1 - x0, height=bottom - top)
        crop = normalized[region.y : region.bottom, region.x : region.right]
        words.append(WordSegment(index=word_index, region=region, crop=crop))

    # Re-index in case any all-blank fragment was skipped.
    return [
        WordSegment(index=i, region=w.region, crop=w.crop)
        for i, w in enumerate(words)
    ]


def segment(
    image: PreprocessedImage, config: SegmentationConfig | None = None
) -> SegmentationResult:
    """Segment a preprocessed image into ordered lines and words.

    Lines are ordered top-to-bottom and words within each line left-to-right.
    Every returned region lies within ``image.width × image.height``.
    """
    config = config or SegmentationConfig()
    ink_mask = image.ink_mask
    normalized = image.normalized
    height, width = ink_mask.shape

    row_ink = ink_mask.sum(axis=1)
    row_threshold = max(
        config.min_row_ink, int(round(config.row_ink_fraction * width))
    )
    is_ink_row = row_ink >= row_threshold
    line_bands = _runs(is_ink_row)

    lines: list[LineSegment] = []
    line_index = 0
    for band_top, band_bottom in line_bands:
        words = _segment_words_in_band(
            ink_mask, normalized, band_top, band_bottom, config
        )
        if not words:
            continue
        # The line region spans from the first word's left to the last word's
        # right, and the min top / max bottom across its words.
        left = min(w.region.x for w in words)
        right = max(w.region.right for w in words)
        top = min(w.region.y for w in words)
        bottom = max(w.region.bottom for w in words)
        line_region = Region(
            x=left, y=top, width=right - left, height=bottom - top
        )
        line_ink_pixels = int(
            ink_mask[line_region.y : line_region.bottom, line_region.x : line_region.right].sum()
        )
        if (
            line_region.width < config.min_line_width_px
            and line_ink_pixels < config.min_line_ink_pixels
        ):
            continue
        lines.append(
            LineSegment(index=line_index, region=line_region, words=words)
        )
        line_index += 1

    return SegmentationResult(
        lines=lines, image_width=width, image_height=height
    )


def segment_image(
    image_bytes: bytes,
    preprocess_config: PreprocessConfig | None = None,
    segmentation_config: SegmentationConfig | None = None,
) -> SegmentationResult:
    """End-to-end: decode + preprocess raw bytes, then segment into lines/words.

    Raises
    ------
    app.ml.preprocessing.ImageDecodeError
        If the bytes cannot be decoded as an image.
    """
    preprocessed = preprocess(image_bytes, preprocess_config)
    return segment(preprocessed, segmentation_config)


__all__ = [
    "Region",
    "WordSegment",
    "LineSegment",
    "SegmentationResult",
    "SegmentationConfig",
    "segment",
    "segment_image",
]
