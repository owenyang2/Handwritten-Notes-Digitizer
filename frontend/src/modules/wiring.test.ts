// Integration wiring tests for task 19.1.
//
// These tests verify that the frontend modules are correctly wired together:
// - UploadUI renders and calls its onSubmit with the selected File
// - CalibrationUI renders prompts and calls its onSubmitSample
// - main.bootstrap wires upload → recognize → editor display → correction → export
// - Capability gating based on GET /health conditionally shows/hides UI elements
//
// We mock the API client so tests run without a real backend.
// Validates: Requirements 11.4

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { UploadUI } from "./uploadUI.js";
import { CalibrationUI } from "./calibrationUI.js";
import { bootstrap } from "../main.js";

Object.assign(navigator, {
  clipboard: {
    writeText: vi.fn().mockResolvedValue(undefined),
  },
});

// ---------------------------------------------------------------------------
// Helper
// ---------------------------------------------------------------------------

function makeMount(): HTMLElement {
  const el = document.createElement("div");
  document.body.appendChild(el);
  return el;
}

function cleanup(el: HTMLElement): void {
  el.remove();
}

// ---------------------------------------------------------------------------
// API client mock helpers
// ---------------------------------------------------------------------------

// Mock API endpoints while the shared transport uses the stubbed fetch below.
vi.mock("../api/client.js", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/client.js")>()),
  getHealth: vi.fn(),
  uploadImage: vi.fn(),
  recognizeImage: vi.fn(),
  getRecognitionResult: vi.fn(),
  createDocument: vi.fn(),
  getDocument: vi.fn(),
  getDocuments: vi.fn(),
  getDocumentsPage: vi.fn(),
  getProcessedImageUrl: vi.fn((imageId: string) => `/api/images/${imageId}/processed`),
  submitCorrection: vi.fn(),
  saveImageReviews: vi.fn(),
  exportText: vi.fn(),
  exportPdf: vi.fn(),
  importDocument: vi.fn(),
  createPageRegion: vi.fn(),
  createRecognitionJob: vi.fn(),
  getRecognitionJob: vi.fn(),
  waitForRecognitionJob: vi.fn(),
  saveRegionReview: vi.fn(),
  listPageRegions: vi.fn(),
  getPageRasterUrl: vi.fn((pageId: string) => `/api/pages/${pageId}/raster`),
  exportDocument: vi.fn(),
  startCalibrationSession: vi.fn(),
  submitCalibrationSample: vi.fn(),
  getCurrentUser: vi.fn().mockResolvedValue(null),
  logout: vi.fn().mockResolvedValue(undefined),
  pushSyncOperations: vi.fn(),
  pullSyncOperations: vi.fn(),
  listSyncConflicts: vi.fn(),
  resolveSyncConflict: vi.fn(),
  registerSyncClient: vi.fn(),
  acknowledgeSyncCursor: vi.fn(),
  getConsent: vi.fn(),
  recordConsent: vi.fn(),
}));

vi.mock("../api/capabilities.js", () => ({
  refreshCapabilities: vi.fn(),
  isAvailable: vi.fn(),
  getSnapshot: vi.fn(),
}));

import * as apiClient from "../api/client.js";
import * as capabilitiesModule from "../api/capabilities.js";

// ---------------------------------------------------------------------------
// UploadUI unit tests
// ---------------------------------------------------------------------------

describe("UploadUI — rendering and interaction", () => {
  let mount: HTMLElement;

  beforeEach(() => {
    mount = makeMount();
  });
  afterEach(() => {
    cleanup(mount);
    vi.resetAllMocks();
  });

  it("renders a drop zone and submit button", () => {
    const ui = new UploadUI({ mount });
    ui.render();

    expect(mount.querySelector("[data-testid='upload-drop-zone']")).not.toBeNull();
    expect(mount.querySelector("[data-testid='upload-submit-btn']")).not.toBeNull();
  });

  it("submit button is disabled before a file is selected", () => {
    const ui = new UploadUI({ mount });
    ui.render();

    const btn = mount.querySelector<HTMLButtonElement>("[data-testid='upload-submit-btn']")!;
    expect(btn.disabled).toBe(true);
  });

  it("submit button enables after a valid file is selected via file input", () => {
    const ui = new UploadUI({ mount });
    ui.render();

    const fileInput = mount.querySelector<HTMLInputElement>("[data-testid='upload-file-input']")!;
    const file = new File(["data"], "test.png", { type: "image/png" });
    Object.defineProperty(fileInput, "files", {
      value: { 0: file, length: 1, item: () => file },
      configurable: true,
    });
    fileInput.dispatchEvent(new Event("change"));

    const btn = mount.querySelector<HTMLButtonElement>("[data-testid='upload-submit-btn']")!;
    expect(btn.disabled).toBe(false);
  });

    it("accepts a PDF upload", () => {
    const ui = new UploadUI({ mount });
    ui.render();

    const fileInput = mount.querySelector<HTMLInputElement>("[data-testid='upload-file-input']")!;
    const file = new File(["%PDF"], "doc.pdf", { type: "application/pdf" });
    Object.defineProperty(fileInput, "files", {
      value: { 0: file, length: 1, item: () => file },
      configurable: true,
    });
    fileInput.dispatchEvent(new Event("change"));

    const status = mount.querySelector("[data-testid='upload-status']")!;
    expect(status.textContent).toMatch(/selected: doc\.pdf/i);
    const btn = mount.querySelector<HTMLButtonElement>("[data-testid='upload-submit-btn']")!;
    expect(btn.disabled).toBe(false);
  });

  it("shows an error status for a non-image MIME type", () => {
    const ui = new UploadUI({ mount });
    ui.render();

    const fileInput = mount.querySelector<HTMLInputElement>("[data-testid='upload-file-input']")!;
    const file = new File(["data"], "notes.txt", { type: "text/plain" });
    Object.defineProperty(fileInput, "files", {
      value: { 0: file, length: 1, item: () => file },
      configurable: true,
    });
    fileInput.dispatchEvent(new Event("change"));

    const status = mount.querySelector("[data-testid='upload-status']")!;
    expect(status.textContent).toMatch(/not a png, jpeg, or pdf/i);
    const btn = mount.querySelector<HTMLButtonElement>("[data-testid='upload-submit-btn']")!;
    expect(btn.disabled).toBe(true);
  });

  it("shows an error status for an empty file", () => {
    const ui = new UploadUI({ mount });
    ui.render();

    const fileInput = mount.querySelector<HTMLInputElement>("[data-testid='upload-file-input']")!;
    const file = new File([], "empty.png", { type: "image/png" });
    Object.defineProperty(fileInput, "files", {
      value: { 0: file, length: 1, item: () => file },
      configurable: true,
    });
    fileInput.dispatchEvent(new Event("change"));

    const status = mount.querySelector("[data-testid='upload-status']")!;
    expect(status.textContent).toMatch(/empty/i);
    const btn = mount.querySelector<HTMLButtonElement>("[data-testid='upload-submit-btn']")!;
    expect(btn.disabled).toBe(true);
  });

  it("calls onSubmit with the selected file when submit button is clicked", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    const ui = new UploadUI({ mount, onSubmit });
    ui.render();

    const fileInput = mount.querySelector<HTMLInputElement>("[data-testid='upload-file-input']")!;
    const file = new File(["img"], "hw.png", { type: "image/png" });
    Object.defineProperty(fileInput, "files", {
      value: { 0: file, length: 1, item: () => file },
      configurable: true,
    });
    fileInput.dispatchEvent(new Event("change"));

    const btn = mount.querySelector<HTMLButtonElement>("[data-testid='upload-submit-btn']")!;
    btn.click();

    // Wait for the async submit to settle.
    await vi.waitFor(() => {
      expect(onSubmit).toHaveBeenCalledWith(file);
    });
  });

  it("setOnSubmit updates the handler after construction", async () => {
    const ui = new UploadUI({ mount });
    ui.render();

    const newHandler = vi.fn().mockResolvedValue(undefined);
    ui.setOnSubmit(newHandler);

    const fileInput = mount.querySelector<HTMLInputElement>("[data-testid='upload-file-input']")!;
    const file = new File(["img"], "hw.jpeg", { type: "image/jpeg" });
    Object.defineProperty(fileInput, "files", {
      value: { 0: file, length: 1, item: () => file },
      configurable: true,
    });
    fileInput.dispatchEvent(new Event("change"));

    const btn = mount.querySelector<HTMLButtonElement>("[data-testid='upload-submit-btn']")!;
    btn.click();

    await vi.waitFor(() => {
      expect(newHandler).toHaveBeenCalledWith(file);
    });
  });
});

// ---------------------------------------------------------------------------
// CalibrationUI unit tests
// ---------------------------------------------------------------------------

describe("CalibrationUI — rendering and interaction", () => {
  let mount: HTMLElement;

  beforeEach(() => {
    mount = makeMount();
  });
  afterEach(() => {
    cleanup(mount);
    vi.resetAllMocks();
  });

  const prompts = [
    { id: "p1", text: "The quick brown fox" },
    { id: "p2", text: "jumps over the lazy dog" },
    { id: "p3", text: "Pack my box with five" },
  ];

  it("renders a prompt item for each provided prompt", () => {
    const ui = new CalibrationUI({ mount });
    ui.render(prompts);

    const items = mount.querySelectorAll("[data-testid='calibration-prompt-item']");
    expect(items.length).toBe(3);
  });

  it("displays the correct prompt text for each item", () => {
    const ui = new CalibrationUI({ mount });
    ui.render(prompts);

    const texts = Array.from(
      mount.querySelectorAll("[data-testid='calibration-prompt-text']"),
    ).map((el) => el.textContent);
    expect(texts).toEqual(prompts.map((p) => p.text));
  });

  it("each prompt has a submit button that is initially disabled", () => {
    const ui = new CalibrationUI({ mount });
    ui.render(prompts);

    const btns = mount.querySelectorAll<HTMLButtonElement>("[data-testid='calibration-submit-btn']");
    expect(btns.length).toBe(3);
    btns.forEach((btn) => expect(btn.disabled).toBe(true));
  });

  it("submit button enables after a valid file is selected", () => {
    const ui = new CalibrationUI({ mount });
    ui.render(prompts);

    const fileInputs = mount.querySelectorAll<HTMLInputElement>("[data-testid='calibration-file-input']");
    const file = new File(["img"], "handwriting.png", { type: "image/png" });
    Object.defineProperty(fileInputs[0], "files", {
      value: { 0: file, length: 1, item: () => file },
      configurable: true,
    });
    fileInputs[0].dispatchEvent(new Event("change"));

    const btns = mount.querySelectorAll<HTMLButtonElement>("[data-testid='calibration-submit-btn']");
    expect(btns[0].disabled).toBe(false);
    // Other buttons unchanged.
    expect(btns[1].disabled).toBe(true);
  });

  it("calls onSubmitSample with correct promptId and file on button click", async () => {
    const onSubmitSample = vi.fn().mockResolvedValue(undefined);
    const ui = new CalibrationUI({ mount, onSubmitSample });
    ui.render(prompts);

    const fileInputs = mount.querySelectorAll<HTMLInputElement>("[data-testid='calibration-file-input']");
    const file = new File(["img"], "calib.png", { type: "image/png" });
    Object.defineProperty(fileInputs[1], "files", {
      value: { 0: file, length: 1, item: () => file },
      configurable: true,
    });
    fileInputs[1].dispatchEvent(new Event("change"));

    const btns = mount.querySelectorAll<HTMLButtonElement>("[data-testid='calibration-submit-btn']");
    btns[1].click();

    await vi.waitFor(() => {
      expect(onSubmitSample).toHaveBeenCalledWith("p2", file);
    });
  });

  it("setOnSubmitSample updates the handler after construction", async () => {
    const ui = new CalibrationUI({ mount });
    ui.render(prompts);

    const newHandler = vi.fn().mockResolvedValue(undefined);
    ui.setOnSubmitSample(newHandler);

    const fileInputs = mount.querySelectorAll<HTMLInputElement>("[data-testid='calibration-file-input']");
    const file = new File(["img"], "calib.jpeg", { type: "image/jpeg" });
    Object.defineProperty(fileInputs[0], "files", {
      value: { 0: file, length: 1, item: () => file },
      configurable: true,
    });
    fileInputs[0].dispatchEvent(new Event("change"));

    const btns = mount.querySelectorAll<HTMLButtonElement>("[data-testid='calibration-submit-btn']");
    btns[0].click();

    await vi.waitFor(() => {
      expect(newHandler).toHaveBeenCalledWith("p1", file);
    });
  });

  it("shows error status for an oversized file and keeps button disabled", () => {
    const ui = new CalibrationUI({ mount });
    ui.render(prompts);

    const fileInputs = mount.querySelectorAll<HTMLInputElement>("[data-testid='calibration-file-input']");
    // Create a file object with a large size (exceeds 10 MB).
    const bigFile = new File(["x"], "big.png", { type: "image/png" });
    Object.defineProperty(bigFile, "size", { value: 11 * 1024 * 1024 });
    Object.defineProperty(fileInputs[0], "files", {
      value: { 0: bigFile, length: 1, item: () => bigFile },
      configurable: true,
    });
    fileInputs[0].dispatchEvent(new Event("change"));

    const statuses = mount.querySelectorAll("[data-testid='calibration-prompt-status']");
    expect(statuses[0].textContent).toMatch(/10 mb/i);

    const btns = mount.querySelectorAll<HTMLButtonElement>("[data-testid='calibration-submit-btn']");
    expect(btns[0].disabled).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// bootstrap() wiring tests (upload → recognize → editor display)
// ---------------------------------------------------------------------------

describe("bootstrap() — upload to recognition wiring (Req 11.4)", () => {
  let root: HTMLElement;

  beforeEach(() => {
    root = makeMount();
    vi.resetAllMocks();

    // Default: all capabilities available.
    vi.mocked(capabilitiesModule.isAvailable).mockReturnValue(true);
    vi.mocked(capabilitiesModule.refreshCapabilities).mockResolvedValue({
      status: "ok",
      capabilities: {
        upload: { available: true },
        recognition: { available: true },
        correction: { available: true },
        calibration: { available: true },
        personalization: { available: true },
        export: { available: true },
      },
    });

    vi.mocked(apiClient.uploadImage).mockResolvedValue({ image_id: "img-123" });
    vi.mocked(apiClient.createDocument).mockResolvedValue({
      document_id: "doc-123",
      title: "hw",
      created_at: "2026-07-01T00:00:00Z",
      image_ids: [],
    });
    vi.mocked(apiClient.getDocuments).mockResolvedValue([]);
    vi.mocked(apiClient.listPageRegions).mockResolvedValue([]);
    vi.mocked(apiClient.getProcessedImageUrl).mockImplementation(
      (imageId: string) => `/api/images/${imageId}/processed`,
    );
    vi.mocked(apiClient.recognizeImage).mockResolvedValue({
      status: "ok",
      recognized_any_text: true,
      image_width: 120,
      image_height: 40,
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
    });
    vi.mocked(apiClient.submitCorrection).mockResolvedValue({
      result: "created",
      pair_id: "pair-1",
    });
    vi.mocked(apiClient.exportText).mockResolvedValue({
      status: "success",
      text: "hello world",
      format: "plain",
    });
    vi.mocked(apiClient.saveImageReviews).mockResolvedValue(undefined);
    vi.mocked(apiClient.exportPdf).mockResolvedValue(new Blob(["%PDF"], { type: "application/pdf" }));

    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string | URL | Request) => {
        const path = typeof url === "string" ? url : url.toString();
        if (path.includes("/personalization/status")) {
          return new Response(
            JSON.stringify({
              training_state: "none",
              adapter_active: false,
              last_run_outcome: null,
              reject_reason: null,
              notification: null,
              unused_training_pairs: 2,
              training_trigger_threshold: 20,
              max_training_epochs: 50,
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
        }
        if (path.includes("/personalization/evaluation")) {
          return new Response(
            JSON.stringify({
              availability: "unavailable",
              before_cer: null,
              before_wer: null,
              after_cer: null,
              after_wer: null,
              delta_cer: null,
              delta_wer: null,
              unavailable_reason: "Not enough data yet.",
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
        }
        if (path.includes("/personalization/training-pairs")) {
          return new Response(
            JSON.stringify({ items: [] }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
        }
        return new Response("Not found", { status: 404 });
      }),
    );
  });

  afterEach(() => {
    cleanup(root);
    vi.resetAllMocks();
    vi.unstubAllGlobals();
  });

  it("renders the upload section on bootstrap", async () => {
    await bootstrap(root);

    expect(root.querySelector("[data-testid='upload-section']")).not.toBeNull();
    expect(root.querySelector("[data-testid='upload-drop-zone']")).not.toBeNull();
  });

  it("keeps calibration visible by default and switches workspace tool tabs", async () => {
    await bootstrap(root);

    const calibrationPanel = root.querySelector<HTMLElement>("#utility-panel-calibration")!;
    const historyPanel = root.querySelector<HTMLElement>("#utility-panel-history")!;
    expect(calibrationPanel.hidden).toBe(false);
    expect(historyPanel.hidden).toBe(true);
    expect(root.querySelector("[data-testid='calibration-start-btn']")).not.toBeNull();

    root.querySelector<HTMLButtonElement>("#utility-tab-history")!.click();

    expect(calibrationPanel.hidden).toBe(true);
    expect(historyPanel.hidden).toBe(false);
  });

  it("editor section is hidden before a file is uploaded", async () => {
    await bootstrap(root);

    const editorSection = root.querySelector<HTMLElement>("[data-testid='editor-section']")!;
    expect(editorSection.style.display).toBe("none");
  });

  it("export section is hidden before a file is uploaded", async () => {
    await bootstrap(root);

    const exportSection = root.querySelector<HTMLElement>("[data-testid='export-section']")!;
    expect(exportSection.style.display).toBe("none");
  });

  it("upload flow calls uploadImage then recognizeImage with the image_id", async () => {
    await bootstrap(root);

    const fileInput = root.querySelector<HTMLInputElement>("[data-testid='upload-file-input']")!;
    const file = new File(["img"], "hw.png", { type: "image/png" });
    Object.defineProperty(fileInput, "files", {
      value: { 0: file, length: 1, item: () => file },
      configurable: true,
    });
    fileInput.dispatchEvent(new Event("change"));

    const btn = root.querySelector<HTMLButtonElement>("[data-testid='upload-submit-btn']")!;
    btn.click();

    await vi.waitFor(() => {
      expect(apiClient.createDocument).toHaveBeenCalledWith("hw");
      expect(apiClient.uploadImage).toHaveBeenCalledWith(file, "doc-123");
      expect(apiClient.recognizeImage).toHaveBeenCalledWith("img-123", expect.any(Function));
      expect(apiClient.getProcessedImageUrl).toHaveBeenCalledWith("img-123");
      expect(root.querySelector<HTMLImageElement>("[data-testid='editor-image']")?.src)
        .toContain("/api/images/img-123/processed");
    });
  });

  it("editor section becomes visible after successful recognition", async () => {
    await bootstrap(root);

    const fileInput = root.querySelector<HTMLInputElement>("[data-testid='upload-file-input']")!;
    const file = new File(["img"], "hw.png", { type: "image/png" });
    Object.defineProperty(fileInput, "files", {
      value: { 0: file, length: 1, item: () => file },
      configurable: true,
    });
    fileInput.dispatchEvent(new Event("change"));

    const btn = root.querySelector<HTMLButtonElement>("[data-testid='upload-submit-btn']")!;
    btn.click();

    await vi.waitFor(() => {
      const editorSection = root.querySelector<HTMLElement>("[data-testid='editor-section']")!;
      expect(editorSection.style.display).not.toBe("none");
    });
  });

  it("editor displays recognized words after recognition", async () => {
    await bootstrap(root);

    const fileInput = root.querySelector<HTMLInputElement>("[data-testid='upload-file-input']")!;
    const file = new File(["img"], "hw.png", { type: "image/png" });
    Object.defineProperty(fileInput, "files", {
      value: { 0: file, length: 1, item: () => file },
      configurable: true,
    });
    fileInput.dispatchEvent(new Event("change"));
    root.querySelector<HTMLButtonElement>("[data-testid='upload-submit-btn']")!.click();

    await vi.waitFor(() => {
      const words = root.querySelectorAll("[data-testid='editor-word']");
      expect(words.length).toBe(2);
      expect(words[0].textContent).toContain("hello");
      expect(words[1].textContent).toContain("world");
    });
  });

  it("export section becomes visible after recognition", async () => {
    await bootstrap(root);

    const fileInput = root.querySelector<HTMLInputElement>("[data-testid='upload-file-input']")!;
    const file = new File(["img"], "hw.png", { type: "image/png" });
    Object.defineProperty(fileInput, "files", {
      value: { 0: file, length: 1, item: () => file },
      configurable: true,
    });
    fileInput.dispatchEvent(new Event("change"));
    root.querySelector<HTMLButtonElement>("[data-testid='upload-submit-btn']")!.click();

    await vi.waitFor(() => {
      const exportSection = root.querySelector<HTMLElement>("[data-testid='export-section']")!;
      expect(exportSection.style.display).not.toBe("none");
    });
  });

  it("shows the formula review workspace after a PDF import", async () => {
    vi.mocked(apiClient.importDocument).mockResolvedValueOnce({
      document_id: "doc-pdf",
      title: "calculus",
      page_count: 1,
      pages: [{
        page_id: "page-1",
        page_index: 0,
        image_id: "img-page-1",
        width: 320,
        height: 180,
        dpi: 200,
        coordinate_space: "page-raster-v1",
      }],
    });

    await bootstrap(root);
    const fileInput = root.querySelector<HTMLInputElement>("[data-testid='upload-file-input']")!;
    const file = new File(["%PDF-1.7"], "calculus.pdf", { type: "application/pdf" });
    Object.defineProperty(fileInput, "files", {
      value: { 0: file, length: 1, item: () => file },
      configurable: true,
    });
    fileInput.dispatchEvent(new Event("change"));
    root.querySelector<HTMLButtonElement>("[data-testid='upload-submit-btn']")!.click();

    await vi.waitFor(() => {
      const section = root.querySelector<HTMLElement>("[data-testid='math-notes-section']")!;
      expect(section.style.display).not.toBe("none");
      expect(section.textContent).toContain("Formula review");
    });
    expect(apiClient.importDocument).toHaveBeenCalledWith(file, "calculus");
  });

  it("reopens an imported document from history without requiring a legacy result", async () => {
    vi.mocked(apiClient.getDocuments).mockResolvedValueOnce([{
      document_id: "doc-pdf",
      title: "calculus",
      created_at: "2026-07-01T00:00:00Z",
      image_ids: ["img-page-1"],
      pages: [{
        page_id: "page-1",
        page_index: 0,
        image_id: "img-page-1",
        width: 320,
        height: 180,
        dpi: 200,
        coordinate_space: "page-raster-v1",
      }],
    }]);
    vi.mocked(apiClient.getDocument).mockResolvedValueOnce({
      document_id: "doc-pdf",
      title: "calculus",
      created_at: "2026-07-01T00:00:00Z",
      image_ids: ["img-page-1"],
      pages: [{
        page_id: "page-1",
        page_index: 0,
        image_id: "img-page-1",
        width: 320,
        height: 180,
        dpi: 200,
        coordinate_space: "page-raster-v1",
      }],
    });

    await bootstrap(root);
    const historyButton = await vi.waitFor(() => {
      const found = root.querySelector<HTMLButtonElement>(".gallery-document");
      expect(found).not.toBeNull();
      return found!;
    });
    historyButton.click();

    await vi.waitFor(() => {
      const section = root.querySelector<HTMLElement>("[data-testid='math-notes-section']")!;
      expect(section.style.display).not.toBe("none");
      expect(section.textContent).toContain("Formula review");
    });
    expect(apiClient.getRecognitionResult).not.toHaveBeenCalled();
  });

  it("confirmed correction calls submitCorrection with correct data", async () => {
    await bootstrap(root);

    const fileInput = root.querySelector<HTMLInputElement>("[data-testid='upload-file-input']")!;
    const file = new File(["img"], "hw.png", { type: "image/png" });
    Object.defineProperty(fileInput, "files", {
      value: { 0: file, length: 1, item: () => file },
      configurable: true,
    });
    fileInput.dispatchEvent(new Event("change"));
    root.querySelector<HTMLButtonElement>("[data-testid='upload-submit-btn']")!.click();

    // Wait for the editor to be populated.
    await vi.waitFor(() => {
      const words = root.querySelectorAll("[data-testid='editor-word']");
      expect(words.length).toBeGreaterThan(0);
    });

    // Double-click the first word to enter edit mode.
    const words = root.querySelectorAll<HTMLElement>("[data-testid='editor-word']");
    words[0].dispatchEvent(new MouseEvent("dblclick", { bubbles: true }));

    const input = root.querySelector<HTMLInputElement>("[data-testid='editor-word-input']")!;
    expect(input).not.toBeNull();
    input.value = "hi";
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));

    await vi.waitFor(() => {
      expect(apiClient.submitCorrection).toHaveBeenCalledWith(
        "img-123",
        expect.objectContaining({
          word_index: 0,
          line_index: 0,
          recognized_text: "hello",
          corrected_text: "hi",
        }),
      );
    });
  });

  it("export button calls exportText with the image_id and format", async () => {
    // Mock URL.createObjectURL/revokeObjectURL (not available in jsdom).
    const originalCreateURL = URL.createObjectURL;
    const originalRevokeURL = URL.revokeObjectURL;
    URL.createObjectURL = vi.fn().mockReturnValue("blob:mock");
    URL.revokeObjectURL = vi.fn();

    // Prevent jsdom navigation error from anchor.click() in export handler.
    const origCreate = document.createElement.bind(document);
    const createSpy = vi.spyOn(document, "createElement").mockImplementation((tag: string) => {
      const el = origCreate(tag);
      if (tag === "a") {
        vi.spyOn(el as HTMLAnchorElement, "click").mockImplementation(() => {/* noop */});
      }
      return el;
    });

    await bootstrap(root);

    const fileInput = root.querySelector<HTMLInputElement>("[data-testid='upload-file-input']")!;
    const file = new File(["img"], "hw.png", { type: "image/png" });
    Object.defineProperty(fileInput, "files", {
      value: { 0: file, length: 1, item: () => file },
      configurable: true,
    });
    fileInput.dispatchEvent(new Event("change"));
    root.querySelector<HTMLButtonElement>("[data-testid='upload-submit-btn']")!.click();

    await vi.waitFor(() => {
      const exportSection = root.querySelector<HTMLElement>("[data-testid='export-section']")!;
      expect(exportSection.style.display).not.toBe("none");
    });

    // Click the plain-text export button.
    const exportBtn = root.querySelector<HTMLButtonElement>("[data-testid='export-btn-copy']")!;
    exportBtn.click();

    await vi.waitFor(() => {
      expect(apiClient.exportText).toHaveBeenCalledWith(
        expect.objectContaining({
          image_id: "img-123",
          format: "plain",
        }),
      );
    });

    URL.createObjectURL = originalCreateURL;
    URL.revokeObjectURL = originalRevokeURL;
    createSpy.mockRestore();
  });

  it("persists edited uncertain line text then exports through the backend", async () => {
    const originalCreateURL = URL.createObjectURL;
    const originalRevokeURL = URL.revokeObjectURL;
    URL.createObjectURL = vi.fn().mockReturnValue("blob:line-edit");
    URL.revokeObjectURL = vi.fn();

    const origCreate = document.createElement.bind(document);
    const createSpy = vi.spyOn(document, "createElement").mockImplementation((tag: string) => {
      const el = origCreate(tag);
      if (tag === "a") {
        vi.spyOn(el as HTMLAnchorElement, "click").mockImplementation(() => {/* noop */});
      }
      return el;
    });

    vi.mocked(apiClient.recognizeImage).mockResolvedValueOnce({
      status: "ok",
      recognized_any_text: true,
      image_width: 320,
      image_height: 60,
      lines: [
        {
          index: 0,
          text: "quaintly but kept driving their oxen forward",
          alignment_status: "uncertain",
          alignment_reason: "Decoded 7 text token(s) for 1 detected word region(s).",
          region: { x: 0, y: 0, width: 300, height: 30 },
          words: [
            {
              index: 0,
              text: "quaintly but kept driving their oxen forward",
              region: { x: 0, y: 0, width: 300, height: 30 },
            },
          ],
        },
      ],
    });

    await bootstrap(root);

    const fileInput = root.querySelector<HTMLInputElement>("[data-testid='upload-file-input']")!;
    const file = new File(["img"], "hw.png", { type: "image/png" });
    Object.defineProperty(fileInput, "files", {
      value: { 0: file, length: 1, item: () => file },
      configurable: true,
    });
    fileInput.dispatchEvent(new Event("change"));
    root.querySelector<HTMLButtonElement>("[data-testid='upload-submit-btn']")!.click();

    const textarea = await vi.waitFor(() => {
      const found = root.querySelector<HTMLTextAreaElement>(
        "[data-testid='editor-line-textarea']",
      );
      expect(found).not.toBeNull();
      return found!;
    });
    textarea.value = "acted quaintly but kept driving their oxen forward";
    textarea.dispatchEvent(new Event("input", { bubbles: true }));

    const exportBtn = root.querySelector<HTMLButtonElement>("[data-testid='export-btn-copy']")!;
    exportBtn.click();

    await vi.waitFor(() => {
      expect(apiClient.saveImageReviews).toHaveBeenCalled();
      expect(apiClient.exportText).toHaveBeenCalledWith(
        expect.objectContaining({
          image_id: "img-123",
          format: "plain",
        }),
      );
      expect(root.querySelector("[data-testid='export-status']")!.textContent).toMatch(/ready/i);
    });

    URL.createObjectURL = originalCreateURL;
    URL.revokeObjectURL = originalRevokeURL;
    createSpy.mockRestore();
  });

  it("saving a reviewed line submits a line crop training pair", async () => {
    vi.mocked(apiClient.recognizeImage).mockResolvedValueOnce({
      status: "ok",
      recognized_any_text: true,
      image_width: 510,
      image_height: 184,
      lines: [
        {
          index: 0,
          text: "Jaded rombies arted quaintly but hept driving",
          alignment_status: "uncertain",
          alignment_reason: "Decoded 7 text token(s) for 3 detected word region(s).",
          region: { x: 10, y: 20, width: 300, height: 44 },
          words: [],
        },
      ],
    });

    await bootstrap(root);

    const fileInput = root.querySelector<HTMLInputElement>("[data-testid='upload-file-input']")!;
    const file = new File(["img"], "hw.png", { type: "image/png" });
    Object.defineProperty(fileInput, "files", {
      value: { 0: file, length: 1, item: () => file },
      configurable: true,
    });
    fileInput.dispatchEvent(new Event("change"));
    root.querySelector<HTMLButtonElement>("[data-testid='upload-submit-btn']")!.click();

    const textarea = await vi.waitFor(() => {
      const found = root.querySelector<HTMLTextAreaElement>(
        "[data-testid='editor-line-textarea']",
      );
      expect(found).not.toBeNull();
      return found!;
    });
    textarea.value = "Jaded zombies acted quaintly but kept driving";
    textarea.dispatchEvent(new Event("input", { bubbles: true }));

    const overlay = root.querySelector<HTMLElement>(".editor-region-overlay")!;
    Object.defineProperty(overlay, "getBoundingClientRect", {
      value: () => ({ x: 0, y: 0, left: 0, top: 0, width: 510, height: 184, right: 510, bottom: 184 }),
      configurable: true,
    });
    const handle = root.querySelector<HTMLElement>("[data-testid='editor-region-resize-handle']")!;
    handle.dispatchEvent(new MouseEvent("mousedown", { button: 0, clientX: 0, clientY: 0, bubbles: true }));
    document.dispatchEvent(new MouseEvent("mousemove", { clientX: 20, clientY: 0, bubbles: true }));
    document.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
    root.querySelector<HTMLButtonElement>("[data-testid='editor-line-save']")!.click();

    await vi.waitFor(() => {
      expect(apiClient.submitCorrection).toHaveBeenCalledWith(
        "img-123",
        expect.objectContaining({
          line_index: 0,
          region: { x: 10, y: 20, width: 320, height: 44 },
          recognized_text: "Jaded rombies arted quaintly but hept driving",
          corrected_text: "Jaded zombies acted quaintly but kept driving",
          store_if_unchanged: true,
        }),
      );
    });
  });
});

// ---------------------------------------------------------------------------
// Capability gating tests (Req 11.5, 11.6)
// ---------------------------------------------------------------------------

describe("bootstrap() — capability gating (Req 11.5, 11.6)", () => {
  let root: HTMLElement;

  beforeEach(() => {
    root = makeMount();
    vi.resetAllMocks();
  });

  afterEach(() => {
    cleanup(root);
    vi.resetAllMocks();
  });

  it("shows a capability banner when the backend reports degraded status", async () => {
    vi.mocked(capabilitiesModule.refreshCapabilities).mockResolvedValue({
      status: "degraded",
      capabilities: {
        upload: { available: true },
        recognition: { available: false, reason: "ML runtime not loaded" },
        correction: { available: true },
        calibration: { available: false, reason: "Calibration service down" },
        personalization: { available: false, reason: "Training service down" },
        export: { available: true },
      },
    });
    vi.mocked(capabilitiesModule.isAvailable).mockImplementation(
      (cap) => !["recognition", "calibration", "personalization"].includes(cap),
    );

    await bootstrap(root);

    // Wait for the capability promise to resolve.
    await vi.waitFor(() => {
      const banner = root.querySelector<HTMLElement>("[data-testid='capability-banner']")!;
      expect(banner.style.display).not.toBe("none");
      expect(banner.textContent).toMatch(/unavailable/i);
    });
  });

  it("capability banner is hidden when all capabilities are available", async () => {
    vi.mocked(capabilitiesModule.refreshCapabilities).mockResolvedValue({
      status: "ok",
      capabilities: {
        upload: { available: true },
        recognition: { available: true },
        correction: { available: true },
        calibration: { available: true },
        personalization: { available: true },
        export: { available: true },
      },
    });
    vi.mocked(capabilitiesModule.isAvailable).mockReturnValue(true);

    await bootstrap(root);

    await new Promise((resolve) => setTimeout(resolve, 10));

    const banner = root.querySelector<HTMLElement>("[data-testid='capability-banner']")!;
    expect(banner.style.display).toBe("none");
  });

  it("renders personalization guide when capability is available", async () => {
    vi.mocked(capabilitiesModule.refreshCapabilities).mockResolvedValue({
      status: "ok",
      capabilities: {
        upload: { available: true },
        recognition: { available: true },
        correction: { available: true },
        calibration: { available: true },
        personalization: { available: true },
        export: { available: true },
      },
    });
    vi.mocked(capabilitiesModule.isAvailable).mockReturnValue(true);

    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string | URL | Request) => {
        const path = typeof url === "string" ? url : url.toString();
        if (path.includes("/personalization/status")) {
          return new Response(
            JSON.stringify({
              training_state: "none",
              adapter_active: false,
              last_run_outcome: null,
              reject_reason: null,
              notification: null,
              unused_training_pairs: 2,
              training_trigger_threshold: 20,
              max_training_epochs: 50,
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
        }
        if (path.includes("/personalization/evaluation")) {
          return new Response(
            JSON.stringify({
              availability: "unavailable",
              before_cer: null,
              before_wer: null,
              after_cer: null,
              after_wer: null,
              delta_cer: null,
              delta_wer: null,
              unavailable_reason: "Not enough data yet.",
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
        }
        if (path.includes("/personalization/training-pairs")) {
          return new Response(
            JSON.stringify({ items: [] }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
        }
        return new Response("Not found", { status: 404 });
      }),
    );

    await bootstrap(root);

    await vi.waitFor(() => {
      expect(root.querySelector("[data-testid='personalization-section']")).not.toBeNull();
      expect(root.querySelector("[data-testid='personalization-guide']")).not.toBeNull();
    });

    vi.unstubAllGlobals();
  });

  it("hides personalization section when capability is unavailable", async () => {
    vi.mocked(capabilitiesModule.refreshCapabilities).mockResolvedValue({
      status: "degraded",
      capabilities: {
        upload: { available: true },
        recognition: { available: true },
        correction: { available: true },
        calibration: { available: true },
        personalization: { available: false, reason: "unavailable" },
        export: { available: true },
      },
    });
    vi.mocked(capabilitiesModule.isAvailable).mockImplementation(
      (cap) => cap !== "personalization",
    );

    await bootstrap(root);

    await vi.waitFor(() => {
      const section = root.querySelector<HTMLElement>(
        "[data-testid='personalization-section']",
      )!;
      expect(section.style.display).toBe("none");
    });
  });

  it("hides calibration section when calibration capability is unavailable", async () => {
    vi.mocked(capabilitiesModule.refreshCapabilities).mockResolvedValue({
      status: "degraded",
      capabilities: {
        upload: { available: true },
        recognition: { available: true },
        correction: { available: true },
        calibration: { available: false, reason: "unavailable" },
        personalization: { available: true },
        export: { available: true },
      },
    });
    vi.mocked(capabilitiesModule.isAvailable).mockImplementation(
      (cap) => cap !== "calibration",
    );

    await bootstrap(root);

    await vi.waitFor(() => {
      const calibrationWrapper = root.querySelector<HTMLElement>(
        "[data-testid='calibration-section-wrapper']",
      )!;
      expect(calibrationWrapper.style.display).toBe("none");
    });
  });
});

// ---------------------------------------------------------------------------
// API client layer tests (unit tests for the client module structure)
// ---------------------------------------------------------------------------

describe("API client module — exports expected functions", () => {
  it("exports getHealth, uploadImage, recognizeImage, submitCorrection, exportText", async () => {
    // Dynamic import to check the actual module structure.
    const clientModule = await import("../api/client.js");
    expect(typeof clientModule.getHealth).toBe("function");
    expect(typeof clientModule.uploadImage).toBe("function");
    expect(typeof clientModule.recognizeImage).toBe("function");
    expect(typeof clientModule.submitCorrection).toBe("function");
    expect(typeof clientModule.exportText).toBe("function");
    expect(typeof clientModule.saveImageReviews).toBe("function");
    expect(typeof clientModule.exportPdf).toBe("function");
    expect(typeof clientModule.importDocument).toBe("function");
    expect(typeof clientModule.createPageRegion).toBe("function");
    expect(typeof clientModule.exportDocument).toBe("function");
    expect(typeof clientModule.startCalibrationSession).toBe("function");
    expect(typeof clientModule.submitCalibrationSample).toBe("function");
  });
});

describe("Capabilities module — exports expected functions", () => {
  it("exports refreshCapabilities, isAvailable, getSnapshot", async () => {
    const module = await import("../api/capabilities.js");
    expect(typeof module.refreshCapabilities).toBe("function");
    expect(typeof module.isAvailable).toBe("function");
    expect(typeof module.getSnapshot).toBe("function");
  });
});
