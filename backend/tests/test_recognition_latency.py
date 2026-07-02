"""Integration test for recognition latency (task 8.6).

Validates that the full recognition pipeline (preprocess → segment → CRNN
inference → result assembly) completes within 10 seconds for images containing
approximately 100 words, as required by Requirement 2.3:

    "WHEN recognition completes for an accepted image containing up to
    100 words, THE Recognition_Service SHALL return the recognized text
    within 10 seconds of image acceptance."

Test strategy
-------------
Three representative synthetic images are created using Pillow:

* **Fixture A** – portrait A4-style page, 10 lines × 10 words per line.
* **Fixture B** – landscape page, 5 lines × 20 words per line (wider words,
  more horizontal spread).
* **Fixture C** – portrait page with tighter word spacing, 12 lines × 8–9
  words per line (≈ 100 words total).

Each word is represented by a black-filled rectangle on a white background,
matching the ink pattern that the segmenter is tuned to detect. Real-size word
rectangles are used so the crop dimensions stress the CNN + BiLSTM stack in the
same proportion as genuine handwriting (40–120 px wide, 20–30 px tall).

The test uses the real pipeline — no mocking — and builds the model once
(module-level fixture) to avoid paying the model-construction cost per test.

**Validates: Requirements 2.3**
"""

from __future__ import annotations

import io
import time
from typing import List, Tuple

import pytest
from PIL import Image, ImageDraw

from app.ml.htr_model import build_model, CRNN
from app.ml.recognition import recognize_bytes, RecognitionResult

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

LATENCY_BUDGET_SECONDS: float = 10.0

# --------------------------------------------------------------------------- #
# Module-level model fixture (build once, reuse across all three test cases)
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def base_model() -> CRNN:
    """Build the CRNN base model once for the entire module."""
    return build_model()


# --------------------------------------------------------------------------- #
# Synthetic image builders
# --------------------------------------------------------------------------- #


def _make_100_word_image_portrait(
    page_width: int = 800,
    page_height: int = 1100,
    n_lines: int = 10,
    words_per_line: int = 10,
    word_width: int = 55,
    word_height: int = 22,
    line_spacing: int = 90,
    word_spacing: int = 15,
    margin_x: int = 40,
    margin_y: int = 60,
    fmt: str = "PNG",
) -> bytes:
    """Create a portrait-format page with *n_lines × words_per_line* word blobs.

    Parameters
    ----------
    page_width, page_height:
        Canvas dimensions in pixels.
    n_lines, words_per_line:
        Grid layout — total word count = ``n_lines * words_per_line``.
    word_width, word_height:
        Pixel dimensions of each word rectangle.
    line_spacing:
        Vertical distance between the top of successive lines (pixels).
    word_spacing:
        Horizontal gap between words (pixels).
    margin_x, margin_y:
        Left and top margins (pixels).
    fmt:
        PIL image format (``"PNG"`` by default).
    """
    img = Image.new("L", (page_width, page_height), color=255)
    draw = ImageDraw.Draw(img)

    for line_idx in range(n_lines):
        y0 = margin_y + line_idx * line_spacing
        y1 = y0 + word_height
        for word_idx in range(words_per_line):
            x0 = margin_x + word_idx * (word_width + word_spacing)
            x1 = x0 + word_width
            draw.rectangle([x0, y0, x1, y1], fill=0)

    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


def _make_100_word_image_landscape(
    page_width: int = 1200,
    page_height: int = 700,
    n_lines: int = 5,
    words_per_line: int = 20,
    word_width: int = 45,
    word_height: int = 22,
    line_spacing: int = 110,
    word_spacing: int = 12,
    margin_x: int = 30,
    margin_y: int = 50,
    fmt: str = "PNG",
) -> bytes:
    """Create a landscape-format page with 5 lines × 20 words (100 words total)."""
    img = Image.new("L", (page_width, page_height), color=255)
    draw = ImageDraw.Draw(img)

    for line_idx in range(n_lines):
        y0 = margin_y + line_idx * line_spacing
        y1 = y0 + word_height
        for word_idx in range(words_per_line):
            x0 = margin_x + word_idx * (word_width + word_spacing)
            x1 = x0 + word_width
            draw.rectangle([x0, y0, x1, y1], fill=0)

    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


def _make_100_word_image_dense(
    page_width: int = 750,
    page_height: int = 1050,
    fmt: str = "PNG",
) -> bytes:
    """Dense portrait layout: 12 lines with 8–9 words each (≈ 100 words total).

    Word widths vary slightly to simulate natural handwriting width variation.
    """
    img = Image.new("L", (page_width, page_height), color=255)
    draw = ImageDraw.Draw(img)

    # 12 lines; alternating 8 and 9 words so total ≈ 12 * 8.5 = 102 words.
    n_lines = 12
    margin_x = 35
    margin_y = 50
    line_spacing = 75
    word_height = 20

    for line_idx in range(n_lines):
        words_this_line = 9 if line_idx % 2 == 0 else 8
        y0 = margin_y + line_idx * line_spacing
        y1 = y0 + word_height
        # Vary word width between 50 and 70 px.
        base_word_w = 50 + (line_idx % 3) * 10  # 50, 60, or 70 px
        word_spacing = 12
        x = margin_x
        for _ in range(words_this_line):
            draw.rectangle([x, y0, x + base_word_w, y1], fill=0)
            x += base_word_w + word_spacing

    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _run_and_measure(image_bytes: bytes, model: CRNN) -> Tuple[RecognitionResult, float]:
    """Run recognize_bytes and return (result, elapsed_seconds)."""
    start = time.monotonic()
    result = recognize_bytes(image_bytes, model=model)
    elapsed = time.monotonic() - start
    return result, elapsed


# --------------------------------------------------------------------------- #
# Latency tests
# --------------------------------------------------------------------------- #


def test_portrait_100_words_within_budget(base_model: CRNN) -> None:
    """Portrait A4-style page (10 × 10 words) completes within 10 seconds.

    Validates: Requirements 2.3
    """
    image_bytes = _make_100_word_image_portrait()
    result, elapsed = _run_and_measure(image_bytes, base_model)

    assert isinstance(result, RecognitionResult), "Pipeline must return a RecognitionResult"
    assert elapsed < LATENCY_BUDGET_SECONDS, (
        f"Portrait fixture took {elapsed:.2f}s, exceeding the {LATENCY_BUDGET_SECONDS}s budget "
        f"(Requirement 2.3)"
    )


def test_landscape_100_words_within_budget(base_model: CRNN) -> None:
    """Landscape page (5 × 20 words) completes within 10 seconds.

    Validates: Requirements 2.3
    """
    image_bytes = _make_100_word_image_landscape()
    result, elapsed = _run_and_measure(image_bytes, base_model)

    assert isinstance(result, RecognitionResult), "Pipeline must return a RecognitionResult"
    assert elapsed < LATENCY_BUDGET_SECONDS, (
        f"Landscape fixture took {elapsed:.2f}s, exceeding the {LATENCY_BUDGET_SECONDS}s budget "
        f"(Requirement 2.3)"
    )


def test_dense_100_words_within_budget(base_model: CRNN) -> None:
    """Dense portrait page (12 lines, ≈ 100 words) completes within 10 seconds.

    Validates: Requirements 2.3
    """
    image_bytes = _make_100_word_image_dense()
    result, elapsed = _run_and_measure(image_bytes, base_model)

    assert isinstance(result, RecognitionResult), "Pipeline must return a RecognitionResult"
    assert elapsed < LATENCY_BUDGET_SECONDS, (
        f"Dense fixture took {elapsed:.2f}s, exceeding the {LATENCY_BUDGET_SECONDS}s budget "
        f"(Requirement 2.3)"
    )
