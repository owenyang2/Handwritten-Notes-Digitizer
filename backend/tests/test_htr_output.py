"""Unit tests for HTR pipeline output on fixed synthetic sample images (task 7.6).

These tests validate the *observable output* of the full HTR pipeline
(preprocess → segment → recognize) against synthetic images whose layout is
fully controlled.  Fixtures are created with Pillow using solid dark rectangles
as stand-ins for handwritten word blobs.

Validates: Requirements 2.1, 2.2, 2.5

Coverage
--------
* A multi-line synthetic page produces multiple RecognizedLine objects.
* Every line contains multiple RecognizedWord objects (word-level grouping,
  not isolated single-pixel characters).
* Each RecognizedWord carries a positive-area region (Req 2.4).
* Word blocks that are close together within a line stay as one word
  (not split into isolated characters — Req 2.2).
* A completely blank/no-text image returns recognized_any_text=False with
  an empty lines list (Req 2.5).
* A single-line, single-word image produces exactly one line with one word.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image as PILImage, ImageDraw

from app.ml.htr_model import build_model
from app.ml.recognition import RecognitionResult, RecognizedLine, RecognizedWord, recognize_bytes


# ---------------------------------------------------------------------------
# Synthetic image helpers
# ---------------------------------------------------------------------------

def _to_png_bytes(img: PILImage.Image) -> bytes:
    """Serialise a Pillow image to PNG bytes."""
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_multiline_page(
    width: int = 300,
    height: int = 160,
    line_configs: list[list[tuple[int, int, int, int]]] | None = None,
) -> bytes:
    """Return a PNG with multiple text lines made from solid dark rectangles.

    Each entry in *line_configs* is a list of ``(x, y, word_w, word_h)``
    tuples that form one line.  Defaults to a two-line page:
      - Line 0: three word blobs at y≈20
      - Line 1: two word blobs at y≈100
    """
    if line_configs is None:
        line_configs = [
            # Line 0: three words
            [(10, 20, 40, 22), (80, 20, 40, 22), (150, 20, 40, 22)],
            # Line 1: two words
            [(10, 100, 50, 22), (130, 100, 50, 22)],
        ]
    img = PILImage.new("L", (width, height), color=255)
    draw = ImageDraw.Draw(img)
    for line in line_configs:
        for x, y, w, h in line:
            draw.rectangle([x, y, x + w - 1, y + h - 1], fill=0)
    return _to_png_bytes(img)


def _make_blank_page(width: int = 200, height: int = 120) -> bytes:
    """Return a plain white PNG — no ink at all."""
    img = PILImage.new("L", (width, height), color=255)
    return _to_png_bytes(img)


def _make_single_line_page(
    n_words: int = 3,
    word_w: int = 35,
    word_h: int = 20,
    gap: int = 20,
    margin: int = 10,
    y: int = 30,
    page_height: int = 80,
) -> bytes:
    """Return a PNG with a single line containing *n_words* word blobs."""
    page_width = margin * 2 + n_words * word_w + (n_words - 1) * gap
    img = PILImage.new("L", (page_width, page_height), color=255)
    draw = ImageDraw.Draw(img)
    for i in range(n_words):
        x = margin + i * (word_w + gap)
        draw.rectangle([x, y, x + word_w - 1, y + word_h - 1], fill=0)
    return _to_png_bytes(img)


def _make_close_glyphs_word_page() -> bytes:
    """Page with one line whose single word is made of tightly spaced glyph blocks.

    Three 10×20 rectangles with 2px inter-glyph gaps represent one word.
    The segmenter must group them as *one* word, not three isolated characters
    (Req 2.2).
    """
    img = PILImage.new("L", (160, 60), color=255)
    draw = ImageDraw.Draw(img)
    # Glyph 1
    draw.rectangle([20, 20, 29, 39], fill=0)
    # Glyph 2 — 2px gap
    draw.rectangle([32, 20, 41, 39], fill=0)
    # Glyph 3 — 2px gap
    draw.rectangle([44, 20, 53, 39], fill=0)
    return _to_png_bytes(img)


# ---------------------------------------------------------------------------
# Shared model fixture (built once per test session for speed)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def base_model():
    """A freshly initialised (untrained) CRNN, shared across tests."""
    return build_model()


# ---------------------------------------------------------------------------
# Test 1: Multi-line page produces multiple lines (Req 2.1)
# ---------------------------------------------------------------------------

def test_multiline_page_produces_multiple_lines(base_model):
    """A two-line synthetic page must yield at least two RecognizedLine objects.

    Validates: Requirements 2.1
    """
    img_bytes = _make_multiline_page()
    result = recognize_bytes(img_bytes, model=base_model)

    assert isinstance(result, RecognitionResult)
    assert len(result.lines) >= 2, (
        f"Expected at least 2 lines from a two-line page, got {len(result.lines)}"
    )


# ---------------------------------------------------------------------------
# Test 2: Lines contain multiple words — word-level segmentation (Req 2.2)
# ---------------------------------------------------------------------------

def test_lines_are_segmented_into_words_not_characters(base_model):
    """Each line in the recognition result must contain multiple word groups.

    The synthetic page has three word blobs on line 0 and two on line 1.
    The pipeline must return those grouped blobs as RecognizedWord objects
    (not individual pixel-level characters).

    Validates: Requirements 2.2
    """
    img_bytes = _make_multiline_page()
    result = recognize_bytes(img_bytes, model=base_model)

    assert len(result.lines) >= 2

    # Line 0 must have the three word blobs; line 1 must have the two word blobs.
    first_line_word_count = result.lines[0].words
    second_line_word_count = result.lines[1].words

    assert len(first_line_word_count) == 3, (
        f"Line 0: expected 3 words, got {len(first_line_word_count)}"
    )
    assert len(second_line_word_count) == 2, (
        f"Line 1: expected 2 words, got {len(second_line_word_count)}"
    )


# ---------------------------------------------------------------------------
# Test 3: Blank/no-text image → recognized_any_text = False (Req 2.5)
# ---------------------------------------------------------------------------

def test_blank_page_returns_no_text_result(base_model):
    """A completely blank image must yield recognized_any_text=False and no lines.

    Validates: Requirements 2.5
    """
    img_bytes = _make_blank_page()
    result = recognize_bytes(img_bytes, model=base_model)

    assert isinstance(result, RecognitionResult)
    assert result.recognized_any_text is False, (
        "recognized_any_text must be False for a blank (no-ink) image"
    )
    assert result.lines == [], (
        f"Expected empty lines list for blank page, got {result.lines!r}"
    )


# ---------------------------------------------------------------------------
# Test 4: Single-line page — correct line and word counts (Req 2.1, 2.2)
# ---------------------------------------------------------------------------

def test_single_line_page_produces_one_line_with_words(base_model):
    """A single-line page with three word blobs produces exactly one line with
    three words.

    Validates: Requirements 2.1, 2.2
    """
    img_bytes = _make_single_line_page(n_words=3)
    result = recognize_bytes(img_bytes, model=base_model)

    assert len(result.lines) == 1, (
        f"Expected 1 line, got {len(result.lines)}"
    )
    assert len(result.lines[0].words) == 3, (
        f"Expected 3 words in the single line, got {len(result.lines[0].words)}"
    )


# ---------------------------------------------------------------------------
# Test 5: Close glyph blocks within one word stay as one word (Req 2.2)
# ---------------------------------------------------------------------------

def test_close_glyph_blocks_are_not_split_into_isolated_characters(base_model):
    """Three tightly spaced glyph blobs (2px gap) must be grouped as one word,
    not split into individual characters.

    This directly verifies the 'not isolated single characters' requirement
    from Req 2.2.

    Validates: Requirements 2.2
    """
    img_bytes = _make_close_glyphs_word_page()
    result = recognize_bytes(img_bytes, model=base_model)

    assert len(result.lines) == 1, (
        f"Expected 1 line for the glyph-cluster page, got {len(result.lines)}"
    )
    assert len(result.lines[0].words) == 1, (
        f"Expected 1 word (glyph cluster), got {len(result.lines[0].words)} words — "
        "close glyph blobs must not be split into isolated characters (Req 2.2)"
    )


# ---------------------------------------------------------------------------
# Test 6: Every word carries a positive-area bounding region (Req 2.4)
# ---------------------------------------------------------------------------

def test_every_word_in_result_has_positive_area_region(base_model):
    """All RecognizedWord objects must carry a region with positive width and height.

    Validates: Requirements 2.1, 2.2
    """
    img_bytes = _make_multiline_page()
    result = recognize_bytes(img_bytes, model=base_model)

    for line_idx, line in enumerate(result.lines):
        assert isinstance(line, RecognizedLine)
        assert len(line.words) > 0, (
            f"Line {line_idx} has no words — each detected line must contain words"
        )
        for word_idx, word in enumerate(line.words):
            assert isinstance(word, RecognizedWord)
            assert word.region.width > 0, (
                f"Word [{line_idx},{word_idx}] has zero-width region"
            )
            assert word.region.height > 0, (
                f"Word [{line_idx},{word_idx}] has zero-height region"
            )


# ---------------------------------------------------------------------------
# Test 7: Lines are ordered top-to-bottom, words left-to-right (Req 2.1, 2.2)
# ---------------------------------------------------------------------------

def test_line_and_word_ordering_in_multiline_result(base_model):
    """Lines must be ordered top-to-bottom; words within each line left-to-right.

    Validates: Requirements 2.1, 2.2
    """
    img_bytes = _make_multiline_page()
    result = recognize_bytes(img_bytes, model=base_model)

    line_tops = [line.region.y for line in result.lines]
    assert line_tops == sorted(line_tops), (
        f"Lines are not top-to-bottom ordered: y values = {line_tops}"
    )

    for line_idx, line in enumerate(result.lines):
        word_lefts = [w.region.x for w in line.words]
        assert word_lefts == sorted(word_lefts), (
            f"Words in line {line_idx} are not left-to-right: x values = {word_lefts}"
        )


# ---------------------------------------------------------------------------
# Test 8: result.recognized_any_text type contract always holds (Req 2.5)
# ---------------------------------------------------------------------------

def test_recognized_any_text_is_bool_for_all_fixtures(base_model):
    """recognized_any_text must be a bool regardless of image content.

    Validates: Requirements 2.5
    """
    for label, img_bytes in [
        ("blank", _make_blank_page()),
        ("multiline", _make_multiline_page()),
        ("single_line", _make_single_line_page()),
    ]:
        result = recognize_bytes(img_bytes, model=base_model)
        assert isinstance(result.recognized_any_text, bool), (
            f"recognized_any_text is not bool for fixture '{label}': "
            f"{type(result.recognized_any_text)}"
        )
