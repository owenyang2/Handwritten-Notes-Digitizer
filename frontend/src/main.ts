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
import {
  uploadImage,
  recognizeImage,
  submitCorrection,
  exportText,
  startCalibrationSession,
  submitCalibrationSample,
  getCurrentUser,
  logout,
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
    words: dto.words.map(mapWord),
  };
}

function mapResult(dto: RecognitionResultDto): RecognitionResult {
  return {
    lines: dto.lines.map(mapLine),
    recognizedAnyText: dto.recognized_any_text,
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

  const appTitle = document.createElement("h1");
  appTitle.className = "app-title";
  appTitle.textContent = "Handwriting Notes Digitizer";
  root.appendChild(appTitle);

  // Capability status banner (shown when backend is degraded / unreachable).
  const capabilityBanner = document.createElement("div");
  capabilityBanner.className = "capability-banner";
  capabilityBanner.setAttribute("data-testid", "capability-banner");
  capabilityBanner.style.display = "none";
  root.appendChild(capabilityBanner);

  // --- Personalization guide + status (visible when capability is available) ---
  const personalizationSection = document.createElement("section");
  personalizationSection.className = "app-section app-section--personalization";
  personalizationSection.setAttribute("data-testid", "personalization-section");
  personalizationSection.setAttribute("aria-label", "Personal handwriting learning");
  root.appendChild(personalizationSection);

  // --- Upload section ---
  const uploadSection = document.createElement("section");
  uploadSection.className = "app-section app-section--upload";
  uploadSection.setAttribute("data-testid", "upload-section");
  uploadSection.setAttribute("aria-label", "Upload image");
  root.appendChild(uploadSection);

  // --- Editor section (hidden until recognition completes) ---
  const editorSection = document.createElement("section");
  editorSection.className = "app-section app-section--editor";
  editorSection.setAttribute("data-testid", "editor-section");
  editorSection.setAttribute("aria-label", "Recognized text editor");
  editorSection.style.display = "none";
  root.appendChild(editorSection);

  // --- Export section (hidden until editor is populated) ---
  const exportSection = document.createElement("section");
  exportSection.className = "app-section app-section--export";
  exportSection.setAttribute("data-testid", "export-section");
  exportSection.setAttribute("aria-label", "Export text");
  exportSection.style.display = "none";
  root.appendChild(exportSection);

  // --- Calibration section (always available but optional) ---
  const calibrationSection = document.createElement("section");
  calibrationSection.className = "app-section app-section--calibration";
  calibrationSection.setAttribute("data-testid", "calibration-section-wrapper");
  calibrationSection.setAttribute("aria-label", "Optional calibration");
  root.appendChild(calibrationSection);

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

  // ------------------------------------------------------------------
  // 4. Wire the upload → recognize → editor flow
  // ------------------------------------------------------------------
  uploadUI.setOnSubmit(async (file: File) => {
    // Gate: upload capability
    if (!isAvailable("upload")) {
      throw new Error("Upload capability is currently unavailable.");
    }

    // Step 1: Upload the image (POST /images).
    const { image_id: imageId } = await uploadImage(file);
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
      recognitionDto = await recognizeImage(imageId);
    } catch (err) {
      if (err instanceof ApiError && err.status === 503) {
        editorSection.style.display = "";
        editorSection.innerHTML =
          "<p>Recognition is currently unavailable. Your image was uploaded.</p>";
        return;
      }
      throw err;
    }

    // Step 3: Map result and render the editor.
    const recognitionResult = mapResult(recognitionDto);

    // Wire up correction callback before rendering.
    editor.setOnCorrection(async (
      wordIndex: number,
      lineIndex: number,
      originalText: string,
      correctedText: string,
    ) => {
      if (!currentImageId || !isAvailable("correction")) return;

      // Find the word's region from the recognition result.
      const line = recognitionResult.lines.find((l) => l.index === lineIndex);
      const word = line?.words.find((w) => w.index === wordIndex);
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
        // Corrections are best-effort in this wiring; the Editor already
        // updated its display. A production app would surface this to the user.
      }
    });

    editor.render(recognitionResult, currentImageUrl!);
    editorSection.style.display = "";

    // Show export section once the editor is populated.
    buildExportSection(exportSection, imageId);
    exportSection.style.display = "";
  });

  uploadUI.render();

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
    }

    if (!isAvailable("personalization")) {
      personalizationSection.style.display = "none";
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
): void {
  section.innerHTML = "";

  const heading = document.createElement("h2");
  heading.textContent = "Export";
  heading.className = "export-heading";
  section.appendChild(heading);

  const statusEl = document.createElement("p");
  statusEl.className = "export-status";
  statusEl.setAttribute("data-testid", "export-status");
  statusEl.setAttribute("aria-live", "polite");
  section.appendChild(statusEl);

  // Two export buttons: plain text and markdown.
  const buildButton = (label: string, format: "plain" | "markdown"): HTMLButtonElement => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = label;
    btn.className = "export-btn";
    btn.setAttribute("data-testid", `export-btn-${format}`);

    btn.addEventListener("click", () => {
      if (!isAvailable("export")) {
        statusEl.textContent = "Export capability is currently unavailable.";
        return;
      }
      btn.disabled = true;
      statusEl.textContent = "Exporting…";

      exportText({ image_id: imageId, format })
        .then((resp) => {
          if (resp.status === "success") {
            const mime = format === "markdown" ? "text/markdown" : "text/plain";
            const blob = new Blob([resp.text], { type: mime });
            const url = URL.createObjectURL(blob);
            const anchor = document.createElement("a");
            anchor.href = url;
            anchor.download = `notes.${format === "markdown" ? "md" : "txt"}`;
            anchor.click();
            URL.revokeObjectURL(url);
            statusEl.textContent = "Export ready.";
            return;
          }

          statusEl.textContent = resp.message;
        })
        .catch((err: unknown) => {
          const message = err instanceof Error ? err.message : "Export failed.";
          statusEl.textContent = `Export failed: ${message}`;
        })
        .finally(() => {
          btn.disabled = false;
        });
    });

    return btn;
  };

  section.appendChild(buildButton("Copy as plain text", "plain"));
  section.appendChild(buildButton("Export as Markdown", "markdown"));
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
  heading.textContent = "Optional calibration";
  heading.className = "calibration-start-heading";
  section.appendChild(heading);

  const description = document.createElement("p");
  description.className = "calibration-start-desc";
  description.textContent =
    "Write a few provided sentences to build training examples quickly. " +
    "This is optional — correcting words on your own uploaded notes works the same way.";
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
  start(appRoot).catch(console.error);
}
