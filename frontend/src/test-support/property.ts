// Shared fast-check configuration for property-based tests.
//
// Property-based tests in this project MUST run a minimum of 100 iterations
// (see tasks.md and the design's Correctness Properties section). Importing the
// `vitest.setup.ts` file (wired via `setupFiles` in `vite.config.ts`) calls
// `fc.configureGlobal({ numRuns: PROPERTY_TEST_RUNS })`, so every `fc.assert`
// inherits the project-wide iteration floor without a per-test override.
//
// Use `PROPERTY_TEST_PARAMS` when you want to be explicit at the call site:
//
//     fc.assert(fc.property(...), PROPERTY_TEST_PARAMS);
//
// Property-test tagging convention (shared with the Python stack)
// ---------------------------------------------------------------
// Every property-based test (fast-check here, Hypothesis on the backend) is
// tagged with a comment in the form:
//
//     // Feature: handwriting-notes-digitizer, Property {number}: {property_text}
//
// placed directly above the test. This links the test back to the numbered
// property in design.md. See `docs/development/testing.md` from the repo root for
// the full convention.

import type { Parameters as FcParameters } from "fast-check";

/** Minimum iterations every property-based test must run (tasks.md). */
export const PROPERTY_TEST_RUNS = 100;

/** Spreadable fast-check parameters enforcing the iteration floor. */
export const PROPERTY_TEST_PARAMS: FcParameters<unknown> = {
  numRuns: PROPERTY_TEST_RUNS,
};
