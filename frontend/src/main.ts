// Frontend entry point.
//
// Wires the four UI modules together through the API client:
//   upload → recognize → Editor display → inline correction → export
//   plus the optional calibration UI and capability gating via GET /health.
//
// Task 19.1 — full wiring of upload → recognize → Editor → correction → export
// and calibration UI via the web interface (Req 11.4).

import { UploadUI } from "./modules/uploadUI.js";
import { Editor } from "./modules/editor.js";
import { CalibrationUI } from "./modules/calibrationUI.js";
import { BeforeAfterView } from "./modules/beforeAfterView.js";
import { AuthGate } from "./modules/authGate.js";
import { GalleryUI } from "./modules/galleryUI.js";
import { registerServiceWorker } from "./serviceWorker.js";
import "./styles.css";
import {
  uploadImage,
  recognizeImage,
  submitCorrection,
  exportText,
  startCalibrationSession,
  submitCalibrationSample,
  getCurrentUser,
  logout,
  getProcessedImageUrl,
  createDocument,
  getDocument,
  getRecognitionResult,
  ApiError,
} from "./api/client.js";
import { refreshCapabilities, isAvailable } from "./api/capabilities.js";
import type { RecognitionResult, RecognizedLine, RecognizedWord } from "./types.js";
import type { RecognitionResultDto, RecognizedLineDto, RecognizedWordDto } from "./api/types.js";

// ---------------------------------------------------------------------------
// DTO → domain type mappers
// ---------------------------------------------------------------------------

function mapWord(dto: RecognizedWordDto): RecognizedWord {
  return {
    index: dto.index,
    text: dto.text,
    region: { x: dto.region.x, y: dto.region.y, width: dto.region.width, height: dto.region.height },
    confidence: dto.confidence,
  };
}

function mapLine(dto: RecognizedLineDto): RecognizedLine {
  return {
    index: dto.index,
    region: { x: dto.region.x, y: dto.region.y, width: dto.region.width, height: dto.region.height },
    text: dto.text,
    alignmentStatus: dto.alignment_status,
    alignmentReason: dto.alignment_reason ?? undefined,
    words: dto.words.map(mapWord),
  };
}

function mapResult(dto: RecognitionResultDto): RecognitionResult {
  return {
    lines: dto.lines.map(mapLine),
    recognizedAnyText: dto.recognized_any_text,
    imageWidth: dto.image_width,
    imageHeight: dto.image_height,
    status: dto.status,
    message: dto.message,
  };
}

// ---------------------------------------------------------------------------
// App bootstrap
// ---------------------------------------------------------------------------

export async function bootstrap(root: HTMLElement): Promise<void> {
  // ------------------------------------------------------------------
  // 1. Fetch capability registry on startup to gate features.
  //    The UI renders optimistically before this completes; gates are
  //    enforced once the snapshot arrives.
  // ------------------------------------------------------------------
  const capabilitiesPromise = refreshCapabilities();

  // ------------------------------------------------------------------
  // 2. Build the page layout
  // ------------------------------------------------------------------
  root.innerHTML = "";

  const shell = document.createElement("div");
  shell.className = "app-shell";
  root.appendChild(shell);

  const rail = document.createElement("aside");
  rail.className = "workflow-rail";
  rail.setAttribute("aria-label", "Workflow");
  shell.appendChild(rail);

  const brand = document.createElement("div");
  brand.className = "app-brand";
  rail.appendChild(brand);

  const appTitle = document.createElement("h1");
  appTitle.className = "app-title";
  appTitle.textContent = "Handwriting Notes Digitizer";
  brand.appendChild(appTitle);

  const brandSub = document.createElement("p");
  brandSub.className = "app-subtitle";
  brandSub.textContent = "Review handwriting, correct regions, and build a personal training set.";
  brand.appendChild(brandSub);

  const workflow = document.createElement("ol");
  workflow.className = "workflow-list";
  const steps = [
    ["Upload", "Add a note image"],
    ["Review", "Fix text and alignment"],
    ["Train", "Audit collected samples"],
    ["Export", "Download clean text"],
  ];
  for (const [name, detail] of steps) {
    const item = document.createElement("li");
    item.className = "workflow-item";
    const label = document.createElement("span");
    label.className = "workflow-item-label";
    label.textContent = name;
    item.appendChild(label);
    const desc = document.createElement("span");
    desc.className = "workflow-item-desc";
    desc.textContent = detail;
    item.appendChild(desc);
    workflow.appendChild(item);
  }
  rail.appendChild(workflow);

  const workspace = document.createElement("main");
  workspace.className = "app-workspace";
  shell.appendChild(workspace);

  const workspaceHeader = document.createElement("header");
  workspaceHeader.className = "workspace-header";
  workspace.appendChild(workspaceHeader);

  const headerCopy = document.createElement("div");
  workspaceHeader.appendChild(headerCopy);
  const headerTitle = document.createElement("h2");
  headerTitle.textContent = "Recognition review";
  headerCopy.appendChild(headerTitle);
  const headerText = document.createElement("p");
  headerText.textContent =
    "Only region-backed word edits feed training. Uncertain lines stay editable for export until their regions are reviewed.";
  headerCopy.appendChild(headerText);

  // Capability status banner (shown when backend is degraded / unreachable).
  const capabilityBanner = document.createElement("div");
  capabilityBanner.className = "capability-banner";
  capabilityBanner.setAttribute("data-testid", "capability-banner");
  capabilityBanner.style.display = "none";
  workspaceHeader.appendChild(capabilityBanner);

  const workGrid = document.createElement("div");
  workGrid.className = "work-grid";
  workspace.appendChild(workGrid);

  const reviewColumn = document.createElement("div");
  reviewColumn.className = "review-column";
  workGrid.appendChild(reviewColumn);

  const sideColumn = document.createElement("aside");
  sideColumn.className = "side-column";
  workGrid.appendChild(sideColumn);

  const utilityTabList = document.createElement("div");
  utilityTabList.className = "utility-tab-list";
  utilityTabList.setAttribute("role", "tablist");
  utilityTabList.setAttribute("aria-label", "Workspace tools");
  sideColumn.appendChild(utilityTabList);

  const utilityPanels = document.createElement("div");
  utilityPanels.className = "utility-tab-panels";
  sideColumn.appendChild(utilityPanels);

  type UtilityTabId = "calibration" | "training" | "export" | "history";
  const utilityTabs = new Map<UtilityTabId, { button: HTMLButtonElement; panel: HTMLElement }>();
  let activeUtilityTab: UtilityTabId = "calibration";

  const activateUtilityTab = (tabId: UtilityTabId): void => {
    const target = utilityTabs.get(tabId);
    if (!target || target.button.hidden) return;
    activeUtilityTab = tabId;
    for (const [id, tab] of utilityTabs) {
      const active = id === tabId;
      tab.button.setAttribute("aria-selected", active ? "true" : "false");
      tab.panel.hidden = !active;
    }
  };

  const addUtilityTab = (tabId: UtilityTabId, label: string): HTMLElement => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "utility-tab";
    button.id = `utility-tab-${tabId}`;
    button.textContent = label;
    button.setAttribute("role", "tab");
    button.setAttribute("aria-controls", `utility-panel-${tabId}`);
    button.setAttribute("aria-selected", tabId === activeUtilityTab ? "true" : "false");
    button.addEventListener("click", () => activateUtilityTab(tabId));
    utilityTabList.appendChild(button);

    const panel = document.createElement("div");
    panel.className = "utility-tab-panel";
    panel.id = `utility-panel-${tabId}`;
    panel.setAttribute("role", "tabpanel");
    panel.setAttribute("aria-labelledby", button.id);
    panel.hidden = tabId !== activeUtilityTab;
    utilityPanels.appendChild(panel);
    utilityTabs.set(tabId, { button, panel });
    return panel;
  };

  const setUtilityTabVisible = (tabId: UtilityTabId, visible: boolean): void => {
    const tab = utilityTabs.get(tabId);
    if (!tab) return;
    tab.button.hidden = !visible;
    tab.panel.hidden = !visible || activeUtilityTab !== tabId;
    if (!visible && activeUtilityTab === tabId) {
      const firstAvailable = [...utilityTabs.entries()].find(([, value]) => !value.button.hidden);
      if (firstAvailable) activateUtilityTab(firstAvailable[0]);
    }
  };

  const calibrationPanel = addUtilityTab("calibration", "Calibration");
  const trainingPanel = addUtilityTab("training", "Training");
  const exportPanel = addUtilityTab("export", "Export");
  const historyPanel = addUtilityTab("history", "History");

  // --- Personalization guide + status (visible when capability is available) ---
  const personalizationSection = document.createElement("section");
  personalizationSection.className = "app-section app-section--personalization panel";
  personalizationSection.setAttribute("data-testid", "personalization-section");
  personalizationSection.setAttribute("aria-label", "Personal handwriting learning");
  trainingPanel.appendChild(personalizationSection);

  // --- Upload section ---
  const uploadSection = document.createElement("section");
  uploadSection.className = "app-section app-section--upload panel";
  uploadSection.setAttribute("data-testid", "upload-section");
  uploadSection.setAttribute("aria-label", "Upload image");
  reviewColumn.appendChild(uploadSection);

  // --- Editor section (hidden until recognition completes) ---
  const editorSection = document.createElement("section");
  editorSection.className = "app-section app-section--editor panel panel--review";
  editorSection.setAttribute("data-testid", "editor-section");
  editorSection.setAttribute("aria-label", "Recognized text editor");
  editorSection.style.display = "none";
  reviewColumn.appendChild(editorSection);

  // --- Export section (hidden until editor is populated) ---
  const exportSection = document.createElement("section");
  exportSection.className = "app-section app-section--export panel";
  exportSection.setAttribute("data-testid", "export-section");
  exportSection.setAttribute("aria-label", "Export text");
  exportSection.style.display = "none";
  exportPanel.appendChild(exportSection);

  // --- Calibration section (always available but optional) ---
  const calibrationSection = document.createElement("section");
  calibrationSection.className = "app-section app-section--calibration panel";
  calibrationSection.setAttribute("data-testid", "calibration-section-wrapper");
  calibrationSection.setAttribute("aria-label", "Optional calibration");
  calibrationPanel.appendChild(calibrationSection);

  const gallerySection = document.createElement("section");
  gallerySection.id = "document-history";
  gallerySection.className = "app-section app-section--gallery panel";
  gallerySection.setAttribute("data-testid", "document-history");
  gallerySection.setAttribute("aria-label", "Document history");
  historyPanel.appendChild(gallerySection);

  // --- Before/After metrics (nested inside personalization section) ---
  const beforeAfterSection = document.createElement("div");
  beforeAfterSection.className = "app-section app-section--before-after";
  beforeAfterSection.setAttribute("data-testid", "before-after-section");
  beforeAfterSection.setAttribute("aria-label", "Accuracy before and after personalization");
  personalizationSection.appendChild(beforeAfterSection);

  // ------------------------------------------------------------------
  // 3. Instantiate UI modules
  // ------------------------------------------------------------------
  const uploadUI = new UploadUI({ mount: uploadSection });
  const editor = new Editor({ mount: editorSection });
  const calibrationUI = new CalibrationUI({ mount: calibrationSection });
  const personalizationView = new BeforeAfterView({ mount: beforeAfterSection });

  const refreshPersonalization = (): void => {
    if (!isAvailable("personalization")) return;
    void personalizationView.refresh();
  };

  // Track state for corrections and export.
  let currentImageId: string | null = null;
  let currentImageUrl: string | null = null;
  let currentDocumentId: string | null = null;

  const renderRecognition = (imageId: string, recognitionDto: RecognitionResultDto): void => {
    const recognitionResult = mapResult(recognitionDto);
    currentImageId = imageId;
    currentImageUrl = getProcessedImageUrl(imageId);

    editor.setOnCorrection(async (wordIndex, lineIndex, originalText, correctedText) => {
      if (!currentImageId || !isAvailable("correction")) return;

      const line = recognitionResult.lines.find((candidate) => candidate.index === lineIndex);
      const word = line?.words.find((candidate) => candidate.index === wordIndex);
      if (!word) return;

      try {
        await submitCorrection(currentImageId, {
          word_index: wordIndex,
          line_index: lineIndex,
          region: word.region,
          recognized_text: originalText,
          corrected_text: correctedText,
        });
        refreshPersonalization();
      } catch {
        throw new Error("Correction could not be saved to training data.");
      }
    });

    editor.setOnLineCorrection(async (lineIndex, region, originalText, correctedText) => {
      if (!currentImageId || !isAvailable("correction")) return;
      await submitCorrection(currentImageId, {
        line_index: lineIndex,
        region,
        recognized_text: originalText,
        corrected_text: correctedText,
        store_if_unchanged: true,
      });
      refreshPersonalization();
    });

    editor.render(recognitionResult, currentImageUrl);
    editorSection.style.display = "";
    buildExportSection(exportSection, imageId, editor);
    exportSection.style.display = "";
  };

  const gallery = new GalleryUI("document-history", (documentId) => {
    void (async () => {
      currentDocumentId = documentId;
      const document = await getDocument(documentId);
      const imageId = document.image_ids.at(-1);
      if (!imageId) {
        uploadUI.updateStatus(`Selected ${document.title}. Add an image to begin review.`, "info");
        return;
      }
      renderRecognition(imageId, await getRecognitionResult(imageId));
      uploadUI.updateStatus(`Opened ${document.title}.`, "success");
    })().catch((err: unknown) => {
      const message = err instanceof Error ? err.message : "Could not open the document.";
      uploadUI.updateStatus(`Error: ${message}`, "error");
    });
  });

  // ------------------------------------------------------------------
  // 4. Wire the upload → recognize → editor flow
  // ------------------------------------------------------------------
  uploadUI.setOnSubmit(async (file: File) => {
    // Gate: upload capability
    if (!isAvailable("upload")) {
      throw new Error("Upload capability is currently unavailable.");
    }

    if (!currentDocumentId) {
      const title = file.name.replace(/\.[^.]+$/, "").trim() || "Untitled Note";
      const document = await createDocument(title);
      currentDocumentId = document.document_id;
    }

    // Step 1: Upload the image into the selected document group.
    const { image_id: imageId } = await uploadImage(file, currentDocumentId);
    currentImageId = imageId;
    // Create an object URL for the image preview. URL.createObjectURL may not
    // be available in all environments (e.g., SSR/test); fall back gracefully.
    try {
      currentImageUrl = URL.createObjectURL(file);
    } catch {
      currentImageUrl = "";
    }

    // Gate: recognition capability
    if (!isAvailable("recognition")) {
      // Show the editor with a degraded-mode notice.
      editorSection.style.display = "";
      editorSection.innerHTML =
        "<p>Recognition is currently unavailable. Your image was uploaded.</p>";
      return;
    }

    // Step 2: Trigger recognition (POST /images/{image_id}/recognize).
    let recognitionDto;
    try {
      recognitionDto = await recognizeImage(imageId, (current, total) => {
         uploadUI.updateStatus(`Recognizing... ${current}/${total} lines`, "info");
      });
    } catch (err) {
      if (err instanceof ApiError && err.status === 503) {
        editorSection.style.display = "";
        editorSection.innerHTML =
          "<p>Recognition is currently unavailable. Your image was uploaded.</p>";
        return;
      }
      throw err;
    }

    renderRecognition(imageId, recognitionDto);
    void gallery.render();
  });

  uploadUI.render();
  void gallery.render();

  // ------------------------------------------------------------------
  // 5. Wire the calibration flow (optional, Req 9)
  // ------------------------------------------------------------------
  calibrationUI.setOnSubmitSample(async (promptId, file) => {
    // The session_id comes from the session started below.  We store it in a
    // closure variable set when the session is created.
    void promptId;
    void file;
    // Handled inside buildCalibrationSection which replaces this handler.
  });

  buildCalibrationSection(calibrationUI, calibrationSection, refreshPersonalization);

  // ------------------------------------------------------------------
  // 6. Apply capability gates once health check resolves
  // ------------------------------------------------------------------
  capabilitiesPromise.then((health) => {
    if (health.status !== "ok") {
      const unavailable = Object.entries(health.capabilities)
        .filter(([, v]) => !v.available)
        .map(([k]) => k)
        .join(", ");
      if (unavailable) {
        capabilityBanner.textContent = `Some capabilities are currently unavailable: ${unavailable}.`;
        capabilityBanner.style.display = "";
      }
    }

    // Hide calibration section if the capability is unavailable.
    if (!isAvailable("calibration")) {
      calibrationSection.style.display = "none";
      setUtilityTabVisible("calibration", false);
    }

    if (!isAvailable("personalization")) {
      personalizationSection.style.display = "none";
      setUtilityTabVisible("training", false);
    } else {
      refreshPersonalization();
    }
  }).catch(() => {
    capabilityBanner.textContent =
      "Could not reach the backend. Some features may be unavailable.";
    capabilityBanner.style.display = "";
  });
}

// ---------------------------------------------------------------------------
// Export section builder
// ---------------------------------------------------------------------------

function buildExportSection(
  section: HTMLElement,
  imageId: string,
  editor: Editor,
): void {
  section.innerHTML = "";

  const heading = document.createElement("h2");
  heading.textContent = "Export current review";
  heading.className = "export-heading";
  section.appendChild(heading);

  const note = document.createElement("p");
  note.className = "export-note";
  note.textContent =
    "Exports use saved backend text for region-backed edits and visible editor text for text-only line review.";
  section.appendChild(note);

  const statusEl = document.createElement("p");
  statusEl.className = "export-status";
  statusEl.setAttribute("data-testid", "export-status");
  statusEl.setAttribute("aria-live", "polite");
  section.appendChild(statusEl);

  const fetchText = async (format: "plain" | "markdown" | "pdf"): Promise<string> => {
    if (!isAvailable("export")) {
      throw new Error("Export capability is currently unavailable.");
    }
    if (editor.hasLocalLineEdits()) {
      const text = editor.getExportText(format);
      if (text.trim() === "") throw new Error("There is no text to export.");
      return text;
    }
    const resp = await exportText({ image_id: imageId, format });
    if (resp.status === "success") return resp.text;
    throw new Error(resp.message || "Export failed.");
  };

  const downloadFile = (text: string, format: "plain" | "markdown" | "pdf") => {
    let mime = "text/plain";
    let extension = "txt";
    let blob: Blob;

    if (format === "pdf") {
      mime = "application/pdf";
      extension = "pdf";
      const byteCharacters = atob(text);
      const byteNumbers = new Array(byteCharacters.length);
      for (let i = 0; i < byteCharacters.length; i++) byteNumbers[i] = byteCharacters.charCodeAt(i);
      blob = new Blob([new Uint8Array(byteNumbers)], { type: mime });
    } else if (format === "markdown") {
      mime = "text/markdown";
      extension = "md";
      blob = new Blob([text], { type: mime });
    } else {
      blob = new Blob([text], { type: mime });
    }

    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `notes.${extension}`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  const buildActionBtn = (label: string, action: () => Promise<void>, testId: string): HTMLButtonElement => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = label;
    btn.className = "export-btn";
    btn.setAttribute("data-testid", testId);

    btn.addEventListener("click", () => {
      btn.disabled = true;
      statusEl.textContent = "Processing…";
      action()
        .then(() => {
          statusEl.textContent = "Ready.";
        })
        .catch((err: unknown) => {
          statusEl.textContent = `Error: ${err instanceof Error ? err.message : String(err)}`;
        })
        .finally(() => {
          btn.disabled = false;
        });
    });

    return btn;
  };

  section.appendChild(buildActionBtn("Copy to Clipboard", async () => {
    const text = await fetchText("plain");
    await navigator.clipboard.writeText(text);
  }, "export-btn-copy"));

  section.appendChild(buildActionBtn("Email Notes", async () => {
    const text = await fetchText("plain");
    const mailto = `mailto:?subject=${encodeURIComponent("Digitized Notes")}&body=${encodeURIComponent(text)}`;
    window.open(mailto, "_blank");
  }, "export-btn-email"));

  section.appendChild(buildActionBtn("Download Markdown (Obsidian)", async () => {
    const text = await fetchText("markdown");
    downloadFile(text, "markdown");
  }, "export-btn-markdown"));

  section.appendChild(buildActionBtn("Download PDF", async () => {
    const text = await fetchText("pdf");
    downloadFile(text, "pdf");
  }, "export-btn-pdf"));
}

// ---------------------------------------------------------------------------
// Calibration section builder
// ---------------------------------------------------------------------------

function buildCalibrationSection(
  calibrationUI: CalibrationUI,
  section: HTMLElement,
  refreshPersonalization: () => void,
): void {
  // Render a "Start calibration" button first; prompts load on demand.
  section.innerHTML = "";

  const heading = document.createElement("h2");
  heading.textContent = "Calibration samples";
  heading.className = "calibration-start-heading";
  section.appendChild(heading);

  const description = document.createElement("p");
  description.className = "calibration-start-desc";
  description.textContent =
    "Write a few provided sentences to create high-quality training examples quickly. " +
    "Correcting your own uploaded notes works too.";
  section.appendChild(description);

  const statusEl = document.createElement("p");
  statusEl.className = "calibration-start-status";
  statusEl.setAttribute("aria-live", "polite");
  section.appendChild(statusEl);

  const startBtn = document.createElement("button");
  startBtn.type = "button";
  startBtn.textContent = "Start Calibration";
  startBtn.className = "calibration-start-btn";
  startBtn.setAttribute("data-testid", "calibration-start-btn");
  section.appendChild(startBtn);

  let activeSessionId: string | null = null;

  startBtn.addEventListener("click", () => {
    if (!isAvailable("calibration")) {
      statusEl.textContent = "Calibration is currently unavailable.";
      return;
    }
    startBtn.disabled = true;
    statusEl.textContent = "Loading calibration prompts…";

    startCalibrationSession()
      .then((resp) => {
        activeSessionId = resp.session_id;
        statusEl.textContent = "";
        startBtn.style.display = "none";

        // Wire the sample submission handler for the active session.
        // The backend identifies a prompt by its 0-based array index (not a
        // stable id), so the CalibrationPrompt.id we hand to the UI below IS
        // that index, stringified — convert it back to a number here.
        calibrationUI.setOnSubmitSample(
          async (promptId: string, file: File) => {
            if (!activeSessionId) throw new Error("No active calibration session.");
            await submitCalibrationSample(activeSessionId, Number(promptId), file);
            refreshPersonalization();
          },
        );

        // The backend returns bare prompt strings; assign ids from position.
        calibrationUI.render(
          resp.prompts.map((text, index) => ({ id: String(index), text })),
        );
      })
      .catch((err: unknown) => {
        const message = err instanceof Error ? err.message : "Failed to start calibration.";
        statusEl.textContent = `Error: ${message}`;
        startBtn.disabled = false;
      });
  });
}

// ---------------------------------------------------------------------------
// Auth-gated entry point
// ---------------------------------------------------------------------------

/**
 * Top-level entry: check the session, then either show the login/register gate
 * or the app. Authentication uses an httponly `session` cookie set by the
 * backend, so being "logged in" persists across reloads until logout/expiry.
 */
export async function start(root: HTMLElement): Promise<void> {
  let user: Awaited<ReturnType<typeof getCurrentUser>> = null;
  try {
    user = await getCurrentUser();
  } catch {
    user = null; // backend unreachable — fall through to the login gate
  }

  if (!user) {
    root.innerHTML = "";
    const title = document.createElement("h1");
    title.className = "app-title";
    title.textContent = "Handwriting Notes Digitizer";
    root.appendChild(title);

    const gateMount = document.createElement("div");
    root.appendChild(gateMount);

    const gate = new AuthGate({
      mount: gateMount,
      onAuthenticated: () => {
        void start(root); // re-check auth, then render the app
      },
    });
    gate.render();
    return;
  }

  await bootstrap(root);
  renderAccountBar(root, user.username);
}

/** Render a small "signed in as … / Log out" bar at the top of the app. */
function renderAccountBar(root: HTMLElement, username: string): void {
  const bar = document.createElement("div");
  bar.className = "account-bar";
  bar.setAttribute("data-testid", "account-bar");

  const who = document.createElement("span");
  who.className = "account-user";
  who.textContent = `Signed in as ${username}`;
  bar.appendChild(who);

  const logoutBtn = document.createElement("button");
  logoutBtn.type = "button";
  logoutBtn.className = "account-logout";
  logoutBtn.setAttribute("data-testid", "account-logout");
  logoutBtn.textContent = "Log out";
  logoutBtn.addEventListener("click", () => {
    logoutBtn.disabled = true;
    logout()
      .catch(() => {
        /* clearing local view regardless of network result */
      })
      .finally(() => {
        void start(root);
      });
  });
  bar.appendChild(logoutBtn);

  root.insertBefore(bar, root.firstChild);
}

// ---------------------------------------------------------------------------
// Auto-start when running in the browser
// ---------------------------------------------------------------------------

const appRoot = document.getElementById("app");
if (appRoot) {
  registerServiceWorker();
  start(appRoot).catch(console.error);
}
