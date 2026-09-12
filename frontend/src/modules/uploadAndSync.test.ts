import { describe, expect, it, vi } from "vitest";
import {
  DurableUploadQueue,
  MemoryUploadQueueStore,
  validateUploadFile,
} from "./uploadQueue.js";
import {
  MemorySyncStateStore,
  SyncCoordinator,
} from "./syncOrchestrator.js";

vi.mock("../api/client.js", () => ({
  pushSyncOperations: vi.fn(async (operations) => ({
    accepted: operations.map((operation: { client_id: string; client_operation_id: string; document_id: string }, index: number) => ({
      client_id: operation.client_id,
      client_operation_id: operation.client_operation_id,
      operation_id: `op-${index}`,
      server_sequence: index + 1,
      document_id: operation.document_id,
      sync_version: 1,
      idempotent: false,
    })),
    conflicts: [],
    current_versions: {},
  })),
  pullSyncOperations: vi.fn(async () => ({
    items: [],
    next_cursor: 0,
    has_more: false,
  })),
  listSyncConflicts: vi.fn(async () => []),
  resolveSyncConflict: vi.fn(async (conflictId: string) => ({
    conflict_id: conflictId,
    document_id: "doc-1",
    client_id: "web",
    client_operation_id: "op-1",
    base_sync_version: 0,
    current_sync_version: 1,
    entity_type: "document",
    entity_id: "doc-1",
    operation_type: "document.rename",
    payload: { title: "A" },
    status: "resolved",
    resolution: { decision: "keep_server" },
    created_at: new Date().toISOString(),
    resolved_at: new Date().toISOString(),
  })),
  registerSyncClient: vi.fn(async (body: { client_id: string }) => ({
    client_id: body.client_id,
    platform: "web",
    device_name: "test",
    cursor: 0,
    last_seen_at: new Date().toISOString(),
    revoked_at: null,
    created_at: new Date().toISOString(),
  })),
  acknowledgeSyncCursor: vi.fn(async (clientId: string, cursor: number) => ({
    client_id: clientId,
    platform: "web",
    device_name: "test",
    cursor,
    last_seen_at: new Date().toISOString(),
    revoked_at: null,
    created_at: new Date().toISOString(),
  })),
}));

describe("validateUploadFile", () => {
  it("accepts png and pdf and rejects oversized files", () => {
    expect(
      validateUploadFile(new File([new Uint8Array([1, 2, 3])], "note.png", { type: "image/png" })),
    ).toBeNull();
    expect(
      validateUploadFile(new File([new Uint8Array([1])], "notes.pdf", { type: "application/pdf" })),
    ).toBeNull();
    const huge = new File([new Uint8Array(11 * 1024 * 1024)], "huge.png", { type: "image/png" });
    expect(validateUploadFile(huge)).toMatch(/larger than/i);
  });
});

describe("DurableUploadQueue", () => {
  it("persists queued work and completes through the processor", async () => {
    const store = new MemoryUploadQueueStore();
    const processor = vi.fn(async ({ update }) => {
      await update({ phase: "uploading", progress: 0.4 });
      await update({ phase: "complete", progress: 1, documentId: "doc-1", imageId: "img-1" });
      return { kind: "image" as const, documentId: "doc-1", imageId: "img-1", pageCount: 1 };
    });
    const queue = new DurableUploadQueue({ store, processor });
    await queue.load();
    const file = new File([new Uint8Array([9, 9])], "page.png", { type: "image/png" });
    const [item] = await queue.enqueue([file]);
    expect(item.status).toBe("queued");
    await queue.start();
    const finished = queue.snapshot().find((entry) => entry.id === item.id);
    expect(finished?.status).toBe("succeeded");
    expect(finished?.documentId).toBe("doc-1");
    expect(processor).toHaveBeenCalledOnce();
  });
});

describe("SyncCoordinator", () => {
  it("queues document operations and flushes them through push", async () => {
    const store = new MemorySyncStateStore();
    const coordinator = new SyncCoordinator({
      userId: "user-1",
      store,
      clientId: "web-client",
    });
    await coordinator.load();
    await coordinator.enqueueDocumentOperation("doc-1", 0, "document.rename", {
      title: "Lecture 3",
    });
    const result = await coordinator.syncNow();
    expect(result.error).toBeNull();
    expect(result.pushed).toBe(1);
    expect(coordinator.snapshot().pending).toHaveLength(0);
  });
});
