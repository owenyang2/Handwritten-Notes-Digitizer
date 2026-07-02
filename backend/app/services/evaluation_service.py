"""Evaluation_Service: CER and WER metric computation plus before/after reporting.

Implements:

* ``cer`` / ``wer`` — character/word-level Levenshtein distance over reference
  length, yielding a non-negative float, or ``None`` when the reference is empty
  (Req 7.4, 7.5, 7.6).
* ``BeforeAfterReport`` — dataclass holding before/after/delta CER and WER
  (each field may be ``None`` when measurement is unavailable) plus an
  ``availability`` flag (Req 7.3, 7.6, 7.7).
* ``compute_before_after`` — measures base-model and adapter CER/WER on the
  user's held-out set and returns a ``BeforeAfterReport`` (Req 7.1, 7.2, 7.3).
* ``store_evaluation_result`` — persists a ``BeforeAfterReport`` as an
  ``EvaluationResult`` row (Req 7.3).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Callable, Optional, Sequence


def _levenshtein(seq_a: list, seq_b: list) -> int:
    """Return the Levenshtein edit distance between two sequences.

    Uses a standard DP table.  Works for both character-level lists (CER) and
    word-level lists (WER).
    """
    m, n = len(seq_a), len(seq_b)

    # Use two rows to keep memory O(min(m, n)).
    if m < n:
        # Ensure seq_a is the shorter sequence so we allocate the smaller row.
        seq_a, seq_b = seq_b, seq_a
        m, n = n, m

    # prev[j] = edit distance between seq_a[:i] and seq_b[:j]
    prev = list(range(n + 1))
    curr = [0] * (n + 1)

    for i in range(1, m + 1):
        curr[0] = i
        for j in range(1, n + 1):
            if seq_a[i - 1] == seq_b[j - 1]:
                curr[j] = prev[j - 1]
            else:
                curr[j] = 1 + min(prev[j - 1], prev[j], curr[j - 1])
        prev, curr = curr, prev

    return prev[n]


def cer(reference: str, hypothesis: str) -> float | None:
    """Compute Character Error Rate.

    CER = character-level Levenshtein distance(reference, hypothesis)
          / len(reference characters)

    Returns:
        A non-negative float when the reference is non-empty, or None when the
        reference contains no characters (Req 7.6 — report unavailable).

    When reference == hypothesis the result is exactly 0.0.

    Handles Unicode, arbitrary whitespace, and long strings.
    """
    ref_chars = list(reference)  # preserves every Unicode code-point as-is
    if len(ref_chars) == 0:
        return None  # empty reference → unavailable (Req 7.6)

    hyp_chars = list(hypothesis)
    distance = _levenshtein(ref_chars, hyp_chars)
    # Distance is always >= 0; dividing by a positive length keeps it >= 0.
    return distance / len(ref_chars)


def wer(reference: str, hypothesis: str) -> float | None:
    """Compute Word Error Rate.

    WER = word-level Levenshtein distance(reference_words, hypothesis_words)
          / len(reference_words)

    Words are obtained by splitting on whitespace (str.split(), which handles
    all Unicode whitespace and ignores leading/trailing/duplicate spaces).

    Returns:
        A non-negative float when the reference contains at least one word, or
        None when the reference is empty or whitespace-only (Req 7.6 — report
        unavailable).

    When reference == hypothesis the result is exactly 0.0.

    Handles Unicode, arbitrary whitespace, and long strings.
    """
    ref_words = reference.split()  # splits on any whitespace, filters empties
    if len(ref_words) == 0:
        return None  # empty/whitespace-only reference → unavailable (Req 7.6)

    hyp_words = hypothesis.split()
    distance = _levenshtein(ref_words, hyp_words)
    # Distance is always >= 0; dividing by a positive length keeps it >= 0.
    return distance / len(ref_words)


# ---------------------------------------------------------------------------
# Before/after report data structures
# ---------------------------------------------------------------------------

#: Type alias for a model callable: accepts raw image bytes (a crop) and returns
#: the recognised text string.  Using a plain Callable keeps the evaluation
#: service decoupled from the full HTR_Engine and makes unit testing trivial.
RecogniserFn = Callable[[bytes], str]

#: A held-out pair is a 2-tuple of (crop_bytes, reference_text).
HeldOutPair = tuple[bytes, str]


@dataclass
class BeforeAfterReport:
    """Before/after CER and WER report for a single training run (Req 7.3).

    Each metric field is either a non-negative float (measurement available) or
    ``None`` (measurement unavailable — empty held-out set, or computation
    failure).  ``availability`` summarises whether *both* before and after
    measurements are present.

    Fields
    ------
    before_cer:
        Base-model CER on the user's held-out set (Req 7.1). ``None`` if
        unavailable.
    before_wer:
        Base-model WER on the user's held-out set (Req 7.1). ``None`` if
        unavailable.
    after_cer:
        Adapter CER on the user's held-out set (Req 7.2). ``None`` if
        unavailable.
    after_wer:
        Adapter WER on the user's held-out set (Req 7.2). ``None`` if
        unavailable.
    delta_cer:
        ``before_cer − after_cer`` (Req 7.3). ``None`` if either is
        unavailable.
    delta_wer:
        ``before_wer − after_wer`` (Req 7.3). ``None`` if either is
        unavailable.
    availability:
        ``"available"`` iff both before and after measurements were computed
        successfully; ``"unavailable"`` otherwise (Req 7.6).
    """

    before_cer: Optional[float]
    before_wer: Optional[float]
    after_cer: Optional[float]
    after_wer: Optional[float]
    delta_cer: Optional[float]
    delta_wer: Optional[float]
    availability: str  # "available" | "unavailable"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _aggregate_metrics(
    held_out: Sequence[HeldOutPair],
    recogniser: RecogniserFn,
) -> tuple[Optional[float], Optional[float]]:
    """Run ``recogniser`` on every pair and return aggregate (CER, WER).

    Concatenates all reference and hypothesis texts with a single space
    separator so aggregate CER/WER is computed over the whole set rather than
    averaged per-pair (matching standard HTR evaluation practice).

    Returns ``(None, None)`` when the held-out set is empty or the concatenated
    reference contains no characters/words.  Individual pair failures are caught
    and treated as empty hypothesis strings (Req 7.7 — retain any successfully
    computed measurement).

    Each metric is computed independently: if CER computation raises an
    unexpected exception, ``None`` is reported for CER while WER is still
    attempted (and vice versa).  This implements the partial-failure contract of
    Req 7.7 at the metric level.
    """
    references: list[str] = []
    hypotheses: list[str] = []

    for crop_bytes, reference_text in held_out:
        references.append(reference_text)
        try:
            hypothesis = recogniser(crop_bytes)
        except Exception:
            # Partial failure: treat this pair as empty hypothesis (Req 7.7).
            hypothesis = ""
        hypotheses.append(hypothesis)

    combined_ref = " ".join(references)
    combined_hyp = " ".join(hypotheses)

    # Compute each metric independently so a failure in one does not suppress
    # the other (Req 7.7 — retain any successfully computed measurement).
    try:
        computed_cer: Optional[float] = cer(combined_ref, combined_hyp)
    except Exception:
        computed_cer = None  # CER unavailable; WER may still be reported

    try:
        computed_wer: Optional[float] = wer(combined_ref, combined_hyp)
    except Exception:
        computed_wer = None  # WER unavailable; CER may still be reported

    return computed_cer, computed_wer


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_before_after(
    held_out_pairs: Sequence[HeldOutPair],
    base_recogniser: RecogniserFn,
    adapter_recogniser: RecogniserFn,
) -> BeforeAfterReport:
    """Measure base-model and adapter CER/WER on the held-out set.

    Computes the before (base model) and after (adapter) CER and WER on
    ``held_out_pairs``, then derives the delta as ``before − after``.

    Both ``base_recogniser`` and ``adapter_recogniser`` are callables of the
    form ``(crop_bytes: bytes) -> str``.  This keeps the evaluation service
    decoupled from the concrete HTR_Engine; callers pass a thin wrapper such as
    ``lambda crop: recognize_bytes(crop, model=base_model).lines[0].words[0].text``
    or a purpose-built fake for tests.

    Availability rules (Req 7.1, 7.2, 7.6):
    - If ``held_out_pairs`` is empty, all fields are ``None`` and
      ``availability`` is ``"unavailable"``.
    - If the concatenated reference contains no characters or words, all fields
      are ``None`` and ``availability`` is ``"unavailable"``.
    - ``delta_cer`` / ``delta_wer`` are ``None`` when either corresponding
      before or after value is ``None`` (Req 7.3).
    - ``availability`` is ``"available"`` only when both before *and* after
      measurements are fully present (both CER and WER).

    Parameters
    ----------
    held_out_pairs:
        Sequence of ``(crop_bytes, reference_text)`` tuples — the user's
        reserved held-out set from the training split.
    base_recogniser:
        Callable that runs the base model on a single crop and returns a text
        string.
    adapter_recogniser:
        Callable that runs the user's adapter (composed with the base model) on
        a single crop and returns a text string.

    Returns
    -------
    BeforeAfterReport
        The complete before/after/delta report with availability status.
    """
    if not held_out_pairs:
        return BeforeAfterReport(
            before_cer=None,
            before_wer=None,
            after_cer=None,
            after_wer=None,
            delta_cer=None,
            delta_wer=None,
            availability="unavailable",
        )

    before_cer_val, before_wer_val = _aggregate_metrics(held_out_pairs, base_recogniser)
    after_cer_val, after_wer_val = _aggregate_metrics(held_out_pairs, adapter_recogniser)

    # Delta is before − after; None when either side is unavailable (Req 7.3).
    delta_cer_val: Optional[float] = (
        before_cer_val - after_cer_val
        if before_cer_val is not None and after_cer_val is not None
        else None
    )
    delta_wer_val: Optional[float] = (
        before_wer_val - after_wer_val
        if before_wer_val is not None and after_wer_val is not None
        else None
    )

    # Available iff all four measurements succeeded (Req 7.6).
    all_available = all(
        v is not None
        for v in (before_cer_val, before_wer_val, after_cer_val, after_wer_val)
    )
    availability = "available" if all_available else "unavailable"

    return BeforeAfterReport(
        before_cer=before_cer_val,
        before_wer=before_wer_val,
        after_cer=after_cer_val,
        after_wer=after_wer_val,
        delta_cer=delta_cer_val,
        delta_wer=delta_wer_val,
        availability=availability,
    )


def store_evaluation_result(
    report: BeforeAfterReport,
    run_id: uuid.UUID,
    user_id: uuid.UUID,
    repos: object,
) -> object:
    """Persist a ``BeforeAfterReport`` as an ``EvaluationResult`` DB row.

    Parameters
    ----------
    report:
        The computed before/after report.
    run_id:
        UUID of the ``TrainingRun`` this evaluation is attached to.
    user_id:
        UUID of the owning user.
    repos:
        A ``RepositoryFactory`` instance (or any object with an
        ``evaluation_results`` attribute implementing ``add``).

    Returns
    -------
    EvaluationResult
        The persisted ORM object (not yet committed; the caller controls the
        transaction boundary).
    """
    from app.data.enums import EvaluationAvailability
    from app.data.models import EvaluationResult

    availability_enum = (
        EvaluationAvailability.AVAILABLE
        if report.availability == "available"
        else EvaluationAvailability.UNAVAILABLE
    )

    result = EvaluationResult(
        run_id=run_id,
        user_id=user_id,
        before_cer=report.before_cer,
        before_wer=report.before_wer,
        after_cer=report.after_cer,
        after_wer=report.after_wer,
        delta_cer=report.delta_cer,
        delta_wer=report.delta_wer,
        availability=availability_enum,
    )
    return repos.evaluation_results.add(result)


__all__ = [
    "cer",
    "wer",
    "BeforeAfterReport",
    "HeldOutPair",
    "RecogniserFn",
    "compute_before_after",
    "store_evaluation_result",
]
