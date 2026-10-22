// Editor module.
//
// Responsibility: display recognized text in reading order alongside the
// original image and support inline editing of any word (Req 3, Req 4).
// The reading-order helper is a pure function used for display ordering and
// property testing (Property 7).

import type { RecognitionResult, RecognizedWord, Region } from "../types.js";

/** Maximum corrected text length accepted per recognized word (Req 4.1). */
export const MAX_WORD_CHARS = 10_000;

/**
 * Callback fired when a confirmed edit changes a word's text (Req 4.2, 4.3).
 *
 * @param wordIndex    The word's index within its line.
 * @param lineIndex    The line's index within the result.
 * @param originalText The recognized text before any correction.
 * @param correctedText The confirmed corrected text (guaranteed ≠ originalText).
 */
export type CorrectionCallback = (
  wordIndex: number,
  lineIndex: number,
  originalText: string,
  correctedText: string,
) => Promise<unknown> | unknown;

export type LineCorrectionCallback = (
  lineIndex: number,
  region: Region,
  originalText: string,
  correctedText: string,
) => Promise<unknown> | unknown;

export interface LineReviewPayload {
  line_index: number;
  reviewed_text: string;
  excluded: boolean;
  region: Region;
}

/** Persist a line's review/exclusion state independently of training data. */
export type ReviewChangeCallback = (
  review: LineReviewPayload,
) => Promise<unknown> | unknown;

/**
 * Return the recognized words in reading order: lines ordered top-to-bottom by
 * line index, and words within each line ordered left-to-right by word index
 * (Req 3.2). Pure function so it can be property-tested independently.
 */
export function readingOrder(result: RecognitionResult): RecognizedWord[] {
  const lines = [...result.lines].sort((a, b) => a.index - b.index);
  const ordered: RecognizedWord[] = [];
  for (const line of lines) {
    const words = [...line.words].sort((a, b) => a.index - b.index);
    ordered.push(...words);
  }
  return ordered;
}

export interface EditorOptions {
  mount: HTMLElement;
  /** Called only when a confirmed edit changes the word's text (Req 4.3, 4.4). */
  onCorrection?: CorrectionCallback;
  /** Called when a reviewed line crop should be saved as training data. */
  onLineCorrection?: LineCorrectionCallback;
  /** Called whenever a line's persisted review state changes. */
  onReviewChange?: ReviewChangeCallback;
}

export class Editor {
  private readonly mount: HTMLElement;
  private onCorrection?: CorrectionCallback;
  private onLineCorrection?: LineCorrectionCallback;
  private onReviewChange?: ReviewChangeCallback;
  private lineOrder: number[] = [];
  private currentLineTexts = new Map<number, string>();
  private currentLineRegions = new Map<number, Region>();
  private currentWordTexts = new Map<string, string>();
  private reviewSaveQueues = new Map<number, Promise<unknown>>();
  private ignoredLineIndices = new Set<number>();
  private lineLevelEdits = false;
  private sortMode: "reading-order" | "confidence" = "reading-order";
  private currentResult?: RecognitionResult;
  private currentImageUrl?: string;
  private stopDrag?: () => void;

  constructor(options: EditorOptions) {
    this.mount = options.mount;
    this.onCorrection = options.onCorrection;
    this.onLineCorrection = options.onLineCorrection;
    this.onReviewChange = options.onReviewChange;
  }

  dispose(): void {
    this.stopDrag?.();
    this.stopDrag = undefined;
  }

  /** Update the correction callback after construction (used by main.ts wiring). */
  setOnCorrection(handler: CorrectionCallback): void {
    this.onCorrection = handler;
  }

  /** Update the line-level correction callback after construction. */
  setOnLineCorrection(handler: LineCorrectionCallback): void {
    this.onLineCorrection = handler;
  }

  /** Update the persisted review-state callback after construction. */
  setOnReviewChange(handler: ReviewChangeCallback): void {
    this.onReviewChange = handler;
  }

  /** Wait for any in-flight line-review writes before an export/reload. */
  async flushReviewSaves(): Promise<void> {
    while (this.reviewSaveQueues.size > 0) {
      const pending = [...this.reviewSaveQueues.values()];
      await Promise.all(pending.map((save) => save.catch(() => undefined)));
    }
  }

  /** True when the user edited a line that has no safe word-region alignment. */
  hasLocalLineEdits(): boolean {
    return this.lineLevelEdits;
  }

  /** Return current editor text in reading order for local export. */
  getExportText(format: "plain" | "markdown" | "pdf"): string {
    const lines = this.lineOrder
      .filter((lineIndex) => !this.ignoredLineIndices.has(lineIndex))
      .map((lineIndex) => (this.currentLineTexts.get(lineIndex) ?? "").trim());
    if (format === "plain") {
      return lines.join("\n");
    }

    const paragraphs: string[][] = [];
    let current: string[] = [];
    for (const line of lines) {
      if (line === "") {
        if (current.length > 0) {
          paragraphs.push(current);
          current = [];
        }
      } else {
        current.push(line);
      }
    }
    if (current.length > 0) paragraphs.push(current);
    return paragraphs.map((paragraph) => paragraph.join("\n")).join("\n\n");
  }

  /** Snapshot of line reviews to persist on the backend before export/reload. */
  getReviewPayload(): LineReviewPayload[] {
    return this.lineOrder.map((lineIndex) => {
      const line = this.currentResult?.lines.find((item) => item.index === lineIndex);
      return {
        line_index: lineIndex,
        reviewed_text: this.currentLineTexts.get(lineIndex) ?? line?.text ?? "",
        excluded: this.ignoredLineIndices.has(lineIndex),
        region:
          this.currentLineRegions.get(lineIndex) ??
          line?.region ??
          { x: 0, y: 0, width: 0, height: 0 },
      };
    });
  }

  private persistReviewChange(lineIndex: number): Promise<unknown> | unknown {
    if (!this.onReviewChange) return undefined;
    const review = this.getReviewPayload().find((item) => item.line_index === lineIndex);
    if (!review) return undefined;

    // Ignore/restore and textarea blur can happen back-to-back (for example,
    // clicking "Not text" blurs the field first). Serialize writes per line so
    // an older request can never arrive after and overwrite the newer state.
    const previous = this.reviewSaveQueues.get(lineIndex) ?? Promise.resolve();
    const next = previous
      .catch(() => undefined)
      .then(() => this.onReviewChange!(review));
    this.reviewSaveQueues.set(lineIndex, next);
    next.then(
      () => {
        if (this.reviewSaveQueues.get(lineIndex) === next) {
          this.reviewSaveQueues.delete(lineIndex);
        }
      },
      () => {
        if (this.reviewSaveQueues.get(lineIndex) === next) {
          this.reviewSaveQueues.delete(lineIndex);
        }
      },
    );
    return next;
  }

  /**
   * Render the image + recognized text and enable inline editing.
   *
   * Req 3.2: Words are rendered in reading order (lines top-to-bottom,
   *          words left-to-right) using readingOrder().
   * Req 3.3: The original image and the recognized text are both visible
   *          at the same time.
   * Req 3.4: When no text was recognized, a "no text recognized" message
   *          is shown alongside the original image.
   * Req 4.1: Each word span is double-click editable up to MAX_WORD_CHARS.
   * Req 4.2: On confirm (Enter or blur) the displayed text updates.
   * Req 4.3: onCorrection is called only when the text changed.
   * Req 4.4: If text is identical to recognized text, onCorrection is NOT called.
   */
  render(result: RecognitionResult, imageUrl: string): void {
    // Clear previous content.
    this.mount.replaceChildren();
    this.lineOrder = [];
    // Only reset text/edits if this is a new image/result, not just a re-render.
    const isNewResult = this.currentResult !== result;
    if (isNewResult) {
      this.currentLineTexts = new Map();
      this.currentLineRegions = new Map();
      this.currentWordTexts = new Map();
      this.reviewSaveQueues = new Map();
      this.ignoredLineIndices = new Set();
      this.lineLevelEdits = false;
    }
    this.currentResult = result;
    this.currentImageUrl = imageUrl;

    // Container holds the image and the text panel side by side (or stacked).
    const container = document.createElement("div");
    container.className = "editor-container";
    container.setAttribute("data-testid", "editor-container");

    // --- Image panel (always visible, satisfies Req 3.3 and 3.4) ---
    const imagePanel = document.createElement("div");
    imagePanel.className = "editor-image-panel";

    const imageStage = document.createElement("div");
    imageStage.className = "editor-image-stage";
    this.applyImageStageGeometry(imageStage, result);

    const img = document.createElement("img");
    img.src = imageUrl;
    img.alt = "Uploaded handwriting image";
    img.className = "editor-image";
    img.setAttribute("data-testid", "editor-image");
    imageStage.appendChild(img);

    const overlay = document.createElement("div");
    overlay.className = "editor-region-overlay";
    imageStage.appendChild(overlay);

    imagePanel.appendChild(imageStage);

    // --- Text panel ---
    const textPanel = document.createElement("div");
    textPanel.className = "editor-text-panel";
    textPanel.setAttribute("data-testid", "editor-text-panel");

    const status = result.status ?? (result.recognizedAnyText ? "ok" : "no_text");

    if (status === "error" || status === "timeout") {
      const errorMsg = document.createElement("p");
      errorMsg.className = `editor-recognition-${status}`;
      errorMsg.setAttribute("data-testid", "editor-recognition-error");
      errorMsg.textContent =
        result.message ??
        (status === "timeout"
          ? "Recognition timed out. Please try again with a clearer or smaller image."
          : "Recognition failed. Please check the backend logs and model setup.");
      textPanel.appendChild(errorMsg);
    } else if (!result.recognizedAnyText) {
      // Req 3.4: show "no text recognized" message alongside the image.
      const noTextMsg = document.createElement("p");
      noTextMsg.className = "editor-no-text";
      noTextMsg.setAttribute("data-testid", "editor-no-text");
      noTextMsg.textContent = "No text recognized";
      textPanel.appendChild(noTextMsg);
    } else {
      const regionState = new Map<number, Region>();
      for (const line of result.lines) {
        regionState.set(line.index, { ...line.region });
      }
      const regionBounds = this.getRegionBounds(result);
      this.renderRegionOverlay(result, overlay, regionState, regionBounds);

      const reviewSummary = document.createElement("div");
      reviewSummary.className = "review-summary";
      reviewSummary.setAttribute("data-testid", "review-summary");
      
      const summaryText = document.createElement("p");
      summaryText.textContent =
        `${result.lines.length} editable line region${result.lines.length === 1 ? "" : "s"}. ` +
        "Save reviewed lines to add clean line crops to training data.";
      reviewSummary.appendChild(summaryText);

      const sortLabel = document.createElement("label");
      sortLabel.textContent = "Sort by: ";
      sortLabel.className = "editor-sort-label";
      const sortSelect = document.createElement("select");
      sortSelect.className = "editor-sort-select";
      for (const optionSpec of [
        ["reading-order", "Reading Order"],
        ["confidence", "Lowest Confidence"],
      ] as const) {
        const option = document.createElement("option");
        option.value = optionSpec[0];
        option.textContent = optionSpec[1];
        option.selected = this.sortMode === optionSpec[0];
        sortSelect.appendChild(option);
      }
      sortSelect.addEventListener("change", (e) => {
        this.sortMode = (e.target as HTMLSelectElement).value as "reading-order" | "confidence";
        this.render(this.currentResult!, this.currentImageUrl!);
      });
      sortLabel.appendChild(sortSelect);
      reviewSummary.appendChild(sortLabel);
      textPanel.appendChild(reviewSummary);

      const textContent = document.createElement("div");
      textContent.className = "editor-words";
      textContent.setAttribute("data-testid", "editor-words");

      // Group words back into lines so we can insert line breaks.
      const lineMap = new Map<number, RecognizedWord[]>();
      for (const line of result.lines) {
        const sorted = [...line.words].sort((a, b) => a.index - b.index);
        lineMap.set(line.index, sorted);
      }
      // Calculate line confidence scores (average of word confidences, default 1.0)
      const lineConfidenceMap = new Map<number, number>();
      for (const line of result.lines) {
        let conf = 1.0;
        if (line.words.length > 0) {
          let sum = 0;
          let count = 0;
          for (const w of line.words) {
            if (w.confidence !== undefined) {
              sum += w.confidence;
              count++;
            }
          }
          if (count > 0) conf = sum / count;
        }
        lineConfidenceMap.set(line.index, conf);
      }

      // Lines sorted by selected mode
      const sortedLineIndices = [...result.lines]
        .sort((a, b) => {
          if (this.sortMode === "confidence") {
            const confA = lineConfidenceMap.get(a.index) ?? 1.0;
            const confB = lineConfidenceMap.get(b.index) ?? 1.0;
            if (confA !== confB) return confA - confB;
          }
          return a.index - b.index;
        })
        .map((l) => l.index);
      this.lineOrder = sortedLineIndices;

      // Render a <span> per word in reading order, with line breaks between lines.
      for (let li = 0; li < sortedLineIndices.length; li++) {
        const lineIdx = sortedLineIndices[li];
        const lineWords = lineMap.get(lineIdx) ?? [];
        const line = result.lines.find((l) => l.index === lineIdx);
        const recognizedLineText = line?.reviewed
          ? (line.text ?? "")
          : (line?.text?.trim() ||
            lineWords.map((word) => word.text).filter((text) => text.length > 0).join(" "));
        // A sort-mode change re-renders the same result object. Preserve any
        // text the reviewer has already typed instead of resetting it to the
        // recognition output on every render.
        if (!this.currentLineTexts.has(lineIdx)) {
          this.currentLineTexts.set(lineIdx, recognizedLineText);
        }
        if (line && !this.currentLineRegions.has(lineIdx)) {
          this.currentLineRegions.set(lineIdx, { ...line.region });
        }
        if (isNewResult) {
          if (line?.excluded) {
            this.ignoredLineIndices.add(lineIdx);
            this.lineLevelEdits = true;
          } else if (line?.reviewed) {
            this.lineLevelEdits = true;
          }
        }

        if (!line) continue;

        const block = document.createElement("div");
        block.className = line.alignmentStatus === "uncertain"
          ? "editor-line-review editor-line-review--uncertain"
          : "editor-line-review editor-line-review--aligned";
        block.setAttribute("data-testid", "editor-line-review");
        block.setAttribute("data-line-index", String(lineIdx));

        const selectReviewLine = (): void => {
          overlay.querySelectorAll(".editor-region-box--selected").forEach((el) => {
            el.classList.remove("editor-region-box--selected");
          });
          this.mount.querySelectorAll(".editor-line-review--selected").forEach((el) => {
            el.classList.remove("editor-line-review--selected");
          });
          overlay.querySelector<HTMLElement>(
            `[data-testid='editor-region-box'][data-line-index='${lineIdx}']`,
          )?.classList.add("editor-region-box--selected");
          block.classList.add("editor-line-review--selected");
        };
        block.addEventListener("pointerdown", selectReviewLine);
        block.addEventListener("focusin", selectReviewLine);

        const label = document.createElement("label");
        label.className = "editor-line-label";
        const conf = lineConfidenceMap.get(lineIdx) ?? 1.0;
        const confPercent = Math.round(conf * 100);
        label.textContent = `Line ${lineIdx + 1} review (Confidence: ${confPercent}%)`;
        block.appendChild(label);

        const textarea = document.createElement("textarea");
        textarea.className = "editor-line-textarea";
        textarea.setAttribute("data-testid", "editor-line-textarea");
        textarea.setAttribute("data-line-index", String(lineIdx));
        textarea.setAttribute("aria-label", `Recognized line ${lineIdx + 1}`);
        textarea.maxLength = MAX_WORD_CHARS;
        textarea.value = this.currentLineTexts.get(lineIdx) ?? recognizedLineText;
        if (line.alignmentReason) {
          textarea.title = line.alignmentReason;
        }
        label.appendChild(textarea);
        textarea.addEventListener("input", () => {
          this.currentLineTexts.set(lineIdx, textarea.value);
          this.lineLevelEdits = true;
        });

        // Highlight low confidence words
        const lowConfWords = lineWords.filter(w => w.confidence !== undefined && w.confidence < 0.8);
        if (lowConfWords.length > 0) {
          const warningDiv = document.createElement("div");
          warningDiv.className = "editor-line-warning";
          warningDiv.style.color = "var(--amber)";
          warningDiv.style.fontSize = "0.85rem";
          warningDiv.style.marginTop = "4px";
          const wordsStr = lowConfWords.map(w => `"${w.text}"`).join(", ");
          warningDiv.textContent = `Low confidence words: ${wordsStr}`;
          label.appendChild(warningDiv);
        }

        const regionEditor = this.buildLineRegionEditor(lineIdx, regionState);
        block.appendChild(regionEditor.element);

        const actionRow = document.createElement("div");
        actionRow.className = "editor-line-actions";

        const saveButton = document.createElement("button");
        saveButton.type = "button";
        saveButton.className = "editor-line-save";
        saveButton.setAttribute("data-testid", "editor-line-save");
        saveButton.textContent = "Save line for training";
        actionRow.appendChild(saveButton);

        const ignoreButton = document.createElement("button");
        ignoreButton.type = "button";
        ignoreButton.className = "editor-line-ignore";
        ignoreButton.setAttribute("data-testid", "editor-line-ignore");
        ignoreButton.setAttribute("aria-pressed", "false");
        ignoreButton.textContent = "Not text";
        actionRow.appendChild(ignoreButton);

        const actionStatus = document.createElement("p");
        actionStatus.className = "editor-line-action-status";
        actionStatus.setAttribute("data-testid", "editor-line-action-status");
        actionStatus.setAttribute("aria-live", "polite");

        // Persist a line edit when the reviewer leaves the field. This keeps
        // ordinary text corrections durable without issuing a request for
        // every keystroke; the explicit save/export paths still persist too.
        textarea.addEventListener("change", () => {
          Promise.resolve(this.persistReviewChange(lineIdx)).catch(() => {
            actionStatus.textContent = "Could not save this line review.";
            actionStatus.className = "editor-line-action-status editor-line-action-status--failed";
          });
        });

        saveButton.addEventListener("click", () => {
          if (!this.onLineCorrection) return;
          saveButton.disabled = true;
          actionStatus.textContent = "Saving line crop to training data...";
          actionStatus.className = "editor-line-action-status editor-line-action-status--pending";
          const correctedText = textarea.value.slice(0, MAX_WORD_CHARS);
          this.currentLineTexts.set(lineIdx, correctedText);
          let saveResult: Promise<unknown> | unknown;
          try {
            saveResult = this.onLineCorrection(
              lineIdx,
              regionEditor.getRegion(),
              recognizedLineText,
              correctedText,
            );
          } catch {
            actionStatus.textContent = "Could not save this line to training data.";
            actionStatus.className = "editor-line-action-status editor-line-action-status--failed";
            saveButton.disabled = false;
            return;
          }
          Promise.resolve(saveResult)
            .then(() => this.persistReviewChange(lineIdx))
            .then(() => {
              actionStatus.textContent = "Saved as reviewed line training data.";
              actionStatus.className = "editor-line-action-status editor-line-action-status--saved";
            })
            .catch(() => {
              actionStatus.textContent = "Could not save this line to training data.";
              actionStatus.className = "editor-line-action-status editor-line-action-status--failed";
              saveButton.disabled = false;
            });
        });

        ignoreButton.addEventListener("click", () => {
          block.classList.toggle("editor-line-review--ignored");
          const ignored = block.classList.contains("editor-line-review--ignored");
          const regionBox = overlay.querySelector<HTMLElement>(
            `[data-testid='editor-region-box'][data-line-index='${lineIdx}']`,
          );
          regionBox?.classList.toggle("editor-region-box--ignored", ignored);
          ignoreButton.setAttribute("aria-pressed", ignored ? "true" : "false");
          ignoreButton.textContent = ignored ? "Restore line" : "Not text";
          textarea.disabled = ignored;
          saveButton.disabled = ignored;
          actionStatus.textContent = ignored
            ? "Marked as not text. Excluded from export and training."
            : "";
          if (ignored) {
            this.ignoredLineIndices.add(lineIdx);
            this.currentLineTexts.set(lineIdx, "");
          } else {
            this.ignoredLineIndices.delete(lineIdx);
            this.currentLineTexts.set(lineIdx, textarea.value);
          }
          this.lineLevelEdits = true;
          Promise.resolve(this.persistReviewChange(lineIdx)).catch(() => {
            actionStatus.textContent = "Could not save this line review.";
          });
        });

        if (this.ignoredLineIndices.has(lineIdx)) {
          block.classList.add("editor-line-review--ignored");
          const regionBox = overlay.querySelector<HTMLElement>(
            `[data-testid='editor-region-box'][data-line-index='${lineIdx}']`,
          );
          regionBox?.classList.add("editor-region-box--ignored");
          ignoreButton.setAttribute("aria-pressed", "true");
          ignoreButton.textContent = "Restore line";
          textarea.disabled = true;
          saveButton.disabled = true;
        }

        block.appendChild(actionRow);
        block.appendChild(actionStatus);

        const note = document.createElement("p");
        note.className = "editor-line-note";
        note.textContent =
          line.alignmentStatus === "uncertain"
            ? (line.alignmentReason ??
              "Review this line crop before training; word boxes are unreliable here.")
            : "Line crop training is preferred. Word chips below are still available for precise corrections.";
        block.appendChild(note);

        const chips = document.createElement("div");
        chips.className = "editor-token-chips";

        const exportWords = lineWords.map(
          (word) => this.currentWordTexts.get(`${lineIdx}:${word.index}`) ?? word.text,
        );

        for (let wi = 0; wi < lineWords.length; wi++) {
          // Use the words belonging to this line directly. The review list may
          // be sorted by confidence, so a single flattened reading-order
          // array would associate chips with the wrong line after sorting.
          const word = lineWords[wi];

          // The "original" recognized text for this word slot (used for change
          // detection). We track the *latest confirmed* text so re-corrections
          // are compared to the previously confirmed value, but the
          // Correction_Pipeline always receives the original recognised text so
          // it can key the training pair on the source region correctly. We
          // keep a separate `recognizedText` that never changes after render.
          const recognizedText = word.text;
          // currentDisplayText tracks what is currently shown (may change after
          // a confirmed edit).
          const wordKey = `${lineIdx}:${word.index}`;
          let currentDisplayText = this.currentWordTexts.get(wordKey) ?? recognizedText;

          // Trailing space separates words on the same line (not after last word).
          const trailingSpace = wi < lineWords.length - 1 ? " " : "";

          const span = document.createElement("span");
          span.className = "editor-word";
          span.setAttribute("data-testid", "editor-word");
          span.setAttribute("data-word-index", String(word.index));
          span.setAttribute("data-line-index", String(lineIdx));
          span.textContent = currentDisplayText + trailingSpace;
          span.title = "Double-click to correct and save this region as training data.";

          // Capture for closures.
          const capturedWordIndex = word.index;
          const capturedLineIndex = lineIdx;
          const capturedTrailingSpace = trailingSpace;
          const capturedOnCorrection = this.onCorrection;

          /**
           * Enter edit mode: replace the span content with an <input>.
           * Req 4.1: input is capped at MAX_WORD_CHARS characters.
           */
          const enterEditMode = (): void => {
            // Avoid creating a second input if already in edit mode.
            if (span.querySelector("input")) return;

            const input = document.createElement("input");
            input.type = "text";
            input.className = "editor-word-input";
            input.setAttribute("data-testid", "editor-word-input");
            input.maxLength = MAX_WORD_CHARS;
            input.value = currentDisplayText;

            // Hide the text temporarily while input is active.
            span.textContent = capturedTrailingSpace; // keep trailing space
            span.insertBefore(input, span.firstChild);
            input.focus();
            input.select();

            /**
             * Confirm the edit: update display, fire callback if changed.
             * Req 4.2: display updates on confirm.
             * Req 4.3/4.4: callback fired only when text changed.
             */
            const confirmEdit = (): void => {
              // Guard against double-confirm (blur fires after Enter).
              if (!span.contains(input)) return;

              const newText = input.value.slice(0, MAX_WORD_CHARS);
              // Remove the input, restore text.
              span.removeChild(input);
              currentDisplayText = newText;
              this.currentWordTexts.set(wordKey, newText);
              span.textContent = newText + capturedTrailingSpace;
              exportWords[wi] = newText;
              this.currentLineTexts.set(
                lineIdx,
                exportWords.filter((text) => text.length > 0).join(" "),
              );

              // Req 4.3: only call back when text actually changed from the
              // originally recognized text (Req 4.4: skip if identical).
              if (newText !== recognizedText && capturedOnCorrection) {
                span.classList.remove("editor-word--saved", "editor-word--failed");
                span.classList.add("editor-word--pending");
                const correctionResult = capturedOnCorrection(
                  capturedWordIndex,
                  capturedLineIndex,
                  recognizedText,
                  newText,
                );
                if (correctionResult instanceof Promise) {
                  correctionResult
                    .then(() => {
                      span.classList.remove("editor-word--pending", "editor-word--failed");
                      span.classList.add("editor-word--saved");
                      span.title = "Saved as region-backed training data.";
                    })
                    .catch(() => {
                      span.classList.remove("editor-word--pending", "editor-word--saved");
                      span.classList.add("editor-word--failed");
                      span.title = "Correction changed the text locally, but saving to training data failed.";
                    });
                } else {
                  span.classList.remove("editor-word--pending", "editor-word--failed");
                  span.classList.add("editor-word--saved");
                  span.title = "Saved as region-backed training data.";
                }
              }
            };

            input.addEventListener("keydown", (e: KeyboardEvent) => {
              if (e.key === "Enter") {
                e.preventDefault();
                confirmEdit();
              } else if (e.key === "Escape") {
                // Cancel: restore previous text without calling back.
                if (span.contains(input)) {
                  span.removeChild(input);
                  span.textContent = currentDisplayText + capturedTrailingSpace;
                }
              }
            });

            // Confirm on blur (clicking away counts as confirm, Req 4.2).
            input.addEventListener("blur", confirmEdit);
          };

          // Req 4.1: double-click to enter edit mode.
          span.addEventListener("dblclick", enterEditMode);

          chips.appendChild(span);
        }

        if (lineWords.length > 0 && line.alignmentStatus !== "uncertain") {
          block.appendChild(chips);
        }

        // Add a line break after each line except the last.
        textContent.appendChild(block);
        if (li < sortedLineIndices.length - 1) {
          textContent.appendChild(document.createElement("div")).className = "editor-line-gap";
        }
      }

      textPanel.appendChild(textContent);
    }

    container.appendChild(imagePanel);
    container.appendChild(textPanel);
    this.mount.appendChild(container);
  }

  private renderRegionOverlay(
    result: RecognitionResult,
    overlay: HTMLElement,
    regionState: Map<number, Region>,
    bounds: { width: number; height: number },
  ): void {
    overlay.replaceChildren();
    if (result.lines.length === 0) return;

    const applyBoxStyle = (box: HTMLElement, region: Region): void => {
      box.style.left = `${(region.x / bounds.width) * 100}%`;
      box.style.top = `${(region.y / bounds.height) * 100}%`;
      box.style.width = `${(region.width / bounds.width) * 100}%`;
      box.style.height = `${(region.height / bounds.height) * 100}%`;
    };

    for (const line of result.lines) {
      const region = regionState.get(line.index) ?? line.region;
      const box = document.createElement("div");
      box.className = line.alignmentStatus === "uncertain"
        ? "editor-region-box editor-region-box--uncertain"
        : "editor-region-box editor-region-box--aligned";
      box.setAttribute("data-testid", "editor-region-box");
      box.setAttribute("data-line-index", String(line.index));
      box.setAttribute("role", "button");
      box.setAttribute("tabindex", "0");
      box.setAttribute("aria-label", `Move line ${line.index + 1} training crop`);
      applyBoxStyle(box, region);

      const lineNumber = document.createElement("span");
      lineNumber.className = "editor-region-label";
      lineNumber.setAttribute("data-testid", "editor-region-label");
      lineNumber.setAttribute("aria-hidden", "true");
      lineNumber.textContent = String(line.index + 1);
      box.appendChild(lineNumber);

      const handle = document.createElement("div");
      handle.className = "editor-region-resize-handle";
      handle.setAttribute("data-testid", "editor-region-resize-handle");
      handle.setAttribute("aria-hidden", "true");
      box.appendChild(handle);

      const selectLine = (): void => {
        overlay.querySelectorAll(".editor-region-box--selected").forEach((el) => {
          el.classList.remove("editor-region-box--selected");
        });
        box.classList.add("editor-region-box--selected");
        this.mount.querySelectorAll(".editor-line-review--selected").forEach((el) => {
          el.classList.remove("editor-line-review--selected");
        });
        const card = this.mount.querySelector<HTMLElement>(
          `[data-testid='editor-line-review'][data-line-index='${line.index}']`,
        );
        card?.classList.add("editor-line-review--selected");
        card?.scrollIntoView?.({ block: "nearest" });
      };

      box.addEventListener("mousedown", (event: MouseEvent) => {
        if (event.button !== 0) return;
        this.stopDrag?.();
        event.preventDefault();
        selectLine();
        const start = { x: event.clientX, y: event.clientY };
        const startRegion = { ...(regionState.get(line.index) ?? line.region) };
        const rect = overlay.getBoundingClientRect();
        const scaleX = bounds.width / Math.max(rect.width, 1);
        const scaleY = bounds.height / Math.max(rect.height, 1);
        const resizing = event.target === handle;

        const onMove = (moveEvent: MouseEvent): void => {
          const dx = Math.round((moveEvent.clientX - start.x) * scaleX);
          const dy = Math.round((moveEvent.clientY - start.y) * scaleY);
          const next = resizing
            ? {
                ...startRegion,
                width: startRegion.width + dx,
                height: startRegion.height + dy,
              }
            : {
                ...startRegion,
                x: startRegion.x + dx,
                y: startRegion.y + dy,
              };
          const clamped = this.clampRegion(next, bounds);
          regionState.set(line.index, clamped);
          this.currentLineRegions.set(line.index, { ...clamped });
          applyBoxStyle(box, clamped);
        };

        const onUp = (): void => {
          document.removeEventListener("mousemove", onMove);
          document.removeEventListener("mouseup", onUp);
          this.stopDrag = undefined;
        };

        this.stopDrag = onUp;
        document.addEventListener("mousemove", onMove);
        document.addEventListener("mouseup", onUp);
      });

      box.addEventListener("click", selectLine);
      overlay.appendChild(box);
    }
  }

  private buildLineRegionEditor(lineIndex: number, regionState: Map<number, Region>): {
    element: HTMLElement;
    getRegion: () => Region;
  } {
    const wrap = document.createElement("div");
    wrap.className = "editor-line-region-editor";
    wrap.setAttribute("data-testid", "editor-line-region-editor");

    const text = document.createElement("p");
    text.className = "editor-line-region-help";
    text.textContent = "Adjust the training crop by dragging the line box on the image. Drag the lower-right handle to resize.";
    wrap.appendChild(text);

    return {
      element: wrap,
      getRegion: () => ({ ...(regionState.get(lineIndex) ?? { x: 0, y: 0, width: 1, height: 1 }) }),
    };
  }

  private getRegionBounds(result: RecognitionResult): { width: number; height: number } {
    if (result.imageWidth && result.imageHeight) {
      return {
        width: Math.max(1, Math.round(result.imageWidth)),
        height: Math.max(1, Math.round(result.imageHeight)),
      };
    }

    const width = Math.max(
      1,
      ...result.lines.map((line) => line.region.x + line.region.width),
      ...result.lines.flatMap((line) => line.words.map((word) => word.region.x + word.region.width)),
    );
    const height = Math.max(
      1,
      ...result.lines.map((line) => line.region.y + line.region.height),
      ...result.lines.flatMap((line) => line.words.map((word) => word.region.y + word.region.height)),
    );
    return {
      width: Math.max(width + 200, Math.ceil(width * 1.35)),
      height: Math.max(height + 120, Math.ceil(height * 1.35)),
    };
  }

  private applyImageStageGeometry(stage: HTMLElement, result: RecognitionResult): void {
    if (!result.imageWidth || !result.imageHeight) return;
    const width = Math.max(1, Math.round(result.imageWidth));
    const height = Math.max(1, Math.round(result.imageHeight));
    const ratio = width / height;
    stage.classList.add("editor-image-stage--mapped");
    stage.style.aspectRatio = `${width} / ${height}`;
    stage.style.width = `min(100%, calc(72vh * ${ratio}))`;
  }

  private clampRegion(region: Region, bounds: { width: number; height: number }): Region {
    const width = Math.max(1, Math.min(Math.round(region.width), bounds.width));
    const height = Math.max(1, Math.min(Math.round(region.height), bounds.height));
    const x = Math.max(0, Math.min(Math.round(region.x), bounds.width - width));
    const y = Math.max(0, Math.min(Math.round(region.y), bounds.height - height));
    return { x, y, width, height };
  }
}
