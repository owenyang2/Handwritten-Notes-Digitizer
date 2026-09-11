// Three-pane math review: original crop, editable LaTeX, rendered preview.
// KaTeX is bundled so the preview does not silently degrade to source text.

import katex from "katex";
import "katex/dist/katex.min.css";

export interface MathEditorOptions {
  mount: HTMLElement;
  latex?: string;
  cropUrl?: string;
  onChange?: (latex: string) => void;
}

export function renderLatexPreview(target: HTMLElement, latex: string): void {
  try {
    katex.render(latex, target, { throwOnError: false, trust: false, strict: "ignore" });
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

  constructor(options: MathEditorOptions) {
    this.mount = options.mount;
    this.onChange = options.onChange;
    this.render(options.latex ?? "", options.cropUrl);
  }

  render(latex: string, cropUrl?: string): void {
    this.mount.replaceChildren();
    const layout = document.createElement("div");
    layout.className = "math-editor";
    layout.setAttribute("data-testid", "math-editor");

    const crop = document.createElement("img");
    crop.className = "math-editor-crop";
    crop.alt = "Original formula crop";
    if (cropUrl) crop.src = cropUrl;
    layout.appendChild(crop);

    const textarea = document.createElement("textarea");
    textarea.className = "math-editor-source";
    textarea.setAttribute("data-testid", "math-editor-source");
    textarea.value = latex;
    layout.appendChild(textarea);

    const preview = document.createElement("div");
    preview.className = "math-editor-preview";
    preview.setAttribute("data-testid", "math-editor-preview");
    renderLatexPreview(preview, latex);
    layout.appendChild(preview);

    textarea.addEventListener("input", () => {
      renderLatexPreview(preview, textarea.value);
      this.onChange?.(textarea.value);
    });

    this.mount.appendChild(layout);
  }
}
