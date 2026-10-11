// Manual formula crop + persisted LaTeX review for an imported PDF page.

import {
  createPageRegion,
  createRecognitionJob,
  exportDocument,
  getPageRasterUrl,
  listPageRegions,
  saveRegionReview,
  waitForRecognitionJob,
  type DocumentPageSummary,
  type PageRegionDetail,
} from "../api/client.js";
import { MathEditor } from "./mathEditor.js";
import { PageViewer } from "./pageViewer.js";
import { ReviewQueue, type ReviewItem } from "./reviewQueue.js";

export interface MathNotesOptions {
  mount: HTMLElement;
  documentId: string;
  pages: DocumentPageSummary[];
}

function cropObjectUrl(
  image: HTMLImageElement,
  bbox: { x: number; y: number; width: number; height: number },
): string {
  const canvas = document.createElement("canvas");
  canvas.width = bbox.width;
  canvas.height = bbox.height;
  const ctx = canvas.getContext("2d");
  ctx?.drawImage(
    image,
    bbox.x,
    bbox.y,
    bbox.width,
    bbox.height,
    0,
    0,
    bbox.width,
    bbox.height,
  );
  return canvas.toDataURL("image/png");
}

export class MathNotesWorkspace {
  private readonly mount: HTMLElement;
  private readonly documentId: string;
  private readonly pages: DocumentPageSummary[];
  private page: DocumentPageSummary;
  private regions: PageRegionDetail[] = [];
  private activeLatex = "";
  private activeRegionId: string | null = null;
  private activePredictionId: string | null = null;
  private activeReviewVersion: number | null = null;
  private activeDocumentRevisionId: string | null = null;
  private activeExcluded = false;
  private includeInTraining = false;
  private recognitionMode: "formula" | "prose" = "formula";

  constructor(options: MathNotesOptions) {
    this.mount = options.mount;
    this.documentId = options.documentId;
    this.pages = [...options.pages].sort((a, b) => a.page_index - b.page_index);
    this.page = this.pages[0];
    void this.render();
  }

  private async render(): Promise<void> {
    this.mount.replaceChildren();
    this.mount.style.display = "";

    const heading = document.createElement("h2");
    heading.textContent = this.recognitionMode === "prose" ? "Prose review" : "Formula review";
    this.mount.appendChild(heading);

    const help = document.createElement("p");
    help.className = "math-notes-help";
    help.textContent =
      "Drag on the page to select a complete expression. Edit the LaTeX, save the review, then export Markdown, LaTeX, HTML, or PDF from the same revision.";
    this.mount.appendChild(help);

    const layoutActions = document.createElement("div");
    layoutActions.className = "math-notes-layout-actions";
    const autoLayoutButton = document.createElement("button");
    autoLayoutButton.type = "button";
    autoLayoutButton.textContent = "Auto-detect page regions";
    autoLayoutButton.setAttribute("data-testid", "math-notes-auto-layout");
    layoutActions.appendChild(autoLayoutButton);
    this.mount.appendChild(layoutActions);
    const modePicker = document.createElement("select");
    modePicker.setAttribute("aria-label", "Recognition task");
    modePicker.setAttribute("data-testid", "recognition-mode");
    for (const [value, label] of [["formula", "Formulas"], ["prose", "Handwritten prose"]]) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      option.selected = value === this.recognitionMode;
      modePicker.appendChild(option);
    }
    modePicker.addEventListener("change", () => {
      this.recognitionMode = modePicker.value === "prose" ? "prose" : "formula";
      heading.textContent = this.recognitionMode === "prose" ? "Prose review" : "Formula review";
      help.textContent = this.recognitionMode === "prose"
        ? "Select a handwritten text line or auto-detect lines. Review and save the text before exporting."
        : "Select a complete expression. Review and save the LaTeX before exporting.";
    });
    layoutActions.prepend(modePicker);

    if (this.pages.length > 1) {
      const pagePicker = document.createElement("label");
      pagePicker.className = "math-notes-page-picker";
      pagePicker.textContent = "Page ";
      const select = document.createElement("select");
      select.setAttribute("data-testid", "math-notes-page-select");
      for (const page of this.pages) {
        const option = document.createElement("option");
        option.value = page.page_id;
        option.textContent = String(page.page_index + 1);
        option.selected = page.page_id === this.page.page_id;
        select.appendChild(option);
      }
      select.addEventListener("change", () => {
        const selected = this.pages.find((page) => page.page_id === select.value);
        if (!selected || selected.page_id === this.page.page_id) return;
        this.page = selected;
        this.activeRegionId = null;
        this.activeLatex = "";
        void this.render();
      });
      pagePicker.appendChild(select);
      this.mount.appendChild(pagePicker);
    }

    const layout = document.createElement("div");
    layout.className = "math-notes-layout";
    this.mount.appendChild(layout);

    const viewerMount = document.createElement("div");
    viewerMount.className = "math-notes-viewer";
    layout.appendChild(viewerMount);

    const side = document.createElement("div");
    side.className = "math-notes-side";
    layout.appendChild(side);

    const queueMount = document.createElement("div");
    side.appendChild(queueMount);
    const editorMount = document.createElement("div");
    side.appendChild(editorMount);

    const status = document.createElement("p");
    status.setAttribute("data-testid", "math-notes-status");
    status.className = "math-notes-status";
    side.appendChild(status);

    const saveBtn = document.createElement("button");
    saveBtn.type = "button";
    saveBtn.textContent = "Save review";
    saveBtn.setAttribute("data-testid", "math-notes-save");
    saveBtn.disabled = true;
    side.appendChild(saveBtn);

    const consentLabel = document.createElement("label");
    consentLabel.className = "math-notes-consent";
    const consent = document.createElement("input");
    consent.type = "checkbox";
    consent.setAttribute("data-testid", "math-notes-consent");
    consent.addEventListener("change", () => {
      this.includeInTraining = consent.checked;
    });
    consentLabel.appendChild(consent);
    const consentText = document.createElement("span");
    consentText.textContent = "Add this reviewed crop to my training set";
    consentLabel.appendChild(consentText);
    side.appendChild(consentLabel);

    const exclusionLabel = document.createElement("label");
    exclusionLabel.className = "math-notes-exclusion";
    const exclusion = document.createElement("input");
    exclusion.type = "checkbox";
    exclusion.disabled = true;
    exclusion.setAttribute("data-testid", "math-notes-excluded");
    exclusion.addEventListener("change", () => {
      this.activeExcluded = exclusion.checked;
      consent.disabled = this.activeExcluded;
      if (this.activeExcluded) {
        consent.checked = false;
        this.includeInTraining = false;
      }
    });
    exclusionLabel.appendChild(exclusion);
    const exclusionText = document.createElement("span");
    exclusionText.textContent = "Exclude this region from exports and training";
    exclusionLabel.appendChild(exclusionText);
    side.appendChild(exclusionLabel);

    const exportRow = document.createElement("div");
    exportRow.className = "math-notes-export";
    for (const format of ["markdown", "latex", "latex_bundle", "html", "pdf"] as const) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = `Export ${format === "latex_bundle" ? "LaTeX bundle" : format}`;
      button.setAttribute("data-testid", `math-notes-export-${format}`);
      button.addEventListener("click", () => {
        void this.exportRevision(format, status).catch((error) => {
          status.textContent = error instanceof Error ? error.message : `Could not export ${format}.`;
        });
      });
      exportRow.appendChild(button);
    }
    side.appendChild(exportRow);

    const rasterUrl = getPageRasterUrl(this.page.page_id);
    new PageViewer({
      mount: viewerMount,
      imageUrl: rasterUrl,
      width: Math.max(1, this.page.width),
      height: Math.max(1, this.page.height),
      onSelect: (bbox) => {
        void this.captureRegion(bbox, editorMount, queueMount, saveBtn, status, exclusion, consent).catch((error) => {
          status.textContent = error instanceof Error ? error.message : "Formula recognition failed.";
          saveBtn.disabled = true;
        });
      },
    });

    saveBtn.addEventListener("click", () => {
      void this.persistActiveReview(status, exclusion, consent).catch((error) => {
        status.textContent = error instanceof Error ? error.message : "Could not save review.";
      });
    });

    autoLayoutButton.addEventListener("click", () => {
      void this.autoLayout(autoLayoutButton, queueMount, editorMount, saveBtn, status, exclusion, consent);
    });

    this.regions = await listPageRegions(this.page.page_id).catch(() => []);
    this.renderQueue(queueMount, editorMount, saveBtn, status, exclusion, consent);
  }

  private renderQueue(
    queueMount: HTMLElement,
    editorMount: HTMLElement,
    saveBtn: HTMLButtonElement,
    status: HTMLElement,
    exclusion: HTMLInputElement,
    consent: HTMLInputElement,
  ): void {
    const items: ReviewItem[] = this.regions.map((region) => ({
      id: region.region_id,
      kind: region.content_type === "figure"
        ? "figure"
        : region.content_type.includes("math")
          ? "math"
          : "text",
      preview: region.reviewed_text || region.prediction_text || "Unreviewed formula",
      unresolved: region.unresolved,
    }));
    new ReviewQueue(queueMount).render(items, (id) => {
      const region = this.regions.find((item) => item.region_id === id);
      if (!region) return;
      this.activeRegionId = region.region_id;
      this.activeLatex = region.reviewed_text || region.prediction_text || "";
      this.activePredictionId = region.prediction_id ?? null;
      this.activeReviewVersion = region.review_version ?? null;
      this.activeExcluded = region.excluded;
      this.includeInTraining = false;
      exclusion.checked = this.activeExcluded;
      exclusion.disabled = false;
      consent.checked = false;
      consent.disabled = this.activeExcluded;
      saveBtn.disabled = false;
      new MathEditor({
        mount: editorMount,
        latex: this.activeLatex,
        plainText: region.content_type === "text",
        onChange: (latex) => {
          this.activeLatex = latex;
        },
      });
      void this.loadCropPreview({x: region.bbox[0], y: region.bbox[1], width: region.bbox[2], height: region.bbox[3]})
        .then((url) => {
          if (this.activeRegionId !== region.region_id || !url) return;
          const crop = editorMount.querySelector<HTMLImageElement>(".math-editor-crop");
          if (crop) { crop.src = url; crop.hidden = false; }
        });
      status.textContent = "Loaded saved region.";
    });
  }

  private async captureRegion(
    bbox: { x: number; y: number; width: number; height: number },
    editorMount: HTMLElement,
    queueMount: HTMLElement,
    saveBtn: HTMLButtonElement,
    status: HTMLElement,
    exclusion: HTMLInputElement,
    consent: HTMLInputElement,
  ): Promise<void> {
    status.textContent = "Queuing selected expression…";
    const created = await createPageRegion(this.page.page_id, bbox, this.recognitionMode === "prose" ? "text" : "math_display", this.page.coordinate_space);
    const job = await createRecognitionJob(this.page.page_id, {
      region_ids: [created.region_id],
      mode: this.recognitionMode,
    });
    const finished = await waitForRecognitionJob(job.job_id, (progress) => {
      status.textContent = progress.progress_total > 0
        ? `Recognizing selected expression… ${progress.progress_current}/${progress.progress_total}`
        : "Recognizing selected expression…";
    });
    const refreshed = await listPageRegions(this.page.page_id).catch(() => []);
    this.regions = refreshed.length > 0 ? refreshed : this.regions;
    const detail = this.regions.find((region) => region.region_id === created.region_id);
    const recognizedText = detail?.prediction_text || "";
    this.activeRegionId = created.region_id;
    this.activeLatex = detail?.reviewed_text || recognizedText;
    this.activePredictionId = detail?.prediction_id ?? null;
    this.activeReviewVersion = detail?.review_version ?? null;
    this.activeExcluded = detail?.excluded ?? false;
    this.includeInTraining = false;
    exclusion.checked = this.activeExcluded;
    exclusion.disabled = false;
    consent.checked = false;
    consent.disabled = this.activeExcluded;
    saveBtn.disabled = false;
    const cropUrl = await this.loadCropPreview(bbox);
    new MathEditor({
      mount: editorMount,
      latex: this.activeLatex,
      cropUrl,
      plainText: this.recognitionMode === "prose",
      onChange: (latex) => {
        this.activeLatex = latex;
      },
    });
    if (finished.state === "failed" || finished.state === "timeout" || finished.state === "no_text") {
      status.textContent =
        finished.error_message ||
        (finished.state === "no_text"
          ? "No formula was detected. Type the LaTeX for this crop, then save the review."
          : "Formula engine unavailable. Type the LaTeX for this crop, then save the review.");
    } else {
      status.textContent = detail?.truncated
        ? "Recognition truncated. Review the LaTeX before saving."
        : "Recognition complete. Review and save.";
    }
    this.renderQueue(queueMount, editorMount, saveBtn, status, exclusion, consent);
  }

  private loadCropPreview(bbox: {x: number; y: number; width: number; height: number}): Promise<string | undefined> {
    return new Promise((resolve) => {
      const img = new Image();
      img.crossOrigin = "use-credentials";
      img.onload = () => {
        try { resolve(cropObjectUrl(img, bbox)); }
        catch { resolve(undefined); }
      };
      img.onerror = () => resolve(undefined);
      img.src = getPageRasterUrl(this.page.page_id);
    });
  }

  private async autoLayout(
    button: HTMLButtonElement,
    queueMount: HTMLElement,
    editorMount: HTMLElement,
    saveBtn: HTMLButtonElement,
    status: HTMLElement,
    exclusion: HTMLInputElement,
    consent: HTMLInputElement,
  ): Promise<void> {
    button.disabled = true;
    status.textContent = "Detecting page regions…";
    try {
      const job = await createRecognitionJob(this.page.page_id, {
        auto_layout: true,
        mode: this.recognitionMode,
      });
      const finished = await waitForRecognitionJob(job.job_id, (progress) => {
        status.textContent = progress.progress_total > 0
          ? `Recognizing page regions… ${progress.progress_current}/${progress.progress_total}`
          : "Detecting page regions…";
      });
      this.regions = await listPageRegions(this.page.page_id).catch(() => this.regions);
      // The worker creates a new document revision when it adds predictions;
      // use the server's current active revision on the next export rather
      // than pinning the client to an older revision from a prior save.
      this.activeDocumentRevisionId = null;
      this.activeRegionId = null;
      this.activePredictionId = null;
      this.activeReviewVersion = null;
      this.activeExcluded = false;
      exclusion.checked = false;
      exclusion.disabled = true;
      consent.checked = false;
      consent.disabled = true;
      saveBtn.disabled = true;
      this.renderQueue(queueMount, editorMount, saveBtn, status, exclusion, consent);
      status.textContent = finished.state === "failed" || finished.state === "timeout" || finished.state === "no_text"
        ? (finished.error_message || "Automatic recognition failed; review the detected crops manually.")
        : `Detected ${this.regions.length} page region${this.regions.length === 1 ? "" : "s"}. Select one to review.`;
    } catch (error) {
      status.textContent = error instanceof Error ? error.message : "Automatic layout failed.";
    } finally {
      button.disabled = false;
    }
  }

  private async persistActiveReview(
    status: HTMLElement,
    exclusion?: HTMLInputElement,
    consent?: HTMLInputElement,
  ): Promise<void> {
    if (!this.activeRegionId) return;
    const response = await saveRegionReview(
      this.activeRegionId,
      this.activeLatex,
      this.activeExcluded,
      this.activePredictionId,
      {
        expectedVersion: this.activeReviewVersion,
        includeInTraining: this.includeInTraining && !this.activeExcluded,
      },
    );
    this.activeReviewVersion = response.version;
    this.activeDocumentRevisionId = response.document_revision_id;
    this.activeExcluded = response.excluded;
    if (exclusion) exclusion.checked = this.activeExcluded;
    if (consent) {
      consent.checked = false;
      consent.disabled = this.activeExcluded;
    }
    this.regions = await listPageRegions(this.page.page_id).catch(() => this.regions);
    status.textContent = response.training_example_id
      ? "Review saved and added to your training set."
      : "Review saved.";
  }

  private async exportRevision(
    format: "markdown" | "latex" | "latex_bundle" | "html" | "pdf",
    status: HTMLElement,
  ): Promise<void> {
    if (this.activeRegionId) {
      await this.persistActiveReview(status);
    }
    const result = await exportDocument(this.documentId, format, this.activeDocumentRevisionId);
    if (result instanceof Blob) {
      const url = URL.createObjectURL(result);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `notes.${format === "latex_bundle" ? "zip" : format}`;
      anchor.click();
      URL.revokeObjectURL(url);
    } else {
      const blob = new Blob([result.text], {
        type: format === "html" ? "text/html" : format === "latex" ? "application/x-tex" : "text/plain",
      });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `notes.${format === "latex" ? "tex" : format === "markdown" ? "md" : "html"}`;
      anchor.click();
      URL.revokeObjectURL(url);
    }
    status.textContent = `Exported ${format} from the saved revision.`;
  }
}
