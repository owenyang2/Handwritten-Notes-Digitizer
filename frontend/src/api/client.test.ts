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

import { afterEach, describe, expect, it, vi } from "vitest";

const encoder = new TextEncoder();

function sseResponse(events: string[]): Response {
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const event of events) controller.enqueue(encoder.encode(event));
      controller.close();
    },
  });
  return new Response(stream, { status: 200 });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

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

describe("recognizeImage SSE handling", () => {
  it("reports progress and accepts CRLF-delimited terminal results", async () => {
    const { recognizeImage } = await import("./client.js");
    const onProgress = vi.fn();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sseResponse([
      'data: {"type":"progress","current":1,"total":2}\r\n\r\n',
      'data: {"type":"result","result":{"outcome":"success","image_id":"img-1","lines":[{"line_index":0,"region":{"x":0,"y":0,"width":10,"height":5},"words":[{"line_index":0,"word_index":0,"text":"hello","region":{"x":0,"y":0,"width":10,"height":5}}]}]}}\r\n\r\n',
    ])));

    const result = await recognizeImage("img-1", onProgress);

    expect(onProgress).toHaveBeenCalledWith(1, 2);
    expect(result.status).toBe("ok");
    expect(result.lines[0].words[0].text).toBe("hello");
  });

  it("rejects when a stream closes before it emits a result", async () => {
    const { recognizeImage } = await import("./client.js");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sseResponse([
      'data: {"type":"progress","current":1,"total":2}\n\n',
    ])));

    await expect(recognizeImage("img-1")).rejects.toThrow(
      "Recognition stream ended before returning a result.",
    );
  });

  it("preserves a backend error response as an ApiError", async () => {
    const { recognizeImage } = await import("./client.js");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "Unavailable" }), {
        status: 503,
        headers: { "Content-Type": "application/json" },
      })),
    );

    await expect(recognizeImage("img-1")).rejects.toMatchObject({
      status: 503,
      message: "Unavailable",
    });
  });
});

describe("calibration request contract", () => {
  it("uses the backend prompt_index multipart field", async () => {
    const { submitCalibrationSample } = await import("./client.js");
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}", { status: 201 }));
    vi.stubGlobal("fetch", fetchMock);

    await submitCalibrationSample(
      "session-1",
      2,
      new File(["sample"], "sample.png", { type: "image/png" }),
    );

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(init.body).toBeInstanceOf(FormData);
    expect((init.body as FormData).get("prompt_index")).toBe("2");
    expect((init.body as FormData).get("promptIndex")).toBeNull();
  });
});
