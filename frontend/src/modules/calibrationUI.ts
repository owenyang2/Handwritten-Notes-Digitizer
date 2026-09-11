// Calibration UI module.
//
// Responsibility: present the optional calibration flow — 3 to 10 prompt
// sentences (each 1-200 chars) for the user to handwrite and submit, feeding
// the same personalization pipeline as inline corrections (Req 9).
//
// Task 19.1 — full implementation of calibration UI.

import type { CalibrationPrompt } from "../types.js";

/** Inclusive bounds on the number of calibration prompts (Req 9.2). */
export const MIN_PROMPTS = 3;
export const MAX_PROMPTS = 10;

/** Inclusive bounds on a single prompt's length in characters (Req 9.2). */
export const MIN_PROMPT_CHARS = 1;
export const MAX_PROMPT_CHARS = 200;

export interface CalibrationUIOptions {
  mount: HTMLElement;
  /**
   * Called when the user submits a handwriting image for a prompt.
   * Returns a promise so the UI can show progress.
   */
  onSubmitSample?: (promptId: string, file: File) => Promise<void> | void;
}

export class CalibrationUI {
  private readonly mount: HTMLElement;
  private onSubmitSample?: (promptId: string, file: File) => Promise<void> | void;

  constructor(options: CalibrationUIOptions) {
    this.mount = options.mount;
    this.onSubmitSample = options.onSubmitSample;
  }

  /** Register an updated onSubmitSample handler (allows wiring after construction). */
  setOnSubmitSample(
    handler: (promptId: string, file: File) => Promise<void> | void,
  ): void {
    this.onSubmitSample = handler;
  }

  /**
   * Render the prompt list and per-prompt upload controls.
   *
   * Req 9.2: between 3 and 10 prompts, each 1–200 characters.
   * Req 9.3: each prompt gets its own file input.
   * Req 9.4: invalid submissions are reported inline.
   */
  render(prompts: CalibrationPrompt[]): void {
    this.mount.replaceChildren();

    const section = document.createElement("section");
    section.className = "calibration-section";
    section.setAttribute("data-testid", "calibration-section");
    section.setAttribute("aria-label", "Optional calibration flow");

    const heading = document.createElement("h2");
    heading.textContent = "Optional calibration (fast-track learning)";
    heading.className = "calibration-heading";
    section.appendChild(heading);

    const description = document.createElement("p");
    description.className = "calibration-description";
    description.textContent =
      "Write each prompted sentence by hand and upload a photo. Each accepted sample " +
      "is saved as a labeled training example (image + known text), exactly like " +
      "correcting a misread word on your own notes. Submit as many as you like — " +
      "when you reach the example threshold shown above, personal training starts automatically. " +
      "You can skip this entirely and learn from corrections alone.";
    section.appendChild(description);

    const list = document.createElement("ol");
    list.className = "calibration-prompt-list";
    list.setAttribute("data-testid", "calibration-prompt-list");

    for (const prompt of prompts) {
      list.appendChild(this.renderPromptItem(prompt));
    }

    section.appendChild(list);
    this.mount.appendChild(section);
  }

  // ---------------------------------------------------------------------------
  // Private helpers
  // ---------------------------------------------------------------------------

  private renderPromptItem(prompt: CalibrationPrompt): HTMLElement {
    const item = document.createElement("li");
    item.className = "calibration-prompt-item";
    item.setAttribute("data-testid", "calibration-prompt-item");
    item.setAttribute("data-prompt-id", prompt.id);

    // Prompt text (bold).
    const promptText = document.createElement("p");
    promptText.className = "calibration-prompt-text";
    promptText.setAttribute("data-testid", "calibration-prompt-text");
    promptText.textContent = prompt.text;
    item.appendChild(promptText);

    // File input for the handwriting image.
    const fileInput = document.createElement("input");
    fileInput.type = "file";
    fileInput.accept = "image/png,image/jpeg";
    fileInput.className = "calibration-file-input";
    fileInput.setAttribute("data-testid", "calibration-file-input");
    fileInput.setAttribute(
      "aria-label",
      `Upload handwriting for: "${prompt.text}"`,
    );
    item.appendChild(fileInput);

    // Status / feedback text.
    const statusEl = document.createElement("p");
    statusEl.className = "calibration-prompt-status";
    statusEl.setAttribute("data-testid", "calibration-prompt-status");
    statusEl.setAttribute("aria-live", "polite");
    statusEl.textContent = "";
    item.appendChild(statusEl);

    // Submit button for this prompt.
    const submitBtn = document.createElement("button");
    submitBtn.type = "button";
    submitBtn.className = "calibration-submit-btn";
    submitBtn.setAttribute("data-testid", "calibration-submit-btn");
    submitBtn.textContent = "Submit sample";
    submitBtn.disabled = true;
    item.appendChild(submitBtn);

    let selectedFile: File | null = null;

    fileInput.addEventListener("change", () => {
      const file = fileInput.files?.[0] ?? null;
      if (!file) {
        selectedFile = null;
        submitBtn.disabled = true;
        statusEl.textContent = "";
        return;
      }
      if (file.size === 0) {
        statusEl.textContent = "⚠ File is empty.";
        statusEl.className = "calibration-prompt-status calibration-prompt-status--error";
        selectedFile = null;
        submitBtn.disabled = true;
        return;
      }
      if (file.size > 10 * 1024 * 1024) {
        statusEl.textContent = "⚠ File exceeds 10 MB.";
        statusEl.className = "calibration-prompt-status calibration-prompt-status--error";
        selectedFile = null;
        submitBtn.disabled = true;
        return;
      }
      selectedFile = file;
      statusEl.textContent = `Selected: ${file.name}`;
      statusEl.className = "calibration-prompt-status calibration-prompt-status--info";
      submitBtn.disabled = false;
    });

    submitBtn.addEventListener("click", () => {
      if (!selectedFile || !this.onSubmitSample) return;
      const file = selectedFile;
      submitBtn.disabled = true;
      statusEl.textContent = "Uploading…";
      statusEl.className = "calibration-prompt-status calibration-prompt-status--info";

      const result = this.onSubmitSample(prompt.id, file);
      const handleSuccess = () => {
        statusEl.textContent = "✓ Sample saved as a training example.";
        statusEl.className = "calibration-prompt-status calibration-prompt-status--success";
        item.classList.add("calibration-prompt-item--done");
        // Don't re-enable button: one submission per prompt session.
      };
      const handleError = (err: unknown) => {
        const message = err instanceof Error ? err.message : "Submission failed.";
        statusEl.textContent = `Error: ${message}`;
        statusEl.className = "calibration-prompt-status calibration-prompt-status--error";
        submitBtn.disabled = false;
      };

      if (result instanceof Promise) {
        result.then(handleSuccess).catch(handleError);
      } else {
        handleSuccess();
      }
    });

    return item;
  }
}
