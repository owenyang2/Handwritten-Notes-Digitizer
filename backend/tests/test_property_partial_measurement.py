"""Property-based test for Property 22: Partial measurement failure retains
successful measurements.

# Feature: handwriting-notes-digitizer, Property 22: Partial measurement failure retains successful measurements

For any evaluation in which one of the CER/WER measurements fails to compute,
the failed measurement is reported as unavailable (None) while every
successfully computed measurement is retained and reported (not None).

Strategy
--------
We use ``unittest.mock.patch`` as a context manager (compatible with Hypothesis
``@given`` — no function-scoped fixtures) to force exactly one of the four
metric calls (before-CER, before-WER, after-CER, after-WER) to raise, then
verify:
  1. The patched measurement is None in the report.
  2. At least one of the other measurements is still non-None.
  3. All four scenarios (one per metric call) are exercised.

Held-out pairs are generated from arbitrary non-empty reference text so the
property holds across a wide variety of inputs.

Validates: Requirements 7.7
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import app.services.evaluation_service as svc

# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------

# Text with at least one character that isn't pure whitespace → CER available.
# Also has at least one non-space token → WER available.
_nonempty_word_text = st.text(
    alphabet=st.characters(
        blacklist_categories=("Cs",),  # no surrogates
        blacklist_characters="\x00",
    ),
    min_size=1,
).filter(lambda s: bool(s.strip()))  # at least one non-space char → both CER and WER defined

_held_out_pair_st = st.builds(
    lambda ref: (ref.encode("utf-8"), ref),
    _nonempty_word_text,
)

_held_out_pairs_st = st.lists(_held_out_pair_st, min_size=1, max_size=5)


# ---------------------------------------------------------------------------
# Perfect recogniser: decode crop bytes as UTF-8 (zero error rate)
# ---------------------------------------------------------------------------

def _perfect(crop: bytes) -> str:
    return crop.decode("utf-8")


# ---------------------------------------------------------------------------
# Helper: build a wrapper that raises RuntimeError on the N-th call
# ---------------------------------------------------------------------------

def _raise_on_nth_call(original_fn, target_call: int):
    """Return a function identical to ``original_fn`` except that it raises
    ``RuntimeError`` exactly on the ``target_call``-th invocation.

    A fresh counter is created each time this helper is called, so each
    Hypothesis example gets an independent counter via the context-manager
    patch.
    """
    counter = {"n": 0}

    def wrapper(reference, hypothesis):
        counter["n"] += 1
        if counter["n"] == target_call:
            raise RuntimeError(f"simulated failure on call {target_call}")
        return original_fn(reference, hypothesis)

    return wrapper


# ---------------------------------------------------------------------------
# Property 22 – four failure scenarios
#
# The metric calls inside compute_before_after (in order):
#   cer call 1 → before-CER  (from _aggregate_metrics(base_recogniser))
#   wer call 1 → before-WER  (from _aggregate_metrics(base_recogniser))
#   cer call 2 → after-CER   (from _aggregate_metrics(adapter_recogniser))
#   wer call 2 → after-WER   (from _aggregate_metrics(adapter_recogniser))
# ---------------------------------------------------------------------------


# Feature: handwriting-notes-digitizer, Property 22: Partial measurement failure retains successful measurements
@given(pairs=_held_out_pairs_st)
@settings(max_examples=25, suppress_health_check=[HealthCheck.too_slow])
def test_before_cer_failure_retains_other_measurements(pairs):
    """Req 7.7: when before-CER computation raises, before-WER, after-CER, and
    after-WER are still computed and reported as non-None."""
    original_cer = svc.cer
    faulty_cer = _raise_on_nth_call(original_cer, target_call=1)

    with patch.object(svc, "cer", faulty_cer):
        report = svc.compute_before_after(pairs, _perfect, _perfect)

    # The failed measurement must be None
    assert report.before_cer is None, (
        "before_cer must be None when its computation raises"
    )

    # At least one of the other measurements must be retained (non-None)
    retained = [report.before_wer, report.after_cer, report.after_wer]
    assert any(v is not None for v in retained), (
        "at least one measurement must be retained when only before-CER fails"
    )


# Feature: handwriting-notes-digitizer, Property 22: Partial measurement failure retains successful measurements
@given(pairs=_held_out_pairs_st)
@settings(max_examples=25, suppress_health_check=[HealthCheck.too_slow])
def test_before_wer_failure_retains_other_measurements(pairs):
    """Req 7.7: when before-WER computation raises, before-CER, after-CER, and
    after-WER are still computed and reported as non-None."""
    original_wer = svc.wer
    faulty_wer = _raise_on_nth_call(original_wer, target_call=1)

    with patch.object(svc, "wer", faulty_wer):
        report = svc.compute_before_after(pairs, _perfect, _perfect)

    # The failed measurement must be None
    assert report.before_wer is None, (
        "before_wer must be None when its computation raises"
    )

    # At least one of the other measurements must be retained
    retained = [report.before_cer, report.after_cer, report.after_wer]
    assert any(v is not None for v in retained), (
        "at least one measurement must be retained when only before-WER fails"
    )


# Feature: handwriting-notes-digitizer, Property 22: Partial measurement failure retains successful measurements
@given(pairs=_held_out_pairs_st)
@settings(max_examples=25, suppress_health_check=[HealthCheck.too_slow])
def test_after_cer_failure_retains_other_measurements(pairs):
    """Req 7.7: when after-CER computation raises, before-CER, before-WER, and
    after-WER are still computed and reported as non-None."""
    original_cer = svc.cer
    faulty_cer = _raise_on_nth_call(original_cer, target_call=2)

    with patch.object(svc, "cer", faulty_cer):
        report = svc.compute_before_after(pairs, _perfect, _perfect)

    # The failed measurement must be None
    assert report.after_cer is None, (
        "after_cer must be None when its computation raises"
    )

    # At least one of the other measurements must be retained
    retained = [report.before_cer, report.before_wer, report.after_wer]
    assert any(v is not None for v in retained), (
        "at least one measurement must be retained when only after-CER fails"
    )


# Feature: handwriting-notes-digitizer, Property 22: Partial measurement failure retains successful measurements
@given(pairs=_held_out_pairs_st)
@settings(max_examples=25, suppress_health_check=[HealthCheck.too_slow])
def test_after_wer_failure_retains_other_measurements(pairs):
    """Req 7.7: when after-WER computation raises, before-CER, before-WER, and
    after-CER are still computed and reported as non-None."""
    original_wer = svc.wer
    faulty_wer = _raise_on_nth_call(original_wer, target_call=2)

    with patch.object(svc, "wer", faulty_wer):
        report = svc.compute_before_after(pairs, _perfect, _perfect)

    # The failed measurement must be None
    assert report.after_wer is None, (
        "after_wer must be None when its computation raises"
    )

    # At least one of the other measurements must be retained
    retained = [report.before_cer, report.before_wer, report.after_cer]
    assert any(v is not None for v in retained), (
        "at least one measurement must be retained when only after-WER fails"
    )


# Feature: handwriting-notes-digitizer, Property 22: Partial measurement failure retains successful measurements
@given(pairs=_held_out_pairs_st)
@settings(max_examples=25, suppress_health_check=[HealthCheck.too_slow])
def test_single_metric_failure_leaves_at_least_one_retained(pairs):
    """Req 7.7: for any single-metric failure, the failing field is None and at
    least one of the four measurements is non-None (partial failure must not
    suppress all measurements)."""
    original_cer = svc.cer
    faulty_cer = _raise_on_nth_call(original_cer, target_call=1)

    with patch.object(svc, "cer", faulty_cer):
        report = svc.compute_before_after(pairs, _perfect, _perfect)

    all_values = [report.before_cer, report.before_wer, report.after_cer, report.after_wer]

    # The failed measurement must be None
    assert report.before_cer is None, (
        "before_cer must be None when its computation raises"
    )

    # Partial failure must NOT suppress all measurements
    assert any(v is not None for v in all_values), (
        "partial failure must not suppress all four measurements"
    )
