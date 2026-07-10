"""Shared pytest + Hypothesis configuration for the backend test suite.

Hypothesis property-test profiles
---------------------------------
Property-based tests in this project MUST run a minimum of 25 iterations
(see tasks.md and the design's Correctness Properties section). We register a
``default`` profile that pins ``max_examples`` to at least 25 and load it for
every test session so that any test using ``@given(...)`` inherits the
project-wide iteration floor without needing per-test ``@settings`` overrides.

Selecting a profile
--------------------
The active profile can be overridden with the ``HYPOTHESIS_PROFILE`` environment
variable, e.g. ``HYPOTHESIS_PROFILE=ci pytest``:

- ``dev``     - fast local feedback, still >=25 iterations.
- ``default`` - the project default, >=25 iterations.
- ``ci``      - more thorough, 500 iterations for continuous integration.

Property-test tagging convention (shared with the TypeScript stack)
-------------------------------------------------------------------
Every property-based test (Hypothesis here, fast-check on the frontend) is
tagged with a comment in the form::

    # Feature: handwriting-notes-digitizer, Property {number}: {property_text}

placed directly above the test function. This links the test back to the
numbered property in design.md. See ``tests/PROPERTY_TESTING.md`` for the full
convention.
"""

import os

os.environ["HND_ENVIRONMENT"] = "test"

from hypothesis import HealthCheck, Verbosity, settings

# Minimum iterations every property-based test must run (tasks.md requirement).
MIN_PROPERTY_ITERATIONS = 25

settings.register_profile(
    "dev",
    max_examples=MIN_PROPERTY_ITERATIONS,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
    verbosity=Verbosity.normal,
)

settings.register_profile(
    "default",
    max_examples=MIN_PROPERTY_ITERATIONS,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
    verbosity=Verbosity.normal,
)

settings.register_profile(
    "ci",
    max_examples=500,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
    verbosity=Verbosity.normal,
)

# Load the requested profile (defaults to "default"); this applies the >=100
# iteration floor across the whole test session.
settings.load_profile(os.getenv("HYPOTHESIS_PROFILE", "default"))

import pytest
from typing import Iterator
from sqlalchemy.orm import Session
@pytest.fixture()
def db_session(session_factory) -> Iterator[Session]:
    with session_factory() as s:
        yield s
