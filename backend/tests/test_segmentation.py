"""Unit tests for HTR preprocessing + layout/word segmentation (task 7.1).

These tests draw small synthetic pages (dark "ink" blocks on a white page) with
Pillow, then assert the segmenter:

* orders detected lines top-to-bottom,
* orders words within a line left-to-right,
* emits a crop and an in-bounds ``{x, y, width, height}`` region per word,
* round-trips raw PNG/JPEG bytes through decode → preprocess → segment, and
* handles blank pages (no text) and undecodable bytes gracefully.

Synthetic images use clearly separated word blocks so projection-profile
segmentation is deterministic and the ordering guarantees are unambiguous.
"""

from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image, ImageDraw

from app.ml.preprocessing import (
    ImageDecodeError,
    PreprocessConfig,
    decode_image,
    deskew,
    ink_mask_from_gray,
    normalize,
    otsu_threshold,
    preprocess,
)
from app.ml.segmentation import (
    Region,
    SegmentationResult,
    segment,
    segment_image,
)


# --------------------------------------------------------------------------- #
# Synthetic-image helpers
# --------------------------------------------------------------------------- #

def _blank_page(width: int = 200, height: int = 120) -> Image.Image:
    """A white page with no ink."""
    return Image.new("L", (width, height), color=255)


def _draw_word(draw: ImageDraw.ImageDraw, x: int, y: int, w: int, h: int) -> None:
    """Draw a solid dark rectangle standing in for a handwritten word."""
    draw.rectangle([x, y, x + w - 1, y + h - 1], fill=0)


def _page_bytes(img: Image.Image, fmt: str = "PNG") -> bytes:
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


def _two_lines_three_and_two_words() -> Image.Image:
    """Page with two text lines: top line has 3 words, bottom line has 2.

    Word blocks are separated by large horizontal gaps and the two lines by a
    large vertical gap so segmentation is unambiguous.
    """
    img = _blank_page(width=240, height=140)
    draw = ImageDraw.Draw(img)
    # Top line (y ~ 20..40): three words across the page.
    _draw_word(draw, x=10, y=20, w=30, h=20)
    _draw_word(draw, x=80, y=20, w=30, h=20)
    _draw_word(draw, x=150, y=20, w=30, h=20)
    # Bottom line (y ~ 90..110): two words.
    _draw_word(draw, x=10, y=90, w=40, h=20)
    _draw_word(draw, x=120, y=90, w=40, h=20)
    return img


# --------------------------------------------------------------------------- #
# Preprocessing tests
# --------------------------------------------------------------------------- #

def test_decode_grayscale_from_png_and_jpeg():
    img = _two_lines_three_and_two_words()
    for fmt in ("PNG", "JPEG"):
        gray = decode_image(_page_bytes(img, fmt))
        assert gray.ndim == 2
        assert gray.dtype == np.uint8
        assert gray.shape == (140, 240)


def test_decode_rejects_empty_and_garbage_bytes():
    with pytest.raises(ImageDecodeError):
        decode_image(b"")
    with pytest.raises(ImageDecodeError):
        decode_image(b"this is not an image")


def test_normalize_scales_to_unit_range():
    gray = decode_image(_page_bytes(_two_lines_three_and_two_words()))
    norm = normalize(gray)
    assert norm.dtype == np.float32
    assert float(norm.min()) >= 0.0
    assert float(norm.max()) <= 1.0


def test_ink_mask_marks_text_not_background():
    gray = decode_image(_page_bytes(_two_lines_three_and_two_words()))
    mask = ink_mask_from_gray(gray)
    # There is ink, but the page is mostly background.
    assert mask.any()
    assert mask.sum() < mask.size / 2


def test_blank_page_has_no_ink():
    gray = decode_image(_page_bytes(_blank_page()))
    assert not ink_mask_from_gray(gray).any()


def test_deskew_is_noop_on_upright_image():
    gray = decode_image(_page_bytes(_two_lines_three_and_two_words()))
    out = deskew(gray, PreprocessConfig())
    # Upright synthetic page should not be rotated/resampled.
    assert np.array_equal(out, gray)


def test_deskew_straightens_a_rotated_page():
    img = _two_lines_three_and_two_words()
    rotated = img.rotate(-6.0, resample=Image.BILINEAR, expand=False, fillcolor=255)
    gray = np.asarray(rotated, dtype=np.uint8)

    before = ink_mask_from_gray(gray).sum(axis=1).astype(np.float64)
    after = ink_mask_from_gray(deskew(gray)).sum(axis=1).astype(np.float64)

    # A straighter page has a spikier row-projection profile.
    def sharpness(profile: np.ndarray) -> float:
        diffs = np.diff(profile)
        return float(np.dot(diffs, diffs))

    assert sharpness(after) > sharpness(before)


def test_otsu_threshold_between_ink_and_background():
    # Mid-gray ink (value 80) on a white page so the optimal split lands
    # strictly between the two intensity classes.
    img = _blank_page(width=120, height=60)
    draw = ImageDraw.Draw(img)
    draw.rectangle([10, 10, 50, 40], fill=80)
    gray = decode_image(_page_bytes(img))
    t = otsu_threshold(gray)
    assert 80 <= t < 255
    # The ink mask selects the mid-gray block, not the white page.
    mask = ink_mask_from_gray(gray, t)
    assert mask.any() and mask.sum() < mask.size / 2


# --------------------------------------------------------------------------- #
# Segmentation tests
# --------------------------------------------------------------------------- #

def _segment_default() -> SegmentationResult:
    return segment(preprocess(_page_bytes(_two_lines_three_and_two_words())))


def test_lines_are_ordered_top_to_bottom():
    result = _segment_default()
    assert len(result.lines) == 2
    tops = [line.region.y for line in result.lines]
    assert tops == sorted(tops)
    assert [line.index for line in result.lines] == [0, 1]


def test_words_are_ordered_left_to_right():
    result = _segment_default()
    assert [len(line.words) for line in result.lines] == [3, 2]
    for line in result.lines:
        lefts = [w.region.x for w in line.words]
        assert lefts == sorted(lefts)
        assert [w.index for w in line.words] == list(range(len(line.words)))


def test_every_word_region_is_in_bounds_and_positive():
    result = _segment_default()
    for line in result.lines:
        for word in line.words:
            region = word.region
            assert region.is_within(result.image_width, result.image_height)
            assert region.width > 0 and region.height > 0
            # Crop dimensions match the region exactly.
            assert word.crop.shape == (region.height, region.width)


def test_word_regions_within_a_line_do_not_horizontally_overlap():
    result = _segment_default()
    for line in result.lines:
        ordered = sorted(line.words, key=lambda w: w.region.x)
        for prev, nxt in zip(ordered, ordered[1:]):
            assert prev.region.right <= nxt.region.x


def test_line_region_encloses_its_words():
    result = _segment_default()
    for line in result.lines:
        for word in line.words:
            assert word.region.x >= line.region.x
            assert word.region.y >= line.region.y
            assert word.region.right <= line.region.right
            assert word.region.bottom <= line.region.bottom


def test_segment_image_from_bytes_png_and_jpeg():
    img = _two_lines_three_and_two_words()
    for fmt in ("PNG", "JPEG"):
        result = segment_image(_page_bytes(img, fmt))
        # JPEG introduces mild artefacts but the layout is unchanged.
        assert len(result.lines) == 2
        assert [len(line.words) for line in result.lines] == [3, 2]


def test_blank_page_yields_no_lines_or_words():
    result = segment_image(_page_bytes(_blank_page()))
    assert result.lines == []
    assert result.recognized_any_region is False


def test_words_are_not_split_into_isolated_characters():
    """Closely spaced character blocks inside a word stay one word (Req 2.2)."""
    img = _blank_page(width=160, height=60)
    draw = ImageDraw.Draw(img)
    # One word made of three glyphs with small (2px) inter-character gaps.
    _draw_word(draw, x=20, y=20, w=10, h=20)
    _draw_word(draw, x=32, y=20, w=10, h=20)
    _draw_word(draw, x=44, y=20, w=10, h=20)
    result = segment(preprocess(_page_bytes(img)))
    assert len(result.lines) == 1
    assert len(result.lines[0].words) == 1


def test_tiny_detached_marks_do_not_create_reviewable_lines():
    """Specks between real lines must not become false OCR line regions."""
    img = _blank_page(width=464, height=301)
    draw = ImageDraw.Draw(img)
    _draw_word(draw, x=16, y=26, w=406, h=52)
    _draw_word(draw, x=37, y=114, w=191, h=63)
    # Detached marks comparable to a dot or short pen fragment.
    _draw_word(draw, x=130, y=97, w=6, h=5)
    _draw_word(draw, x=39, y=105, w=4, h=7)

    result = segment(preprocess(_page_bytes(img)))

    assert len(result.lines) == 2
    assert [line.index for line in result.lines] == [0, 1]


def test_narrow_line_with_substantial_ink_is_preserved():
    """A valid narrow line must not be confused with a small detached mark."""
    img = _blank_page(width=160, height=100)
    draw = ImageDraw.Draw(img)
    _draw_word(draw, x=40, y=20, w=8, h=40)

    result = segment(preprocess(_page_bytes(img)))

    assert len(result.lines) == 1
    assert result.lines[0].region.width < 12


def test_region_to_dict_shape():
    region = Region(x=1, y=2, width=3, height=4)
    assert region.to_dict() == {"x": 1, "y": 2, "width": 3, "height": 4}
    assert region.is_within(10, 10)
    assert not region.is_within(2, 10)
