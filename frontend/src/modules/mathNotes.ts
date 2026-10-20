// The durable page/review contracts remain independent of the React shell.
import {
  ApiError,
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
import { isAvailable } from "../api/capabilities.js";
import { MathEditor } from "./mathEditor.js";
import { PageViewer } from "./pageViewer.js";
import { ReviewQueue } from "./reviewQueue.js";

export interface MathNotesOptions {
  mount: HTMLElement;
  documentId: string;
  pages: DocumentPageSummary[];
  onNavigate?: (pageId: string, regionId?: string) => void;
}
interface Draft {
  region: PageRegionDetail;
  text: string;
  excluded: boolean;
  training: boolean;
  dirty: boolean;
}
type Format = "markdown" | "latex" | "latex_bundle" | "html" | "pdf";
const formats: Format[] = ["markdown", "latex", "latex_bundle", "html", "pdf"];
const button = (label: string, action: () => void, testId?: string) => {
  const node = document.createElement("button");
  node.type = "button";
  node.textContent = label;
  if (testId) node.dataset.testid = testId;
  node.addEventListener("click", action);
  return node;
};

export class MathNotesWorkspace {
  private readonly mount: HTMLElement;
  private readonly options: MathNotesOptions;
  private page: DocumentPageSummary;
  private regions: PageRegionDetail[] = [];
  private drafts = new Map<string, Draft>();
  private activeRegionId: string | null = null;
  private activeDocumentRevisionId: string | null = null;
  private recognitionMode: "formula" | "prose" = "formula";
  private generation = 0;
  private disposed = false;
  private busy = false;
  private viewer?: PageViewer;
  private queueMount!: HTMLElement;
  private editorMount!: HTMLElement;
  private status!: HTMLElement;
  private saveBtn!: HTMLButtonElement;
  private exclusion!: HTMLInputElement;
  private consent!: HTMLInputElement;
  private reloadButton!: HTMLButtonElement;
  private controller = new AbortController();
  private ready: Promise<void>;
  private beforeUnload = (event: BeforeUnloadEvent) => {
    if (this.hasUnsavedChanges()) {
      event.preventDefault();
      event.returnValue = "";
    }
  };

  constructor(options: MathNotesOptions) {
    this.options = options;
    this.mount = options.mount;
    this.page = [...options.pages].sort(
      (a, b) => a.page_index - b.page_index,
    )[0];
    window.addEventListener("beforeunload", this.beforeUnload);
    this.ready = this.render();
  }
  dispose(): void {
    this.disposed = true;
    ++this.generation;
    this.controller.abort();
    this.viewer?.dispose();
    window.removeEventListener("beforeunload", this.beforeUnload);
    this.mount.querySelector<HTMLDialogElement>("dialog[open]")?.close?.();
  }
  hasUnsavedChanges(): boolean {
    return [...this.drafts.values()].some((draft) => draft.dirty);
  }
  async selectPage(pageId: string, regionId?: string): Promise<void> {
    await this.ready;
    const next = this.options.pages.find((page) => page.page_id === pageId);
    if (!next || this.disposed || this.busy) return;
    if (next.page_id !== this.page.page_id) {
      this.page = next;
      this.activeRegionId = null;
      this.regions = [];
      this.ready = this.render();
      await this.ready;
    }
    if (regionId && regionId !== this.activeRegionId)
      this.selectRegion(regionId);
  }
  private draft(): Draft | undefined {
    return this.activeRegionId
      ? this.drafts.get(this.activeRegionId)
      : undefined;
  }
  private markDirty(): void {
    const draft = this.draft();
    if (!draft) return;
    draft.dirty = true;
    this.status.textContent =
      "Changes not saved. Save review to keep this correction.";
    this.refreshQueue();
  }
  private async render(): Promise<void> {
    const generation = ++this.generation;
    this.viewer?.dispose();
    const loading = document.createElement("p");
    loading.setAttribute("role", "status");
    loading.textContent = "Loading original and saved regions…";
    this.mount.replaceChildren(loading);
    this.mount.style.display = "";
    const content = document.createDocumentFragment();
    const header = document.createElement("div");
    header.className = "review-header";
    const heading = document.createElement("h2");
    heading.textContent = "Formula review";
    const help = document.createElement("p");
    help.className = "math-notes-help";
    help.textContent =
      "Select a region, compare it with the original, and save your correction.";
    header.append(heading, help);
    content.append(header);
    const toolbar = document.createElement("div");
    toolbar.className = "math-notes-layout-actions";
    const mode = document.createElement("select");
    mode.setAttribute("aria-label", "Recognition task");
    mode.dataset.testid = "recognition-mode";
    for (const [value, label] of [
      ["formula", "Formulas"],
      ["prose", "Handwritten prose"],
    ]) {
      const option = new Option(label, value);
      mode.add(option);
    }
    mode.value = this.recognitionMode;
    mode.onchange = () => {
      this.recognitionMode = mode.value as typeof this.recognitionMode;
      heading.textContent =
        this.recognitionMode === "prose" ? "Prose review" : "Formula review";
    };
    const convert = button(
      "Convert page",
      () => void this.recognize(),
      "math-notes-auto-layout",
    );
    toolbar.append(
      mode,
      convert,
      button("Export", () => this.openExport(), "notebook-export-open"),
    );
    content.append(toolbar);
    const pageRail = document.createElement("details");
    pageRail.className = "page-rail";
    const summary = document.createElement("summary");
    summary.textContent = `Page ${this.page.page_index + 1} of ${this.options.pages.length}`;
    pageRail.append(summary);
    const pages = document.createElement("nav");
    pages.setAttribute("aria-label", "Notebook pages");
    for (const page of this.options.pages) {
      const link = document.createElement("a");
      link.href = `/notebooks/${encodeURIComponent(this.options.documentId)}/pages/${encodeURIComponent(page.page_id)}`;
      const img = document.createElement("img");
      img.src = getPageRasterUrl(page.page_id);
      img.alt = "";
      img.loading = "lazy";
      const label = document.createElement("span");
      label.textContent = `Page ${page.page_index + 1}`;
      link.append(img, label);
      link.setAttribute(
        "aria-current",
        page.page_id === this.page.page_id ? "page" : "false",
      );
      link.onclick = (event) => {
        event.preventDefault();
        if (!this.busy)
          void this.selectPage(page.page_id).then(() =>
            this.options.onNavigate?.(page.page_id),
          );
      };
      pages.append(link);
    }
    pageRail.append(pages);
    toolbar.prepend(pageRail);
    const paneSwitch = document.createElement("div");
    paneSwitch.className = "review-pane-switch";
    paneSwitch.setAttribute("aria-label", "Review pane");
    this.mount.dataset.pane = "text";
    for (const [value, label] of [
      ["original", "Original"],
      ["text", "Result"],
    ]) {
      const control = button(label, () => {
        this.mount.dataset.pane = value;
        for (const item of paneSwitch.querySelectorAll("button"))
          item.setAttribute("aria-pressed", String(item === control));
      });
      control.setAttribute("aria-pressed", String(value === "text"));
      paneSwitch.append(control);
    }
    content.append(paneSwitch);
    const balance = document.createElement("label");
    balance.className = "pane-balance";
    balance.textContent = "Pane balance ";
    const slider = document.createElement("input");
    slider.type = "range";
    slider.min = "50";
    slider.max = "200";
    slider.value = "100";
    slider.setAttribute("aria-label", "Original pane width");
    slider.oninput = () =>
      this.mount.style.setProperty(
        "--source-fr",
        `${Number(slider.value) / 100}fr`,
      );
    balance.append(slider);
    const viewOptions = document.createElement("details");
    viewOptions.className = "review-view-options";
    const viewSummary = document.createElement("summary");
    viewSummary.textContent = "View options";
    viewOptions.append(viewSummary, balance);
    toolbar.append(viewOptions);
    const layout = document.createElement("div");
    layout.className = "math-notes-layout";
    content.append(layout);
    const original = document.createElement("section");
    original.className = "math-notes-original";
    original.setAttribute("aria-label", "Original");
    const originalHeading = document.createElement("h3");
    originalHeading.textContent = "Original";
    const originalHint = document.createElement("span");
    originalHint.textContent = "Select a region to compare";
    originalHeading.append(originalHint);
    original.append(originalHeading);
    const viewerMount = document.createElement("div");
    viewerMount.className = "math-notes-viewer";
    original.append(viewerMount);
    this.viewer = new PageViewer({
      mount: viewerMount,
      imageUrl: getPageRasterUrl(this.page.page_id),
      width: Math.max(1, this.page.width),
      height: Math.max(1, this.page.height),
      onSelect: (bbox) => void this.recognize(bbox),
    });
    const zoom = document.createElement("label");
    zoom.textContent = "Zoom ";
    const range = document.createElement("input");
    range.type = "range";
    range.min = "100";
    range.max = "200";
    range.step = "10";
    range.value = "100";
    range.setAttribute("aria-label", "Original zoom");
    range.oninput = () => this.viewer?.setZoom(Number(range.value));
    zoom.append(range);
    original.append(
      zoom,
      button("Fit to width", () => {
        range.value = "100";
        this.viewer?.setZoom(100);
      }),
    );
    // A numeric alternative makes crop creation possible without precise dragging.
    const manual = document.createElement("details");
    const manualTitle = document.createElement("summary");
    manualTitle.textContent = "Select crop by coordinates";
    manual.append(manualTitle);
    const form = document.createElement("form");
    form.className = "crop-coordinate-form";
    const fields = ["x", "y", "width", "height"].map((name, index) => {
      const label = document.createElement("label");
      label.textContent = name;
      const input = document.createElement("input");
      input.type = "number";
      input.required = true;
      input.min = index < 2 ? "0" : "3";
      input.max = String(index % 2 === 0 ? this.page.width : this.page.height);
      input.value = index < 2 ? "0" : "100";
      label.append(input);
      form.append(label);
      return input;
    });
    const submit = document.createElement("button");
    submit.textContent = "Convert crop";
    submit.type = "submit";
    form.append(submit);
    form.onsubmit = (event) => {
      event.preventDefault();
      const [x, y, width, height] = fields.map((field) => Number(field.value));
      if (x + width > this.page.width || y + height > this.page.height) {
        this.status.textContent = "The crop must fit inside the original page.";
        return;
      }
      void this.recognize({ x, y, width, height });
    };
    manual.append(form);
    original.append(manual);
    const side = document.createElement("section");
    side.className = "math-notes-side";
    side.setAttribute("aria-label", "Result");
    const title = document.createElement("h3");
    title.textContent = "Result";
    const resultHint = document.createElement("span");
    resultHint.textContent = "Your editable notes";
    title.append(resultHint);
    this.queueMount = document.createElement("div");
    this.editorMount = document.createElement("div");
    this.editorMount.textContent =
      "Select a region to review, or convert this page.";
    this.status = document.createElement("p");
    this.status.className = "math-notes-status";
    this.status.dataset.testid = "math-notes-status";
    this.status.setAttribute("role", "status");
    this.saveBtn = button(
      "Save review",
      () => void this.saveCurrent(),
      "math-notes-save",
    );
    this.saveBtn.disabled = true;
    const next = button("Next region", () => {
      const index = this.regions.findIndex(
        (region) => region.region_id === this.activeRegionId,
      );
      const region = this.regions[index + 1] ?? this.regions[0];
      if (region) this.selectRegion(region.region_id);
    });
    const actions = document.createElement("div");
    actions.className = "review-actions";
    actions.append(this.saveBtn, next);
    const options = document.createElement("details");
    const optionsTitle = document.createElement("summary");
    optionsTitle.textContent = "Region options and training consent";
    options.append(optionsTitle);
    const checkbox = (
      text: string,
      testId: string,
      change: (checked: boolean) => void,
    ) => {
      const label = document.createElement("label");
      label.className = "math-notes-consent";
      const input = document.createElement("input");
      input.type = "checkbox";
      input.dataset.testid = testId;
      input.disabled = true;
      input.onchange = () => change(input.checked);
      label.append(input, document.createTextNode(text));
      options.append(label);
      return input;
    };
    this.exclusion = checkbox(
      "Exclude this region from exports and training",
      "math-notes-excluded",
      (checked) => {
        const draft = this.draft();
        if (!draft) return;
        draft.excluded = checked;
        if (checked) {
          draft.training = false;
          this.consent.checked = false;
        }
        this.consent.disabled = checked;
        this.markDirty();
      },
    );
    this.consent = checkbox(
      "Add this reviewed crop to my training set",
      "math-notes-consent",
      (checked) => {
        const draft = this.draft();
        if (draft) {
          draft.training = checked;
          this.markDirty();
        }
      },
    );
    options.append(
      button("Copy unsaved corrections", () => {
        void navigator.clipboard
          .writeText(
            [...this.drafts.values()]
              .filter((draft) => draft.dirty)
              .map((draft) => draft.text)
              .join("\n\n"),
          )
          .then(() => {
            this.status.textContent = "Unsaved corrections copied.";
          })
          .catch(() => {
            this.status.textContent =
              "Clipboard unavailable. Select and copy the text in the correction field.";
          });
      }),
      button("Discard drafts and convert again", () => {
        if (!isAvailable("recognition")) {
          this.status.textContent =
            "Recognition is unavailable. Your corrections are retained.";
          return;
        }
        if (
          this.busy ||
          !window.confirm(
            "Discard unsaved corrections and convert this page again? Copy any corrections you want to keep first.",
          )
        )
          return;
        this.drafts.clear();
        void this.recognize();
      }),
    );
    this.reloadButton = button(
      "Compare saved review",
      () => void this.compareSaved(),
    );
    this.reloadButton.hidden = true;
    side.append(
      title,
      this.queueMount,
      this.editorMount,
      this.status,
      this.reloadButton,
      actions,
      options,
    );
    layout.append(original, side);
    this.status.textContent = "Loading saved regions…";
    try {
      const regions = await listPageRegions(this.page.page_id);
      if (this.disposed || generation !== this.generation) return;
      this.regions = regions ?? [];
      this.refreshQueue();
      this.status.textContent = this.hasUnsavedChanges()
        ? "Changes not saved."
        : "Select a region, or convert this page.";
    } catch (error) {
      if (!this.disposed && generation === this.generation)
        this.status.textContent = `Could not load regions. ${error instanceof Error ? error.message : "Try reopening this page."}`;
    }
    // Commit the complete review together instead of shifting visible controls as regions arrive.
    if (!this.disposed && generation === this.generation)
      this.mount.replaceChildren(content);
  }
  private refreshQueue(): void {
    new ReviewQueue(this.queueMount).render(
      this.regions.map((region) => ({
        id: region.region_id,
        kind: region.content_type.includes("math") ? "math" : "text",
        preview:
          this.drafts.get(region.region_id)?.text ??
          region.reviewed_text ??
          region.prediction_text ??
          "Unreviewed region",
        unresolved: region.unresolved,
      })),
      (id) => this.selectRegion(id),
    );
    const count = this.regions.filter((region) => region.unresolved).length;
    this.queueMount.setAttribute("aria-label", `${count} regions need review`);
    const summary = document.createElement("p");
    summary.className = "muted";
    summary.textContent = `${count} need review · ${this.regions.length} regions`;
    this.queueMount.prepend(summary);
    for (const item of this.queueMount.querySelectorAll<HTMLElement>(
      "[data-region-id]",
    ))
      item.setAttribute(
        "aria-pressed",
        String(item.dataset.regionId === this.activeRegionId),
      );
    this.viewer?.setRegions(this.regions, this.activeRegionId, (id) =>
      this.selectRegion(id),
    );
  }
  private selectRegion(id: string): void {
    if (this.busy) return;
    const region = this.regions.find((item) => item.region_id === id);
    if (!region) return;
    if (!this.drafts.has(id))
      this.drafts.set(id, {
        region,
        text: region.reviewed_text ?? region.prediction_text ?? "",
        excluded: region.excluded,
        training: false,
        dirty: false,
      });
    this.activeRegionId = id;
    const draft = this.drafts.get(id)!;
    this.exclusion.checked = draft.excluded;
    this.exclusion.disabled = false;
    this.consent.checked = draft.training;
    this.consent.disabled = draft.excluded;
    this.saveBtn.disabled = false;
    new MathEditor({
      mount: this.editorMount,
      latex: draft.text,
      plainText: region.content_type === "text",
      onChange: (text) => {
        draft.text = text;
        this.markDirty();
      },
    });
    const crop =
      this.editorMount.querySelector<HTMLImageElement>(".math-editor-crop");
    // Display the original using a clipped image, never recolor or rewrite source pixels.
    if (crop) {
      const [x, y, width, height] = region.bbox;
      const container = document.createElement("div");
      container.className = "selected-source-crop";
      container.style.aspectRatio = `${width} / ${height}`;
      crop.replaceWith(container);
      const image = document.createElement("img");
      image.src = getPageRasterUrl(this.page.page_id);
      image.alt = "Selected original handwriting region";
      image.style.width = `${(this.page.width / width) * 100}%`;
      image.style.maxWidth = "none";
      image.style.left = `${(-x / width) * 100}%`;
      image.style.top = `${(-y / height) * 100}%`;
      container.append(image);
    }
    this.status.textContent = draft.dirty
      ? "Changes not saved."
      : region.truncated
        ? "Recognition incomplete. Check the original before saving."
        : "Review this region. Save review keeps your correction; training is optional.";
    this.refreshQueue();
    this.options.onNavigate?.(this.page.page_id, id);
  }
  private async save(draft: Draft): Promise<void> {
    // Capture the submitted state so edits cannot be marked saved by an older request.
    const text = draft.text,
      excluded = draft.excluded,
      training = draft.training;
    const saved = await saveRegionReview(
      draft.region.region_id,
      text,
      excluded,
      draft.region.prediction_id ?? null,
      {
        expectedVersion: draft.region.review_version ?? null,
        includeInTraining: training && !excluded,
      },
    );
    draft.region = {
      ...draft.region,
      reviewed_text: text,
      excluded,
      review_version: saved.version,
      unresolved: false,
    };
    draft.dirty = draft.text !== text || draft.excluded !== excluded;
    draft.training = false;
    this.activeDocumentRevisionId = saved.document_revision_id;
    this.regions = this.regions.map((region) =>
      region.region_id === draft.region.region_id ? draft.region : region,
    );
  }
  private async saveCurrent(): Promise<void> {
    const draft = this.draft();
    if (!draft || this.busy) return;
    this.busy = true;
    this.saveBtn.disabled = true;
    try {
      await this.save(draft);
      this.status.textContent = draft.dirty
        ? "Changes not saved."
        : "Review saved. Training consent applies only to the selected crop.";
      this.consent.checked = false;
      this.reloadButton.hidden = true;
      this.refreshQueue();
    } catch (error) {
      this.showSaveError(error);
    } finally {
      this.busy = false;
      this.saveBtn.disabled = false;
    }
  }
  private showSaveError(error: unknown): void {
    this.status.textContent =
      error instanceof ApiError && error.status === 401
        ? "Your session expired. Sign in again in Settings, then retry saving. Your correction is still here."
        : error instanceof ApiError &&
            (error.status === 409 || error.status === 412)
          ? `The saved review or source changed. Your correction is still here. Compare the saved review before retrying. ${error.message}`
          : `Review could not be saved. Your correction is still here. ${error instanceof Error ? error.message : "Try again."}`;
    this.reloadButton.hidden = false;
  }
  private async compareSaved(): Promise<void> {
    const draft = this.draft();
    if (!draft) return;
    try {
      const fresh = (await listPageRegions(this.page.page_id)).find(
        (region) => region.region_id === draft.region.region_id,
      );
      if (!fresh) {
        this.status.textContent =
          "This region no longer exists. Your correction is still visible; copy it before converting again.";
        return;
      }
      const existing = this.editorMount.querySelector(".saved-comparison");
      existing?.remove();
      const comparison = document.createElement("div");
      comparison.className = "saved-comparison";
      const title = document.createElement("h4");
      title.textContent = "Last saved review";
      const text = document.createElement("pre");
      text.textContent =
        fresh.reviewed_text ?? fresh.prediction_text ?? "No saved text.";
      comparison.append(
        title,
        text,
        button("Use latest version and retry my correction", () => {
          if (
            fresh.prediction_id !== draft.region.prediction_id ||
            fresh.crop_revision !== draft.region.crop_revision
          ) {
            this.status.textContent =
              "Source changed — convert again. Copy your correction before reconverting.";
            return;
          }
          draft.region = fresh;
          void this.saveCurrent();
        }),
      );
      this.editorMount.append(comparison);
    } catch (error) {
      this.showSaveError(error);
    }
  }
  private async recognize(bbox?: {
    x: number;
    y: number;
    width: number;
    height: number;
  }): Promise<void> {
    if (this.busy || this.disposed) return;
    if (!isAvailable("recognition")) {
      this.status.textContent =
        "Recognition is unavailable. Saved regions remain editable.";
      return;
    }
    if (this.hasUnsavedChanges()) {
      this.status.textContent =
        "Save your reviews before converting again. If the source changed, copy your corrections and use “Discard drafts and convert again” in Region options.";
      return;
    }
    this.busy = true;
    const generation = this.generation;
    this.status.textContent = "Preparing recognition…";
    try {
      const created = bbox
        ? await createPageRegion(
            this.page.page_id,
            bbox,
            this.recognitionMode === "prose" ? "text" : "math_display",
            this.page.coordinate_space,
          )
        : null;
      if (this.disposed) return;
      const job = await createRecognitionJob(this.page.page_id, {
        ...(created
          ? { region_ids: [created.region_id] }
          : { auto_layout: true }),
        mode: this.recognitionMode,
      });
      const finished = await waitForRecognitionJob(
        job.job_id,
        (progress) => {
          if (!this.disposed)
            this.status.textContent =
              progress.progress_total > 0
                ? `Converting… ${progress.progress_current}/${progress.progress_total}`
                : "Converting…";
        },
        60_000,
        this.controller.signal,
      );
      const regions = await listPageRegions(this.page.page_id);
      if (this.disposed || generation !== this.generation) return;
      this.regions = regions;
      this.drafts.clear();
      this.activeDocumentRevisionId = null;
      this.activeRegionId = null;
      this.busy = false;
      this.refreshQueue();
      if (created) this.selectRegion(created.region_id);
      this.status.textContent = ["failed", "timeout", "no_text"].includes(
        finished.state,
      )
        ? finished.error_message ||
          "Recognition did not return text. Select a region and correct it manually."
        : "Ready to review. Select a region to begin.";
    } catch (error) {
      if (!this.disposed)
        this.status.textContent = `Conversion failed. ${error instanceof Error ? error.message : "Try again."}`;
    } finally {
      this.busy = false;
    }
  }
  private openExport(): void {
    if (!isAvailable("export")) {
      this.status.textContent = "Export is currently unavailable.";
      return;
    }
    if (this.mount.querySelector("dialog[open]")) return;
    this.mount.querySelector("dialog")?.remove();
    const dialog = document.createElement("dialog");
    dialog.className = "export-dialog";
    dialog.setAttribute("aria-label", "Export notebook");
    const heading = document.createElement("h2");
    heading.textContent = "Export notebook";
    const note = document.createElement("p");
    note.textContent = this.hasUnsavedChanges()
      ? "You have unsaved corrections. Save them with your export, or export the last saved version."
      : "Exports use the saved document revision. Excluded regions are omitted.";
    const context = document.createElement("p");
    context.className = "muted";
    context.textContent = `Scope: entire notebook · ${this.activeDocumentRevisionId ? `Saved revision ${this.activeDocumentRevisionId}` : "Latest saved revision"}`;
    const select = document.createElement("select");
    select.setAttribute("aria-label", "Export format");
    for (const format of formats)
      select.add(
        new Option(
          format === "latex_bundle"
            ? "LaTeX bundle (.zip)"
            : format === "latex"
              ? "LaTeX"
              : format[0].toUpperCase() + format.slice(1),
          format,
        ),
      );
    try {
      const last = localStorage.getItem("notes-export-format") as Format;
      if (formats.includes(last)) select.value = last;
    } catch {
      /* Default remains Markdown. */
    }
    const status = document.createElement("p");
    status.setAttribute("role", "status");
    const run = async (saveDrafts: boolean) => {
      if (this.busy) return;
      this.busy = true;
      const controls = dialog.querySelectorAll<HTMLButtonElement>("button");
      controls.forEach((control) => (control.disabled = true));
      try {
        if (saveDrafts)
          for (const draft of this.drafts.values())
            if (draft.dirty) await this.save(draft);
        const format = select.value as Format;
        const result = await exportDocument(
          this.options.documentId,
          format,
          this.activeDocumentRevisionId,
        );
        const blob =
          result instanceof Blob
            ? result
            : new Blob([result.text], {
                type: format === "html" ? "text/html" : "text/plain",
              });
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = `notes.${format === "markdown" ? "md" : format === "latex" ? "tex" : format === "latex_bundle" ? "zip" : format}`;
        anchor.click();
        window.setTimeout(() => URL.revokeObjectURL(url), 1000);
        try {
          localStorage.setItem("notes-export-format", format);
        } catch {
          /* Export still succeeds. */
        }
        status.textContent = "Exported the saved revision.";
        this.status.textContent = this.hasUnsavedChanges()
          ? "Exported last saved version. Changes not saved."
          : "Review saved and exported.";
      } catch (error) {
        status.textContent = `Export failed. ${error instanceof Error ? error.message : "Try again."}`;
        this.showSaveError(error);
      } finally {
        this.busy = false;
        controls.forEach((control) => (control.disabled = false));
        this.refreshQueue();
      }
    };
    dialog.append(
      heading,
      note,
      context,
      select,
      status,
      button("Save and export", () => void run(true), "math-notes-export-save"),
      button(
        "Export last saved version",
        () => void run(false),
        "math-notes-export-saved",
      ),
      button("Close", () => dialog.close()),
    );
    this.mount.append(dialog);
    dialog.showModal();
  }
}
