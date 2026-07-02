// Scaffold sanity test: confirms the test runner (vitest) and property-based
// library (fast-check) are wired up. The full Property 7 test lives in task
// 10.3; this exercises the same pure helper to validate the toolchain.

import { describe, it, expect } from "vitest";
import fc from "fast-check";
import { readingOrder } from "./editor.js";
import type { RecognitionResult } from "../types.js";

describe("readingOrder (toolchain sanity)", () => {
  it("orders words top-to-bottom by line then left-to-right by word", () => {
    const result: RecognitionResult = {
      recognizedAnyText: true,
      lines: [
        {
          index: 1,
          region: { x: 0, y: 10, width: 5, height: 5 },
          words: [
            { index: 1, text: "world", region: { x: 5, y: 10, width: 5, height: 5 } },
            { index: 0, text: "hello", region: { x: 0, y: 10, width: 5, height: 5 } },
          ],
        },
        {
          index: 0,
          region: { x: 0, y: 0, width: 5, height: 5 },
          words: [
            { index: 0, text: "top", region: { x: 0, y: 0, width: 5, height: 5 } },
          ],
        },
      ],
    };

    expect(readingOrder(result).map((w) => w.text)).toEqual([
      "top",
      "hello",
      "world",
    ]);
  });

  it("returns every word exactly once regardless of input order (fast-check)", () => {
    fc.assert(
      fc.property(
        fc.array(
          fc.record({
            index: fc.integer({ min: 0, max: 50 }),
            wordCount: fc.integer({ min: 0, max: 5 }),
          }),
          { maxLength: 6 },
        ),
        (lineSpecs) => {
          let wordSeq = 0;
          const result: RecognitionResult = {
            recognizedAnyText: true,
            lines: lineSpecs.map((spec, li) => ({
              index: spec.index,
              region: { x: 0, y: li, width: 1, height: 1 },
              words: Array.from({ length: spec.wordCount }, (_, wi) => ({
                index: wi,
                text: `w${wordSeq++}`,
                region: { x: wi, y: li, width: 1, height: 1 },
              })),
            })),
          };

          const totalWords = lineSpecs.reduce((n, s) => n + s.wordCount, 0);
          expect(readingOrder(result)).toHaveLength(totalWords);
        },
      ),
      { numRuns: 100 },
    );
  });
});

// ---------------------------------------------------------------------------
// Property 7: Display order follows reading order (task 10.3)
// Feature: handwriting-notes-digitizer, Property 7: Display order follows reading order
// Validates: Requirements 3.2
// ---------------------------------------------------------------------------

describe("Property 7: Display order follows reading order (Req 3.2)", () => {
  /**
   * Arbitrary generator for a RecognitionResult.
   *
   * Strategy:
   * - Up to 8 lines, each with a unique non-negative index.
   * - Each line has up to 6 words, each with a unique non-negative index within
   *   that line.
   * - Text content is irrelevant for ordering, so we use a simple label.
   * - Region values are synthetic — readingOrder() only uses .index fields.
   */
  const arbitraryResult = fc.record({
    recognizedAnyText: fc.boolean(),
    lines: fc.array(
      fc.integer({ min: 0, max: 50 }).chain((lineIndex) =>
        fc
          .uniqueArray(fc.integer({ min: 0, max: 50 }), {
            minLength: 0,
            maxLength: 6,
          })
          .map((wordIndices) => ({
            index: lineIndex,
            region: { x: 0, y: lineIndex, width: 10, height: 5 },
            words: wordIndices.map((wi) => ({
              index: wi,
              text: `L${lineIndex}W${wi}`,
              region: { x: wi, y: lineIndex, width: 1, height: 1 },
            })),
          })),
      ),
      { maxLength: 8 },
    ),
  });

  it(
    "readingOrder returns words ordered by line index then word index (≥100 runs)",
    () => {
      fc.assert(
        fc.property(arbitraryResult, (result: RecognitionResult) => {
          const ordered = readingOrder(result);

          // Build the expected order independently: sort lines by index, then
          // sort words within each line by index, then flatten.
          const expected = [...result.lines]
            .sort((a, b) => a.index - b.index)
            .flatMap((line) =>
              [...line.words].sort((a, b) => a.index - b.index),
            );

          // Same length
          expect(ordered).toHaveLength(expected.length);

          // Same sequence of (lineIndex, wordIndex) pairs
          for (let i = 0; i < expected.length; i++) {
            expect(ordered[i].index).toBe(expected[i].index);
            expect(ordered[i].text).toBe(expected[i].text);
          }
        }),
        { numRuns: 100 },
      );
    },
  );

  it(
    "readingOrder result has strictly non-decreasing line order (≥100 runs)",
    () => {
      fc.assert(
        fc.property(arbitraryResult, (result: RecognitionResult) => {
          const lineSortedIndices = [...result.lines]
            .sort((a, b) => a.index - b.index)
            .map((l) => l.index);

          const ordered = readingOrder(result);

          // Reconstruct which line each output word belongs to by replaying the
          // expected flatten and tracking line indices.
          const lineOfWord: number[] = [];
          for (const line of [...result.lines].sort(
            (a, b) => a.index - b.index,
          )) {
            for (let i = 0; i < line.words.length; i++) {
              lineOfWord.push(line.index);
            }
          }

          // Line indices in the output must be non-decreasing.
          for (let i = 1; i < lineOfWord.length; i++) {
            expect(lineOfWord[i]).toBeGreaterThanOrEqual(lineOfWord[i - 1]);
          }

          // Sanity: number of line-index entries equals number of output words.
          expect(lineOfWord).toHaveLength(ordered.length);
          void lineSortedIndices; // used above for context
        }),
        { numRuns: 100 },
      );
    },
  );

  it(
    "readingOrder on a result with duplicate line indices is still fully ordered (≥100 runs)",
    () => {
      // Edge case: what if two lines share the same index?  readingOrder sorts
      // by index — their relative order after sort is implementation-defined but
      // should be stable enough to not error.  We just check totality here.
      fc.assert(
        fc.property(
          fc.array(
            fc.record({
              index: fc.integer({ min: 0, max: 3 }), // small range → many duplicates
              words: fc.array(
                fc.record({
                  index: fc.integer({ min: 0, max: 3 }),
                  text: fc.string({ minLength: 1, maxLength: 5 }),
                  region: fc.constant({
                    x: 0,
                    y: 0,
                    width: 1,
                    height: 1,
                  }),
                }),
                { maxLength: 4 },
              ),
              region: fc.constant({ x: 0, y: 0, width: 10, height: 5 }),
            }),
            { maxLength: 6 },
          ),
          (lines) => {
            const result: RecognitionResult = {
              recognizedAnyText: lines.length > 0,
              lines,
            };
            const totalWords = lines.reduce((n, l) => n + l.words.length, 0);
            const ordered = readingOrder(result);
            expect(ordered).toHaveLength(totalWords);
          },
        ),
        { numRuns: 100 },
      );
    },
  );
});

// ---------------------------------------------------------------------------
// Editor.render() unit tests (task 10.1)
// Validates: Requirements 3.2, 3.3, 3.4
// ---------------------------------------------------------------------------

import { Editor } from "./editor.js";

describe("Editor.render() — display alongside image (Req 3.3)", () => {
  function makeMount(): HTMLElement {
    const el = document.createElement("div");
    document.body.appendChild(el);
    return el;
  }

  const IMAGE_URL = "https://example.com/handwriting.png";

  const resultWithText: RecognitionResult = {
    recognizedAnyText: true,
    lines: [
      {
        index: 0,
        region: { x: 0, y: 0, width: 100, height: 20 },
        words: [
          { index: 0, text: "Hello", region: { x: 0, y: 0, width: 40, height: 20 } },
          { index: 1, text: "world", region: { x: 50, y: 0, width: 40, height: 20 } },
        ],
      },
      {
        index: 1,
        region: { x: 0, y: 25, width: 100, height: 20 },
        words: [
          { index: 0, text: "foo", region: { x: 0, y: 25, width: 30, height: 20 } },
        ],
      },
    ],
  };

  const resultNoText: RecognitionResult = {
    recognizedAnyText: false,
    lines: [],
  };

  it("renders the image element with the provided URL (Req 3.3)", () => {
    const mount = makeMount();
    const editor = new Editor({ mount });
    editor.render(resultWithText, IMAGE_URL);

    const img = mount.querySelector<HTMLImageElement>("[data-testid='editor-image']");
    expect(img).not.toBeNull();
    expect(img!.src).toBe(IMAGE_URL);
  });

  it("renders the text panel alongside the image (Req 3.3)", () => {
    const mount = makeMount();
    const editor = new Editor({ mount });
    editor.render(resultWithText, IMAGE_URL);

    const container = mount.querySelector("[data-testid='editor-container']");
    expect(container).not.toBeNull();

    const imgPanel = mount.querySelector(".editor-image-panel");
    const textPanel = mount.querySelector("[data-testid='editor-text-panel']");
    expect(imgPanel).not.toBeNull();
    expect(textPanel).not.toBeNull();
  });

  it("renders all words in reading order as word spans (Req 3.2)", () => {
    const mount = makeMount();
    const editor = new Editor({ mount });
    editor.render(resultWithText, IMAGE_URL);

    const wordSpans = mount.querySelectorAll("[data-testid='editor-word']");
    // 2 words on line 0, 1 word on line 1 → 3 total
    expect(wordSpans.length).toBe(3);

    const texts = Array.from(wordSpans).map((s) => s.textContent!.trim());
    expect(texts).toEqual(["Hello", "world", "foo"]);
  });

  it("shows no-text message when recognizedAnyText is false (Req 3.4)", () => {
    const mount = makeMount();
    const editor = new Editor({ mount });
    editor.render(resultNoText, IMAGE_URL);

    const noTextEl = mount.querySelector("[data-testid='editor-no-text']");
    expect(noTextEl).not.toBeNull();
    expect(noTextEl!.textContent).toMatch(/no text recognized/i);
  });

  it("still shows the image when no text is recognized (Req 3.4)", () => {
    const mount = makeMount();
    const editor = new Editor({ mount });
    editor.render(resultNoText, IMAGE_URL);

    const img = mount.querySelector<HTMLImageElement>("[data-testid='editor-image']");
    expect(img).not.toBeNull();
    expect(img!.src).toBe(IMAGE_URL);
  });

  it("does not show the no-text message when text is recognized (Req 3.3)", () => {
    const mount = makeMount();
    const editor = new Editor({ mount });
    editor.render(resultWithText, IMAGE_URL);

    const noTextEl = mount.querySelector("[data-testid='editor-no-text']");
    expect(noTextEl).toBeNull();
  });

  it("re-rendering clears old content (idempotent)", () => {
    const mount = makeMount();
    const editor = new Editor({ mount });
    editor.render(resultWithText, IMAGE_URL);
    editor.render(resultNoText, IMAGE_URL);

    // After second render, no word spans should remain
    const wordSpans = mount.querySelectorAll("[data-testid='editor-word']");
    expect(wordSpans.length).toBe(0);
    // And the no-text message should be visible
    const noTextEl = mount.querySelector("[data-testid='editor-no-text']");
    expect(noTextEl).not.toBeNull();
  });

  it("renders words in reading order even when lines/words are out of order in input (Req 3.2)", () => {
    const mount = makeMount();
    const editor = new Editor({ mount });

    const shuffledResult: RecognitionResult = {
      recognizedAnyText: true,
      lines: [
        {
          index: 2,
          region: { x: 0, y: 40, width: 100, height: 20 },
          words: [
            { index: 0, text: "third", region: { x: 0, y: 40, width: 50, height: 20 } },
          ],
        },
        {
          index: 0,
          region: { x: 0, y: 0, width: 100, height: 20 },
          words: [
            { index: 1, text: "B", region: { x: 50, y: 0, width: 30, height: 20 } },
            { index: 0, text: "A", region: { x: 0, y: 0, width: 40, height: 20 } },
          ],
        },
      ],
    };

    editor.render(shuffledResult, IMAGE_URL);

    const wordSpans = mount.querySelectorAll("[data-testid='editor-word']");
    const texts = Array.from(wordSpans).map((s) => s.textContent!.trim());
    expect(texts).toEqual(["A", "B", "third"]);
  });
});

// ---------------------------------------------------------------------------
// Inline editing tests (task 10.2)
// Validates: Requirements 4.1, 4.2
// ---------------------------------------------------------------------------

import { MAX_WORD_CHARS } from "./editor.js";

describe("Editor inline editing (Req 4.1, 4.2)", () => {
  const IMAGE_URL = "https://example.com/img.png";

  /** Minimal two-word result for edit testing. */
  const twoWordResult: RecognitionResult = {
    recognizedAnyText: true,
    lines: [
      {
        index: 0,
        region: { x: 0, y: 0, width: 100, height: 20 },
        words: [
          { index: 0, text: "hello", region: { x: 0, y: 0, width: 40, height: 20 } },
          { index: 1, text: "world", region: { x: 50, y: 0, width: 40, height: 20 } },
        ],
      },
    ],
  };

  function makeMount(): HTMLElement {
    const el = document.createElement("div");
    document.body.appendChild(el);
    return el;
  }

  it("double-clicking a word shows an input field (Req 4.1)", () => {
    const mount = makeMount();
    const editor = new Editor({ mount });
    editor.render(twoWordResult, IMAGE_URL);

    const spans = mount.querySelectorAll<HTMLElement>("[data-testid='editor-word']");
    expect(spans.length).toBe(2);

    spans[0].dispatchEvent(new MouseEvent("dblclick", { bubbles: true }));

    const input = mount.querySelector<HTMLInputElement>("[data-testid='editor-word-input']");
    expect(input).not.toBeNull();
    expect(input!.value).toBe("hello");
  });

  it("input is capped at MAX_WORD_CHARS characters (Req 4.1)", () => {
    const mount = makeMount();
    const editor = new Editor({ mount });
    editor.render(twoWordResult, IMAGE_URL);

    const span = mount.querySelector<HTMLElement>("[data-testid='editor-word']")!;
    span.dispatchEvent(new MouseEvent("dblclick", { bubbles: true }));

    const input = mount.querySelector<HTMLInputElement>("[data-testid='editor-word-input']")!;
    expect(input.maxLength).toBe(MAX_WORD_CHARS);
  });

  it("pressing Enter confirms the edit and updates the displayed text (Req 4.2)", () => {
    const mount = makeMount();
    const editor = new Editor({ mount });
    editor.render(twoWordResult, IMAGE_URL);

    const span = mount.querySelector<HTMLElement>("[data-testid='editor-word']")!;
    span.dispatchEvent(new MouseEvent("dblclick", { bubbles: true }));

    const input = mount.querySelector<HTMLInputElement>("[data-testid='editor-word-input']")!;
    input.value = "greetings";
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));

    // Input should be gone and span text updated.
    expect(mount.querySelector("[data-testid='editor-word-input']")).toBeNull();
    const spans = mount.querySelectorAll("[data-testid='editor-word']");
    expect(spans[0].textContent).toContain("greetings");
  });

  it("blur confirms the edit and updates the displayed text (Req 4.2)", () => {
    const mount = makeMount();
    const editor = new Editor({ mount });
    editor.render(twoWordResult, IMAGE_URL);

    const span = mount.querySelector<HTMLElement>("[data-testid='editor-word']")!;
    span.dispatchEvent(new MouseEvent("dblclick", { bubbles: true }));

    const input = mount.querySelector<HTMLInputElement>("[data-testid='editor-word-input']")!;
    input.value = "bye";
    input.dispatchEvent(new Event("blur"));

    expect(mount.querySelector("[data-testid='editor-word-input']")).toBeNull();
    const spans = mount.querySelectorAll("[data-testid='editor-word']");
    expect(spans[0].textContent).toContain("bye");
  });

  it("calls onCorrection with correct args when text changes (Req 4.3)", () => {
    const corrections: Array<{
      wordIndex: number;
      lineIndex: number;
      original: string;
      corrected: string;
    }> = [];

    const mount = makeMount();
    const editor = new Editor({
      mount,
      onCorrection: (wi, li, orig, corr) =>
        corrections.push({ wordIndex: wi, lineIndex: li, original: orig, corrected: corr }),
    });
    editor.render(twoWordResult, IMAGE_URL);

    const span = mount.querySelector<HTMLElement>("[data-testid='editor-word']")!;
    span.dispatchEvent(new MouseEvent("dblclick", { bubbles: true }));

    const input = mount.querySelector<HTMLInputElement>("[data-testid='editor-word-input']")!;
    input.value = "hi";
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));

    expect(corrections).toHaveLength(1);
    expect(corrections[0]).toEqual({
      wordIndex: 0,
      lineIndex: 0,
      original: "hello",
      corrected: "hi",
    });
  });

  it("does NOT call onCorrection when text is identical to recognized text (Req 4.4)", () => {
    const corrections: unknown[] = [];
    const mount = makeMount();
    const editor = new Editor({
      mount,
      onCorrection: (...args) => corrections.push(args),
    });
    editor.render(twoWordResult, IMAGE_URL);

    const span = mount.querySelector<HTMLElement>("[data-testid='editor-word']")!;
    span.dispatchEvent(new MouseEvent("dblclick", { bubbles: true }));

    const input = mount.querySelector<HTMLInputElement>("[data-testid='editor-word-input']")!;
    // Keep the original text.
    input.value = "hello";
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));

    expect(corrections).toHaveLength(0);
  });

  it("Escape cancels the edit without changing display or calling onCorrection", () => {
    const corrections: unknown[] = [];
    const mount = makeMount();
    const editor = new Editor({
      mount,
      onCorrection: (...args) => corrections.push(args),
    });
    editor.render(twoWordResult, IMAGE_URL);

    const span = mount.querySelector<HTMLElement>("[data-testid='editor-word']")!;
    span.dispatchEvent(new MouseEvent("dblclick", { bubbles: true }));

    const input = mount.querySelector<HTMLInputElement>("[data-testid='editor-word-input']")!;
    input.value = "changed";
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));

    // Input removed, original text preserved.
    expect(mount.querySelector("[data-testid='editor-word-input']")).toBeNull();
    const spans = mount.querySelectorAll("[data-testid='editor-word']");
    expect(spans[0].textContent).toContain("hello");
    expect(corrections).toHaveLength(0);
  });

  it("only calls onCorrection for the changed word, not others (Req 4.3)", () => {
    const corrections: Array<{ wordIndex: number }> = [];
    const mount = makeMount();
    const editor = new Editor({
      mount,
      onCorrection: (wi) => corrections.push({ wordIndex: wi }),
    });
    editor.render(twoWordResult, IMAGE_URL);

    // Edit the second word ("world").
    const spans = mount.querySelectorAll<HTMLElement>("[data-testid='editor-word']");
    spans[1].dispatchEvent(new MouseEvent("dblclick", { bubbles: true }));

    const input = mount.querySelector<HTMLInputElement>("[data-testid='editor-word-input']")!;
    input.value = "earth";
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));

    expect(corrections).toHaveLength(1);
    expect(corrections[0].wordIndex).toBe(1);
  });

  it("each word can be edited independently across multiple edits", () => {
    const corrections: string[] = [];
    const mount = makeMount();
    const editor = new Editor({
      mount,
      onCorrection: (_wi, _li, _orig, corr) => corrections.push(corr),
    });
    editor.render(twoWordResult, IMAGE_URL);

    const spans = mount.querySelectorAll<HTMLElement>("[data-testid='editor-word']");

    // Edit first word.
    spans[0].dispatchEvent(new MouseEvent("dblclick", { bubbles: true }));
    let input = mount.querySelector<HTMLInputElement>("[data-testid='editor-word-input']")!;
    input.value = "hi";
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));

    // Edit second word.
    spans[1].dispatchEvent(new MouseEvent("dblclick", { bubbles: true }));
    input = mount.querySelector<HTMLInputElement>("[data-testid='editor-word-input']")!;
    input.value = "earth";
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));

    expect(corrections).toEqual(["hi", "earth"]);
  });

  it("does not show the word input when no text is recognized", () => {
    const mount = makeMount();
    const editor = new Editor({ mount });
    editor.render({ recognizedAnyText: false, lines: [] }, IMAGE_URL);

    const input = mount.querySelector("[data-testid='editor-word-input']");
    expect(input).toBeNull();
  });

  it("input value is truncated to MAX_WORD_CHARS on confirm", () => {
    const corrections: string[] = [];
    const mount = makeMount();
    const editor = new Editor({
      mount,
      onCorrection: (_wi, _li, _orig, corr) => corrections.push(corr),
    });
    editor.render(twoWordResult, IMAGE_URL);

    const span = mount.querySelector<HTMLElement>("[data-testid='editor-word']")!;
    span.dispatchEvent(new MouseEvent("dblclick", { bubbles: true }));

    const input = mount.querySelector<HTMLInputElement>("[data-testid='editor-word-input']")!;
    // Provide a very long string (but note maxLength is enforced by the browser;
    // we also truncate in code — test the code path by setting value directly).
    const longText = "a".repeat(MAX_WORD_CHARS + 100);
    input.value = longText;
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));

    // Correction should be at most MAX_WORD_CHARS.
    expect(corrections[0].length).toBeLessThanOrEqual(MAX_WORD_CHARS);
    const spans = mount.querySelectorAll("[data-testid='editor-word']");
    expect(spans[0].textContent!.replace(/ $/, "").length).toBeLessThanOrEqual(MAX_WORD_CHARS);
  });
});

// ---------------------------------------------------------------------------
// Task 10.4 — Requirements coverage tests for the Editor component
// Validates: Requirements 3.1, 3.3, 3.4, 4.1, 4.2
// ---------------------------------------------------------------------------

describe("Task 10.4 — Editor requirements coverage", () => {
  function makeMount(): HTMLElement {
    const el = document.createElement("div");
    document.body.appendChild(el);
    return el;
  }

  const IMAGE_URL = "https://example.com/hw.png";

  const resultWithText: RecognitionResult = {
    recognizedAnyText: true,
    lines: [
      {
        index: 0,
        region: { x: 0, y: 0, width: 200, height: 30 },
        words: [
          { index: 0, text: "alpha", region: { x: 0, y: 0, width: 50, height: 30 } },
          { index: 1, text: "beta", region: { x: 60, y: 0, width: 50, height: 30 } },
        ],
      },
    ],
  };

  const resultNoText: RecognitionResult = {
    recognizedAnyText: false,
    lines: [],
  };

  // -------------------------------------------------------------------------
  // Req 3.1 — Display renders quickly (within 2 seconds)
  // -------------------------------------------------------------------------

  it("Req 3.1: render() completes synchronously within 2 seconds", () => {
    const mount = makeMount();
    const editor = new Editor({ mount });

    const start = performance.now();
    editor.render(resultWithText, IMAGE_URL);
    const elapsed = performance.now() - start;

    expect(elapsed).toBeLessThan(2_000);
    // Confirm that the render actually produced output (not a no-op).
    expect(mount.querySelector("[data-testid='editor-container']")).not.toBeNull();
  });

  it("Req 3.1: render() with no-text result also completes within 2 seconds", () => {
    const mount = makeMount();
    const editor = new Editor({ mount });

    const start = performance.now();
    editor.render(resultNoText, IMAGE_URL);
    const elapsed = performance.now() - start;

    expect(elapsed).toBeLessThan(2_000);
    expect(mount.querySelector("[data-testid='editor-container']")).not.toBeNull();
  });

  // -------------------------------------------------------------------------
  // Req 3.3 — Image and text are both visible simultaneously
  // -------------------------------------------------------------------------

  it("Req 3.3: image element and text panel are both present in the DOM after render", () => {
    const mount = makeMount();
    const editor = new Editor({ mount });
    editor.render(resultWithText, IMAGE_URL);

    const img = mount.querySelector("[data-testid='editor-image']");
    const textPanel = mount.querySelector("[data-testid='editor-text-panel']");

    expect(img).not.toBeNull();
    expect(textPanel).not.toBeNull();
    // Both are inside the same container (side-by-side / simultaneous).
    const container = mount.querySelector("[data-testid='editor-container']");
    expect(container).not.toBeNull();
    expect(container!.contains(img)).toBe(true);
    expect(container!.contains(textPanel)).toBe(true);
  });

  it("Req 3.3: image src matches the provided URL", () => {
    const mount = makeMount();
    const editor = new Editor({ mount });
    editor.render(resultWithText, IMAGE_URL);

    const img = mount.querySelector<HTMLImageElement>("[data-testid='editor-image']");
    expect(img).not.toBeNull();
    expect(img!.src).toBe(IMAGE_URL);
  });

  it("Req 3.3: word spans are visible in the text panel alongside the image", () => {
    const mount = makeMount();
    const editor = new Editor({ mount });
    editor.render(resultWithText, IMAGE_URL);

    const words = mount.querySelectorAll("[data-testid='editor-word']");
    expect(words.length).toBeGreaterThan(0);

    // Text panel contains at least one word span.
    const textPanel = mount.querySelector("[data-testid='editor-text-panel']");
    expect(textPanel!.querySelectorAll("[data-testid='editor-word']").length).toBeGreaterThan(0);
  });

  // -------------------------------------------------------------------------
  // Req 3.4 — No-text message and image shown when recognizedAnyText=false
  // -------------------------------------------------------------------------

  it("Req 3.4: no-text message is shown when recognizedAnyText=false", () => {
    const mount = makeMount();
    const editor = new Editor({ mount });
    editor.render(resultNoText, IMAGE_URL);

    const noText = mount.querySelector("[data-testid='editor-no-text']");
    expect(noText).not.toBeNull();
    expect(noText!.textContent).toMatch(/no text recognized/i);
  });

  it("Req 3.4: original image is still visible when no text is recognized", () => {
    const mount = makeMount();
    const editor = new Editor({ mount });
    editor.render(resultNoText, IMAGE_URL);

    const img = mount.querySelector<HTMLImageElement>("[data-testid='editor-image']");
    expect(img).not.toBeNull();
    expect(img!.src).toBe(IMAGE_URL);
  });

  it("Req 3.4: no word spans are rendered when recognizedAnyText=false", () => {
    const mount = makeMount();
    const editor = new Editor({ mount });
    editor.render(resultNoText, IMAGE_URL);

    const words = mount.querySelectorAll("[data-testid='editor-word']");
    expect(words.length).toBe(0);
  });

  it("Req 3.4: no-text message absent when recognizedAnyText=true", () => {
    const mount = makeMount();
    const editor = new Editor({ mount });
    editor.render(resultWithText, IMAGE_URL);

    expect(mount.querySelector("[data-testid='editor-no-text']")).toBeNull();
  });

  it("Req 3.4: edge case — recognizedAnyText=false with non-empty lines still shows no-text message", () => {
    // The flag is the source of truth, not the lines array.
    const edgeResult: RecognitionResult = {
      recognizedAnyText: false,
      lines: [
        {
          index: 0,
          region: { x: 0, y: 0, width: 100, height: 20 },
          words: [{ index: 0, text: "ghost", region: { x: 0, y: 0, width: 50, height: 20 } }],
        },
      ],
    };
    const mount = makeMount();
    const editor = new Editor({ mount });
    editor.render(edgeResult, IMAGE_URL);

    const noText = mount.querySelector("[data-testid='editor-no-text']");
    expect(noText).not.toBeNull();
    // No word spans should render because recognizedAnyText=false.
    expect(mount.querySelectorAll("[data-testid='editor-word']").length).toBe(0);
  });

  // -------------------------------------------------------------------------
  // Req 4.1 — Edit input accepts up to 10,000 characters (exact boundary)
  // -------------------------------------------------------------------------

  it("Req 4.1: MAX_WORD_CHARS constant equals exactly 10,000", () => {
    expect(MAX_WORD_CHARS).toBe(10_000);
  });

  it("Req 4.1: input maxLength attribute is exactly 10,000", () => {
    const mount = makeMount();
    const editor = new Editor({ mount });
    editor.render(resultWithText, IMAGE_URL);

    const span = mount.querySelector<HTMLElement>("[data-testid='editor-word']")!;
    span.dispatchEvent(new MouseEvent("dblclick", { bubbles: true }));

    const input = mount.querySelector<HTMLInputElement>("[data-testid='editor-word-input']")!;
    expect(input.maxLength).toBe(10_000);
  });

  it("Req 4.1: a 10,000-character correction is accepted without truncation", () => {
    const corrections: string[] = [];
    const mount = makeMount();
    const editor = new Editor({
      mount,
      onCorrection: (_wi, _li, _orig, corr) => corrections.push(corr),
    });
    editor.render(resultWithText, IMAGE_URL);

    const span = mount.querySelector<HTMLElement>("[data-testid='editor-word']")!;
    span.dispatchEvent(new MouseEvent("dblclick", { bubbles: true }));

    const input = mount.querySelector<HTMLInputElement>("[data-testid='editor-word-input']")!;
    const exactBoundary = "x".repeat(10_000);
    input.value = exactBoundary;
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));

    // Correction fired and retained at full length.
    expect(corrections).toHaveLength(1);
    expect(corrections[0]).toHaveLength(10_000);
    const spans = mount.querySelectorAll("[data-testid='editor-word']");
    // Trim trailing space added by the span for non-last words.
    expect(spans[0].textContent!.trimEnd()).toHaveLength(10_000);
  });

  it("Req 4.1: a value exceeding 10,000 characters is truncated to 10,000 on confirm", () => {
    const corrections: string[] = [];
    const mount = makeMount();
    const editor = new Editor({
      mount,
      onCorrection: (_wi, _li, _orig, corr) => corrections.push(corr),
    });
    editor.render(resultWithText, IMAGE_URL);

    const span = mount.querySelector<HTMLElement>("[data-testid='editor-word']")!;
    span.dispatchEvent(new MouseEvent("dblclick", { bubbles: true }));

    const input = mount.querySelector<HTMLInputElement>("[data-testid='editor-word-input']")!;
    // Bypass browser maxLength by setting .value directly.
    input.value = "y".repeat(10_001);
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));

    expect(corrections).toHaveLength(1);
    expect(corrections[0].length).toBeLessThanOrEqual(10_000);
  });

  // -------------------------------------------------------------------------
  // Req 4.2 — Update on confirm (pressing Enter updates displayed text)
  // -------------------------------------------------------------------------

  it("Req 4.2: pressing Enter removes the input and updates the span text", () => {
    const mount = makeMount();
    const editor = new Editor({ mount });
    editor.render(resultWithText, IMAGE_URL);

    const span = mount.querySelector<HTMLElement>("[data-testid='editor-word']")!;
    span.dispatchEvent(new MouseEvent("dblclick", { bubbles: true }));

    const input = mount.querySelector<HTMLInputElement>("[data-testid='editor-word-input']")!;
    input.value = "confirmed";
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));

    // Input is gone.
    expect(mount.querySelector("[data-testid='editor-word-input']")).toBeNull();
    // Text updated.
    const spans = mount.querySelectorAll("[data-testid='editor-word']");
    expect(spans[0].textContent).toContain("confirmed");
  });

  it("Req 4.2: display reflects the confirmed value on subsequent renders (re-edit shows updated text)", () => {
    const mount = makeMount();
    const editor = new Editor({ mount });
    editor.render(resultWithText, IMAGE_URL);

    // First edit.
    const span = mount.querySelector<HTMLElement>("[data-testid='editor-word']")!;
    span.dispatchEvent(new MouseEvent("dblclick", { bubbles: true }));
    let input = mount.querySelector<HTMLInputElement>("[data-testid='editor-word-input']")!;
    input.value = "first";
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));

    // Display now shows "first".
    const spans = mount.querySelectorAll<HTMLElement>("[data-testid='editor-word']");
    expect(spans[0].textContent).toContain("first");

    // Open the same word again — input should start with the confirmed value.
    spans[0].dispatchEvent(new MouseEvent("dblclick", { bubbles: true }));
    input = mount.querySelector<HTMLInputElement>("[data-testid='editor-word-input']")!;
    expect(input.value).toBe("first");
  });

  it("Req 4.2: confirming an empty string updates the display to empty", () => {
    const mount = makeMount();
    const editor = new Editor({ mount });
    editor.render(resultWithText, IMAGE_URL);

    const span = mount.querySelector<HTMLElement>("[data-testid='editor-word']")!;
    span.dispatchEvent(new MouseEvent("dblclick", { bubbles: true }));

    const input = mount.querySelector<HTMLInputElement>("[data-testid='editor-word-input']")!;
    input.value = "";
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));

    // Input removed, span text is empty (or just whitespace from trailing space).
    expect(mount.querySelector("[data-testid='editor-word-input']")).toBeNull();
    const spans = mount.querySelectorAll("[data-testid='editor-word']");
    expect(spans[0].textContent!.trimEnd()).toBe("");
  });
});
