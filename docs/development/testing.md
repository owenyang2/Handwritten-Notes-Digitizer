# Testing

Run web and backend tests from the repository root after installing their
dependencies:

```bash
make test
```

`make test` covers those two suites. Native tests and shared-contract checks
are separate:

| Check | Command from the repository root | Boundary |
| --- | --- | --- |
| Backend | `make test-backend` | Unit, integration, and Hypothesis properties; real PostgreSQL/S3 checks are opt-in |
| Website | `make test-frontend` | Vitest and fast-check |
| Website build | `cd frontend && npm run typecheck && npm run build` | TypeScript and production assets |
| Swift package | `cd ios && swift test --filter PersonalizedNotesKitTests` | Shared store, ink, and sync logic; no physical Pencil testing |
| Contract drift | `backend/.venv/bin/python scripts/check_generated_contracts.py` | Checked-in OpenAPI, ink fixture, and error codes match regeneration |
| Swift wire boundary | `backend/.venv/bin/python scripts/check_swift_api_models.py` | Swift DTO compatibility with OpenAPI |

For UIKit/PencilKit app tests and UI tests, generate the project described in
the [iPad guide](../../ios/README.md) and use its Xcode test targets. The opt-in
real-model simulator journey also needs installed models and disposable demo
credentials; normal CI does not supply those. Simulator checks do not establish
physical Apple Pencil behavior.

The checked-in [CI workflow](../../.github/workflows/ci.yml) runs documentation, frontend,
backend and Swift package jobs. Local test counts and recorded workflows are listed in
[dated evidence](../release-evidence/README.md); those reports do not certify the
current remote CI or model accuracy.

## Documentation and browser media

```bash
make docs-check
make docs-screenshots
```

The first command needs only Python 3.11+. The second needs the frontend npm
dependencies and Playwright Chromium (`cd frontend && npx playwright install chromium`).
It builds the production website, runs the existing responsive visual journey,
and refreshes the documented screenshots and their source manifest. Normal
browser tests write to ignored test output; dated evidence is preserved.

See the [documentation standard](documentation.md) for PR impact checks.

## Property-based testing conventions

This project uses property-based testing on both stacks:

- **Python backend** — [Hypothesis](https://hypothesis.readthedocs.io/) on top of pytest.
- **TypeScript frontend** — [fast-check](https://fast-check.dev/) on top of Vitest.

The [original design specification](../../.kiro/specs/handwriting-notes-digitizer/design.md)'s _Correctness Properties_ section defines numbered
properties (Property 1, Property 2, ...). Each property is implemented as a
**single** property-based test running the configured iteration floor.

## Iteration settings

Shared configuration defines the actual settings; individual tests inherit
them unless they explicitly override them:

- **Backend:** `backend/tests/conftest.py` registers Hypothesis
  profiles: `dev` and `default` use `max_examples = 25`; `ci` uses 500.
  Select one with the `HYPOTHESIS_PROFILE` environment variable.
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

- `{number}` is the property number from the original design specification linked above.
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
