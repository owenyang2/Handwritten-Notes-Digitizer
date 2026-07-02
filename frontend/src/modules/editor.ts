// Editor module.
//
// Responsibility: display recognized text in reading order alongside the
// original image and support inline editing of any word (Req 3, Req 4).
// The reading-order helper is a pure function used for display ordering and
// property testing (Property 7).

import type { RecognitionResult, RecognizedWord } from "../types.js";

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
) => void;

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
}

export class Editor {
  private readonly mount: HTMLElement;
  private onCorrection?: CorrectionCallback;

  constructor(options: EditorOptions) {
    this.mount = options.mount;
    this.onCorrection = options.onCorrection;
  }

  /** Update the correction callback after construction (used by main.ts wiring). */
  setOnCorrection(handler: CorrectionCallback): void {
    this.onCorrection = handler;
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
    this.mount.innerHTML = "";

    // Container holds the image and the text panel side by side (or stacked).
    const container = document.createElement("div");
    container.className = "editor-container";
    container.setAttribute("data-testid", "editor-container");

    // --- Image panel (always visible, satisfies Req 3.3 and 3.4) ---
    const imagePanel = document.createElement("div");
    imagePanel.className = "editor-image-panel";

    const img = document.createElement("img");
    img.src = imageUrl;
    img.alt = "Uploaded handwriting image";
    img.className = "editor-image";
    img.setAttribute("data-testid", "editor-image");
    imagePanel.appendChild(img);

    // --- Text panel ---
    const textPanel = document.createElement("div");
    textPanel.className = "editor-text-panel";
    textPanel.setAttribute("data-testid", "editor-text-panel");

    if (!result.recognizedAnyText) {
      // Req 3.4: show "no text recognized" message alongside the image.
      const noTextMsg = document.createElement("p");
      noTextMsg.className = "editor-no-text";
      noTextMsg.setAttribute("data-testid", "editor-no-text");
      noTextMsg.textContent = "No text recognized";
      textPanel.appendChild(noTextMsg);
    } else {
      // Req 3.2: render words in reading order.
      const words = readingOrder(result);
      const textContent = document.createElement("div");
      textContent.className = "editor-words";
      textContent.setAttribute("data-testid", "editor-words");

      // Group words back into lines so we can insert line breaks.
      const lineMap = new Map<number, RecognizedWord[]>();
      for (const line of result.lines) {
        const sorted = [...line.words].sort((a, b) => a.index - b.index);
        lineMap.set(line.index, sorted);
      }
      // Lines sorted top-to-bottom by their index (same order as readingOrder).
      const sortedLineIndices = [...result.lines]
        .sort((a, b) => a.index - b.index)
        .map((l) => l.index);

      // Render a <span> per word in reading order, with line breaks between lines.
      let wordPos = 0;
      for (let li = 0; li < sortedLineIndices.length; li++) {
        const lineIdx = sortedLineIndices[li];
        const lineWords = lineMap.get(lineIdx) ?? [];

        for (let wi = 0; wi < lineWords.length; wi++) {
          const word = words[wordPos++];

          // The "original" recognized text for this word slot (used for change
          // detection). We track the *latest confirmed* text so re-corrections
          // are compared to the previously confirmed value, but the
          // Correction_Pipeline always receives the original recognised text so
          // it can key the training pair on the source region correctly. We
          // keep a separate `recognizedText` that never changes after render.
          const recognizedText = word.text;
          // currentDisplayText tracks what is currently shown (may change after
          // a confirmed edit).
          let currentDisplayText = recognizedText;

          // Trailing space separates words on the same line (not after last word).
          const trailingSpace = wi < lineWords.length - 1 ? " " : "";

          const span = document.createElement("span");
          span.className = "editor-word";
          span.setAttribute("data-testid", "editor-word");
          span.setAttribute("data-word-index", String(word.index));
          span.setAttribute("data-line-index", String(lineIdx));
          span.textContent = currentDisplayText + trailingSpace;

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
              span.textContent = newText + capturedTrailingSpace;

              // Req 4.3: only call back when text actually changed from the
              // originally recognized text (Req 4.4: skip if identical).
              if (newText !== recognizedText && capturedOnCorrection) {
                capturedOnCorrection(
                  capturedWordIndex,
                  capturedLineIndex,
                  recognizedText,
                  newText,
                );
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

          textContent.appendChild(span);
        }

        // Add a line break after each line except the last.
        if (li < sortedLineIndices.length - 1) {
          textContent.appendChild(document.createElement("br"));
        }
      }

      textPanel.appendChild(textContent);
    }

    container.appendChild(imagePanel);
    container.appendChild(textPanel);
    this.mount.appendChild(container);
  }
}
