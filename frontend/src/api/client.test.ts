// End-to-end integration test for the frontend API client module (task 19.3).
//
// Asserts that the frontend API client module exports the expected functions
// required to connect the UI to all backend capabilities:
//   - getHealth          → GET /health (capability registry)
//   - uploadImage        → POST /images
//   - recognizeImage     → POST /images/{id}/recognize
//   - submitCorrection   → POST /images/{id}/corrections
//   - exportText         → POST /export
//   - startCalibrationSession  → POST /calibration/session
//   - submitCalibrationSample  → POST /calibration/{session_id}/samples
//
// Validates: Requirements 11.4

import { describe, it, expect } from "vitest";

describe("API client module — full capability surface (Req 11.4)", () => {
  it("exports getHealth as a function (GET /health)", async () => {
    const { getHealth } = await import("./client.js");
    expect(typeof getHealth).toBe("function");
  });

  it("exports uploadImage as a function (POST /images)", async () => {
    const { uploadImage } = await import("./client.js");
    expect(typeof uploadImage).toBe("function");
  });

  it("exports recognizeImage as a function (POST /images/{id}/recognize)", async () => {
    const { recognizeImage } = await import("./client.js");
    expect(typeof recognizeImage).toBe("function");
  });

  it("exports submitCorrection as a function (POST /images/{id}/corrections)", async () => {
    const { submitCorrection } = await import("./client.js");
    expect(typeof submitCorrection).toBe("function");
  });

  it("exports exportText as a function (POST /export)", async () => {
    const { exportText } = await import("./client.js");
    expect(typeof exportText).toBe("function");
  });

  it("exports startCalibrationSession as a function (POST /calibration/session)", async () => {
    const { startCalibrationSession } = await import("./client.js");
    expect(typeof startCalibrationSession).toBe("function");
  });

  it("exports submitCalibrationSample as a function (POST /calibration/{session_id}/samples)", async () => {
    const { submitCalibrationSample } = await import("./client.js");
    expect(typeof submitCalibrationSample).toBe("function");
  });

  it("exports all required capability functions in a single import", async () => {
    // Assert all seven functions are present and callable in one check.
    const clientModule = await import("./client.js");
    const requiredFunctions = [
      "getHealth",
      "uploadImage",
      "recognizeImage",
      "submitCorrection",
      "exportText",
      "startCalibrationSession",
      "submitCalibrationSample",
    ] as const;

    for (const fnName of requiredFunctions) {
      expect(
        typeof clientModule[fnName],
        `client.ts must export '${fnName}' as a function`,
      ).toBe("function");
    }
  });

  it("exports ApiError class for typed error handling", async () => {
    const { ApiError } = await import("./client.js");
    expect(typeof ApiError).toBe("function"); // class is typeof "function"
    // Verify it can be instantiated with status + message.
    const err = new ApiError(404, "Not Found");
    expect(err instanceof Error).toBe(true);
    expect(err.status).toBe(404);
    expect(err.message).toBe("Not Found");
  });
});
