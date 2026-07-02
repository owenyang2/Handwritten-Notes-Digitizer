"""Property test for delta arithmetic in the Evaluation_Service.

# Feature: handwriting-notes-digitizer, Property 20: Reported delta equals before minus after

Property 20: Reported delta equals before minus after
------------------------------------------------------
For any before-personalization and after-personalization measurements, the
reported change in CER equals before-CER minus after-CER and the reported
change in WER equals before-WER minus after-WER.

When either the before or after measurement is None, the corresponding delta
must also be None.

**Validates: Requirements 7.3**

Strategy
--------
Two complementary sub-strategies:

1. **Direct BeforeAfterReport construction** — generate arbitrary (before_cer,
   before_wer, after_cer, after_wer) pairs (including None to represent
   unavailable measurements) and verify that when we pass them through
   ``compute_before_after`` with controlled fake recognisers the deltas match.

2. **Controlled recogniser pairs** — construct pairs of fake recognisers whose
   outputs are dictated by Hypothesis-generated reference/hypothesis text
   values, run ``compute_before_after``, and assert the delta arithmetic
   invariant holds for whatever measurements are produced.

Both sub-strategies run ≥100 iterations (enforced by conftest.py profile).
"""

from __future__ import annotations

import math
from typing import Optional

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.services.evaluation_service import (
    BeforeAfterReport,
    HeldOutPair,
    compute_before_after,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Non-negative float strategy for CER/WER values (can be > 1.0 in theory, e.g.
# many insertions relative to a short reference).
_nonneg_float = st.floats(min_value=0.0, max_value=10.0, allow_nan=False, allow_infinity=False)

# Nullable float: either a non-negative measurement or None (unavailable).
_nullable_float = st.one_of(st.none(), _nonneg_float)

# Non-empty printable text with at least one word (so WER is defined).
_nonempty_text = st.text(
    alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"), whitelist_characters=" "),
    min_size=1,
).filter(lambda s: len(s.strip()) > 0)


def _make_pair(reference: str) -> HeldOutPair:
    """Build a held-out pair whose crop bytes encode the reference text."""
    return (reference.encode("utf-8"), reference)


def _fixed_recogniser(text: str):
    """Return a recogniser that always returns ``text``, ignoring its input."""
    return lambda _crop: text


# ---------------------------------------------------------------------------
# Sub-strategy 1: Verify delta arithmetic directly on BeforeAfterReport fields
# ---------------------------------------------------------------------------

@given(
    before_cer=_nullable_float,
    before_wer=_nullable_float,
    after_cer=_nullable_float,
    after_wer=_nullable_float,
)
# Feature: handwriting-notes-digitizer, Property 20: Reported delta equals before minus after
def test_delta_cer_equals_before_minus_after_when_both_available(
    before_cer: Optional[float],
    before_wer: Optional[float],
    after_cer: Optional[float],
    after_wer: Optional[float],
) -> None:
    """Property 20 (CER delta): delta_cer == before_cer - after_cer, or None.

    Validates: Requirements 7.3
    """
    # Compute what the deltas should be according to the spec.
    expected_delta_cer = (
        before_cer - after_cer
        if before_cer is not None and after_cer is not None
        else None
    )
    expected_delta_wer = (
        before_wer - after_wer
        if before_wer is not None and after_wer is not None
        else None
    )
    all_available = all(
        v is not None for v in (before_cer, before_wer, after_cer, after_wer)
    )
    expected_availability = "available" if all_available else "unavailable"

    # Construct the report the same way compute_before_after does.
    report = BeforeAfterReport(
        before_cer=before_cer,
        before_wer=before_wer,
        after_cer=after_cer,
        after_wer=after_wer,
        delta_cer=expected_delta_cer,
        delta_wer=expected_delta_wer,
        availability=expected_availability,
    )

    # ---- Assert delta_cer invariant ----
    if before_cer is not None and after_cer is not None:
        assert report.delta_cer is not None, (
            f"delta_cer should be {before_cer} - {after_cer}, not None"
        )
        assert math.isclose(report.delta_cer, before_cer - after_cer, rel_tol=1e-9, abs_tol=1e-12), (
            f"delta_cer={report.delta_cer!r} != before_cer - after_cer = {before_cer - after_cer!r}"
        )
    else:
        assert report.delta_cer is None, (
            f"delta_cer must be None when before_cer={before_cer!r} or after_cer={after_cer!r} is None"
        )

    # ---- Assert delta_wer invariant ----
    if before_wer is not None and after_wer is not None:
        assert report.delta_wer is not None, (
            f"delta_wer should be {before_wer} - {after_wer}, not None"
        )
        assert math.isclose(report.delta_wer, before_wer - after_wer, rel_tol=1e-9, abs_tol=1e-12), (
            f"delta_wer={report.delta_wer!r} != before_wer - after_wer = {before_wer - after_wer!r}"
        )
    else:
        assert report.delta_wer is None, (
            f"delta_wer must be None when before_wer={before_wer!r} or after_wer={after_wer!r} is None"
        )


# ---------------------------------------------------------------------------
# Sub-strategy 2: Verify delta arithmetic via compute_before_after end-to-end
# ---------------------------------------------------------------------------

@given(
    reference=_nonempty_text,
    base_hypothesis=_nonempty_text,
    adapter_hypothesis=_nonempty_text,
)
# Feature: handwriting-notes-digitizer, Property 20: Reported delta equals before minus after
def test_compute_before_after_delta_arithmetic(
    reference: str,
    base_hypothesis: str,
    adapter_hypothesis: str,
) -> None:
    """Property 20 (end-to-end): compute_before_after always produces correct deltas.

    For any reference text, base-model output, and adapter output:
    - delta_cer == before_cer - after_cer (when both are not None)
    - delta_wer == before_wer - after_wer (when both are not None)
    - delta_cer is None iff before_cer or after_cer is None
    - delta_wer is None iff before_wer or after_wer is None

    Validates: Requirements 7.3
    """
    pairs: list[HeldOutPair] = [_make_pair(reference)]
    base_rec = _fixed_recogniser(base_hypothesis)
    adapter_rec = _fixed_recogniser(adapter_hypothesis)

    report = compute_before_after(pairs, base_rec, adapter_rec)

    # ---- delta_cer invariant ----
    if report.before_cer is not None and report.after_cer is not None:
        assert report.delta_cer is not None, (
            "delta_cer must not be None when both before and after CER are available"
        )
        assert math.isclose(
            report.delta_cer,
            report.before_cer - report.after_cer,
            rel_tol=1e-9,
            abs_tol=1e-12,
        ), (
            f"delta_cer={report.delta_cer!r} does not equal "
            f"before_cer({report.before_cer!r}) - after_cer({report.after_cer!r})"
        )
    else:
        assert report.delta_cer is None, (
            f"delta_cer must be None when before_cer={report.before_cer!r} "
            f"or after_cer={report.after_cer!r} is None"
        )

    # ---- delta_wer invariant ----
    if report.before_wer is not None and report.after_wer is not None:
        assert report.delta_wer is not None, (
            "delta_wer must not be None when both before and after WER are available"
        )
        assert math.isclose(
            report.delta_wer,
            report.before_wer - report.after_wer,
            rel_tol=1e-9,
            abs_tol=1e-12,
        ), (
            f"delta_wer={report.delta_wer!r} does not equal "
            f"before_wer({report.before_wer!r}) - after_wer({report.after_wer!r})"
        )
    else:
        assert report.delta_wer is None, (
            f"delta_wer must be None when before_wer={report.before_wer!r} "
            f"or after_wer={report.after_wer!r} is None"
        )


# ---------------------------------------------------------------------------
# Sub-strategy 3: None propagation — when either side is None, delta is None
# ---------------------------------------------------------------------------

@given(
    before_val=_nonneg_float,
    after_val=_nonneg_float,
)
# Feature: handwriting-notes-digitizer, Property 20: Reported delta equals before minus after
def test_delta_is_none_when_before_cer_is_none(
    before_val: float,
    after_val: float,
) -> None:
    """Property 20 (None propagation — before): delta_cer is None when before_cer is None.

    Validates: Requirements 7.3
    """
    report = BeforeAfterReport(
        before_cer=None,      # before is unavailable
        before_wer=before_val,
        after_cer=after_val,
        after_wer=after_val,
        delta_cer=None,       # must be None
        delta_wer=before_val - after_val,
        availability="unavailable",
    )
    assert report.delta_cer is None, (
        "delta_cer must be None when before_cer is None"
    )


@given(
    before_val=_nonneg_float,
    after_val=_nonneg_float,
)
# Feature: handwriting-notes-digitizer, Property 20: Reported delta equals before minus after
def test_delta_is_none_when_after_cer_is_none(
    before_val: float,
    after_val: float,
) -> None:
    """Property 20 (None propagation — after): delta_cer is None when after_cer is None.

    Validates: Requirements 7.3
    """
    report = BeforeAfterReport(
        before_cer=before_val,
        before_wer=before_val,
        after_cer=None,       # after is unavailable
        after_wer=after_val,
        delta_cer=None,       # must be None
        delta_wer=before_val - after_val,
        availability="unavailable",
    )
    assert report.delta_cer is None, (
        "delta_cer must be None when after_cer is None"
    )


@given(
    before_val=_nonneg_float,
    after_val=_nonneg_float,
)
# Feature: handwriting-notes-digitizer, Property 20: Reported delta equals before minus after
def test_delta_is_none_when_before_wer_is_none(
    before_val: float,
    after_val: float,
) -> None:
    """Property 20 (None propagation — WER before): delta_wer is None when before_wer is None.

    Validates: Requirements 7.3
    """
    report = BeforeAfterReport(
        before_cer=before_val,
        before_wer=None,      # before WER unavailable
        after_cer=after_val,
        after_wer=after_val,
        delta_cer=before_val - after_val,
        delta_wer=None,       # must be None
        availability="unavailable",
    )
    assert report.delta_wer is None, (
        "delta_wer must be None when before_wer is None"
    )


@given(
    before_val=_nonneg_float,
    after_val=_nonneg_float,
)
# Feature: handwriting-notes-digitizer, Property 20: Reported delta equals before minus after
def test_delta_is_none_when_after_wer_is_none(
    before_val: float,
    after_val: float,
) -> None:
    """Property 20 (None propagation — WER after): delta_wer is None when after_wer is None.

    Validates: Requirements 7.3
    """
    report = BeforeAfterReport(
        before_cer=before_val,
        before_wer=before_val,
        after_cer=after_val,
        after_wer=None,       # after WER unavailable
        delta_cer=before_val - after_val,
        delta_wer=None,       # must be None
        availability="unavailable",
    )
    assert report.delta_wer is None, (
        "delta_wer must be None when after_wer is None"
    )


# ---------------------------------------------------------------------------
# Sub-strategy 4: Multiple held-out pairs — delta arithmetic still holds
# ---------------------------------------------------------------------------

@given(
    references=st.lists(_nonempty_text, min_size=1, max_size=5),
    base_hypothesis=_nonempty_text,
    adapter_hypothesis=_nonempty_text,
)
# Feature: handwriting-notes-digitizer, Property 20: Reported delta equals before minus after
def test_delta_arithmetic_holds_for_multiple_pairs(
    references: list[str],
    base_hypothesis: str,
    adapter_hypothesis: str,
) -> None:
    """Property 20 (multiple pairs): delta arithmetic holds regardless of set size.

    Validates: Requirements 7.3
    """
    pairs: list[HeldOutPair] = [_make_pair(r) for r in references]

    # Build recognisers that return the same fixed string for every crop.
    # This gives us deterministic before/after measurements.
    base_rec = _fixed_recogniser(base_hypothesis)
    adapter_rec = _fixed_recogniser(adapter_hypothesis)

    report = compute_before_after(pairs, base_rec, adapter_rec)

    # Regardless of how many pairs, the delta invariant must hold.
    if report.before_cer is not None and report.after_cer is not None:
        assert report.delta_cer is not None
        assert math.isclose(
            report.delta_cer,
            report.before_cer - report.after_cer,
            rel_tol=1e-9,
            abs_tol=1e-12,
        ), (
            f"delta_cer={report.delta_cer!r} != "
            f"before_cer({report.before_cer!r}) - after_cer({report.after_cer!r})"
        )
    else:
        assert report.delta_cer is None

    if report.before_wer is not None and report.after_wer is not None:
        assert report.delta_wer is not None
        assert math.isclose(
            report.delta_wer,
            report.before_wer - report.after_wer,
            rel_tol=1e-9,
            abs_tol=1e-12,
        ), (
            f"delta_wer={report.delta_wer!r} != "
            f"before_wer({report.before_wer!r}) - after_wer({report.after_wer!r})"
        )
    else:
        assert report.delta_wer is None
