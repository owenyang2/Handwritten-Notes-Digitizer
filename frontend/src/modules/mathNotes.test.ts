import { describe, it, expect, vi } from "vitest";
vi.mock("../api/client.js", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/client.js")>()),
  listPageRegions: vi.fn(),
}));
import { MathEditor } from "./mathEditor.js";
import { MathNotesWorkspace } from "./mathNotes.js";
import { PageViewer } from "./pageViewer.js";
import { ReviewQueue } from "./reviewQueue.js";
import * as apiClient from "../api/client.js";
import type { DocumentPageSummary, PageRegionDetail } from "../api/client.js";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("MathEditor", () => {
  it("keeps prose, currency and markup literal in the review preview", () => {
    const mount = document.createElement("div");
    new MathEditor({ mount, latex: "Cost $10 <b>today</b>", plainText: true });
    const preview = mount.querySelector("[data-testid='math-editor-preview']")!;
    expect(preview.textContent).toBe("Cost $10 <b>today</b>");
    expect(preview.querySelector("b, .katex")).toBeNull();
  });
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

describe("MathNotesWorkspace page loading", () => {
  it("keeps the current review in place while loading, blocks stale edits, and retries without losing drafts", async () => {
    const page = (page_id: string, page_index: number): DocumentPageSummary => ({
      page_id,
      page_index,
      image_id: null,
      width: 400,
      height: 200,
      dpi: 200,
      coordinate_space: "page_pixels",
    });
    const region = (region_id: string, page_id: string, prediction_text: string): PageRegionDetail => ({
      region_id,
      page_id,
      content_type: "text",
      bbox: [0, 0, 120, 40],
      order_index: 0,
      prediction_text,
      reviewed_text: null,
      excluded: false,
      unresolved: true,
    });
    const firstPageRegions = [
      region("region-1", "page-1", "Original note"),
      region("region-2", "page-1", "Another note"),
    ];
    const secondPageRegions = [region("region-3", "page-2", "Second page note")];
    const loading = deferred<PageRegionDetail[]>();
    vi.mocked(apiClient.listPageRegions).mockReset();
    vi.mocked(apiClient.listPageRegions)
      .mockResolvedValueOnce(firstPageRegions)
      .mockReturnValueOnce(loading.promise)
      .mockResolvedValueOnce(secondPageRegions)
      .mockResolvedValueOnce(firstPageRegions);

    const mount = document.createElement("div");
    document.body.append(mount);
    const onNavigate = vi.fn();
    const workspace = new MathNotesWorkspace({
      mount,
      documentId: "notebook-1",
      pages: [page("page-1", 0), page("page-2", 1)],
      onNavigate,
    });
    try {
      await workspace.selectPage("page-1");
      mount.querySelector<HTMLButtonElement>('[data-region-id="region-1"]')!.click();
      const editor = mount.querySelector<HTMLTextAreaElement>("[data-testid='math-editor-source']")!;
      editor.value = "Unsaved correction";
      editor.dispatchEvent(new Event("input", { bubbles: true }));
      expect(workspace.hasUnsavedChanges()).toBe(true);

      const layout = mount.querySelector(".math-notes-layout");
      const viewer = mount.querySelector("[data-testid='page-viewer']");
      const pageSummary = mount.querySelector(".page-rail > summary");
      const switching = workspace.selectPage("page-2");
      await Promise.resolve();

      expect(apiClient.listPageRegions).toHaveBeenCalledTimes(2);
      expect(mount.querySelector(".math-notes-layout")).toBe(layout);
      expect(mount.querySelector("[data-testid='page-viewer']")).toBe(viewer);
      expect(mount.querySelector(".page-rail > summary")).toBe(pageSummary);
      expect(pageSummary?.textContent).toBe("Page 1 of 2");
      expect(mount.getAttribute("aria-busy")).toBe("true");
      expect(mount.inert).toBe(true);
      expect(mount.querySelector("[data-testid='math-notes-status']")?.textContent).toBe("Loading page 2…");

      mount.querySelector<HTMLButtonElement>('[data-region-id="region-2"]')!.click();
      expect(mount.querySelector("[data-testid='math-editor-source']")).toBe(editor);
      expect(editor.value).toBe("Unsaved correction");

      loading.reject(new Error("Network offline"));
      await switching;
      expect(mount.querySelector(".page-rail > summary")?.textContent).toBe("Page 1 of 2");
      expect(mount.getAttribute("aria-busy")).toBeNull();
      expect(mount.inert).toBe(false);
      expect(mount.querySelector("[data-testid='math-notes-status']")?.textContent).toContain("Select the page to retry.");
      expect(mount.querySelector("[data-testid='math-editor-source']")).toBe(editor);

      const retryLink = mount.querySelector<HTMLAnchorElement>('.page-rail a[href*="page-2"]')!;
      retryLink.focus();
      retryLink.click();
      await vi.waitFor(() => expect(mount.querySelector(".page-rail > summary")?.textContent).toBe("Page 2 of 2"));
      await vi.waitFor(() => expect(onNavigate).toHaveBeenCalledWith("page-2"));
      expect(document.activeElement).toBe(mount.querySelector(".page-rail > summary"));

      await workspace.selectPage("page-1");
      mount.querySelector<HTMLButtonElement>('[data-region-id="region-1"]')!.click();
      expect(mount.querySelector<HTMLTextAreaElement>("[data-testid='math-editor-source']")?.value).toBe("Unsaved correction");
      expect(workspace.hasUnsavedChanges()).toBe(true);
    } finally {
      workspace.dispose();
      mount.remove();
    }
  });
});
