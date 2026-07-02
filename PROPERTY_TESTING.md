# Property-Based Testing Conventions

This project uses property-based testing on both stacks:

- **Python backend** — [Hypothesis](https://hypothesis.readthedocs.io/) on top of pytest.
- **TypeScript frontend** — [fast-check](https://fast-check.dev/) on top of Vitest.

The design document's _Correctness Properties_ section defines numbered
properties (Property 1, Property 2, ...). Each property is implemented as a
**single** property-based test running a **minimum of 100 iterations**.

## Iteration floor (>= 100)

The 100-iteration minimum is enforced by shared configuration, so individual
tests do not need to repeat it:

- **Backend:** `backend/tests/conftest.py` registers and loads a Hypothesis
  profile (`default`) with `max_examples = 100`. Override per session with the
  `HYPOTHESIS_PROFILE` environment variable (`dev`, `default`, or `ci`).
- **Frontend:** `frontend/vitest.setup.ts` calls
  `fc.configureGlobal({ numRuns: 100 })`. The constant lives in
  `frontend/src/test-support/property.ts` (`PROPERTY_TEST_RUNS`,
  `PROPERTY_TEST_PARAMS`).

## Tagging convention (shared across both stacks)

Tag every property-based test with a comment placed directly above the test,
in this exact form:

```
Feature: handwriting-notes-digitizer, Property {number}: {property_text}
```

- `{number}` is the property number from `design.md`.
- `{property_text}` is the property's one-line statement.

Use each stack's comment syntax:

**Python (Hypothesis)**

```python
# Feature: handwriting-notes-digitizer, Property 1: Image validation accepts exactly the valid set
@given(...)
def test_image_validation_accepts_exactly_the_valid_set(...):
    ...
```

**TypeScript (fast-check)**

```ts
// Feature: handwriting-notes-digitizer, Property 7: Display order follows reading order
it("orders words in reading order", () => {
  fc.assert(fc.property(/* ... */));
});
```

Additionally, link the requirements the property validates using the format
`**Validates: Requirements X.Y**` in the test's docstring or a nearby comment,
matching the design's property entries.

## Running the suites

```bash
# Backend (from backend/)
.venv/bin/python -m pytest

# Frontend (from frontend/)
npm run test   # vitest --run
```
