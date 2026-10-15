"""Sanity tests confirming the Hypothesis toolchain enforces the >=25
iteration floor and that a tagged property test runs end to end.

These guard the project convention that every property-based test runs a
minimum of 25 iterations (see docs/development/testing.md).
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from tests.conftest import MIN_PROPERTY_ITERATIONS


def test_active_profile_enforces_minimum_iterations():
    # The loaded profile (default/dev/ci) must run at least 25 examples.
    assert settings().max_examples >= MIN_PROPERTY_ITERATIONS


# Feature: handwriting-notes-digitizer, Property 0: Toolchain sanity - Hypothesis runs >=100 iterations
@given(st.integers())
def test_property_toolchain_sanity(value: int):
    # A trivial always-true property; its purpose is to confirm @given works
    # and inherits the project-wide iteration floor from the loaded profile.
    assert value == value
