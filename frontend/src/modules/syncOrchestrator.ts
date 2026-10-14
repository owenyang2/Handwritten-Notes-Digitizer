import {
  listSyncConflicts,
  pullSyncOperations,
  pushSyncOperations,
  resolveSyncConflict,
  registerSyncClient,
  acknowledgeSyncCursor,
  type SyncConflict,
  type SyncOperationRequest,
} from "../api/client.js";

export interface StoredSyncState {
  clientId: string;
  cursor: number;
  pending: SyncOperationRequest[];
  lastSyncAt: string | null;
  lastError: string | null;
}

export interface SyncStateStore {
  load(userId: string): Promise<StoredSyncState | null>;
  save(userId: string, state: StoredSyncState): Promise<void>;
}

export class MemorySyncStateStore implements SyncStateStore {
  private readonly states = new Map<string, StoredSyncState>();

  async load(userId: string): Promise<StoredSyncState | null> {
    const state = this.states.get(userId);
    return state ? { ...state, pending: state.pending.map((item) => ({ ...item, payload: { ...item.payload } })) } : null;
  }

  async save(userId: string, state: StoredSyncState): Promise<void> {
    this.states.set(userId, {
      ...state,
      pending: state.pending.map((item) => ({ ...item, payload: { ...item.payload } })),
    });
  }
}

/** Device-local persistence for JSON operation metadata; note bytes never enter this store. */
export class LocalStorageSyncStateStore implements SyncStateStore {
  constructor(private readonly keyPrefix = "handwriting-notes-sync-v1") {}

  async load(userId: string): Promise<StoredSyncState | null> {
    const storage = this.getStorage();
    const raw = storage.getItem(this.key(userId));
    if (!raw) return null;
    try {
      const parsed: unknown = JSON.parse(raw);
      if (!parsed || typeof parsed !== "object") return null;
      const value = parsed as Partial<StoredSyncState>;
      if (
        typeof value.clientId !== "string" ||
        typeof value.cursor !== "number" ||
        !Array.isArray(value.pending)
      ) return null;
      return {
        clientId: value.clientId,
        cursor: Math.max(0, Math.floor(value.cursor)),
        pending: value.pending as SyncOperationRequest[],
        lastSyncAt: typeof value.lastSyncAt === "string" ? value.lastSyncAt : null,
        lastError: typeof value.lastError === "string" ? value.lastError : null,
      };
    } catch {
      return null;
    }
  }

  async save(userId: string, state: StoredSyncState): Promise<void> {
    this.getStorage().setItem(this.key(userId), JSON.stringify(state));
  }

  private key(userId: string): string {
    return `${this.keyPrefix}:${userId}`;
  }

  private getStorage(): Storage {
    if (typeof localStorage === "undefined") {
      throw new Error("This browser does not provide local storage for sync state.");
    }
    return localStorage;
  }
}

export interface SyncSnapshot {
  clientId: string;
  cursor: number;
  pending: SyncOperationRequest[];
  lastSyncAt: string | null;
  lastError: string | null;
  conflicts: SyncConflict[];
  durable: boolean;
}

export interface SyncRunResult {
  pushed: number;
  pulled: number;
  conflicts: SyncConflict[];
  error: string | null;
}

export interface SyncCoordinatorOptions {
  userId: string;
  store?: SyncStateStore;
  clientId?: string;
  onDocumentsChanged?: () => void;
}

function createId(prefix: string): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function readableError(error: unknown): string {
  return error instanceof Error && error.message ? error.message : "Sync could not complete.";
}

function operationMatchesConflict(operation: SyncOperationRequest, conflict: SyncConflict): boolean {
  return operation.document_id === conflict.document_id &&
    operation.entity_type === conflict.entity_type &&
    operation.entity_id === conflict.entity_id &&
    operation.operation_type === conflict.operation_type &&
    JSON.stringify(operation.payload) === JSON.stringify(conflict.payload);
}

/**
 * Coordinates the browser's durable operation queue with the existing server
 * push/pull contract. Direct note content is never placed in localStorage by
 * this class; only the JSON operation envelope is retained for retry.
 */
export class SyncCoordinator {
  private readonly userId: string;
  private readonly store: SyncStateStore;
  private readonly onDocumentsChanged?: () => void;
  private readonly listeners = new Set<(snapshot: SyncSnapshot) => void>();
  private state: StoredSyncState;
  private conflicts: SyncConflict[] = [];
  private loaded = false;
  private loadPromise: Promise<void> | null = null;
  private autoSyncStarted = false;
  private autoSyncCleanup?: () => void;

  constructor(options: SyncCoordinatorOptions) {
    this.userId = options.userId;
    this.store = options.store ?? new LocalStorageSyncStateStore();
    this.onDocumentsChanged = options.onDocumentsChanged;
    this.state = {
      clientId: options.clientId ?? createId("web"),
      cursor: 0,
      pending: [],
      lastSyncAt: null,
      lastError: null,
    };
  }

  subscribe(listener: (snapshot: SyncSnapshot) => void): () => void {
    this.listeners.add(listener);
    listener(this.snapshot());
    return () => this.listeners.delete(listener);
  }

  snapshot(): SyncSnapshot {
    return {
      clientId: this.state.clientId,
      cursor: this.state.cursor,
      pending: this.state.pending.map((item) => ({ ...item, payload: { ...item.payload } })),
      lastSyncAt: this.state.lastSyncAt,
      lastError: this.state.lastError,
      conflicts: this.conflicts.map((conflict) => ({ ...conflict, payload: { ...conflict.payload } })),
      durable: this.store instanceof LocalStorageSyncStateStore,
    };
  }

  async load(): Promise<void> {
    if (this.loaded) return;
    if (this.loadPromise) return this.loadPromise;
    this.loadPromise = (async () => {
      try {
        const stored = await this.store.load(this.userId);
        if (stored) {
          this.state = {
            clientId: stored.clientId || this.state.clientId,
            cursor: Math.max(0, Math.floor(stored.cursor)),
            pending: stored.pending,
            lastSyncAt: stored.lastSyncAt,
            lastError: stored.lastError,
          };
        }
        this.loaded = true;
        this.emit();
      } catch (error: unknown) {
        this.state.lastError = `Sync state is not durable: ${readableError(error)}`;
        this.emit();
        throw new Error(this.state.lastError);
      } finally {
        this.loadPromise = null;
      }
    })();
    return this.loadPromise;
  }

  async enqueueDocumentOperation(
    documentId: string,
    baseSyncVersion: number,
    operationType: string,
    payload: Record<string, unknown>,
    entityId = documentId,
  ): Promise<SyncOperationRequest> {
    await this.load();
    const operation: SyncOperationRequest = {
      client_id: this.state.clientId,
      client_operation_id: createId("operation"),
      document_id: documentId,
      base_sync_version: Math.max(0, Math.floor(baseSyncVersion)),
      entity_type: "document",
      entity_id: entityId,
      operation_type: operationType,
      payload: { ...payload },
    };
    this.state.pending.push(operation);
    await this.persist();
    this.emit();
    return operation;
  }

  async flush(): Promise<{ pushed: number; conflicts: SyncConflict[]; error: string | null }> {
    await this.load();
    if (this.state.pending.length === 0) {
      return { pushed: 0, conflicts: [], error: null };
    }
    const batch = this.state.pending.slice(0, 50);
    try {
      const response = await pushSyncOperations(batch);
      const acceptedIds = new Set(response.accepted.map((item) => item.client_operation_id));
      const conflictIds = new Set(response.conflicts.map((item) => item.client_operation_id));
      this.state.pending = this.state.pending.filter(
        (item) => !acceptedIds.has(item.client_operation_id) && !conflictIds.has(item.client_operation_id),
      );
      this.conflicts = this.mergeConflicts(response.conflicts);
      this.state.lastError = null;
      await this.persist();
      this.emit();
      if (acceptedIds.size > 0) this.onDocumentsChanged?.();
      return { pushed: acceptedIds.size, conflicts: response.conflicts, error: null };
    } catch (error: unknown) {
      this.state.lastError = readableError(error);
      await this.persistSafely();
      this.emit();
      return { pushed: 0, conflicts: [], error: this.state.lastError };
    }
  }

  async pullAll(): Promise<{ pulled: number; error: string | null }> {
    await this.load();
    let pulled = 0;
    try {
      let hasMore = true;
      while (hasMore) {
        const response = await pullSyncOperations(this.state.cursor, 50);
        pulled += response.items.length;
        this.state.cursor = Math.max(this.state.cursor, response.next_cursor);
        hasMore = response.has_more;
        if (response.items.length === 0) hasMore = false;
      }
      this.state.lastError = null;
      await this.persist();
      this.emit();
      if (pulled > 0) this.onDocumentsChanged?.();
      return { pulled, error: null };
    } catch (error: unknown) {
      this.state.lastError = readableError(error);
      await this.persistSafely();
      this.emit();
      return { pulled, error: this.state.lastError };
    }
  }

  async refreshConflicts(): Promise<SyncConflict[]> {
    await this.load();
    try {
      this.conflicts = await listSyncConflicts(false);
      this.state.lastError = null;
      await this.persist();
      this.emit();
    } catch (error: unknown) {
      this.state.lastError = readableError(error);
      await this.persistSafely();
      this.emit();
    }
    return this.conflicts.map((conflict) => ({ ...conflict, payload: { ...conflict.payload } }));
  }

  async syncNow(): Promise<SyncRunResult> {
    await this.load();
    try {
      await registerSyncClient({
        client_id: this.state.clientId,
        platform: "web",
        device_name: typeof navigator !== "undefined" ? navigator.userAgent.slice(0, 128) : "web",
      });
    } catch {
      // Registration is best-effort; push/pull still work without a durable ack.
    }
    const push = await this.flush();
    const pull = await this.pullAll();
    try {
      await acknowledgeSyncCursor(this.state.clientId, this.state.cursor);
    } catch {
      // Acknowledgement failure must not discard accepted local results.
    }
    const conflicts = await this.refreshConflicts();
    const error = push.error ?? pull.error ?? this.state.lastError;
    this.state.lastSyncAt = error ? this.state.lastSyncAt : new Date().toISOString();
    await this.persistSafely();
    this.emit();
    return { pushed: push.pushed, pulled: pull.pulled, conflicts, error: error ?? null };
  }

  async keepServer(conflictId: string): Promise<void> {
    await resolveSyncConflict(conflictId, "keep_server");
    this.conflicts = this.conflicts.filter((conflict) => conflict.conflict_id !== conflictId);
    await this.persistSafely();
    this.emit();
  }

  async retryConflict(conflictId: string): Promise<SyncRunResult> {
    const conflict = this.conflicts.find((item) => item.conflict_id === conflictId);
    if (!conflict) throw new Error("This sync conflict is no longer available.");
    const operation = await this.enqueueDocumentOperation(
      conflict.document_id,
      conflict.current_sync_version,
      conflict.operation_type,
      conflict.payload,
      conflict.entity_id ?? conflict.document_id,
    );
    const result = await this.flush();
    if (result.error) return { pushed: 0, pulled: 0, conflicts: this.conflicts, error: result.error };
    const accepted = !this.state.pending.some(
      (item) => item.client_operation_id === operation.client_operation_id,
    ) && !result.conflicts.some((item) => operationMatchesConflict(operation, item));
    if (accepted) {
      await resolveSyncConflict(conflictId, "retry_as_new", {
        client_operation_id: operation.client_operation_id,
      });
      this.conflicts = this.conflicts.filter((item) => item.conflict_id !== conflictId);
      await this.persistSafely();
      this.emit();
    }
    return {
      pushed: result.pushed,
      pulled: 0,
      conflicts: this.conflicts,
      error: null,
    };
  }

  startAutoSync(): void {
    if (this.autoSyncStarted || typeof window === "undefined") return;
    this.autoSyncStarted = true;
    const trigger = (): void => {
      if (navigator.onLine === false) return;
      void this.syncNow();
    };
    window.addEventListener("online", trigger);
    const onVisibility = (): void => {
      if (document.visibilityState === "visible") trigger();
    };
    document.addEventListener("visibilitychange", onVisibility);
    this.autoSyncCleanup = () => {
      window.removeEventListener("online", trigger);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }

  stopAutoSync(): void {
    this.autoSyncCleanup?.();
    this.autoSyncCleanup = undefined;
    this.autoSyncStarted = false;
  }

  private mergeConflicts(next: SyncConflict[]): SyncConflict[] {
    const byId = new Map(this.conflicts.map((conflict) => [conflict.conflict_id, conflict]));
    for (const conflict of next) byId.set(conflict.conflict_id, conflict);
    return [...byId.values()];
  }

  private async persist(): Promise<void> {
    await this.store.save(this.userId, this.state);
  }

  private async persistSafely(): Promise<void> {
    try {
      await this.persist();
    } catch {
      // The snapshot still exposes the error; a transient storage failure must
      // not hide the server response or make the UI throw during a retry.
    }
  }

  private emit(): void {
    const snapshot = this.snapshot();
    for (const listener of this.listeners) listener(snapshot);
  }
}
