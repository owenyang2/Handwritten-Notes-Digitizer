import { ACCEPTED_IMAGE_TYPES, MAX_UPLOAD_BYTES } from "./uploadUI.js";

/** Queue state is deliberately small enough to persist and inspect safely. */
export type UploadQueueStatus = "queued" | "processing" | "succeeded" | "failed" | "cancelled";
export type UploadQueuePhase =
  | "queued"
  | "preparing"
  | "uploading"
  | "recognizing"
  | "imported"
  | "complete"
  | "failed"
  | "cancelled";

export interface UploadQueueItem {
  id: string;
  fileName: string;
  fileType: string;
  fileSize: number;
  createdAt: string;
  updatedAt: string;
  status: UploadQueueStatus;
  phase: UploadQueuePhase;
  progress: number;
  attempt: number;
  errorMessage?: string;
  documentId?: string;
  imageId?: string;
  pageCount?: number;
}

export interface PersistedUploadQueueItem extends UploadQueueItem {
  /** Blob is kept in IndexedDB; it is never serialized into localStorage. */
  file: Blob | null;
}

export interface UploadQueueStore {
  list(): Promise<PersistedUploadQueueItem[]>;
  put(item: PersistedUploadQueueItem): Promise<void>;
  delete(id: string): Promise<void>;
  clear(): Promise<void>;
}

export interface UploadProcessContext {
  item: UploadQueueItem;
  file: File;
  signal: AbortSignal;
  update(update: {
    phase?: UploadQueuePhase;
    progress?: number;
    documentId?: string;
    imageId?: string;
    pageCount?: number;
  }): Promise<void>;
}

export interface UploadProcessResult {
  kind: "image" | "pdf";
  documentId: string;
  imageId?: string;
  pageCount?: number;
}

export type UploadProcessor = (
  context: UploadProcessContext,
) => Promise<UploadProcessResult>;

export interface DurableUploadQueueOptions {
  store: UploadQueueStore;
  processor: UploadProcessor;
  now?: () => Date;
  idFactory?: () => string;
}

export function validateUploadFile(file: File): string | null {
  const acceptedType = (ACCEPTED_IMAGE_TYPES as readonly string[]).includes(file.type);
  const acceptedPdf = file.name.toLowerCase().endsWith(".pdf");
  if (!acceptedType && !acceptedPdf) {
    return `“${file.name}” is not a PNG, JPEG, or PDF.`;
  }
  if (file.size === 0) {
    return `“${file.name}” is empty.`;
  }
  if (file.size > MAX_UPLOAD_BYTES) {
    return `“${file.name}” is larger than ${Math.round(MAX_UPLOAD_BYTES / (1024 * 1024))} MB.`;
  }
  return null;
}

/** In-memory store used by tests and by callers that want an isolated queue. */
export class MemoryUploadQueueStore implements UploadQueueStore {
  private readonly records = new Map<string, PersistedUploadQueueItem>();

  async list(): Promise<PersistedUploadQueueItem[]> {
    return [...this.records.values()].map((record) => ({ ...record }));
  }

  async put(item: PersistedUploadQueueItem): Promise<void> {
    this.records.set(item.id, { ...item });
  }

  async delete(id: string): Promise<void> {
    this.records.delete(id);
  }

  async clear(): Promise<void> {
    this.records.clear();
  }
}

function requestResult<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error("IndexedDB request failed."));
  });
}

function transactionComplete(transaction: IDBTransaction): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error ?? new Error("IndexedDB transaction failed."));
    transaction.onabort = () => reject(transaction.error ?? new Error("IndexedDB transaction aborted."));
  });
}

/**
 * Browser-persistent queue storage. Files are structured-cloned as Blobs in
 * IndexedDB so a pending import survives a reload without putting note bytes
 * into localStorage. The queue reports an error instead of silently falling
 * back to a non-durable store when IndexedDB is unavailable.
 */
export class IndexedDbUploadQueueStore implements UploadQueueStore {
  private readonly database: Promise<IDBDatabase>;

  constructor(
    private readonly databaseName = "handwriting-notes-digitizer",
    private readonly storeName = "upload-queue",
  ) {
    if (typeof indexedDB === "undefined") {
      this.database = Promise.reject(
        new Error("This browser does not provide IndexedDB for durable uploads."),
      );
      return;
    }
    this.database = new Promise<IDBDatabase>((resolve, reject) => {
      const request = indexedDB.open(this.databaseName, 1);
      request.onupgradeneeded = () => {
        if (!request.result.objectStoreNames.contains(this.storeName)) {
          request.result.createObjectStore(this.storeName, { keyPath: "id" });
        }
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error ?? new Error("Could not open durable upload storage."));
      request.onblocked = () => reject(new Error("Durable upload storage is blocked by another tab."));
    });
  }

  async list(): Promise<PersistedUploadQueueItem[]> {
    const db = await this.database;
    const transaction = db.transaction(this.storeName, "readonly");
    const completion = transactionComplete(transaction);
    const request = transaction.objectStore(this.storeName).getAll();
    const result = await requestResult(request);
    await completion;
    return result as PersistedUploadQueueItem[];
  }

  async put(item: PersistedUploadQueueItem): Promise<void> {
    const db = await this.database;
    const transaction = db.transaction(this.storeName, "readwrite");
    transaction.objectStore(this.storeName).put(item);
    await transactionComplete(transaction);
  }

  async delete(id: string): Promise<void> {
    const db = await this.database;
    const transaction = db.transaction(this.storeName, "readwrite");
    transaction.objectStore(this.storeName).delete(id);
    await transactionComplete(transaction);
  }

  async clear(): Promise<void> {
    const db = await this.database;
    const transaction = db.transaction(this.storeName, "readwrite");
    transaction.objectStore(this.storeName).clear();
    await transactionComplete(transaction);
  }
}

function asFile(item: PersistedUploadQueueItem): File | null {
  if (!(item.file instanceof Blob)) return null;
  return new File([item.file], item.fileName, {
    type: item.fileType || item.file.type,
    lastModified: Date.parse(item.createdAt) || Date.now(),
  });
}

function clampProgress(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.min(1, Math.max(0, value));
}

function defaultId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `upload-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function errorMessage(error: unknown): string {
  return error instanceof Error && error.message
    ? error.message
    : "The import could not be completed.";
}

function isAbort(error: unknown, signal: AbortSignal): boolean {
  return signal.aborted || (error instanceof DOMException && error.name === "AbortError");
}

/** Durable, sequential, retryable upload/import orchestration. */
export class DurableUploadQueue {
  private readonly items = new Map<string, UploadQueueItem>();
  private readonly files = new Map<string, File>();
  private readonly controllers = new Map<string, AbortController>();
  private readonly listeners = new Set<(items: UploadQueueItem[]) => void>();
  private readonly now: () => Date;
  private readonly idFactory: () => string;
  private loaded = false;
  private runPromise: Promise<void> | null = null;
  private persistenceError: string | null = null;

  constructor(private readonly options: DurableUploadQueueOptions) {
    this.now = options.now ?? (() => new Date());
    this.idFactory = options.idFactory ?? defaultId;
  }

  get isLoaded(): boolean {
    return this.loaded;
  }

  get persistenceAvailable(): boolean {
    return this.persistenceError === null;
  }

  get persistenceMessage(): string | null {
    return this.persistenceError;
  }

  subscribe(listener: (items: UploadQueueItem[]) => void): () => void {
    this.listeners.add(listener);
    listener(this.snapshot());
    return () => this.listeners.delete(listener);
  }

  snapshot(): UploadQueueItem[] {
    return [...this.items.values()]
      .sort((a, b) => a.createdAt.localeCompare(b.createdAt) || a.id.localeCompare(b.id))
      .map((item) => ({ ...item }));
  }

  pendingCount(): number {
    return [...this.items.values()].filter(
      (item) => item.status === "queued" || item.status === "processing",
    ).length;
  }

  async load(): Promise<UploadQueueItem[]> {
    if (this.loaded) return this.snapshot();
    try {
      const records = await this.options.store.list();
      for (const record of records) {
        const item: UploadQueueItem = { ...record };
        if (item.status === "processing") {
          item.status = "queued";
          item.phase = "queued";
          item.errorMessage = undefined;
          item.updatedAt = this.now().toISOString();
        }
        this.items.set(item.id, item);
        const file = asFile(record);
        if (file) this.files.set(item.id, file);
      }
      this.loaded = true;
      this.persistenceError = null;
      for (const item of this.items.values()) {
        if (item.status === "queued" && !this.files.has(item.id)) {
          item.status = "failed";
          item.phase = "failed";
          item.errorMessage = "The saved file is no longer available in this browser.";
          await this.persist(item);
        }
      }
      this.emit();
      return this.snapshot();
    } catch (error: unknown) {
      this.persistenceError = errorMessage(error);
      throw new Error(`Durable import queue unavailable: ${this.persistenceError}`);
    }
  }

  async enqueue(files: File[]): Promise<UploadQueueItem[]> {
    if (!this.loaded) await this.load();
    if (!this.persistenceAvailable) {
      throw new Error(this.persistenceMessage ?? "Durable import queue unavailable.");
    }
    const validationErrors = files
      .map((file) => validateUploadFile(file))
      .filter((message): message is string => message !== null);
    if (validationErrors.length > 0) {
      throw new Error(validationErrors.join(" "));
    }
    const added: UploadQueueItem[] = [];
    for (const file of files) {
      const timestamp = this.now().toISOString();
      const item: UploadQueueItem = {
        id: this.idFactory(),
        fileName: file.name,
        fileType: file.type,
        fileSize: file.size,
        createdAt: timestamp,
        updatedAt: timestamp,
        status: "queued",
        phase: "queued",
        progress: 0,
        attempt: 0,
      };
      this.items.set(item.id, item);
      this.files.set(item.id, file);
      await this.persist(item);
      added.push({ ...item });
    }
    this.emit();
    return added;
  }

  async retry(id: string): Promise<void> {
    if (!this.loaded) await this.load();
    const item = this.items.get(id);
    if (!item || item.status !== "failed") return;
    if (!this.files.has(id)) {
      item.errorMessage = "The original file is no longer available; add it again.";
      await this.persist(item);
      this.emit();
      return;
    }
    item.status = "queued";
    item.phase = "queued";
    item.progress = 0;
    item.errorMessage = undefined;
    item.updatedAt = this.now().toISOString();
    await this.persist(item);
    this.emit();
  }

  async remove(id: string): Promise<void> {
    this.controllers.get(id)?.abort();
    this.controllers.delete(id);
    this.files.delete(id);
    this.items.delete(id);
    await this.options.store.delete(id);
    this.emit();
  }

  async clearCompleted(): Promise<void> {
    const completed = [...this.items.values()]
      .filter((item) => item.status === "succeeded" || item.status === "cancelled")
      .map((item) => item.id);
    for (const id of completed) {
      this.files.delete(id);
      this.items.delete(id);
      await this.options.store.delete(id);
    }
    if (completed.length > 0) this.emit();
  }

  async cancel(id: string): Promise<void> {
    const item = this.items.get(id);
    if (!item) return;
    const controller = this.controllers.get(id);
    if (controller) {
      controller.abort();
      return;
    }
    item.status = "cancelled";
    item.phase = "cancelled";
    item.errorMessage = "Cancelled before import started.";
    item.updatedAt = this.now().toISOString();
    this.files.delete(id);
    await this.options.store.delete(id);
    this.emit();
  }

  async start(): Promise<void> {
    if (!this.loaded) await this.load();
    if (this.runPromise) return this.runPromise;
    this.runPromise = this.processLoop().finally(() => {
      this.runPromise = null;
    });
    return this.runPromise;
  }

  private async processLoop(): Promise<void> {
    while (true) {
      const next = this.snapshot().find((item) => item.status === "queued");
      if (!next) return;
      await this.processOne(next.id);
    }
  }

  private async processOne(id: string): Promise<void> {
    const item = this.items.get(id);
    const file = this.files.get(id);
    if (!item || !file) return;
    const controller = new AbortController();
    this.controllers.set(id, controller);
    item.status = "processing";
    item.phase = "preparing";
    item.progress = Math.max(0.01, item.progress);
    item.attempt += 1;
    item.errorMessage = undefined;
    item.updatedAt = this.now().toISOString();
    await this.persist(item);
    this.emit();

    const update = async (change: {
      phase?: UploadQueuePhase;
      progress?: number;
      documentId?: string;
      imageId?: string;
      pageCount?: number;
    }): Promise<void> => {
      const current = this.items.get(id);
      if (!current) return;
      if (change.phase) current.phase = change.phase;
      if (change.progress !== undefined) current.progress = clampProgress(change.progress);
      if (change.documentId) current.documentId = change.documentId;
      if (change.imageId) current.imageId = change.imageId;
      if (change.pageCount !== undefined) current.pageCount = change.pageCount;
      current.updatedAt = this.now().toISOString();
      await this.persist(current);
      this.emit();
    };

    try {
      const result = await this.options.processor({
        item: { ...item },
        file,
        signal: controller.signal,
        update,
      });
      if (controller.signal.aborted) throw new DOMException("The import was cancelled.", "AbortError");
      item.status = "succeeded";
      item.phase = "complete";
      item.progress = 1;
      item.documentId = result.documentId;
      item.imageId = result.imageId;
      item.pageCount = result.pageCount;
      item.updatedAt = this.now().toISOString();
      this.files.delete(id);
      await this.persist(item);
    } catch (error: unknown) {
      if (isAbort(error, controller.signal)) {
        item.status = "cancelled";
        item.phase = "cancelled";
        item.errorMessage = "Cancelled. No further files in the batch were started.";
        item.updatedAt = this.now().toISOString();
        this.files.delete(id);
        await this.options.store.delete(id);
      } else {
        item.status = "failed";
        item.phase = "failed";
        item.errorMessage = errorMessage(error);
        item.updatedAt = this.now().toISOString();
        await this.persist(item);
      }
    } finally {
      this.controllers.delete(id);
      this.emit();
    }
  }

  private async persist(item: UploadQueueItem): Promise<void> {
    await this.options.store.put({
      ...item,
      file: this.files.get(item.id) ?? null,
    });
  }

  private emit(): void {
    const snapshot = this.snapshot();
    for (const listener of this.listeners) listener(snapshot);
  }
}

export function createDefaultUploadQueue(processor: UploadProcessor): DurableUploadQueue {
  return new DurableUploadQueue({
    store: new IndexedDbUploadQueueStore(),
    processor,
  });
}
