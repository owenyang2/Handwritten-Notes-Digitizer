// Global Vitest setup: applied before every test file via `setupFiles` in
// `vite.config.ts`.
//
// Configures fast-check so that all property-based tests default to at least
// PROPERTY_TEST_RUNS (100) iterations, satisfying the project requirement that
// every property test runs a minimum of 100 iterations.
import fc from "fast-check";
import { PROPERTY_TEST_RUNS } from "./src/test-support/property.js";

fc.configureGlobal({ numRuns: PROPERTY_TEST_RUNS });
