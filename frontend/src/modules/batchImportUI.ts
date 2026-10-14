import {
  ACCEPTED_IMAGE_TYPES,
  MAX_UPLOAD_BYTES,
} from "./uploadUI.js";
import {
  createDefaultUploadQueue,
  DurableUploadQueue,
  type UploadProcessResult,
  type UploadProcessor,
  type UploadQueueItem,
  type UploadQueueStatus,
} from "./uploadQueue.js";

export interface BatchImportUIOptions {
  mount: HTMLElement;
  processor: UploadProcessor;
  queue?: DurableUploadQueue;
  onCompleted?: (item: UploadQueueItem, result: UploadProcessResult) => void;
}

const statusLabels: Record<UploadQueueStatus, string> = {
  queued: "Waiting",
  processing: "Working",
  succeeded: "Imported",
  failed: "Needs attention",
  cancelled: "Cancelled",
};

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function phaseLabel(item: UploadQueueItem): string {
  switch (item.phase) {
    case "preparing": return "Preparing";
    case "uploading": return "Uploading";
    case "recognizing": return "Recognizing";
    case "imported": return "Imported; ready for review";
    case "complete": return "Ready to review";
    case "failed": return "Import failed";
    case "cancelled": return "Cancelled";
    default: return "Waiting to start";
  }
}

/**
 * Accessible batch import surface backed by the durable queue. The queue is
 * sequential on purpose: it keeps memory bounded and gives each item a clear
 * retry boundary while the backend remains the source of truth for results.
 */
export class BatchImportUI {
  private readonly mount: HTMLElement;
  private readonly queue: DurableUploadQueue;
  private readonly onCompleted?: BatchImportUIOptions["onCompleted"];
  private selectedFiles: File[] = [];
  private unsubscribe: (() => void) | null = null;
  private seenStatuses = new Map<string, UploadQueueStatus>();
  private initialized = false;
  private capabilityAvailable = true;
  private capabilityReason: string | null = null;
  private fileInput!: HTMLInputElement;
  private dropZone!: HTMLElement;
  private addButton!: HTMLButtonElement;
  private startButton!: HTMLButtonElement;
  private cancelButton!: HTMLButtonElement;
  private clearButton!: HTMLButtonElement;
  private selectionStatus!: HTMLElement;
  private queueStatus!: HTMLElement;
  private queueList!: HTMLOListElement;

  constructor(options: BatchImportUIOptions) {
    this.mount = options.mount;
    this.queue = options.queue ?? createDefaultUploadQueue(options.processor);
    this.onCompleted = options.onCompleted;
  }

  get durableQueue(): DurableUploadQueue {
    return this.queue;
  }

  dispose(): void {
    this.unsubscribe?.();
    this.unsubscribe = null;
  }

  render(): void {
    this.unsubscribe?.();
    this.mount.replaceChildren();

    const heading = document.createElement("h2");
    heading.className = "batch-import-heading";
    heading.textContent = "Batch import";
    this.mount.appendChild(heading);

    const description = document.createElement("p");
    description.className = "batch-import-description";
    description.textContent =
      "Add several PDFs or images at once. The queue is saved in this browser and keeps failed files available for a deliberate retry.";
    this.mount.appendChild(description);

    const note = document.createElement("p");
    note.className = "batch-import-note";
    note.textContent = `PNG, JPEG, and PDF files up to ${Math.round(MAX_UPLOAD_BYTES / (1024 * 1024))} MB each.`;
    this.mount.appendChild(note);

    const controls = document.createElement("div");
    controls.className = "batch-import-controls";

    this.dropZone = document.createElement("div");
    this.dropZone.className = "batch-import-drop-zone";
    this.dropZone.setAttribute("role", "button");
    this.dropZone.tabIndex = 0;
    this.dropZone.setAttribute("aria-controls", "batch-import-file-input");
    this.dropZone.setAttribute("aria-describedby", "batch-import-selection-status");
    this.dropZone.textContent = "Drop files here or choose from this device";
    this.dropZone.addEventListener("click", () => this.fileInput.click());
    this.dropZone.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        this.fileInput.click();
      }
    });
    this.dropZone.addEventListener("dragover", (event) => {
      event.preventDefault();
      this.dropZone.classList.add("batch-import-drop-zone--active");
    });
    this.dropZone.addEventListener("dragleave", () => {
      this.dropZone.classList.remove("batch-import-drop-zone--active");
    });
    this.dropZone.addEventListener("drop", (event) => {
      event.preventDefault();
      this.dropZone.classList.remove("batch-import-drop-zone--active");
      this.setSelectedFiles([...event.dataTransfer?.files ?? []]);
    });
    controls.appendChild(this.dropZone);

    this.fileInput = document.createElement("input");
    this.fileInput.id = "batch-import-file-input";
    this.fileInput.type = "file";
    this.fileInput.multiple = true;
    this.fileInput.accept = ACCEPTED_IMAGE_TYPES.join(",");
    this.fileInput.className = "visually-hidden";
    this.fileInput.setAttribute("aria-label", "Choose files to import");
    this.fileInput.addEventListener("change", () => {
      this.setSelectedFiles([...this.fileInput.files ?? []]);
    });
    controls.appendChild(this.fileInput);

    const actionGroup = document.createElement("div");
    actionGroup.className = "batch-import-action-group";

    this.addButton = document.createElement("button");
    this.addButton.type = "button";
    this.addButton.className = "batch-import-add";
    this.addButton.textContent = "Add files to queue";
    this.addButton.addEventListener("click", () => void this.addSelectedFiles());
    actionGroup.appendChild(this.addButton);

    this.startButton = document.createElement("button");
    this.startButton.type = "button";
    this.startButton.className = "batch-import-start";
    this.startButton.textContent = "Start imports";
    this.startButton.addEventListener("click", () => void this.start());
    actionGroup.appendChild(this.startButton);

    this.cancelButton = document.createElement("button");
    this.cancelButton.type = "button";
    this.cancelButton.className = "batch-import-cancel";
    this.cancelButton.textContent = "Cancel batch";
    this.cancelButton.addEventListener("click", () => void this.cancelBatch());
    actionGroup.appendChild(this.cancelButton);

    this.clearButton = document.createElement("button");
    this.clearButton.type = "button";
    this.clearButton.className = "batch-import-clear";
    this.clearButton.textContent = "Clear finished";
    this.clearButton.addEventListener("click", () => void this.queue.clearCompleted());
    actionGroup.appendChild(this.clearButton);

    controls.appendChild(actionGroup);
    this.mount.appendChild(controls);

    this.selectionStatus = document.createElement("p");
    this.selectionStatus.id = "batch-import-selection-status";
    this.selectionStatus.className = "batch-import-selection-status";
    this.selectionStatus.setAttribute("aria-live", "polite");
    this.mount.appendChild(this.selectionStatus);

    this.queueStatus = document.createElement("p");
    this.queueStatus.className = "batch-import-queue-status";
    this.queueStatus.setAttribute("data-testid", "batch-import-status");
    this.queueStatus.setAttribute("aria-live", "polite");
    this.mount.appendChild(this.queueStatus);

    this.queueList = document.createElement("ol");
    this.queueList.className = "batch-import-list";
    this.queueList.setAttribute("data-testid", "batch-import-list");
    this.queueList.setAttribute("aria-label", "Import queue");
    this.mount.appendChild(this.queueList);

    this.unsubscribe = this.queue.subscribe((items) => this.renderQueue(items));
    this.setCapabilityAvailable(this.capabilityAvailable, this.capabilityReason ?? undefined);
    void this.initialize();
  }

  setCapabilityAvailable(available: boolean, reason?: string): void {
    this.capabilityAvailable = available;
    this.capabilityReason = reason ?? null;
    if (!this.addButton) return;
    this.addButton.disabled = !available;
    this.startButton.disabled = !available;
    this.dropZone.setAttribute("aria-disabled", String(!available));
    if (!available) {
      this.queueStatus.textContent = reason
        ? `Import is unavailable: ${reason}`
        : "Import is currently unavailable.";
      this.queueStatus.className = "batch-import-queue-status batch-import-queue-status--error";
    }
  }

  async resume(): Promise<void> {
    if (!this.initialized || !this.capabilityAvailable) return;
    if (this.queue.pendingCount() > 0) await this.start();
  }

  private async initialize(): Promise<void> {
    try {
      await this.queue.load();
      this.initialized = true;
      this.renderQueue(this.queue.snapshot());
      if (this.queue.pendingCount() > 0) {
        this.queueStatus.textContent =
          "Pending imports were recovered from this browser. Start the batch when you are ready.";
      } else if (this.capabilityAvailable) {
        this.queueStatus.textContent = "No pending imports.";
      }
    } catch (error: unknown) {
      this.initialized = false;
      this.setCapabilityAvailable(false, error instanceof Error ? error.message : "Storage unavailable.");
    }
  }

  private setSelectedFiles(files: File[]): void {
    this.selectedFiles = files;
    if (files.length === 0) {
      this.selectionStatus.textContent = "No files selected.";
      this.addButton.disabled = !this.capabilityAvailable || true;
      return;
    }
    this.addButton.disabled = !this.capabilityAvailable;
    const totalBytes = files.reduce((sum, file) => sum + file.size, 0);
    this.selectionStatus.textContent = `${files.length} file${files.length === 1 ? "" : "s"} ready (${formatBytes(totalBytes)} total). Review the list, then add them to the queue.`;
  }

  private async addSelectedFiles(): Promise<void> {
    if (this.selectedFiles.length === 0 || !this.capabilityAvailable) return;
    const files = [...this.selectedFiles];
    this.addButton.disabled = true;
    try {
      await this.queue.enqueue(files);
      this.selectedFiles = [];
      this.fileInput.value = "";
      this.selectionStatus.textContent = `${files.length} file${files.length === 1 ? "" : "s"} added. Start the batch when ready.`;
      this.queueStatus.textContent = "Files are safely queued in this browser.";
      this.queueStatus.className = "batch-import-queue-status";
    } catch (error: unknown) {
      this.selectionStatus.textContent = error instanceof Error
        ? error.message
        : "One or more files could not be queued.";
      this.selectionStatus.className = "batch-import-selection-status batch-import-selection-status--error";
    } finally {
      this.addButton.disabled = !this.capabilityAvailable || this.selectedFiles.length === 0;
    }
  }

  private async start(): Promise<void> {
    if (!this.initialized || !this.capabilityAvailable) return;
    this.startButton.disabled = true;
    this.queueStatus.textContent = "Importing one file at a time…";
    this.queueStatus.className = "batch-import-queue-status";
    await this.queue.start();
    this.startButton.disabled = false;
    if (this.queue.pendingCount() === 0) {
      this.queueStatus.textContent = "Batch finished. Completed items are ready to review; failed items can be retried.";
    }
  }

  private async cancelBatch(): Promise<void> {
    await Promise.all(
      this.queue.snapshot()
        .filter((item) => item.status === "queued" || item.status === "processing")
        .map((item) => this.queue.cancel(item.id)),
    );
    this.queueStatus.textContent = "Batch cancelled. Completed imports were kept; queued files can be added again.";
  }

  private renderQueue(items: UploadQueueItem[]): void {
    if (!this.queueList) return;
    for (const item of items) {
      const previous = this.seenStatuses.get(item.id);
      if (item.status === "succeeded" && previous !== undefined && previous !== "succeeded" && item.documentId) {
        this.onCompleted?.(item, {
          kind: item.fileName.toLowerCase().endsWith(".pdf") ? "pdf" : "image",
          documentId: item.documentId,
          imageId: item.imageId,
          pageCount: item.pageCount,
        });
      }
      this.seenStatuses.set(item.id, item.status);
    }
    const existingIds = new Set(items.map((item) => item.id));
    for (const id of this.seenStatuses.keys()) {
      if (!existingIds.has(id)) this.seenStatuses.delete(id);
    }
    this.queueList.replaceChildren(...items.map((item) => this.renderItem(item)));

    const pending = items.filter((item) => item.status === "queued" || item.status === "processing").length;
    const failed = items.filter((item) => item.status === "failed").length;
    this.startButton.disabled = !this.capabilityAvailable || pending === 0;
    this.cancelButton.disabled = pending === 0;
    this.clearButton.disabled = !items.some(
      (item) => item.status === "succeeded" || item.status === "cancelled",
    );
    if (this.initialized && pending > 0 && this.capabilityAvailable) {
      this.queueStatus.textContent = `${pending} import${pending === 1 ? "" : "s"} pending${failed ? `; ${failed} needs a retry` : ""}.`;
    }
  }

  private renderItem(item: UploadQueueItem): HTMLLIElement {
    const row = document.createElement("li");
    row.className = `batch-import-item batch-import-item--${item.status}`;
    row.setAttribute("data-testid", "batch-import-item");
    row.dataset.uploadId = item.id;

    const copy = document.createElement("div");
    copy.className = "batch-import-item-copy";
    const name = document.createElement("strong");
    name.textContent = item.fileName;
    copy.appendChild(name);
    const meta = document.createElement("span");
    meta.textContent = `${formatBytes(item.fileSize)} · ${statusLabels[item.status]} · ${phaseLabel(item)}`;
    copy.appendChild(meta);
    if (item.errorMessage) {
      const error = document.createElement("span");
      error.className = "batch-import-item-error";
      error.textContent = item.errorMessage;
      copy.appendChild(error);
    }
    row.appendChild(copy);

    const progress = document.createElement("progress");
    progress.max = 1;
    progress.value = item.progress;
    progress.setAttribute("aria-label", `Import progress for ${item.fileName}`);
    progress.setAttribute("aria-valuetext", `${Math.round(item.progress * 100)} percent`);
    row.appendChild(progress);

    const actions = document.createElement("div");
    actions.className = "batch-import-item-actions";
    if (item.status === "failed") {
      const retry = document.createElement("button");
      retry.type = "button";
      retry.className = "batch-import-retry";
      retry.textContent = "Retry";
      retry.setAttribute("aria-label", `Retry ${item.fileName}`);
      retry.addEventListener("click", () => {
        void this.queue.retry(item.id).then(() => this.start());
      });
      actions.appendChild(retry);
    }
    if (item.status === "queued" || item.status === "processing") {
      const cancel = document.createElement("button");
      cancel.type = "button";
      cancel.className = "batch-import-item-cancel";
      cancel.textContent = "Cancel";
      cancel.setAttribute("aria-label", `Cancel ${item.fileName}`);
      cancel.addEventListener("click", () => void this.queue.cancel(item.id));
      actions.appendChild(cancel);
    }
    if (item.status === "succeeded" || item.status === "cancelled") {
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "batch-import-item-remove";
      remove.textContent = "Remove";
      remove.setAttribute("aria-label", `Remove ${item.fileName} from the queue`);
      remove.addEventListener("click", () => void this.queue.remove(item.id));
      actions.appendChild(remove);
    }
    row.appendChild(actions);
    return row;
  }
}
