// Sanity tests confirming the fast-check toolchain enforces the >=100 iteration
// floor via the global setup, and that the shared params expose the same floor.
// Guards the project convention that every property test runs >=100 iterations
// (see PROPERTY_TESTING.md).

import { describe, it, expect } from "vitest";
import fc from "fast-check";
import { PROPERTY_TEST_RUNS, PROPERTY_TEST_PARAMS } from "./property.js";

describe("property-test configuration", () => {
  it("exposes a shared iteration floor of at least 100", () => {
    expect(PROPERTY_TEST_RUNS).toBeGreaterThanOrEqual(100);
    expect(PROPERTY_TEST_PARAMS.numRuns).toBeGreaterThanOrEqual(100);
  });

  it("applies the global numRuns floor from vitest.setup.ts", () => {
    expect(fc.readConfigureGlobal().numRuns).toBeGreaterThanOrEqual(100);
  });

  // Feature: handwriting-notes-digitizer, Property 0: Toolchain sanity - fast-check runs >=100 iterations
  it("runs a trivial property end to end", () => {
    fc.assert(
      fc.property(fc.integer(), (n) => {
        return n === n;
      }),
    );
  });
});
