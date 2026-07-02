"""Property-based test for CER/WER metric definition (design Property 21).

# Feature: handwriting-notes-digitizer, Property 21: CER and WER metric definition and non-negativity

This module verifies **Property 21**:

    For any reference and hypothesis text, CER equals the character-level
    Levenshtein distance divided by the number of reference characters; WER
    equals the word-level Levenshtein distance divided by the number of
    reference words; both are non-negative, both are zero when reference and
    hypothesis are identical, and an empty reference yields an "unavailable"
    result rather than a division error.

**Validates: Requirements 7.4, 7.5, 7.6**

Properties tested
-----------------
1. CER and WER are always non-negative (or None if reference is empty/no-words).
2. When reference == hypothesis: CER is 0.0; WER is 0.0 (or None if no words).
3. When reference is empty: CER returns None (unavailable, no division error).
4. When reference is whitespace-only: WER returns None (no words → unavailable).
5. CER == char-level Levenshtein distance / len(reference characters).
6. WER == word-level Levenshtein distance / len(reference words).
7. All properties hold for empty, whitespace-only, Unicode, and long strings.

Generator strategy
------------------
``text_pair()`` produces ``(reference, hypothesis)`` pairs drawn from a rich
alphabet including ASCII, Unicode letters, digits, whitespace, punctuation, and
emoji, across a wide range of lengths (0 to 500 characters), ensuring the test
exercises:
- empty strings (min_size=0)
- whitespace-only strings (space/tab/newline characters in the alphabet)
- short strings (common case)
- long strings (stress the DP correctness and non-negativity invariant)
- Unicode code-points (emoji, accented letters, CJK characters)
"""

from __future__ import annotations

import unicodedata

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.services.evaluation_service import _levenshtein, cer, wer

# ---------------------------------------------------------------------------
# Shared generator strategy
# ---------------------------------------------------------------------------

# Alphabet: printable ASCII + common Unicode ranges + whitespace + emoji
_ALPHABET = st.characters(
    whitelist_categories=(
        "Lu",  # uppercase letters
        "Ll",  # lowercase letters
        "Nd",  # decimal digits
        "Zs",  # space separators
        "Po",  # other punctuation
        "So",  # other symbols (includes many emoji/misc)
    ),
    whitelist_characters="\t\n\r ",  # explicit whitespace characters
    blacklist_categories=("Cs",),    # exclude surrogate pairs (invalid in Python str)
)


def _text(min_size: int = 0, max_size: int = 500) -> st.SearchStrategy[str]:
    """Draw a single text string from the rich alphabet."""
    return st.text(alphabet=_ALPHABET, min_size=min_size, max_size=max_size)


def _text_pair() -> st.SearchStrategy[tuple[str, str]]:
    """Draw a (reference, hypothesis) pair of text strings."""
    return st.tuples(_text(), _text())


def _non_empty_text() -> st.SearchStrategy[str]:
    """Draw a text string that contains at least one character."""
    return _text(min_size=1)


# ---------------------------------------------------------------------------
# Helper: pure-Python Levenshtein (independent reference implementation)
# ---------------------------------------------------------------------------

def _reference_levenshtein(seq_a: list, seq_b: list) -> int:
    """Straightforward O(m*n) DP — used to cross-check ``_levenshtein``."""
    m, n = len(seq_a), len(seq_b)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(m + 1):
        dp[i][0] = i
    for j in range(n + 1):
        dp[0][j] = j
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if seq_a[i - 1] == seq_b[j - 1]:
                dp[i][j] = dp[i - 1][j - 1]
            else:
                dp[i][j] = 1 + min(dp[i - 1][j - 1], dp[i - 1][j], dp[i][j - 1])
    return dp[m][n]


# ---------------------------------------------------------------------------
# Property 21.1 — Non-negativity
# ---------------------------------------------------------------------------

# Feature: handwriting-notes-digitizer, Property 21: CER and WER metric definition and non-negativity
@given(pair=_text_pair())
def test_cer_is_non_negative_or_none(pair: tuple[str, str]) -> None:
    """CER is always >= 0.0, or None when the reference has no characters.

    **Validates: Requirements 7.4, 7.6**
    """
    reference, hypothesis = pair
    result = cer(reference, hypothesis)
    if result is None:
        # None is only allowed when reference has no characters.
        assert list(reference) == [], (
            f"cer() returned None for non-empty reference {reference!r}"
        )
    else:
        assert result >= 0.0, (
            f"cer() returned negative value {result} for reference={reference!r}, "
            f"hypothesis={hypothesis!r}"
        )


# Feature: handwriting-notes-digitizer, Property 21: CER and WER metric definition and non-negativity
@given(pair=_text_pair())
def test_wer_is_non_negative_or_none(pair: tuple[str, str]) -> None:
    """WER is always >= 0.0, or None when the reference has no words.

    **Validates: Requirements 7.5, 7.6**
    """
    reference, hypothesis = pair
    result = wer(reference, hypothesis)
    if result is None:
        # None is only allowed when reference has no words (empty or whitespace-only).
        assert reference.split() == [], (
            f"wer() returned None for reference with words: {reference!r}"
        )
    else:
        assert result >= 0.0, (
            f"wer() returned negative value {result} for reference={reference!r}, "
            f"hypothesis={hypothesis!r}"
        )


# ---------------------------------------------------------------------------
# Property 21.2 — Zero when reference equals hypothesis
# ---------------------------------------------------------------------------

# Feature: handwriting-notes-digitizer, Property 21: CER and WER metric definition and non-negativity
@given(text=_non_empty_text())
def test_cer_is_zero_when_identical(text: str) -> None:
    """CER is exactly 0.0 when reference == hypothesis (non-empty reference).

    **Validates: Requirements 7.4**
    """
    result = cer(text, text)
    assert result == 0.0, (
        f"cer(text, text) should be 0.0 for text={text!r}, got {result}"
    )


# Feature: handwriting-notes-digitizer, Property 21: CER and WER metric definition and non-negativity
@given(text=_text(min_size=1).filter(lambda t: len(t.split()) > 0))
def test_wer_is_zero_when_identical(text: str) -> None:
    """WER is exactly 0.0 when reference == hypothesis (reference with words).

    **Validates: Requirements 7.5**
    """
    result = wer(text, text)
    assert result == 0.0, (
        f"wer(text, text) should be 0.0 for text={text!r}, got {result}"
    )


# ---------------------------------------------------------------------------
# Property 21.3 — Empty reference yields None (no division error)
# ---------------------------------------------------------------------------

# Feature: handwriting-notes-digitizer, Property 21: CER and WER metric definition and non-negativity
@given(hypothesis=_text())
def test_cer_empty_reference_returns_none(hypothesis: str) -> None:
    """CER returns None (not a ZeroDivisionError) when reference is empty.

    **Validates: Requirements 7.6**
    """
    result = cer("", hypothesis)
    assert result is None, (
        f"cer('', hypothesis) should be None for hypothesis={hypothesis!r}, got {result}"
    )


# Feature: handwriting-notes-digitizer, Property 21: CER and WER metric definition and non-negativity
@given(hypothesis=_text())
def test_wer_empty_reference_returns_none(hypothesis: str) -> None:
    """WER returns None (not a ZeroDivisionError) when reference is empty.

    **Validates: Requirements 7.6**
    """
    result = wer("", hypothesis)
    assert result is None, (
        f"wer('', hypothesis) should be None for hypothesis={hypothesis!r}, got {result}"
    )


# ---------------------------------------------------------------------------
# Property 21.4 — Whitespace-only reference: WER returns None
# ---------------------------------------------------------------------------

# Feature: handwriting-notes-digitizer, Property 21: CER and WER metric definition and non-negativity
@given(
    whitespace=st.text(
        alphabet=st.characters(whitelist_categories=("Zs",), whitelist_characters="\t\n\r "),
        min_size=1,
        max_size=100,
    ),
    hypothesis=_text(),
)
def test_wer_whitespace_only_reference_returns_none(whitespace: str, hypothesis: str) -> None:
    """WER returns None when the reference contains only whitespace (no words).

    **Validates: Requirements 7.6**
    """
    # Confirm there truly are no words in the whitespace reference.
    assert whitespace.split() == [], (
        f"Test setup error: {whitespace!r} should have no words"
    )
    result = wer(whitespace, hypothesis)
    assert result is None, (
        f"wer(whitespace_ref, hypothesis) should be None for ref={whitespace!r}, "
        f"hypothesis={hypothesis!r}, got {result}"
    )


# ---------------------------------------------------------------------------
# Property 21.5 — CER definition: equals char-Levenshtein / len(ref chars)
# ---------------------------------------------------------------------------

# Feature: handwriting-notes-digitizer, Property 21: CER and WER metric definition and non-negativity
@given(pair=st.tuples(_non_empty_text(), _text()))
def test_cer_equals_char_levenshtein_over_ref_length(pair: tuple[str, str]) -> None:
    """CER == char-level Levenshtein distance / number of reference characters.

    Cross-checks the production implementation against an independent
    reference implementation of Levenshtein distance.

    **Validates: Requirements 7.4**
    """
    reference, hypothesis = pair
    ref_chars = list(reference)
    assert len(ref_chars) > 0  # guaranteed by _non_empty_text()

    hyp_chars = list(hypothesis)
    expected_distance = _reference_levenshtein(ref_chars, hyp_chars)
    expected_cer = expected_distance / len(ref_chars)

    result = cer(reference, hypothesis)
    assert result is not None, (
        f"cer() unexpectedly returned None for reference={reference!r}"
    )
    assert result == pytest.approx(expected_cer, rel=1e-9), (
        f"CER mismatch: expected {expected_cer} (distance={expected_distance}, "
        f"ref_len={len(ref_chars)}), got {result} for "
        f"reference={reference!r}, hypothesis={hypothesis!r}"
    )


# ---------------------------------------------------------------------------
# Property 21.6 — WER definition: equals word-Levenshtein / len(ref words)
# ---------------------------------------------------------------------------

# Feature: handwriting-notes-digitizer, Property 21: CER and WER metric definition and non-negativity
@given(pair=st.tuples(_text().filter(lambda t: len(t.split()) > 0), _text()))
def test_wer_equals_word_levenshtein_over_ref_length(pair: tuple[str, str]) -> None:
    """WER == word-level Levenshtein distance / number of reference words.

    Cross-checks the production implementation against an independent
    reference implementation of Levenshtein distance operating on word lists.

    **Validates: Requirements 7.5**
    """
    reference, hypothesis = pair
    ref_words = reference.split()
    assert len(ref_words) > 0  # guaranteed by the filter above

    hyp_words = hypothesis.split()
    expected_distance = _reference_levenshtein(ref_words, hyp_words)
    expected_wer = expected_distance / len(ref_words)

    result = wer(reference, hypothesis)
    assert result is not None, (
        f"wer() unexpectedly returned None for reference={reference!r}"
    )
    assert result == pytest.approx(expected_wer, rel=1e-9), (
        f"WER mismatch: expected {expected_wer} (distance={expected_distance}, "
        f"ref_words={len(ref_words)}), got {result} for "
        f"reference={reference!r}, hypothesis={hypothesis!r}"
    )


# ---------------------------------------------------------------------------
# Property 21.7 — Unicode and long strings (stress / coverage)
# ---------------------------------------------------------------------------

# Feature: handwriting-notes-digitizer, Property 21: CER and WER metric definition and non-negativity
@given(
    pair=st.tuples(
        st.text(
            alphabet=st.characters(
                whitelist_categories=("Lu", "Ll", "Lo", "Nd", "So", "Zs"),
                blacklist_categories=("Cs",),
            ),
            min_size=1,
            max_size=500,
        ),
        st.text(
            alphabet=st.characters(
                whitelist_categories=("Lu", "Ll", "Lo", "Nd", "So", "Zs"),
                blacklist_categories=("Cs",),
            ),
            min_size=0,
            max_size=500,
        ),
    )
)
def test_cer_and_wer_handle_unicode_and_long_strings(pair: tuple[str, str]) -> None:
    """CER and WER do not raise exceptions and respect non-negativity for any
    combination of Unicode characters and string lengths up to 500 chars.

    Covers empty, whitespace-only, multi-byte Unicode (CJK, emoji, accented),
    and long strings in a single property.

    **Validates: Requirements 7.4, 7.5, 7.6**
    """
    reference, hypothesis = pair

    # CER: must not raise; must be non-negative or None
    cer_result = cer(reference, hypothesis)
    if cer_result is None:
        assert list(reference) == [], (
            f"cer() returned None for non-empty reference {reference!r}"
        )
    else:
        assert cer_result >= 0.0

    # WER: must not raise; must be non-negative or None
    wer_result = wer(reference, hypothesis)
    if wer_result is None:
        assert reference.split() == [], (
            f"wer() returned None for reference with words: {reference!r}"
        )
    else:
        assert wer_result >= 0.0
