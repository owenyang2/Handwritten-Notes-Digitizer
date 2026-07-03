"""Focused tests for line-to-region alignment metadata."""

from app.services.recognition_service import _line_alignment_metadata


def test_matching_token_and_region_counts_are_aligned() -> None:
    status, reason = _line_alignment_metadata(
        "acted quaintly but kept driving their oxen forward",
        8,
    )

    assert status == "aligned"
    assert reason is None


def test_token_region_count_mismatch_is_uncertain() -> None:
    status, reason = _line_alignment_metadata(
        "quaintly but kept driving their oxen forward",
        1,
    )

    assert status == "uncertain"
    assert reason is not None
    assert "7 text token" in reason
    assert "1 detected word region" in reason
