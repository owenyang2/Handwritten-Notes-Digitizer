"""Property-based test for held-out split sizing and disjointness (design Property 17).

# Feature: handwriting-notes-digitizer, Property 17: Held-out set sizing and disjointness

This module verifies **Property 17**:

    For any set of N of a user's training pairs, the reserved held-out set has
    size equal to max(round(0.2 × N), 5) bounded by N, the held-out set and the
    training set are disjoint, and together they partition the user's pairs.

**Validates: Requirements 6.1**

Properties tested
-----------------
1. Held-out set size == max(round(0.2 * N), 5) bounded by N.
2. Held-out set and training set are disjoint (no pair appears in both).
3. Union of held-out + training equals the complete set of pairs (partition).

Generator strategy
------------------
``pairs_list()`` generates lists of N distinct "pairs" (represented as unique
integers for cheapness) where N >= 5, since the requirement specifies a minimum
held-out set of 5 which requires at least 5 pairs.  N is drawn from a broad
range (5 to 500) to exercise the sizing formula across the boundary where 20%
first exceeds 5 (at N = 26, round(0.2*26) = round(5.2) = 5; at N = 30,
round(0.2*30) = 6) as well as large inputs.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from app.services.personalization_service import split_held_out


# ---------------------------------------------------------------------------
# Reference sizing formula (independent implementation for cross-checking)
# ---------------------------------------------------------------------------

def _expected_held_out_size(n: int) -> int:
    """Reference implementation of Requirement 6.1 sizing: max(round(0.2*N), 5) bounded by N."""
    return min(max(round(0.2 * n), 5), n)


# ---------------------------------------------------------------------------
# Generator strategy: lists of N distinct pairs where N >= 5
# ---------------------------------------------------------------------------

@st.composite
def pairs_list(draw: st.DrawFn) -> list[int]:
    """Draw a list of N distinct integer pair IDs where N is in [5, 500].

    Using integers as "pairs" keeps the test cheap while exercising the full
    sizing and partitioning logic.  Each element is unique so that set-based
    disjointness checks are meaningful.
    """
    n = draw(st.integers(min_value=5, max_value=500))
    # Draw n distinct integers as fake pair IDs (unique so set ops are exact).
    ids = draw(st.lists(
        st.integers(min_value=0, max_value=10_000),
        min_size=n,
        max_size=n,
        unique=True,
    ))
    return ids


# ---------------------------------------------------------------------------
# Property 17: Held-out set sizing
# ---------------------------------------------------------------------------

# Feature: handwriting-notes-digitizer, Property 17: Held-out set sizing and disjointness
@given(pairs=pairs_list())
def test_held_out_size_matches_formula(pairs: list[int]) -> None:
    """Held-out size == max(round(0.2 * N), 5) bounded by N.

    **Validates: Requirements 6.1**
    """
    n = len(pairs)
    _, held_out = split_held_out(pairs)

    expected = _expected_held_out_size(n)
    assert len(held_out) == expected, (
        f"N={n}: expected held-out size {expected} "
        f"(max(round(0.2*{n}), 5) = max({round(0.2 * n)}, 5) = {max(round(0.2 * n), 5)}), "
        f"got {len(held_out)}"
    )


# ---------------------------------------------------------------------------
# Property 17: Disjointness
# ---------------------------------------------------------------------------

# Feature: handwriting-notes-digitizer, Property 17: Held-out set sizing and disjointness
@given(pairs=pairs_list())
def test_held_out_and_training_are_disjoint(pairs: list[int]) -> None:
    """Held-out set and training set share no pair IDs.

    **Validates: Requirements 6.1**
    """
    training, held_out = split_held_out(pairs)

    training_ids = set(training)
    held_out_ids = set(held_out)
    overlap = training_ids & held_out_ids

    assert len(overlap) == 0, (
        f"N={len(pairs)}: held-out and training sets overlap on {len(overlap)} pair(s): "
        f"{overlap}"
    )


# ---------------------------------------------------------------------------
# Property 17: Partition (union covers all pairs, no gaps or duplicates)
# ---------------------------------------------------------------------------

# Feature: handwriting-notes-digitizer, Property 17: Held-out set sizing and disjointness
@given(pairs=pairs_list())
def test_held_out_and_training_partition_all_pairs(pairs: list[int]) -> None:
    """Union of held-out + training equals the full set of pairs (partition).

    **Validates: Requirements 6.1**
    """
    training, held_out = split_held_out(pairs)

    combined = sorted(training + held_out)
    original = sorted(pairs)

    assert combined == original, (
        f"N={len(pairs)}: training + held_out does not equal all pairs.\n"
        f"  original ({len(original)}): {original[:20]}{'...' if len(original) > 20 else ''}\n"
        f"  combined ({len(combined)}): {combined[:20]}{'...' if len(combined) > 20 else ''}"
    )


# ---------------------------------------------------------------------------
# Combined: all three properties in a single pass (efficiency)
# ---------------------------------------------------------------------------

# Feature: handwriting-notes-digitizer, Property 17: Held-out set sizing and disjointness
@given(pairs=pairs_list())
def test_held_out_split_all_properties(pairs: list[int]) -> None:
    """Assert sizing, disjointness, and partition simultaneously for all N >= 5.

    This is the primary property test for Property 17.  It checks all three
    sub-properties in every generated example, providing the strongest overall
    coverage for Requirement 6.1.

    **Validates: Requirements 6.1**
    """
    n = len(pairs)
    training, held_out = split_held_out(pairs)

    # --- 1. Sizing ---
    expected_size = _expected_held_out_size(n)
    assert len(held_out) == expected_size, (
        f"N={n}: held-out size should be {expected_size}, got {len(held_out)}"
    )

    # --- 2. Disjointness ---
    training_ids = set(training)
    held_out_ids = set(held_out)
    assert training_ids.isdisjoint(held_out_ids), (
        f"N={n}: overlap between training and held-out: "
        f"{training_ids & held_out_ids}"
    )

    # --- 3. Partition ---
    assert sorted(training + held_out) == sorted(pairs), (
        f"N={n}: training + held_out does not cover all original pairs"
    )

    # --- 4. Bound: held-out size is always bounded by N ---
    assert len(held_out) <= n, (
        f"N={n}: held-out size {len(held_out)} exceeds N"
    )
