// Three-pane math review: original crop, editable LaTeX, rendered preview.
// KaTeX is bundled so the preview does not silently degrade to source text.

import katex from "katex";
import "katex/dist/katex.min.css";

export interface MathEditorOptions {
  mount: HTMLElement;
  latex?: string;
  cropUrl?: string;
  plainText?: boolean;
  onChange?: (latex: string) => void;
}

export function renderLatexPreview(target: HTMLElement, latex: string): void {
  try {
    katex.render(latex, target, {
      throwOnError: false,
      trust: false,
      strict: "ignore",
    });
    return;
  } catch {
    // Unsupported commands stay visible as escaped source text.
  }
  target.replaceChildren();
  target.textContent = latex;
}

export class MathEditor {
  private readonly mount: HTMLElement;
  private onChange?: (latex: string) => void;
  private plainText = false;

  constructor(options: MathEditorOptions) {
    this.mount = options.mount;
    this.onChange = options.onChange;
    this.plainText = options.plainText ?? false;
    this.render(options.latex ?? "", options.cropUrl);
  }

  render(latex: string, cropUrl?: string): void {
    this.mount.replaceChildren();
    const layout = document.createElement("div");
    layout.className = "math-editor";
    layout.setAttribute("data-testid", "math-editor");

    const crop = document.createElement("img");
    crop.className = "math-editor-crop";
    crop.alt = this.plainText
      ? "Original handwriting crop"
      : "Original formula crop";
    crop.hidden = !cropUrl;
    if (cropUrl) crop.src = cropUrl;
    layout.appendChild(crop);

    const textarea = document.createElement("textarea");
    textarea.className = this.plainText
      ? "math-editor-source math-editor-source--prose"
      : "math-editor-source";
    textarea.setAttribute("data-testid", "math-editor-source");
    textarea.value = latex;
    textarea.setAttribute(
      "aria-label",
      this.plainText ? "Corrected text" : "Editable LaTeX",
    );
    if (this.plainText) layout.appendChild(textarea);
    else {
      const details = document.createElement("details");
      details.open = !latex;
      const summary = document.createElement("summary");
      summary.textContent = "Edit LaTeX";
      details.append(summary, textarea);
      layout.append(details);
    }

    const preview = document.createElement("div");
    preview.className = "math-editor-preview";
    preview.setAttribute("data-testid", "math-editor-preview");
    if (this.plainText) preview.textContent = latex;
    else renderLatexPreview(preview, latex);
    if (this.plainText) preview.hidden = true;
    layout.prepend(preview);

    textarea.addEventListener("input", () => {
      if (this.plainText) preview.textContent = textarea.value;
      else renderLatexPreview(preview, textarea.value);
      this.onChange?.(textarea.value);
    });

    this.mount.appendChild(layout);
  }
}
