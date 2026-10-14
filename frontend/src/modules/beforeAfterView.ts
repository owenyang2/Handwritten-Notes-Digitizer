// Before/After accuracy view module.
//
// Responsibility: surface the personalization outcome — before/after CER and
// WER on the user's held-out set plus the change (before - after) — and an
// "unavailable" state when a measurement could not be computed (Req 7.3, 7.6).
//
// Also surfaces personalization status: training in progress, completed
// (accepted / rejected / failed), or not yet started (Req 5.6, 5.7).
//
// Implements task 19.2:
//   1. Calls GET /personalization/status to show training state.
//   2. Calls GET /personalization/evaluation to fetch before/after CER/WER.
//   3. Displays metrics in the Before/After view.
//   4. Handles unavailable measurements gracefully.
//   5. Shows an appropriate message when personalization failed or was rejected.

import type { BeforeAfterReport } from "../types.js";
import { ApiError, fetchJson } from "../api/client.js";

// --------------------------------------------------------------------------- //
// API response shapes (mirrors backend Pydantic models)
// --------------------------------------------------------------------------- //

export interface PersonalizationStatusResponse {
  training_state: "in_progress" | "completed" | "none";
  adapter_active: boolean;
  last_run_outcome: "accepted" | "rejected" | "failed" | null;
  reject_reason: string | null;
  notification: string | null;
  unused_training_pairs: number;
  training_trigger_threshold: number;
  max_training_epochs: number;
}

export interface ModelAssignmentSummaryResponse {
  assignment_id: string;
  task: string;
  base_model_version: string;
  adapter_version: string;
  personalized: boolean;
  active: boolean;
  activation_reason: string;
  created_at: string;
  retired_at: string | null;
}

export interface EvaluationApiResponse {
  availability: "available" | "unavailable";
  before_cer: number | null;
  before_wer: number | null;
  after_cer: number | null;
  after_wer: number | null;
  delta_cer: number | null;
  delta_wer: number | null;
  unavailable_reason: string | null;
}

export interface TrainingPairSummaryResponse {
  pair_id: string;
  origin: "correction" | "calibration";
  corrected_text: string;
  created_at: string;
  used: boolean;
}

export interface TrainingPairListResponse {
  items: TrainingPairSummaryResponse[];
}

// --------------------------------------------------------------------------- //
// Helpers
// --------------------------------------------------------------------------- //

/** Convert a camelCase BeforeAfterReport to snake_case EvaluationApiResponse format. */
export function reportFromApi(api: EvaluationApiResponse): BeforeAfterReport {
  return {
    availability: api.availability,
    beforeCer: api.before_cer,
    beforeWer: api.before_wer,
    afterCer: api.after_cer,
    afterWer: api.after_wer,
    deltaCer: api.delta_cer,
    deltaWer: api.delta_wer,
  };
}

/**
 * Format a metric value for display. Returns "unavailable" when null, or the
 * value formatted as a percentage (e.g., 0.123 → "12.3%").
 */
export function formatMetric(value: number | null): string {
  if (value === null) return "unavailable";
  return (value * 100).toFixed(1) + "%";
}

/**
 * Format a delta value for display. Positive delta means improvement (before >
 * after). Returns "unavailable" when null, or a signed percentage string with
 * a "▼" prefix for positive deltas (improvement) and "▲" for negative (worse).
 */
export function formatDelta(value: number | null): string {
  if (value === null) return "unavailable";
  const pct = (value * 100).toFixed(1);
  if (value > 0) return `▼ ${pct}%`; // before > after → improved
  if (value < 0) return `▲ ${Math.abs(value * 100).toFixed(1)}%`; // got worse
  return "0.0% (no change)";
}

// --------------------------------------------------------------------------- //
// Component
// --------------------------------------------------------------------------- //

export interface BeforeAfterViewOptions {
  mount: HTMLElement;
  /** Base URL for API calls (default: empty string, i.e., same origin). */
  apiBase?: string;
  /** Authentication token for Authorization header. */
  authToken?: string;
}

export class BeforeAfterView {
  private readonly mount: HTMLElement;
  private readonly apiBase: string;
  private authToken: string | undefined;
  private refreshTimer: number | null = null;
  private refreshInFlight = false;
  private disposed = false;
  private closeZoom?: () => void;
  private latestStatus?: PersonalizationStatusResponse;
  private latestReport?: BeforeAfterReport;
  private latestTrainingPairs: TrainingPairSummaryResponse[] = [];
  private latestAssignments: ModelAssignmentSummaryResponse[] = [];
  private latestUnavailableReason?: string | null;

  constructor(options: BeforeAfterViewOptions) {
    this.mount = options.mount;
    this.apiBase = options.apiBase ?? "/api";
    this.authToken = options.authToken;
  }

  dispose(): void {
    this.disposed = true;
    this.closeZoom?.();
    if (this.refreshTimer !== null) window.clearInterval(this.refreshTimer);
    this.refreshTimer = null;
  }

  /** Update the auth token (e.g., after login). */
  setAuthToken(token: string): void {
    this.authToken = token;
  }

  // --------------------------------------------------------------------------
  // API calls
  // --------------------------------------------------------------------------

  private request<T>(path: string, init?: RequestInit): Promise<T> {
    return fetchJson<T>(path, {
      ...init,
      headers: {
        ...(init?.headers ?? {}),
        ...(this.authToken ? { Authorization: `Bearer ${this.authToken}` } : {}),
      },
    }, this.apiBase);
  }

  /** Fetch GET /personalization/status for the authenticated user. */
  async fetchStatus(): Promise<PersonalizationStatusResponse> {
    return this.request("/personalization/status");
  }

  /** Fetch GET /personalization/evaluation for the authenticated user. */
  async fetchEvaluation(): Promise<EvaluationApiResponse> {
    return this.request("/personalization/evaluation");
  }

  /** Fetch GET /personalization/training-pairs for transparency data. */
  async fetchTrainingPairs(): Promise<TrainingPairListResponse> {
    return this.request("/personalization/training-pairs");
  }

  /** Fetch safe model-routing evidence for the authenticated user. */
  async fetchAssignments(): Promise<ModelAssignmentSummaryResponse[]> {
    try {
      return await this.request("/personalization/assignments");
    } catch (err) {
      // Assignment evidence is optional against an older backend.
      if (err instanceof ApiError && err.status === 404) return [];
      throw err;
    }
  }

  /** Update the label text for one stored training example. */
  async updateTrainingPair(pairId: string, correctedText: string): Promise<TrainingPairSummaryResponse> {
    return this.request(`/personalization/training-pairs/${pairId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ corrected_text: correctedText }),
    });
  }

  /** Delete one stored training example. */
  async deleteTrainingPair(pairId: string): Promise<void> {
    await this.request(`/personalization/training-pairs/${pairId}`, { method: "DELETE" });
  }

  // --------------------------------------------------------------------------
  // Rendering helpers
  // --------------------------------------------------------------------------

  /** Clear the mount and show a loading spinner. */
  private renderLoading(): void {
    this.mount.replaceChildren();
    const el = document.createElement("p");
    el.className = "before-after-loading";
    el.setAttribute("data-testid", "before-after-loading");
    el.textContent = "Loading personalization status…";
    this.mount.appendChild(el);
  }

  /** Render an error message inside the mount. */
  private renderError(message: string): void {
    this.mount.replaceChildren();
    const el = document.createElement("p");
    el.className = "before-after-error";
    el.setAttribute("data-testid", "before-after-error");
    el.textContent = message;
    this.mount.appendChild(el);
  }

  /**
   * Explain the three-stage recognition pipeline and show progress toward the
   * next adapter training run.
   */
  private buildPipelineGuideSection(status: PersonalizationStatusResponse): HTMLElement {
    const section = document.createElement("section");
    section.className = "personalization-guide";
    section.setAttribute("data-testid", "personalization-guide");

    const heading = document.createElement("h2");
    heading.textContent = "How the system learns your handwriting";
    section.appendChild(heading);

    const intro = document.createElement("p");
    intro.className = "personalization-guide-intro";
    intro.textContent =
      "Recognition uses a shared base model trained on general handwriting. " +
      "You improve accuracy for your own writing by supplying labeled examples — " +
      "the system trains a small personal adapter on top of the base model.";
    section.appendChild(intro);

    const steps = document.createElement("ol");
    steps.className = "personalization-guide-steps";
    steps.setAttribute("data-testid", "personalization-guide-steps");

    const stepTexts = [
      "Upload a note — the base model recognizes text immediately (no waiting required).",
      "Fix mistakes in the editor, and/or submit optional calibration handwriting samples. Each fix or sample saves an image + the correct text as a training example.",
      "After enough examples accumulate, a background job trains your personal adapter. If it improves accuracy on held-out checks, it becomes active for your future uploads.",
    ];
    for (const text of stepTexts) {
      const li = document.createElement("li");
      li.textContent = text;
      steps.appendChild(li);
    }
    section.appendChild(steps);

    const rawThreshold = Number(status.training_trigger_threshold);
    const rawUnused = Number(status.unused_training_pairs);
    const threshold = Number.isFinite(rawThreshold) && rawThreshold > 0
      ? Math.max(1, Math.floor(rawThreshold))
      : 1;
    const unused = Number.isFinite(rawUnused) && rawUnused >= 0
      ? Math.max(0, Math.floor(rawUnused))
      : 0;
    const progressWrap = document.createElement("div");
    progressWrap.className = "personalization-progress";
    progressWrap.setAttribute("data-testid", "personalization-progress");

    const progressLabel = document.createElement("p");
    progressLabel.className = "personalization-progress-label";
    const remaining = Math.max(0, threshold - unused);
    if (status.training_state === "in_progress") {
      progressLabel.textContent =
        `Training your personal model now (using ${unused} saved example${unused === 1 ? "" : "s"})…`;
    } else if (status.adapter_active) {
      progressLabel.textContent =
        `Personal adapter active. ${unused} new example${unused === 1 ? "" : "s"} saved toward the next training round.`;
    } else if (remaining === 0) {
      progressLabel.textContent =
        `${unused} of ${threshold} examples saved — training should start soon.`;
    } else {
      progressLabel.textContent =
        `${unused} of ${threshold} examples saved — ${remaining} more until personal training starts.`;
    }
    progressWrap.appendChild(progressLabel);

    const progress = document.createElement("progress");
    progress.setAttribute("data-testid", "personalization-progress-bar");
    progress.max = threshold;
    progress.value = Math.min(unused, threshold);
    progress.textContent = `${unused} / ${threshold}`;
    progressWrap.appendChild(progress);

    const sources = document.createElement("p");
    sources.className = "personalization-guide-sources";
    sources.textContent =
      "Examples come from inline corrections on uploaded notes and from optional calibration samples. " +
      "Both use the same pipeline — you do not need calibration if you prefer correcting real notes.";
    progressWrap.appendChild(sources);

    const epochsInfo = document.createElement("p");
    epochsInfo.className = "personalization-guide-epochs";
    const maxEpochs = Number.isFinite(Number(status.max_training_epochs))
      ? Math.max(1, Math.floor(Number(status.max_training_epochs)))
      : 1;
    epochsInfo.textContent =
      `Each training round fine-tunes in the background for up to ${maxEpochs} epoch` +
      `${maxEpochs === 1 ? "" : "s"}.`;
    progressWrap.appendChild(epochsInfo);

    section.appendChild(progressWrap);
    return section;
  }

  private buildTrainingExamplesSection(trainingPairs: TrainingPairSummaryResponse[]): HTMLElement {
    const section = document.createElement("section");
    section.className = "training-examples";
    section.setAttribute("data-testid", "training-examples");

    const heading = document.createElement("h3");
    heading.textContent = "Training examples collected so far";
    section.appendChild(heading);

    const description = document.createElement("p");
    description.textContent =
      "These are the exact samples currently stored for your personalization pipeline.";
    section.appendChild(description);

    if (trainingPairs.length === 0) {
      const empty = document.createElement("p");
      empty.className = "training-examples-empty";
      empty.textContent = "No training examples have been collected yet.";
      section.appendChild(empty);
      return section;
    }

    const list = document.createElement("div");
    list.className = "training-example-list";

    for (const pair of trainingPairs) {
      const card = document.createElement("article");
      card.className = "training-example-card";
      card.setAttribute("data-testid", "training-example-card");

      const img = document.createElement("img");
      img.className = "training-example-image";
      img.alt = `Training sample ${pair.pair_id}`;
      img.loading = "lazy";
      img.src = `${this.apiBase}/personalization/training-pairs/${pair.pair_id}/image`;
      card.appendChild(img);

      const zoomButton = document.createElement("button");
      zoomButton.type = "button";
      zoomButton.className = "training-example-zoom";
      zoomButton.setAttribute("data-testid", "training-example-zoom");
      zoomButton.textContent = "Zoom sample";
      zoomButton.addEventListener("click", () => {
        this.closeZoom?.();
        const dialog = document.createElement("div");
        dialog.className = "training-example-zoom-dialog";
        dialog.setAttribute("data-testid", "training-example-zoom-dialog");
        dialog.setAttribute("role", "dialog");
        dialog.setAttribute("aria-modal", "true");
        dialog.setAttribute("aria-label", "Zoomed training sample");

        const dialogImage = document.createElement("img");
        dialogImage.className = "training-example-zoom-image";
        dialogImage.src = img.src;
        dialogImage.alt = img.alt;
        dialog.appendChild(dialogImage);

        const closeButton = document.createElement("button");
        closeButton.type = "button";
        closeButton.className = "training-example-zoom-close";
        closeButton.setAttribute("data-testid", "training-example-zoom-close");
        closeButton.textContent = "Close";
        const close = (): void => {
          document.removeEventListener("keydown", onKeyDown);
          dialog.remove();
          this.closeZoom = undefined;
        };
        const onKeyDown = (event: KeyboardEvent): void => {
          if (event.key === "Escape") close();
        };
        closeButton.addEventListener("click", close);
        dialog.addEventListener("click", (event) => {
          if (event.target === dialog) close();
        });
        dialog.appendChild(closeButton);
        document.addEventListener("keydown", onKeyDown);
        this.closeZoom = close;
        document.body.appendChild(dialog);
        closeButton.focus();
      });
      card.appendChild(zoomButton);

      const text = document.createElement("p");
      text.className = "training-example-text";
      text.textContent = pair.corrected_text;
      card.appendChild(text);

      const editor = document.createElement("label");
      editor.className = "training-example-editor";
      editor.textContent = "Correct text";
      const textarea = document.createElement("textarea");
      textarea.className = "training-example-textarea";
      textarea.setAttribute("data-testid", "training-example-textarea");
      textarea.value = pair.corrected_text;
      editor.appendChild(textarea);
      card.appendChild(editor);

      const origin = document.createElement("p");
      origin.className = "training-example-origin";
      origin.textContent = pair.origin === "calibration"
        ? "Source: calibration sample"
        : "Source: correction on uploaded note";
      card.appendChild(origin);

      const state = document.createElement("p");
      state.className = "training-example-state";
      state.textContent = pair.used
        ? "Status: already consumed by a completed training run."
        : "Status: counted toward the next training run.";
      card.appendChild(state);

      const created = document.createElement("p");
      created.className = "training-example-created";
      created.textContent = `Added: ${new Date(pair.created_at).toLocaleString()}`;
      card.appendChild(created);

      const actions = document.createElement("div");
      actions.className = "training-example-actions";

      const saveButton = document.createElement("button");
      saveButton.type = "button";
      saveButton.className = "training-example-save";
      saveButton.setAttribute("data-testid", "training-example-save");
      saveButton.textContent = "Save label";
      actions.appendChild(saveButton);

      const deleteButton = document.createElement("button");
      deleteButton.type = "button";
      deleteButton.className = "training-example-delete";
      deleteButton.setAttribute("data-testid", "training-example-delete");
      deleteButton.textContent = "Delete sample";
      actions.appendChild(deleteButton);
      card.appendChild(actions);

      const actionStatus = document.createElement("p");
      actionStatus.className = "training-example-action-status";
      actionStatus.setAttribute("data-testid", "training-example-action-status");
      actionStatus.setAttribute("aria-live", "polite");
      card.appendChild(actionStatus);

      saveButton.addEventListener("click", () => {
        const nextText = textarea.value.trim();
        if (!nextText) {
          actionStatus.textContent = "Correct text cannot be blank.";
          actionStatus.className = "training-example-action-status training-example-action-status--failed";
          return;
        }
        saveButton.disabled = true;
        actionStatus.textContent = "Saving label...";
        actionStatus.className = "training-example-action-status training-example-action-status--pending";
        void this.updateTrainingPair(pair.pair_id, nextText)
          .then((updated) => {
            text.textContent = updated.corrected_text;
            textarea.value = updated.corrected_text;
            state.textContent = updated.used
              ? "Status: already consumed by a completed training run."
              : "Status: counted toward the next training run.";
            actionStatus.textContent = "Label saved for future training.";
            actionStatus.className = "training-example-action-status training-example-action-status--saved";
          })
          .catch(() => {
            actionStatus.textContent = "Could not save this label.";
            actionStatus.className = "training-example-action-status training-example-action-status--failed";
          })
          .finally(() => {
            saveButton.disabled = false;
          });
      });

      deleteButton.addEventListener("click", () => {
        deleteButton.disabled = true;
        saveButton.disabled = true;
        actionStatus.textContent = "Deleting sample...";
        actionStatus.className = "training-example-action-status training-example-action-status--pending";
        void this.deleteTrainingPair(pair.pair_id)
          .then(() => {
            const nextStatus = this.latestStatus
              ? {
                  ...this.latestStatus,
                  unused_training_pairs: pair.used
                    ? this.latestStatus.unused_training_pairs
                    : Math.max(0, this.latestStatus.unused_training_pairs - 1),
                }
              : undefined;
            const remainingPairs = this.latestTrainingPairs.filter(
              (candidate) => candidate.pair_id !== pair.pair_id,
            );
            if (nextStatus && this.latestReport) {
              this.renderFull(
                nextStatus,
                this.latestReport,
                remainingPairs,
                this.latestUnavailableReason,
                this.latestAssignments,
              );
              return;
            }
            card.remove();
          })
          .catch((err: unknown) => {
            const message = err instanceof Error ? err.message : "Unknown error";
            actionStatus.textContent = `Could not delete this sample: ${message}`;
            actionStatus.className = "training-example-action-status training-example-action-status--failed";
            deleteButton.disabled = false;
            saveButton.disabled = false;
          });
      });

      list.appendChild(card);
    }

    section.appendChild(list);
    return section;
  }

  private updateRefreshPolling(state: PersonalizationStatusResponse["training_state"]): void {
    if (state === "in_progress") {
      if (this.refreshTimer !== null) return;
      this.refreshTimer = window.setInterval(() => {
        void this.refresh();
      }, 4000);
      return;
    }
    if (this.refreshTimer !== null) {
      window.clearInterval(this.refreshTimer);
      this.refreshTimer = null;
    }
  }

  /**
   * Render the personalization status section (training state + notification).
   * Returns the section element so it can be appended to the container.
   */
  private buildStatusSection(status: PersonalizationStatusResponse): HTMLElement {
    const section = document.createElement("section");
    section.className = "before-after-status";
    section.setAttribute("data-testid", "before-after-status");

    const heading = document.createElement("h3");
    heading.textContent = "Training status";
    section.appendChild(heading);

    // Training state badge.
    const stateBadge = document.createElement("p");
    stateBadge.setAttribute("data-testid", "personalization-training-state");
    stateBadge.className = `before-after-state before-after-state--${status.training_state}`;

    const stateLabels: Record<string, string> = {
      in_progress: "Training in progress…",
      completed: "Training completed",
      none: "No training started",
    };
    stateBadge.textContent = stateLabels[status.training_state] ?? status.training_state;
    section.appendChild(stateBadge);

    // Adapter status.
    const adapterP = document.createElement("p");
    adapterP.setAttribute("data-testid", "personalization-adapter-active");
    adapterP.className = "before-after-adapter-status";
    adapterP.textContent = status.adapter_active
      ? "Personalized adapter is active."
      : "No personalized adapter is active (using base model).";
    section.appendChild(adapterP);

    // Last run outcome badge (only when a run has completed).
    if (status.last_run_outcome !== null) {
      const outcomeP = document.createElement("p");
      outcomeP.setAttribute("data-testid", "personalization-last-outcome");
      outcomeP.className = `before-after-outcome before-after-outcome--${status.last_run_outcome}`;

      const outcomeLabels: Record<string, string> = {
        accepted: "Last run: accepted ✓",
        rejected: "Last run: rejected — accuracy did not improve",
        failed: "Last run: failed — training did not complete",
      };
      outcomeP.textContent = outcomeLabels[status.last_run_outcome] ?? status.last_run_outcome;
      section.appendChild(outcomeP);
    }

    // Notification message (if provided by the backend).
    if (status.notification) {
      const notifP = document.createElement("p");
      notifP.setAttribute("data-testid", "personalization-notification");
      notifP.className = "before-after-notification";
      notifP.textContent = status.notification;
      section.appendChild(notifP);
    }

    // Rejection reason (when present).
    if (status.reject_reason) {
      const reasonP = document.createElement("p");
      reasonP.setAttribute("data-testid", "personalization-reject-reason");
      reasonP.className = "before-after-reject-reason";
      reasonP.textContent = `Reason: ${status.reject_reason}`;
      section.appendChild(reasonP);
    }

    return section;
  }

  private buildAssignmentsSection(
    assignments: ModelAssignmentSummaryResponse[],
  ): HTMLElement {
    const section = document.createElement("section");
    section.className = "personalization-assignments";
    section.setAttribute("data-testid", "personalization-assignments");

    const heading = document.createElement("h3");
    heading.textContent = "Model routing evidence";
    section.appendChild(heading);

    const description = document.createElement("p");
    description.textContent =
      "Each recognition result records the task and exact base/adapter assignment used. " +
      "Only compatible active adapters are eligible; no checkpoint paths are shown here.";
    section.appendChild(description);

    if (assignments.length === 0) {
      const empty = document.createElement("p");
      empty.className = "personalization-assignments-empty";
      empty.textContent = "No model assignment has been recorded yet.";
      section.appendChild(empty);
      return section;
    }

    const table = document.createElement("table");
    table.className = "personalization-assignments-table";
    table.setAttribute("data-testid", "personalization-assignments-table");
    table.setAttribute("aria-label", "Model routing evidence");

    const head = document.createElement("thead");
    const headRow = document.createElement("tr");
    ["Task", "Base model", "Adapter", "State", "Activated"].forEach((label) => {
      const cell = document.createElement("th");
      cell.scope = "col";
      cell.textContent = label;
      headRow.appendChild(cell);
    });
    head.appendChild(headRow);
    table.appendChild(head);

    const body = document.createElement("tbody");
    for (const assignment of assignments) {
      const row = document.createElement("tr");
      row.setAttribute("data-testid", "personalization-assignment");

      const task = document.createElement("td");
      task.textContent = assignment.task.replaceAll("_", " ");
      row.appendChild(task);

      const base = document.createElement("td");
      base.textContent = assignment.base_model_version;
      row.appendChild(base);

      const adapter = document.createElement("td");
      adapter.textContent = assignment.personalized
        ? `Personal adapter (${assignment.adapter_version})`
        : "Base only";
      row.appendChild(adapter);

      const state = document.createElement("td");
      state.textContent = assignment.active ? "Active" : "Retired";
      row.appendChild(state);

      const activated = document.createElement("td");
      activated.textContent = new Date(assignment.created_at).toLocaleString();
      row.appendChild(activated);
      body.appendChild(row);
    }
    table.appendChild(body);
    section.appendChild(table);
    return section;
  }

  private buildTimelineSection(status: PersonalizationStatusResponse): HTMLElement {
    const section = document.createElement("section");
    section.className = "personalization-timeline";
    section.setAttribute("data-testid", "personalization-timeline");

    const heading = document.createElement("h3");
    heading.textContent = "Training timeline";
    section.appendChild(heading);

    const list = document.createElement("ol");
    list.className = "personalization-timeline-list";

    const threshold = Number.isFinite(Number(status.training_trigger_threshold))
      ? Math.max(1, Math.floor(Number(status.training_trigger_threshold)))
      : 1;
    const unused = Number.isFinite(Number(status.unused_training_pairs))
      ? Math.max(0, Math.floor(Number(status.unused_training_pairs)))
      : 0;
    const hasEnoughSamples = unused >= threshold;

    const trainingRunning = status.training_state === "in_progress";
    const hasCompletedRun = status.training_state === "completed";
    const adapterActive = status.adapter_active;

    const stages: Array<{ title: string; detail: string; state: "done" | "active" | "pending" }> = [
      {
        title: "1) Collect labeled samples",
        detail: `${unused}/${threshold} samples currently stored.`,
        state: hasEnoughSamples ? "done" : "active",
      },
      {
        title: "2) Queue and run personalization training",
        detail: trainingRunning
          ? "Training worker is currently running."
          : hasCompletedRun
            ? "Latest training run completed."
            : hasEnoughSamples
              ? "Threshold reached; training should start shortly."
              : "Waiting for enough samples.",
        state: trainingRunning ? "active" : hasCompletedRun ? "done" : hasEnoughSamples ? "active" : "pending",
      },
      {
        title: "3) Evaluate and decide",
        detail: status.last_run_outcome === "accepted"
          ? "Run accepted: adapter improves held-out metrics."
          : status.last_run_outcome === "rejected"
            ? "Run rejected: did not beat guardrails."
            : status.last_run_outcome === "failed"
              ? "Run failed: training did not complete."
              : "No completed run yet.",
        state: hasCompletedRun ? "done" : "pending",
      },
      {
        title: "4) Activate personalized adapter",
        detail: adapterActive
          ? "Adapter active for new recognition requests."
          : "Base model still active.",
        state: adapterActive ? "done" : "pending",
      },
    ];

    for (const stage of stages) {
      const li = document.createElement("li");
      li.className = `personalization-timeline-item personalization-timeline-item--${stage.state}`;
      li.setAttribute("data-testid", "personalization-timeline-item");

      const title = document.createElement("p");
      title.className = "personalization-timeline-title";
      title.textContent = stage.title;
      li.appendChild(title);

      const detail = document.createElement("p");
      detail.className = "personalization-timeline-detail";
      detail.textContent = stage.detail;
      li.appendChild(detail);
      list.appendChild(li);
    }

    section.appendChild(list);
    return section;
  }

  /**
   * Render the before/after accuracy section from a BeforeAfterReport.
   * Shows "unavailable" for any metric that is null.
   */
  private buildEvaluationSection(report: BeforeAfterReport, unavailableReason?: string | null): HTMLElement {
    const section = document.createElement("section");
    section.className = "before-after-evaluation";
    section.setAttribute("data-testid", "before-after-evaluation");

    const heading = document.createElement("h3");
    heading.textContent = "Before / After Accuracy";
    section.appendChild(heading);

    // Overall availability banner.
    if (report.availability === "unavailable") {
      const banner = document.createElement("p");
      banner.className = "before-after-unavailable-banner";
      banner.setAttribute("data-testid", "before-after-unavailable-banner");
      banner.textContent =
        unavailableReason ??
        "Accuracy measurement is unavailable. Complete more corrections to enable evaluation.";
      section.appendChild(banner);
    }

    // Metrics table.
    const table = document.createElement("table");
    table.className = "before-after-table";
    table.setAttribute("data-testid", "before-after-table");
    table.setAttribute("aria-label", "Before and after personalization accuracy metrics");

    // Header row.
    const thead = document.createElement("thead");
    const headerRow = document.createElement("tr");
    ["Metric", "Before", "After", "Improvement (↓ = better)"].forEach((col) => {
      const th = document.createElement("th");
      th.setAttribute("scope", "col");
      th.textContent = col;
      headerRow.appendChild(th);
    });
    thead.appendChild(headerRow);
    table.appendChild(thead);

    // Body rows.
    const tbody = document.createElement("tbody");
    const rows: Array<{
      label: string;
      before: number | null;
      after: number | null;
      delta: number | null;
      testId: string;
    }> = [
      {
        label: "CER",
        before: report.beforeCer,
        after: report.afterCer,
        delta: report.deltaCer,
        testId: "metric-row-cer",
      },
      {
        label: "WER",
        before: report.beforeWer,
        after: report.afterWer,
        delta: report.deltaWer,
        testId: "metric-row-wer",
      },
    ];

    for (const row of rows) {
      const tr = document.createElement("tr");
      tr.setAttribute("data-testid", row.testId);

      const labelTd = document.createElement("td");
      labelTd.textContent = row.label;
      tr.appendChild(labelTd);

      const beforeTd = document.createElement("td");
      beforeTd.setAttribute("data-testid", `before-${row.label.toLowerCase()}`);
      beforeTd.textContent = formatMetric(row.before);
      if (row.before === null) beforeTd.className = "before-after-unavailable";
      tr.appendChild(beforeTd);

      const afterTd = document.createElement("td");
      afterTd.setAttribute("data-testid", `after-${row.label.toLowerCase()}`);
      afterTd.textContent = formatMetric(row.after);
      if (row.after === null) afterTd.className = "before-after-unavailable";
      tr.appendChild(afterTd);

      const deltaTd = document.createElement("td");
      deltaTd.setAttribute("data-testid", `delta-${row.label.toLowerCase()}`);
      deltaTd.textContent = formatDelta(row.delta);
      if (row.delta === null) {
        deltaTd.className = "before-after-unavailable";
      } else if (row.delta > 0) {
        deltaTd.className = "before-after-improved";
      } else if (row.delta < 0) {
        deltaTd.className = "before-after-worsened";
      }
      tr.appendChild(deltaTd);

      tbody.appendChild(tr);
    }

    table.appendChild(tbody);
    section.appendChild(table);

    return section;
  }

  // --------------------------------------------------------------------------
  // Public interface
  // --------------------------------------------------------------------------

  /**
   * Render the before/after CER/WER report (or an unavailable notice).
   *
   * This is the pure/synchronous rendering path used when the caller has already
   * fetched the report. To fetch-and-render in one step, call `refresh()`.
   */
  render(report: BeforeAfterReport, unavailableReason?: string | null): void {
    this.mount.replaceChildren();

    const container = document.createElement("div");
    container.className = "before-after-container";
    container.setAttribute("data-testid", "before-after-container");

    container.appendChild(this.buildEvaluationSection(report, unavailableReason));
    this.mount.appendChild(container);
  }

  /**
   * Render both the personalization status and the before/after evaluation.
   * This is the full view combining both API calls.
   */
  renderFull(
    status: PersonalizationStatusResponse,
    report: BeforeAfterReport,
    trainingPairs: TrainingPairSummaryResponse[] = [],
    unavailableReason?: string | null,
    assignments: ModelAssignmentSummaryResponse[] = [],
  ): void {
    if (this.disposed) return;
    this.latestStatus = status;
    this.latestReport = report;
    this.latestTrainingPairs = trainingPairs;
    this.latestAssignments = assignments;
    this.latestUnavailableReason = unavailableReason;
    this.mount.replaceChildren();

    const container = document.createElement("div");
    container.className = "before-after-container";
    container.setAttribute("data-testid", "before-after-container");

    container.appendChild(this.buildPipelineGuideSection(status));
    container.appendChild(this.buildStatusSection(status));
    container.appendChild(this.buildTimelineSection(status));
    container.appendChild(this.buildAssignmentsSection(assignments));
    container.appendChild(this.buildTrainingExamplesSection(trainingPairs));
    container.appendChild(this.buildEvaluationSection(report, unavailableReason));
    this.mount.appendChild(container);
  }

  /**
   * Fetch status and evaluation from the backend, then render the full view.
   * Shows a loading state while fetching and an error message on failure.
   */
  async refresh(): Promise<void> {
    if (this.disposed || this.refreshInFlight) return;
    this.refreshInFlight = true;
    this.renderLoading();

    try {
      const [statusData, evalData, pairsData, assignments] = await Promise.all([
        this.fetchStatus(),
        this.fetchEvaluation(),
        this.fetchTrainingPairs(),
        this.fetchAssignments(),
      ]);

      if (this.disposed) return;
      const report = reportFromApi(evalData);
      this.renderFull(
        statusData,
        report,
        pairsData.items,
        evalData.unavailable_reason,
        assignments,
      );
      this.updateRefreshPolling(statusData.training_state);
    } catch (err: unknown) {
      if (this.disposed) return;
      const message =
        err instanceof Error ? err.message : "Failed to load personalization data.";
      this.renderError(message);
      this.updateRefreshPolling("none");
    } finally {
      this.refreshInFlight = false;
    }
  }
}
