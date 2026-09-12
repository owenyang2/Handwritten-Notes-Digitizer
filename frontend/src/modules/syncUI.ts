import type { SyncConflict } from "../api/client.js";
import { SyncCoordinator, type SyncSnapshot } from "./syncOrchestrator.js";

export interface SyncUIOptions {
  mount: HTMLElement;
  coordinator: SyncCoordinator;
  onDocumentsChanged?: () => void;
}

function formatSyncTime(value: string | null): string {
  if (!value) return "Not synced yet";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Last sync time unavailable" : `Last synced ${date.toLocaleString()}`;
}

function conflictDescription(conflict: SyncConflict): string {
  if (conflict.operation_type === "document.rename") {
    const title = conflict.payload.title;
    return typeof title === "string" ? `Rename document to “${title}”` : "Rename document";
  }
  if (conflict.operation_type === "document.archive") {
    return conflict.payload.archived === true ? "Archive document" : "Restore document from archive";
  }
  if (conflict.operation_type === "document.trash") return "Move document to trash";
  if (conflict.operation_type === "document.restore") return "Restore document from trash";
  return "A document change needs review";
}

/** Compact sync status and conflict resolution surface for the web client. */
export class SyncUI {
  private readonly mount: HTMLElement;
  private readonly coordinator: SyncCoordinator;
  private readonly onDocumentsChanged?: () => void;
  private unsubscribe: (() => void) | null = null;
  private syncButton!: HTMLButtonElement;
  private conflictRefreshButton!: HTMLButtonElement;
  private status!: HTMLElement;
  private summary!: HTMLElement;
  private conflictsMount!: HTMLElement;

  constructor(options: SyncUIOptions) {
    this.mount = options.mount;
    this.coordinator = options.coordinator;
    this.onDocumentsChanged = options.onDocumentsChanged;
  }

  render(): void {
    this.unsubscribe?.();
    this.mount.replaceChildren();

    const heading = document.createElement("h2");
    heading.className = "sync-heading";
    heading.textContent = "Cross-device sync";
    this.mount.appendChild(heading);

    const description = document.createElement("p");
    description.className = "sync-description";
    description.textContent =
      "Library changes are queued locally first, then applied through the shared server operation log. Conflicts keep both versions until you choose what to do.";
    this.mount.appendChild(description);

    this.summary = document.createElement("div");
    this.summary.className = "sync-summary";
    this.summary.setAttribute("data-testid", "sync-summary");
    this.mount.appendChild(this.summary);

    const actions = document.createElement("div");
    actions.className = "sync-actions";
    this.syncButton = document.createElement("button");
    this.syncButton.type = "button";
    this.syncButton.className = "sync-now";
    this.syncButton.textContent = "Sync now";
    this.syncButton.addEventListener("click", () => void this.syncNow());
    actions.appendChild(this.syncButton);

    this.conflictRefreshButton = document.createElement("button");
    this.conflictRefreshButton.type = "button";
    this.conflictRefreshButton.className = "sync-refresh-conflicts";
    this.conflictRefreshButton.textContent = "Refresh conflicts";
    this.conflictRefreshButton.addEventListener("click", () => void this.refreshConflicts());
    actions.appendChild(this.conflictRefreshButton);
    this.mount.appendChild(actions);

    this.status = document.createElement("p");
    this.status.className = "sync-status";
    this.status.setAttribute("data-testid", "sync-status");
    this.status.setAttribute("aria-live", "polite");
    this.mount.appendChild(this.status);

    this.conflictsMount = document.createElement("div");
    this.conflictsMount.className = "sync-conflicts";
    this.conflictsMount.setAttribute("data-testid", "sync-conflicts");
    this.mount.appendChild(this.conflictsMount);

    this.unsubscribe = this.coordinator.subscribe((snapshot) => this.renderState(snapshot));
  }

  private async syncNow(): Promise<void> {
    this.syncButton.disabled = true;
    this.conflictRefreshButton.disabled = true;
    this.status.textContent = "Syncing…";
    try {
      const result = await this.coordinator.syncNow();
      if (result.error) {
        this.status.textContent = `Sync paused: ${result.error} Pending changes remain on this device.`;
        this.status.className = "sync-status sync-status--error";
      } else {
        this.status.textContent = `Sync complete: ${result.pushed} sent, ${result.pulled} received.`;
        this.status.className = "sync-status sync-status--success";
        if (result.pushed > 0 || result.pulled > 0) this.onDocumentsChanged?.();
      }
    } catch (error: unknown) {
      this.status.textContent = error instanceof Error ? error.message : "Sync could not complete.";
      this.status.className = "sync-status sync-status--error";
    } finally {
      this.syncButton.disabled = false;
      this.conflictRefreshButton.disabled = false;
    }
  }

  private async refreshConflicts(): Promise<void> {
    this.conflictRefreshButton.disabled = true;
    try {
      await this.coordinator.refreshConflicts();
      this.status.textContent = "Conflict list refreshed.";
      this.status.className = "sync-status";
    } catch (error: unknown) {
      this.status.textContent = error instanceof Error ? error.message : "Could not refresh conflicts.";
      this.status.className = "sync-status sync-status--error";
    } finally {
      this.conflictRefreshButton.disabled = false;
    }
  }

  private renderState(snapshot: SyncSnapshot): void {
    if (!this.summary || !this.conflictsMount) return;
    const pending = snapshot.pending.length;
    const conflictCount = snapshot.conflicts.length;
    this.summary.replaceChildren();
    const pendingLine = document.createElement("p");
    pendingLine.textContent = `${pending} pending change${pending === 1 ? "" : "s"} · server cursor ${snapshot.cursor}`;
    this.summary.appendChild(pendingLine);
    const timeLine = document.createElement("p");
    timeLine.textContent = formatSyncTime(snapshot.lastSyncAt);
    this.summary.appendChild(timeLine);
    const storageLine = document.createElement("p");
    storageLine.textContent = snapshot.durable
      ? "Pending operation metadata is saved in this browser."
      : "Sync metadata is session-only in this browser.";
    storageLine.className = snapshot.durable ? "sync-storage" : "sync-storage sync-storage--warning";
    this.summary.appendChild(storageLine);

    this.syncButton.disabled = pending === 0 && conflictCount === 0;
    this.renderConflicts(snapshot.conflicts);
    if (snapshot.lastError && !this.status.textContent) {
      this.status.textContent = snapshot.lastError;
      this.status.className = "sync-status sync-status--error";
    }
  }

  private renderConflicts(conflicts: SyncConflict[]): void {
    this.conflictsMount.replaceChildren();
    const heading = document.createElement("h3");
    heading.textContent = "Conflicts needing a decision";
    this.conflictsMount.appendChild(heading);
    if (conflicts.length === 0) {
      const empty = document.createElement("p");
      empty.className = "sync-conflicts-empty";
      empty.textContent = "No unresolved conflicts.";
      this.conflictsMount.appendChild(empty);
      return;
    }
    const list = document.createElement("ol");
    list.className = "sync-conflict-list";
    for (const conflict of conflicts) list.appendChild(this.renderConflict(conflict));
    this.conflictsMount.appendChild(list);
  }

  private renderConflict(conflict: SyncConflict): HTMLLIElement {
    const item = document.createElement("li");
    item.className = "sync-conflict-item";
    item.setAttribute("data-testid", "sync-conflict");
    const detail = document.createElement("p");
    detail.textContent = `${conflictDescription(conflict)} · local version ${conflict.base_sync_version}, server version ${conflict.current_sync_version}`;
    item.appendChild(detail);
    const help = document.createElement("p");
    help.className = "sync-conflict-help";
    help.textContent = "Keeping the server discards this pending operation. Retrying creates a new operation against the server version.";
    item.appendChild(help);
    const actions = document.createElement("div");
    actions.className = "sync-conflict-actions";
    const keep = document.createElement("button");
    keep.type = "button";
    keep.textContent = "Keep server";
    keep.setAttribute("aria-label", `Keep server version for ${conflict.document_id}`);
    keep.addEventListener("click", () => {
      keep.disabled = true;
      retry.disabled = true;
      void this.coordinator.keepServer(conflict.conflict_id).then(() => {
        this.status.textContent = "Server version kept.";
        this.status.className = "sync-status sync-status--success";
        this.onDocumentsChanged?.();
      }).catch((error: unknown) => {
        keep.disabled = false;
        retry.disabled = false;
        this.status.textContent = error instanceof Error ? error.message : "Could not resolve the conflict.";
        this.status.className = "sync-status sync-status--error";
      });
    });
    actions.appendChild(keep);
    const retry = document.createElement("button");
    retry.type = "button";
    retry.textContent = "Retry as new change";
    retry.setAttribute("aria-label", `Retry change for ${conflict.document_id}`);
    retry.addEventListener("click", () => {
      keep.disabled = true;
      retry.disabled = true;
      void this.coordinator.retryConflict(conflict.conflict_id).then((result) => {
        this.status.textContent = result.error ?? "Change retried against the current server version.";
        this.status.className = result.error ? "sync-status sync-status--error" : "sync-status sync-status--success";
        if (!result.error) this.onDocumentsChanged?.();
      }).catch((error: unknown) => {
        keep.disabled = false;
        retry.disabled = false;
        this.status.textContent = error instanceof Error ? error.message : "Could not retry the conflict.";
        this.status.className = "sync-status sync-status--error";
      });
    });
    actions.appendChild(retry);
    item.appendChild(actions);
    return item;
  }
}

