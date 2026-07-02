"""Property-based tests for calibration prompt-set bounds — task 12.3.

# Feature: handwriting-notes-digitizer, Property 24: Calibration prompt-set bounds

For any calibration session created via ``create_calibration_session()``:
  - The number of prompts is between MIN_PROMPTS (3) and MAX_PROMPTS (10) inclusive.
  - Each prompt is between 1 and MAX_PROMPT_LENGTH (200) characters.
  - No duplicate prompts appear within a single session.

**Validates: Requirements 9.2**
"""

from __future__ import annotations

import random

from hypothesis import given, settings
from hypothesis import strategies as st

from app.services.calibration_service import (
    MAX_PROMPT_LENGTH,
    MAX_PROMPTS,
    MIN_PROMPTS,
    create_calibration_session,
)


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Generate optional num_prompts values covering the full valid range plus None
# (which lets the service choose a count randomly from [MIN_PROMPTS, MAX_PROMPTS]).
_num_prompts_strategy = st.one_of(
    st.none(),
    st.integers(min_value=MIN_PROMPTS, max_value=MAX_PROMPTS),
)

# A broad range of random seeds for the RNG so many different sessions
# are explored across the 100+ iterations Hypothesis runs.
_seed_strategy = st.integers(min_value=0, max_value=2**32 - 1)


# ---------------------------------------------------------------------------
# Property 24: Calibration prompt-set bounds
# ---------------------------------------------------------------------------

# Feature: handwriting-notes-digitizer, Property 24: Calibration prompt-set bounds
@given(num_prompts=_num_prompts_strategy, seed=_seed_strategy)
def test_calibration_prompt_count_within_bounds(
    num_prompts: int | None, seed: int
) -> None:
    """Property 24 (count): prompt count is always in [MIN_PROMPTS, MAX_PROMPTS].

    **Validates: Requirements 9.2**
    """
    rng = random.Random(seed)
    store: dict = {}

    session = create_calibration_session(
        num_prompts=num_prompts,
        rng=rng,
        session_store=store,
    )

    assert MIN_PROMPTS <= len(session.prompts) <= MAX_PROMPTS, (
        f"Expected prompt count in [{MIN_PROMPTS}, {MAX_PROMPTS}], "
        f"got {len(session.prompts)} (num_prompts={num_prompts}, seed={seed})"
    )


# Feature: handwriting-notes-digitizer, Property 24: Calibration prompt-set bounds
@given(num_prompts=_num_prompts_strategy, seed=_seed_strategy)
def test_calibration_each_prompt_length_within_bounds(
    num_prompts: int | None, seed: int
) -> None:
    """Property 24 (length): every prompt is between 1 and MAX_PROMPT_LENGTH chars.

    **Validates: Requirements 9.2**
    """
    rng = random.Random(seed)
    store: dict = {}

    session = create_calibration_session(
        num_prompts=num_prompts,
        rng=rng,
        session_store=store,
    )

    for prompt in session.prompts:
        assert 1 <= len(prompt) <= MAX_PROMPT_LENGTH, (
            f"Prompt length {len(prompt)} out of bounds [1, {MAX_PROMPT_LENGTH}]: "
            f"{prompt!r} (num_prompts={num_prompts}, seed={seed})"
        )


# Feature: handwriting-notes-digitizer, Property 24: Calibration prompt-set bounds
@given(num_prompts=_num_prompts_strategy, seed=_seed_strategy)
def test_calibration_no_duplicate_prompts_within_session(
    num_prompts: int | None, seed: int
) -> None:
    """Property 24 (uniqueness): no duplicate prompts within a single session.

    **Validates: Requirements 9.2**
    """
    rng = random.Random(seed)
    store: dict = {}

    session = create_calibration_session(
        num_prompts=num_prompts,
        rng=rng,
        session_store=store,
    )

    assert len(session.prompts) == len(set(session.prompts)), (
        f"Duplicate prompts found in session: {session.prompts} "
        f"(num_prompts={num_prompts}, seed={seed})"
    )
