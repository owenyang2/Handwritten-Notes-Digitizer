import { describe, it, expect } from "vitest";
import { MathEditor } from "./mathEditor.js";
import { PageViewer } from "./pageViewer.js";
import { ReviewQueue } from "./reviewQueue.js";

describe("MathEditor", () => {
  it("renders a supported formula with KaTeX", () => {
    const mount = document.createElement("div");
    new MathEditor({ mount, latex: "x^2" });
    expect(mount.querySelector("[data-testid='math-editor-preview'] .katex")).not.toBeNull();
  });

  it("renders LaTeX as text and does not interpret markup", () => {
    const mount = document.createElement("div");
    document.body.appendChild(mount);
    const editor = new MathEditor({
      mount,
      latex: '<img src=x onerror=alert(1)>',
    });
    void editor;
    expect(mount.querySelector("img[src='x']")).toBeNull();
    expect(mount.querySelector("[data-testid='math-editor-preview']")!.textContent).toContain(
      "<img src=x onerror=alert(1)>",
    );
    mount.remove();
  });
});

describe("PageViewer", () => {
  it("emits a complete-expression crop on pointer drag", () => {
    const mount = document.createElement("div");
    document.body.appendChild(mount);
    const selected: Array<{ x: number; y: number; width: number; height: number }> = [];
    const canvasProto = HTMLCanvasElement.prototype as unknown as {
      getContext: (type?: string) => { drawImage: () => void } | null;
    };
    const originalGetContext = canvasProto.getContext;
    canvasProto.getContext = () => ({ drawImage() {} });
    new PageViewer({
      mount,
      imageUrl: "data:image/gif;base64,R0lGODlhAQABAAAAACw=",
      width: 200,
      height: 100,
      onSelect: (bbox) => selected.push(bbox),
    });
    const canvas = mount.querySelector("canvas")!;
    canvas.getBoundingClientRect = () =>
      ({ left: 0, top: 0, right: 200, bottom: 100, width: 200, height: 100, x: 0, y: 0, toJSON() {} });
    const pointer = (name: string, x: number, y: number) => {
      const event = new Event(name, { bubbles: true });
      Object.assign(event, { clientX: x, clientY: y });
      return event;
    };
    canvas.dispatchEvent(pointer("pointerdown", 10, 20));
    canvas.dispatchEvent(pointer("pointerup", 40, 50));
    expect(selected).toEqual([{ x: 10, y: 20, width: 30, height: 30 }]);
    canvasProto.getContext = originalGetContext;
    mount.remove();
  });
});

describe("ReviewQueue", () => {
  it("marks unresolved items", () => {
    const mount = document.createElement("div");
    new ReviewQueue(mount).render(
      [{ id: "r1", kind: "math", preview: "x^2", unresolved: true }],
    );
    expect(mount.querySelector("[data-unresolved='true']")?.textContent).toBe("x^2");
  });
});
