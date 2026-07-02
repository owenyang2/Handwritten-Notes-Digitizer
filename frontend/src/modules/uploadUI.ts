// Upload UI module.
//
// Responsibility: let an authenticated user select and submit a PNG/JPEG image
// of handwritten notes for recognition. Full validation is enforced by the
// backend Recognition_Service (Req 1.1-1.6); this module provides the client
// surface and a lightweight pre-submit hint.
//
// Task 19.1 — full implementation of upload → recognize flow.

/** Formats the upload UI accepts; the backend remains the source of truth. */
export const ACCEPTED_IMAGE_TYPES = ["image/png", "image/jpeg"] as const;

/** Maximum accepted upload size in bytes (10 MB), per Req 1.3. */
export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;

export interface UploadUIOptions {
  /** Container element the upload UI mounts into. */
  mount: HTMLElement;
  /**
   * Called when the user submits a file for recognition.
   * Returns a promise so the caller can disable the button while processing.
   */
  onSubmit?: (file: File) => Promise<void> | void;
}

export class UploadUI {
  private readonly mount: HTMLElement;
  private onSubmit?: (file: File) => Promise<void> | void;

  private fileInput!: HTMLInputElement;
  private dropZone!: HTMLElement;
  private submitButton!: HTMLButtonElement;
  private statusEl!: HTMLElement;
  private selectedFile: File | null = null;

  constructor(options: UploadUIOptions) {
    this.mount = options.mount;
    this.onSubmit = options.onSubmit;
  }

  /** Register an updated onSubmit handler (allows wiring after construction). */
  setOnSubmit(handler: (file: File) => Promise<void> | void): void {
    this.onSubmit = handler;
  }

  /** Render the upload control into the mount element. */
  render(): void {
    this.mount.innerHTML = "";

    const wrapper = document.createElement("div");
    wrapper.className = "upload-wrapper";
    wrapper.setAttribute("data-testid", "upload-wrapper");

    // --- Drop zone ---
    this.dropZone = document.createElement("div");
    this.dropZone.className = "upload-drop-zone";
    this.dropZone.setAttribute("data-testid", "upload-drop-zone");
    this.dropZone.setAttribute("role", "button");
    this.dropZone.setAttribute("tabindex", "0");
    this.dropZone.setAttribute(
      "aria-label",
      "Upload a handwriting image. Click or drag a PNG/JPEG here.",
    );
    this.dropZone.textContent =
      "Drag a PNG / JPEG image here, or click to browse";

    // Hidden file input triggered by drop zone click / Enter.
    this.fileInput = document.createElement("input");
    this.fileInput.type = "file";
    this.fileInput.accept = ACCEPTED_IMAGE_TYPES.join(",");
    this.fileInput.setAttribute("aria-hidden", "true");
    this.fileInput.setAttribute("data-testid", "upload-file-input");
    this.fileInput.style.display = "none";

    this.dropZone.addEventListener("click", () => this.fileInput.click());
    this.dropZone.addEventListener("keydown", (e: KeyboardEvent) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        this.fileInput.click();
      }
    });

    // Drag-and-drop handling.
    this.dropZone.addEventListener("dragover", (e: DragEvent) => {
      e.preventDefault();
      this.dropZone.classList.add("upload-drop-zone--active");
    });
    this.dropZone.addEventListener("dragleave", () => {
      this.dropZone.classList.remove("upload-drop-zone--active");
    });
    this.dropZone.addEventListener("drop", (e: DragEvent) => {
      e.preventDefault();
      this.dropZone.classList.remove("upload-drop-zone--active");
      const files = e.dataTransfer?.files;
      if (files && files.length > 0) {
        this.handleFileSelected(files[0]);
      }
    });

    this.fileInput.addEventListener("change", () => {
      if (this.fileInput.files && this.fileInput.files.length > 0) {
        this.handleFileSelected(this.fileInput.files[0]);
      }
    });

    // --- Status / filename display ---
    this.statusEl = document.createElement("p");
    this.statusEl.className = "upload-status";
    this.statusEl.setAttribute("data-testid", "upload-status");
    this.statusEl.setAttribute("aria-live", "polite");
    this.statusEl.textContent = "";

    // --- Submit button ---
    this.submitButton = document.createElement("button");
    this.submitButton.type = "button";
    this.submitButton.className = "upload-submit-btn";
    this.submitButton.setAttribute("data-testid", "upload-submit-btn");
    this.submitButton.textContent = "Recognize";
    this.submitButton.disabled = true;

    this.submitButton.addEventListener("click", () => {
      void this.handleSubmit();
    });

    wrapper.appendChild(this.dropZone);
    wrapper.appendChild(this.fileInput);
    wrapper.appendChild(this.statusEl);
    wrapper.appendChild(this.submitButton);
    this.mount.appendChild(wrapper);
  }

  // ---------------------------------------------------------------------------
  // Private helpers
  // ---------------------------------------------------------------------------

  private handleFileSelected(file: File): void {
    // Client-side hints only (backend is the source of truth for rejection).
    if (!ACCEPTED_IMAGE_TYPES.includes(file.type as (typeof ACCEPTED_IMAGE_TYPES)[number])) {
      this.showStatus(
        `⚠ "${file.name}" is not a PNG or JPEG. Please choose a different file.`,
        "error",
      );
      this.selectedFile = null;
      this.submitButton.disabled = true;
      return;
    }
    if (file.size > MAX_UPLOAD_BYTES) {
      this.showStatus(
        `⚠ "${file.name}" is larger than 10 MB. Please choose a smaller file.`,
        "error",
      );
      this.selectedFile = null;
      this.submitButton.disabled = true;
      return;
    }
    if (file.size === 0) {
      this.showStatus(`⚠ "${file.name}" is empty. Please choose a valid image.`, "error");
      this.selectedFile = null;
      this.submitButton.disabled = true;
      return;
    }

    this.selectedFile = file;
    this.showStatus(`Selected: ${file.name}`, "info");
    this.submitButton.disabled = false;
  }

  private async handleSubmit(): Promise<void> {
    if (!this.selectedFile || !this.onSubmit) return;

    this.submitButton.disabled = true;
    this.showStatus("Uploading…", "info");

    try {
      await this.onSubmit(this.selectedFile);
      // After a successful submit the orchestrating code (main.ts) will call
      // render() on the Editor; we reset to allow another upload.
      this.selectedFile = null;
      this.fileInput.value = "";
      this.showStatus("Upload complete.", "success");
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Upload failed.";
      this.showStatus(`Error: ${message}`, "error");
      this.submitButton.disabled = false;
    }
  }

  private showStatus(
    message: string,
    type: "info" | "error" | "success",
  ): void {
    this.statusEl.textContent = message;
    this.statusEl.className = `upload-status upload-status--${type}`;
    this.dropZone.textContent =
      type === "error"
        ? "Drag a PNG / JPEG image here, or click to browse"
        : this.selectedFile
          ? `Selected: ${this.selectedFile.name}`
          : "Drag a PNG / JPEG image here, or click to browse";
  }
}
