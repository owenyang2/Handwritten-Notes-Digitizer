"""Property test for recognized-word region validity (design Property 4).

Design property under test:

    Property 4: Every recognized word carries a valid region
    For any recognition result produced by ``assemble_result()``, each
    ``RecognizedWord.region`` satisfies:
        - x >= 0, y >= 0
        - width > 0, height > 0
        - x + width  <= image_width
        - y + height <= image_height

**Validates: Requirements 2.4**

Strategy
--------
``assemble_result(seg_result, model)`` is responsible for:

1. Running each word crop through the model (``_recognize_word``).
2. Attaching the source ``word.region`` from the segmentation result to each
   returned ``RecognizedWord``.

The region validity invariant (Property 4) is a property of steps 1+2 together:
the function must faithfully forward valid regions and must never produce a
``RecognizedWord`` whose region falls outside the image.

We test this by:

* Generating synthetic ``SegmentationResult`` objects whose word regions are
  all valid by construction (non-negative origin, positive dimensions, within
  image bounds).
* Passing each result through ``assemble_result`` with a **stub model** whose
  ``__call__`` returns a plausible log-probability tensor for any crop shape.
  This isolates the region-forwarding contract from the CNN architecture and
  removes all minimum-width constraints.
* Asserting that every ``RecognizedWord.region`` in the returned
  ``RecognitionResult`` satisfies Property 4's six conditions.

Using a stub model keeps 100+ iterations cheap (no CNN, no LSTM) while still
exercising the real ``assemble_result`` code path end-to-end.
"""

# Feature: handwriting-notes-digitizer, Property 4: Every recognized word carries a valid region

from __future__ import annotations

import numpy as np
import torch
from hypothesis import given
from hypothesis import strategies as st
from torch import nn

from app.ml.htr_config import DEFAULT_HTR_CONFIG, HTRModelConfig
from app.ml.recognition import RecognitionResult, assemble_result
from app.ml.segmentation import (
    LineSegment,
    Region,
    SegmentationResult,
    WordSegment,
)


# ---------------------------------------------------------------------------
# Stub model — cheap stand-in for the CRNN during property tests
# ---------------------------------------------------------------------------


class _StubCRNN(nn.Module):
    """Minimal stand-in for ``CRNN`` that satisfies the ``assemble_result`` contract.

    ``assemble_result`` calls ``_recognize_word(word, model)`` which:
      1. Builds a tensor of shape ``(1, 1, target_height, W)``.
      2. Calls ``model(tensor)`` expecting output ``(T, 1, C)``.
      3. Passes the result to ``greedy_ctc_decode``.

    This stub ignores the tensor content and returns a valid ``(T, 1, C)``
    log-probability tensor so the decode step completes without error.

    The only attributes ``assemble_result`` reads from the model are
    ``model.config.image_height`` (via ``_recognize_word``) and
    ``model.config.blank_index`` (via ``greedy_ctc_decode``). We satisfy both
    by attaching a minimal config.
    """

    def __init__(self) -> None:
        super().__init__()
        # We need .config.image_height and .config.blank_index only.
        self.config = DEFAULT_HTR_CONFIG

    def eval(self):  # type: ignore[override]
        return self

    def forward(self, images: torch.Tensor) -> torch.Tensor:
        # images: (N=1, C=1, H, W) — any H, any W.
        # Return (T=1, N=1, C) log-probs; T=1 is the minimal CTC sequence.
        num_classes = self.config.num_classes_with_blank  # e.g. 80
        # All mass on blank (index 0) → decoded text is empty. That is fine;
        # we are testing region validity, not decode accuracy.
        log_probs = torch.full((1, 1, num_classes), fill_value=-1e9)
        log_probs[0, 0, self.config.blank_index] = 0.0  # log(1) = 0
        return log_probs


_STUB_MODEL = _StubCRNN()


# ---------------------------------------------------------------------------
# Hypothesis generators
# ---------------------------------------------------------------------------

# Image dimensions — no constraints needed beyond being large enough to fit
# at least one word with minimum region width=1.
_image_dims = st.tuples(
    st.integers(min_value=4, max_value=200),   # width
    st.integers(min_value=4, max_value=200),   # height
)


def _region_strategy(image_width: int, image_height: int) -> st.SearchStrategy[Region]:
    """Generate a valid Region that fits entirely within (image_width × image_height).

    All six Property 4 conditions hold by construction on generated regions:
        x >= 0, y >= 0, width > 0, height > 0,
        x + width <= image_width, y + height <= image_height.
    """
    return st.integers(min_value=0, max_value=image_width - 1).flatmap(
        lambda x: st.integers(min_value=1, max_value=image_width - x).flatmap(
            lambda w: st.integers(min_value=0, max_value=image_height - 1).flatmap(
                lambda y: st.integers(min_value=1, max_value=image_height - y).map(
                    lambda h: Region(x=x, y=y, width=w, height=h)
                )
            )
        )
    )


def _word_segment(index: int, region: Region) -> WordSegment:
    """Build a WordSegment with a zero-valued float32 crop matching the region."""
    crop = np.zeros((region.height, region.width), dtype=np.float32)
    return WordSegment(index=index, region=region, crop=crop)


def _line_bounding_region(regions: list[Region]) -> Region:
    """Bounding Region enclosing all supplied regions."""
    left = min(r.x for r in regions)
    top = min(r.y for r in regions)
    right = max(r.x + r.width for r in regions)
    bottom = max(r.y + r.height for r in regions)
    return Region(x=left, y=top, width=right - left, height=bottom - top)


def _line_segment_strategy(
    line_index: int,
    image_width: int,
    image_height: int,
) -> st.SearchStrategy[LineSegment]:
    """Generate a LineSegment with 1–4 words whose regions all fit in the image."""
    return st.integers(min_value=1, max_value=4).flatmap(
        lambda n: st.lists(
            _region_strategy(image_width, image_height),
            min_size=n,
            max_size=n,
        ).map(
            lambda regions: LineSegment(
                index=line_index,
                region=_line_bounding_region(regions),
                words=[_word_segment(i, r) for i, r in enumerate(regions)],
            )
        )
    )


def _segmentation_result_strategy() -> st.SearchStrategy[SegmentationResult]:
    """Generate a SegmentationResult with 1–3 lines and valid word regions."""
    return _image_dims.flatmap(
        lambda dims: st.integers(min_value=1, max_value=3).flatmap(
            lambda n_lines: st.tuples(
                *[
                    _line_segment_strategy(i, dims[0], dims[1])
                    for i in range(n_lines)
                ]
            ).map(
                lambda lines: SegmentationResult(
                    lines=list(lines),
                    image_width=dims[0],
                    image_height=dims[1],
                )
            )
        )
    )


# ---------------------------------------------------------------------------
# Property test
# ---------------------------------------------------------------------------

# Feature: handwriting-notes-digitizer, Property 4: Every recognized word carries a valid region
@given(seg_result=_segmentation_result_strategy())
def test_every_recognized_word_carries_a_valid_region(
    seg_result: SegmentationResult,
) -> None:
    """Validates: Requirements 2.4.

    For any recognition result produced by ``assemble_result()``, every
    ``RecognizedWord.region`` satisfies all of:
        - x >= 0
        - y >= 0
        - width > 0
        - height > 0
        - x + width  <= image_width   (stays within the source image)
        - y + height <= image_height  (stays within the source image)
    """
    image_width = seg_result.image_width
    image_height = seg_result.image_height

    result: RecognitionResult = assemble_result(seg_result, _STUB_MODEL)

    for line in result.lines:
        for word in line.words:
            r = word.region

            # Non-negative origin.
            assert r.x >= 0, (
                f"word region x={r.x} is negative "
                f"(image {image_width}×{image_height})"
            )
            assert r.y >= 0, (
                f"word region y={r.y} is negative "
                f"(image {image_width}×{image_height})"
            )

            # Strictly positive dimensions.
            assert r.width > 0, (
                f"word region width={r.width} is not positive "
                f"(image {image_width}×{image_height})"
            )
            assert r.height > 0, (
                f"word region height={r.height} is not positive "
                f"(image {image_width}×{image_height})"
            )

            # Region stays within the image bounds.
            assert r.x + r.width <= image_width, (
                f"word region right edge {r.x + r.width} exceeds "
                f"image_width={image_width}"
            )
            assert r.y + r.height <= image_height, (
                f"word region bottom edge {r.y + r.height} exceeds "
                f"image_height={image_height}"
            )
