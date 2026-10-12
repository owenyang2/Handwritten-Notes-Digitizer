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

describe("document library contract", () => {
  it("serializes lifecycle filters and pagination into the request", async () => {
    const { getDocuments } = await import("./client.js");
    const fetchMock = vi.fn().mockResolvedValue(new Response("[]", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await getDocuments({
      query: "linear algebra",
      includeArchived: true,
      includeTrashed: true,
      view: "trash",
      limit: 10,
      offset: 20,
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/documents?query=linear+algebra&include_archived=true&include_trashed=true&view=trash&limit=10&offset=20",
      expect.objectContaining({ method: "GET" }),
    );
  });

  it("uses the reversible lifecycle endpoints", async () => {
    const { restoreDocument, trashDocument, updateDocument } = await import("./client.js");
    const fetchMock = vi.fn().mockImplementation(
      () =>
        new Response(JSON.stringify({ document_id: "doc-1" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await updateDocument("doc-1", { archived: true });
    await restoreDocument("doc-1");
    await trashDocument("doc-1");

    expect(fetchMock.mock.calls.map(([url, init]) => [url, init?.method])).toEqual([
      ["/api/documents/doc-1", "PATCH"],
      ["/api/documents/doc-1/restore", "POST"],
      ["/api/documents/doc-1", "DELETE"],
    ]);
  });
});

describe("sync client contract", () => {
  it("pushes a bounded operation batch with the expected wire shape", async () => {
    const { pushSyncOperations } = await import("./client.js");
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ accepted: [], conflicts: [], current_versions: {} }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const operation = {
      client_id: "ipad-1",
      client_operation_id: "op-1",
      document_id: "doc-1",
      base_sync_version: 0,
      entity_type: "document",
      entity_id: "doc-1",
      operation_type: "document.rename",
      payload: { title: "Updated" },
    };
    await pushSyncOperations([operation]);

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/sync/push");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ operations: [operation] });
  });

  it("serializes pull cursors and conflict decisions", async () => {
    const { listSyncConflicts, pullSyncOperations, resolveSyncConflict } =
      await import("./client.js");
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ items: [], next_cursor: 12, has_more: false }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await pullSyncOperations(7, 20);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/sync/pull?cursor=7&limit=20");

    fetchMock.mockResolvedValue(
      new Response(JSON.stringify([]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    await listSyncConflicts(true);
    expect(fetchMock.mock.calls[1][0]).toBe("/api/sync/conflicts?include_resolved=true");

    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ conflict_id: "conflict-1" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    await resolveSyncConflict("conflict-1", "retry_as_new", { base: 12 });
    const [url, init] = fetchMock.mock.calls[2] as [string, RequestInit];
    expect(url).toBe("/api/sync/conflicts/conflict-1/resolve");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({
      decision: "retry_as_new",
      details: { base: 12 },
    });
  });
});
