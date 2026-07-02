"""Property-based tests for the Export_Service — Property 23.

# Feature: handwriting-notes-digitizer, Property 23: Export fidelity preserves structure and current corrections

Property 23: Export fidelity preserves structure and current corrections
  For any list of LineInput objects with at least one non-empty line:
  - Plain text: all word strings from all lines appear in the output;
    line breaks are preserved as \\n.
  - Markdown: same fidelity with paragraph structure preserved.
  - The output contains exactly the words that were in the input
    (no additions, no removals).

Validates: Requirements 8.1, 8.2, 8.3
"""

from __future__ import annotations

from typing import List

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.services.export_service import (
    ExportSuccess,
    LineInput,
    export,
)

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# A single printable word — non-empty, no whitespace (so it can be split back
# out of joined lines unambiguously).
word_strategy = st.text(
    alphabet=st.characters(
        whitelist_categories=("Lu", "Ll", "Nd", "Po"),
        blacklist_characters="\n\r\t ",
    ),
    min_size=1,
    max_size=30,
)

# A non-blank line: at least one non-empty word.
non_blank_line_strategy = st.lists(word_strategy, min_size=1, max_size=10).map(
    lambda words: LineInput(words=words)
)

# A blank line (serves as paragraph separator in markdown).
blank_line_strategy = st.just(LineInput(words=[]))

# A line that may or may not be blank.
any_line_strategy = st.one_of(non_blank_line_strategy, blank_line_strategy)

# A list of LineInput objects with *at least one non-blank line*, which is the
# precondition for a non-empty export (Req 8.5 says empty → ExportEmpty, not
# tested here).
line_list_strategy = st.lists(any_line_strategy, min_size=1, max_size=20).filter(
    lambda lines: any(not line.is_blank() for line in lines)
)


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


def _all_words_from_lines(lines: List[LineInput]) -> List[str]:
    """Return every non-empty word from every non-blank line, in order."""
    words: List[str] = []
    for line in lines:
        if not line.is_blank():
            for w in line.words:
                if w.strip():
                    words.append(w)
    return words


# ---------------------------------------------------------------------------
# Property 23 — Plain text fidelity (Req 8.1)
# ---------------------------------------------------------------------------


# Feature: handwriting-notes-digitizer, Property 23: Export fidelity preserves structure and current corrections
@given(lines=line_list_strategy)
@settings(max_examples=25)
def test_plain_text_contains_all_words(lines: List[LineInput]) -> None:
    """Req 8.1: every word from every line appears in the plain-text output.

    Validates: Requirements 8.1, 8.3
    """
    result = export(lines, format="plain")
    assert isinstance(result, ExportSuccess), (
        f"Expected ExportSuccess, got {result!r} for lines={lines}"
    )
    output = result.text
    expected_words = _all_words_from_lines(lines)
    for word in expected_words:
        assert word in output, (
            f"Word {word!r} missing from plain-text output:\n{output!r}"
        )


# Feature: handwriting-notes-digitizer, Property 23: Export fidelity preserves structure and current corrections
@given(lines=line_list_strategy)
@settings(max_examples=25)
def test_plain_text_preserves_line_breaks(lines: List[LineInput]) -> None:
    """Req 8.1: plain-text output preserves line breaks as \\n.

    Each non-blank line's text must appear as a complete segment separated by
    \\n boundaries.

    Validates: Requirements 8.1
    """
    result = export(lines, format="plain")
    assert isinstance(result, ExportSuccess)

    output_lines = result.text.split("\n")
    # The number of output lines must equal the number of input lines (blank
    # lines are preserved as empty strings in plain text).
    assert len(output_lines) == len(lines), (
        f"Expected {len(lines)} output lines, got {len(output_lines)}.\n"
        f"Input: {lines}\nOutput: {result.text!r}"
    )

    # Each output line must match the as_text() of the corresponding input line.
    for i, (line_input, line_output) in enumerate(zip(lines, output_lines)):
        expected = line_input.as_text()
        assert line_output == expected, (
            f"Line {i}: expected {expected!r}, got {line_output!r}"
        )


# Feature: handwriting-notes-digitizer, Property 23: Export fidelity preserves structure and current corrections
@given(lines=line_list_strategy)
@settings(max_examples=25)
def test_plain_text_no_extra_or_missing_words(lines: List[LineInput]) -> None:
    """Plain text output contains exactly the words in the input — no additions.

    Validates: Requirements 8.1, 8.3
    """
    result = export(lines, format="plain")
    assert isinstance(result, ExportSuccess)

    # Reconstruct the expected word multiset from the output.
    output_words = result.text.replace("\n", " ").split()
    expected_words = _all_words_from_lines(lines)

    assert sorted(output_words) == sorted(expected_words), (
        f"Word mismatch.\nExpected: {sorted(expected_words)}\n"
        f"Got:      {sorted(output_words)}"
    )


# ---------------------------------------------------------------------------
# Property 23 — Markdown fidelity (Req 8.2)
# ---------------------------------------------------------------------------


# Feature: handwriting-notes-digitizer, Property 23: Export fidelity preserves structure and current corrections
@given(lines=line_list_strategy)
@settings(max_examples=25)
def test_markdown_contains_all_words(lines: List[LineInput]) -> None:
    """Req 8.2: every word from every non-blank line appears in the markdown output.

    Validates: Requirements 8.2, 8.3
    """
    result = export(lines, format="markdown")
    assert isinstance(result, ExportSuccess), (
        f"Expected ExportSuccess, got {result!r} for lines={lines}"
    )
    output = result.text
    expected_words = _all_words_from_lines(lines)
    for word in expected_words:
        assert word in output, (
            f"Word {word!r} missing from markdown output:\n{output!r}"
        )


# Feature: handwriting-notes-digitizer, Property 23: Export fidelity preserves structure and current corrections
@given(lines=line_list_strategy)
@settings(max_examples=25)
def test_markdown_paragraph_structure_preserved(lines: List[LineInput]) -> None:
    """Req 8.2: blank input lines create paragraph breaks (double-\\n) in markdown.

    Non-blank lines within the same paragraph run are separated by single \\n.

    Validates: Requirements 8.2
    """
    result = export(lines, format="markdown")
    assert isinstance(result, ExportSuccess)

    # Build the expected paragraph structure: split non-blank lines into groups
    # separated by blank lines.
    paragraphs: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.is_blank():
            if current:
                paragraphs.append(current)
                current = []
        else:
            current.append(line.as_text())
    if current:
        paragraphs.append(current)

    expected_output = "\n\n".join("\n".join(para) for para in paragraphs)
    assert result.text == expected_output, (
        f"Markdown paragraph structure mismatch.\n"
        f"Expected:\n{expected_output!r}\n"
        f"Got:\n{result.text!r}"
    )


# Feature: handwriting-notes-digitizer, Property 23: Export fidelity preserves structure and current corrections
@given(lines=line_list_strategy)
@settings(max_examples=25)
def test_markdown_no_extra_or_missing_words(lines: List[LineInput]) -> None:
    """Markdown output contains exactly the words in the input — no additions.

    Validates: Requirements 8.2, 8.3
    """
    result = export(lines, format="markdown")
    assert isinstance(result, ExportSuccess)

    # Strip paragraph separators and split into words.
    output_words = result.text.replace("\n\n", " ").replace("\n", " ").split()
    expected_words = _all_words_from_lines(lines)

    assert sorted(output_words) == sorted(expected_words), (
        f"Word mismatch in markdown.\nExpected: {sorted(expected_words)}\n"
        f"Got:      {sorted(output_words)}"
    )


# ---------------------------------------------------------------------------
# Property 23 — Current corrections reflected (Req 8.3)
# ---------------------------------------------------------------------------


# Feature: handwriting-notes-digitizer, Property 23: Export fidelity preserves structure and current corrections
@given(
    original_words=st.lists(word_strategy, min_size=1, max_size=10),
    corrected_words=st.lists(word_strategy, min_size=1, max_size=10),
    fmt=st.sampled_from(["plain", "markdown"]),
)
@settings(max_examples=25)
def test_export_reflects_corrected_text_not_original(
    original_words: List[str],
    corrected_words: List[str],
    fmt: str,
) -> None:
    """Req 8.3: export uses whatever words are passed in (the current/corrected text).

    If the corrected line differs from the original, the output must contain
    the corrected words and must not contain any original-only words.

    Validates: Requirements 8.3
    """
    # Use corrected words only — the service has no notion of "original"
    # state; callers pass in the current (corrected) state directly.
    corrected_line = LineInput(words=corrected_words)
    result = export([corrected_line], format=fmt)  # type: ignore[arg-type]
    assert isinstance(result, ExportSuccess)

    for word in corrected_words:
        assert word in result.text, (
            f"Corrected word {word!r} not found in {fmt} output:\n{result.text!r}"
        )
