// Connect the React workspace to the existing upload, review, and sync contracts.

import { UploadUI } from "./modules/uploadUI.js";
import { Editor } from "./modules/editor.js";
import { CalibrationUI } from "./modules/calibrationUI.js";
import { BeforeAfterView } from "./modules/beforeAfterView.js";
import { AuthGate } from "./modules/authGate.js";
import { mountShell, type ShellHandle } from "./components/AppShell.js";
import type { MathNotesWorkspace } from "./modules/mathNotes.js";
import { BatchImportUI } from "./modules/batchImportUI.js";
import { SyncCoordinator } from "./modules/syncOrchestrator.js";
import { SyncUI } from "./modules/syncUI.js";
import { ConsentUI } from "./modules/consentUI.js";
import "./styles.css";
import {
  uploadImage,
  recognizeImage,
  submitCorrection,
  exportText,
  exportPdf,
  saveImageReviews,
  importDocument,
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
import type {
  RecognitionResult,
  RecognizedLine,
  RecognizedWord,
} from "./types.js";
import type {
  RecognitionResultDto,
  RecognizedLineDto,
  RecognizedWordDto,
} from "./api/types.js";

// ---------------------------------------------------------------------------
// DTO → domain type mappers
// ---------------------------------------------------------------------------

function mapWord(dto: RecognizedWordDto): RecognizedWord {
  return {
    index: dto.index,
    text: dto.text,
    region: {
      x: dto.region.x,
      y: dto.region.y,
      width: dto.region.width,
      height: dto.region.height,
    },
    confidence: dto.confidence,
  };
}

function mapLine(dto: RecognizedLineDto): RecognizedLine {
  return {
    index: dto.index,
    region: {
      x: dto.region.x,
      y: dto.region.y,
      width: dto.region.width,
      height: dto.region.height,
    },
    text: dto.text,
    alignmentStatus: dto.alignment_status,
    alignmentReason: dto.alignment_reason ?? undefined,
    excluded: dto.excluded,
    reviewed: dto.reviewed,
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

const cleanups = new WeakMap<HTMLElement, () => void>();
export function disposeApp(root: HTMLElement): void {
  cleanups.get(root)?.();
}

export async function bootstrap(
  root: HTMLElement,
  username = "",
): Promise<void> {
  cleanups.get(root)?.();
  let disposed = false;
  const disposers: Array<() => void> = [];
  // ------------------------------------------------------------------
  // 1. Fetch capability registry on startup to gate features.
  //    The UI renders optimistically before this completes; gates are
  //    enforced once the snapshot arrives.
  // ------------------------------------------------------------------
  const capabilitiesPromise = refreshCapabilities();

  // ------------------------------------------------------------------
  // 2. Build the page layout
  // ------------------------------------------------------------------
  root.replaceChildren();

  const section = (testId: string, label: string): HTMLElement => {
    const node = document.createElement("section");
    node.className = "app-section";
    node.dataset.testid = testId;
    node.setAttribute("aria-label", label);
    return node;
  };
  const capabilityBanner = section("capability-banner", "Service availability");
  capabilityBanner.className = "capability-banner";
  capabilityBanner.style.display = "none";
  const calibrationPanel = section("calibration-tools", "Calibration");
  const trainingPanel = section("training-tools", "Personalization");
  const exportPanel = section("export-tools", "Export");
  const importPanel = section("import-tools", "Batch import");
  const syncPanel = section("sync-tools", "Sync");
  const privacyPanel = section("privacy-tools", "Privacy");
  const setUtilityTabVisible = (
    id: "calibration" | "training",
    visible: boolean,
  ): void => {
    (id === "calibration" ? calibrationPanel : trainingPanel).hidden = !visible;
  };

  // --- Personalization guide + status (visible when capability is available) ---
  const personalizationSection = document.createElement("section");
  personalizationSection.className =
    "app-section app-section--personalization panel";
  personalizationSection.setAttribute("data-testid", "personalization-section");
  personalizationSection.setAttribute(
    "aria-label",
    "Personal handwriting learning",
  );
  trainingPanel.appendChild(personalizationSection);

  // --- Upload section ---
  const uploadSection = document.createElement("section");
  uploadSection.className = "app-section app-section--upload panel";
  uploadSection.setAttribute("data-testid", "upload-section");
  uploadSection.setAttribute("aria-label", "Upload image");

  // --- Editor section (hidden until recognition completes) ---
  const editorSection = document.createElement("section");
  editorSection.className =
    "app-section app-section--editor panel panel--review";
  editorSection.setAttribute("data-testid", "editor-section");
  editorSection.setAttribute("aria-label", "Recognized text editor");
  editorSection.style.display = "none";

  const mathNotesSection = document.createElement("section");
  mathNotesSection.className = "app-section app-section--math panel";
  mathNotesSection.setAttribute("data-testid", "math-notes-section");
  mathNotesSection.setAttribute("aria-label", "Formula crop review");
  mathNotesSection.style.display = "none";

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

  // --- Before/After metrics (nested inside personalization section) ---
  const beforeAfterSection = document.createElement("div");
  beforeAfterSection.className = "app-section app-section--before-after";
  beforeAfterSection.setAttribute("data-testid", "before-after-section");
  beforeAfterSection.setAttribute(
    "aria-label",
    "Accuracy before and after personalization",
  );
  personalizationSection.appendChild(beforeAfterSection);

  // ------------------------------------------------------------------
  // 3. Instantiate UI modules
  // ------------------------------------------------------------------
  const uploadUI = new UploadUI({ mount: uploadSection });
  const editor = new Editor({ mount: editorSection });
  const calibrationUI = new CalibrationUI({ mount: calibrationSection });
  const personalizationView = new BeforeAfterView({
    mount: beforeAfterSection,
  });

  const refreshPersonalization = (): void => {
    if (!isAvailable("personalization")) return;
    void personalizationView.refresh();
  };

  // Track state for corrections and export.
  let currentImageId: string | null = null;
  let currentImageUrl: string | null = null;
  let currentDocumentId: string | null = null;
  let loadedDocumentId: string | null = null;
  let mathWorkspace: MathNotesWorkspace | null = null;
  let openGeneration = 0;
  let shell: ShellHandle;

  const openMathNotes = async (
    documentId: string,
    pages: Array<{
      page_id: string;
      page_index: number;
      image_id: string | null;
      width: number;
      height: number;
      dpi: number;
      coordinate_space: string;
    }>,
  ): Promise<void> => {
    if (pages.length === 0) {
      mathNotesSection.style.display = "none";
      return;
    }
    const { MathNotesWorkspace } = await import("./modules/mathNotes.js");
    if (disposed || currentDocumentId !== documentId) return;
    mathNotesSection.style.display = "";
    mathWorkspace?.dispose();
    mathWorkspace = new MathNotesWorkspace({
      mount: mathNotesSection,
      documentId,
      pages,
      onNavigate: (pageId, regionId) => {
        const url = `/notebooks/${encodeURIComponent(documentId)}/pages/${encodeURIComponent(pageId)}${regionId ? `?region=${encodeURIComponent(regionId)}` : ""}`;
        shell.navigateTo(url, window.location.pathname === url.split("?")[0]);
      },
    });
  };

  const renderRecognition = (
    imageId: string,
    recognitionDto: RecognitionResultDto,
  ): void => {
    const recognitionResult = mapResult(recognitionDto);
    currentImageId = imageId;
    currentImageUrl = getProcessedImageUrl(imageId);
    // A normal image review and the PDF formula workspace are mutually
    // exclusive views. Hide any stale PDF workspace when switching back to an
    // image, so exports and controls cannot appear to target the wrong input.
    mathNotesSection.style.display = "none";

    editor.setOnCorrection(
      async (wordIndex, lineIndex, originalText, correctedText) => {
        if (!isAvailable("correction")) return;

        const line = recognitionResult.lines.find(
          (candidate) => candidate.index === lineIndex,
        );
        const word = line?.words.find(
          (candidate) => candidate.index === wordIndex,
        );
        if (!word) return;

        try {
          await submitCorrection(imageId, {
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
      },
    );

    editor.setOnLineCorrection(
      async (lineIndex, region, originalText, correctedText) => {
        if (!isAvailable("correction")) return;
        await submitCorrection(imageId, {
          line_index: lineIndex,
          region,
          recognized_text: originalText,
          corrected_text: correctedText,
          store_if_unchanged: true,
        });
        refreshPersonalization();
      },
    );

    editor.setOnReviewChange(async (review) => {
      await saveImageReviews(imageId, [review]);
    });

    editor.render(recognitionResult, currentImageUrl);
    editorSection.style.display = "";
    buildExportSection(exportSection, imageId, editor);
    exportSection.style.display = "";
  };

  const renderUnconverted = (imageId: string) => {
    currentImageId = imageId;
    const sourceDocumentId = currentDocumentId;
    mathNotesSection.style.display = "none";
    exportSection.style.display = "none";
    editorSection.style.display = "";
    editorSection.replaceChildren();
    const image = document.createElement("img");
    image.src = getProcessedImageUrl(imageId);
    image.alt = "Original imported handwriting";
    image.className = "unconverted-page";
    const message = document.createElement("p");
    message.textContent =
      "Imported. Convert this page when you’re ready to review its text.";
    const convert = document.createElement("button");
    convert.type = "button";
    convert.textContent = "Convert page";
    convert.onclick = async () => {
      if (!isAvailable("recognition")) {
        message.textContent =
          "Recognition is unavailable. Your imported page is retained.";
        return;
      }
      convert.disabled = true;
      try {
        const result = await recognizeImage(imageId);
        if (
          disposed ||
          currentDocumentId !== sourceDocumentId ||
          currentImageId !== imageId
        )
          return;
        renderRecognition(imageId, result);
      } catch (error) {
        message.textContent =
          error instanceof Error
            ? error.message
            : "Conversion failed. Try again.";
        convert.disabled = false;
      }
    };
    editorSection.append(message, convert, image);
    return { message, convert };
  };
  const openDocument = async (
    documentId: string,
    pageId?: string,
    regionId?: string,
  ): Promise<void> => {
    if (loadedDocumentId === documentId && (mathWorkspace || currentImageId)) {
      if (mathWorkspace) mathNotesSection.style.display = "";
      else editorSection.style.display = "";
      if (pageId) await mathWorkspace?.selectPage(pageId, regionId);
      return;
    }
    if (
      mathWorkspace?.hasUnsavedChanges() &&
      !window.confirm(
        "This notebook has unsaved reviews. Leave and discard these changes?",
      )
    ) {
      if (loadedDocumentId) shell.openDocument(loadedDocumentId);
      return;
    }
    mathNotesSection.style.display = "none";
    editorSection.style.display = "none";
    const generation = ++openGeneration;
    const notebook = await getDocument(documentId);
    if (generation !== openGeneration || disposed) return;
    currentDocumentId = documentId;
    currentImageId = null;
    loadedDocumentId = null;
    shell.setDocument(notebook);
    if (notebook.pages?.length) {
      currentImageId =
        notebook.pages.find((page) => page.image_id)?.image_id ?? null;
      editorSection.style.display = "none";
      exportSection.style.display = "none";
      await openMathNotes(documentId, notebook.pages);
      if (generation !== openGeneration || disposed) return;
      if (pageId) await mathWorkspace?.selectPage(pageId, regionId);
      if (generation !== openGeneration || disposed) return;
    } else {
      mathWorkspace?.dispose();
      mathWorkspace = null;
      mathNotesSection.style.display = "none";
      const imageId = notebook.image_ids.at(-1);
      if (imageId) {
        try {
          const result = await getRecognitionResult(imageId);
          if (generation !== openGeneration || disposed) return;
          renderRecognition(imageId, result);
        } catch (error) {
          if (generation !== openGeneration || disposed) return;
          if (error instanceof ApiError && error.status === 404)
            renderUnconverted(imageId);
          else throw error;
        }
      } else {
        editorSection.style.display = "";
        editorSection.textContent =
          "This notebook has no pages. Import notes from the Library to begin.";
      }
    }
    loadedDocumentId = documentId;
  };
  shell = mountShell(root, {
    mounts: {
      upload: uploadSection,
      batch: importPanel,
      editor: editorSection,
      math: mathNotesSection,
      export: exportPanel,
      calibration: calibrationPanel,
      training: trainingPanel,
      sync: syncPanel,
      privacy: privacyPanel,
      banner: capabilityBanner,
    },
    username,
    onOpen: openDocument,
    onReauthenticated: async () => {
      const user = await getCurrentUser();
      if (disposed || (user && user.username === username)) return;
      // Another account's session must not inherit this workspace or its sync queue.
      cleanups.get(root)?.();
      window.history.replaceState(null, "", "/library");
      await start(root);
    },
    onCloseImport: () => uploadUI.stopCamera(),
    onLogout: () => {
      if (
        mathWorkspace?.hasUnsavedChanges() &&
        !window.confirm("Sign out and discard unsaved reviews?")
      )
        return;
      void logout()
        .then(() => {
          cleanups.get(root)?.();
          window.history.replaceState(null, "", "/library");
          return start(root);
        })
        .catch(() => {
          capabilityBanner.textContent =
            "Sign out failed. Check your connection and try again.";
          capabilityBanner.style.display = "";
        });
    },
  });
  cleanups.set(root, () => {
    disposed = true;
    ++openGeneration;
    uploadUI.dispose();
    editor.dispose();
    personalizationView.dispose();
    mathWorkspace?.dispose();
    for (const dispose of disposers) dispose();
    shell.dispose();
    cleanups.delete(root);
  });

  // ------------------------------------------------------------------
  // 4. Wire the upload → recognize → editor flow
  // ------------------------------------------------------------------
  uploadUI.setOnSubmit(async (file: File) => {
    // Gate: upload capability
    if (!isAvailable("upload")) {
      throw new Error("Upload capability is currently unavailable.");
    }

    if (
      mathWorkspace?.hasUnsavedChanges() &&
      !window.confirm("Discard unsaved reviews and import a new notebook?")
    )
      return;
    const uploadGeneration = ++openGeneration;
    currentImageId = null;
    currentDocumentId = null;
    loadedDocumentId = null;
    mathWorkspace?.dispose();
    mathWorkspace = null;

    const isPdf =
      file.type === "application/pdf" ||
      file.name.toLowerCase().endsWith(".pdf");
    let importedPages = false;
    if (isPdf || !uploadUI.convertAfterImport) {
      const title = file.name.replace(/\.[^.]+$/, "").trim() || "Untitled Note";
      const imported = await importDocument(file, title);
      if (disposed || uploadGeneration !== openGeneration) {
        shell.refreshLibrary();
        return;
      }
      currentDocumentId = imported.document_id;
      shell.setDocument({
        ...imported,
        title,
        created_at: new Date().toISOString(),
        image_ids: [],
      });
      const firstImageId = imported.pages.find(
        (page) => page.image_id,
      )?.image_id;
      if (!firstImageId) {
        throw new Error(
          "The file was imported but no page rasters were created.",
        );
      }
      currentImageId = firstImageId;
      await openMathNotes(imported.document_id, imported.pages);
      editorSection.style.display = "none";
      exportSection.style.display = "none";
      importedPages = true;
    } else if (!currentDocumentId) {
      const title = file.name.replace(/\.[^.]+$/, "").trim() || "Untitled Note";
      const document = await createDocument(title);
      if (disposed || uploadGeneration !== openGeneration) {
        shell.refreshLibrary();
        return;
      }
      currentDocumentId = document.document_id;
      shell.setDocument(document);
    }

    if (!currentImageId) {
      const { image_id: uploadedId } = await uploadImage(
        file,
        currentDocumentId,
      );
      if (disposed || uploadGeneration !== openGeneration) {
        shell.refreshLibrary();
        return;
      }
      currentImageId = uploadedId;
    }
    if (disposed || uploadGeneration !== openGeneration) return;
    const imageId = currentImageId;
    if (!imageId) {
      throw new Error("Upload did not produce an image id.");
    }
    loadedDocumentId = currentDocumentId;
    if (currentDocumentId) shell.openDocument(currentDocumentId);
    shell.closeImport();
    if (importedPages) {
      uploadUI.updateStatus(
        "Notes imported. Select a region or convert the page to begin review.",
        "success",
      );
      shell.refreshLibrary();
      return;
    }
    const pendingReview = renderUnconverted(imageId);
    if (!isAvailable("recognition")) {
      pendingReview.message.textContent =
        "Recognition is currently unavailable. Your image was uploaded.";
      return;
    }
    pendingReview.convert.disabled = true;
    pendingReview.message.textContent =
      "Converting this page… Your original is retained.";

    // Step 2: Trigger recognition (POST /images/{image_id}/recognize).
    let recognitionDto;
    try {
      recognitionDto = await recognizeImage(imageId, (current, total) => {
        pendingReview.message.textContent = `Converting… ${current}/${total} lines`;
        uploadUI.updateStatus(
          `Recognizing... ${current}/${total} lines`,
          "info",
        );
      });
    } catch (err) {
      if (disposed || uploadGeneration !== openGeneration) return;
      pendingReview.convert.disabled = false;
      pendingReview.message.textContent =
        err instanceof ApiError && err.status === 503
          ? "Recognition is currently unavailable. Your image was uploaded."
          : `Conversion failed. Your original is retained. ${err instanceof Error ? err.message : "Try again."}`;
      if (err instanceof ApiError && err.status === 503) return;
      throw err;
    }

    if (disposed || uploadGeneration !== openGeneration) return;
    renderRecognition(imageId, recognitionDto);
    shell.refreshLibrary();
  });

  uploadUI.render();

  const batchImportSection = document.createElement("section");
  batchImportSection.className = "app-section app-section--batch-import panel";
  batchImportSection.setAttribute("data-testid", "batch-import-section");
  batchImportSection.setAttribute("aria-label", "Batch import");
  importPanel.appendChild(batchImportSection);

  const batchImport = new BatchImportUI({
    mount: batchImportSection,
    processor: async ({ file, signal, update, item }) => {
      if (!isAvailable("upload"))
        throw new Error("Import is currently unavailable.");
      await update({ phase: "uploading" });
      const title = file.name.replace(/\.[^.]+$/, "") || "Imported notes";
      const imported = item.documentId
        ? await getDocument(item.documentId)
        : await importDocument(file, title, signal);
      await update({
        phase: "imported",
        documentId: imported.document_id,
        pageCount: imported.pages?.length ?? 0,
      });
      shell.refreshLibrary();
      return {
        kind:
          file.type === "application/pdf"
            ? ("pdf" as const)
            : ("image" as const),
        documentId: imported.document_id,
        pageCount: imported.pages?.length ?? 0,
      };
    },
    onCompleted: () => {
      shell.refreshLibrary();
    },
  });
  void batchImport.render();
  disposers.push(() => batchImport.dispose());

  const syncSection = document.createElement("section");
  syncSection.className = "app-section app-section--sync panel";
  syncSection.setAttribute("data-testid", "sync-section");
  syncSection.setAttribute("aria-label", "Cross-device sync");
  syncPanel.appendChild(syncSection);

  const privacySection = document.createElement("section");
  privacySection.className = "app-section app-section--privacy panel";
  privacySection.setAttribute("data-testid", "privacy-section");
  privacySection.setAttribute("aria-label", "Personal training consent");
  privacyPanel.appendChild(privacySection);
  void new ConsentUI(privacySection).render();

  void (async () => {
    let currentUser: Awaited<ReturnType<typeof getCurrentUser>> = null;
    try {
      currentUser = await Promise.resolve(getCurrentUser());
    } catch {
      currentUser = null;
    }
    if (!currentUser) return;
    const coordinator = new SyncCoordinator({
      userId: currentUser.user_id,
      onDocumentsChanged: () => {
        shell.refreshLibrary();
      },
    });
    if (disposed) return;
    const syncUI = new SyncUI({
      mount: syncSection,
      coordinator,
      onDocumentsChanged: () => {
        shell.refreshLibrary();
      },
    });
    syncUI.render();
    disposers.push(() => {
      syncUI.dispose();
      coordinator.stopAutoSync();
    });
    try {
      await coordinator.load();
      if (!disposed) coordinator.startAutoSync();
    } catch {
      // Local sync durability may be unavailable in private browsing.
    }
  })();

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

  buildCalibrationSection(
    calibrationUI,
    calibrationSection,
    refreshPersonalization,
  );

  // ------------------------------------------------------------------
  // 6. Apply capability gates once health check resolves
  // ------------------------------------------------------------------
  capabilitiesPromise
    .then((health) => {
      if (disposed) return;
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
    })
    .catch(() => {
      if (disposed) return;
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
  section.replaceChildren();

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

  const persistReviews = async (): Promise<void> => {
    await editor.flushReviewSaves();
    await saveImageReviews(imageId, editor.getReviewPayload());
  };

  let saveBeforeExport = true;
  const fetchText = async (
    format: "plain" | "markdown" | "pdf",
  ): Promise<string> => {
    if (!isAvailable("export")) {
      throw new Error("Export capability is currently unavailable.");
    }
    if (saveBeforeExport) await persistReviews();
    if (format === "pdf") {
      const blob = await exportPdf(imageId);
      return URL.createObjectURL(blob);
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
      if (text.startsWith("blob:")) {
        const anchor = document.createElement("a");
        anchor.href = text;
        anchor.download = `notes.${extension}`;
        anchor.click();
        URL.revokeObjectURL(text);
        return;
      }
      const byteCharacters = atob(text);
      const byteNumbers = new Array(byteCharacters.length);
      for (let i = 0; i < byteCharacters.length; i++)
        byteNumbers[i] = byteCharacters.charCodeAt(i);
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

  const buildActionBtn = (
    label: string,
    action: () => Promise<void>,
    testId: string,
  ): HTMLButtonElement => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = label;
    btn.className = "export-btn";
    btn.setAttribute("data-testid", testId);
    btn.dataset.exportAction = "true";

    btn.addEventListener("click", () => {
      section
        .querySelectorAll<HTMLButtonElement>("[data-export-action]")
        .forEach((control) => (control.disabled = true));
      statusEl.textContent = "Processing…";
      action()
        .then(() => {
          statusEl.textContent = "Ready.";
        })
        .catch((err: unknown) => {
          statusEl.textContent = `Error: ${err instanceof Error ? err.message : String(err)}`;
        })
        .finally(() => {
          section
            .querySelectorAll<HTMLButtonElement>("[data-export-action]")
            .forEach((control) => (control.disabled = false));
        });
    });

    return btn;
  };

  const format = document.createElement("select");
  format.setAttribute("aria-label", "Export format");
  for (const [value, label] of [
    ["copy", "Plain text · Copy"],
    ["email", "Plain text · Email"],
    ["markdown", "Markdown (.md)"],
    ["pdf", "PDF (.pdf)"],
  ])
    format.add(new Option(label, value));
  try {
    const last = localStorage.getItem("notes-image-export-format");
    if (last && [...format.options].some((option) => option.value === last))
      format.value = last;
  } catch {
    /* Use the default format. */
  }
  const runExport = async (): Promise<void> => {
    const choice = format.value;
    const text = await fetchText(
      choice === "markdown" ? "markdown" : choice === "pdf" ? "pdf" : "plain",
    );
    if (choice === "copy") await navigator.clipboard.writeText(text);
    else if (choice === "email")
      window.open(
        `mailto:?subject=${encodeURIComponent("Digitized Notes")}&body=${encodeURIComponent(text)}`,
        "_blank",
      );
    else downloadFile(text, choice === "pdf" ? "pdf" : "markdown");
    try {
      localStorage.setItem("notes-image-export-format", choice);
    } catch {
      /* Export succeeded. */
    }
  };
  note.textContent =
    "Scope: this image. Save and export includes current corrections. Export last saved version leaves current drafts out. Excluded lines are omitted.";
  section.append(
    format,
    buildActionBtn(
      "Save and export",
      async () => {
        saveBeforeExport = true;
        await runExport();
      },
      "export-confirm",
    ),
    buildActionBtn(
      "Export last saved version",
      async () => {
        saveBeforeExport = false;
        await runExport();
      },
      "export-saved",
    ),
  );
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
  section.replaceChildren();

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
            if (!activeSessionId)
              throw new Error("No active calibration session.");
            await submitCalibrationSample(
              activeSessionId,
              Number(promptId),
              file,
            );
            refreshPersonalization();
          },
        );

        // The backend returns bare prompt strings; assign ids from position.
        calibrationUI.render(
          resp.prompts.map((text, index) => ({ id: String(index), text })),
        );
      })
      .catch((err: unknown) => {
        const message =
          err instanceof Error ? err.message : "Failed to start calibration.";
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
  let connectionFailed = false;
  try {
    user = await getCurrentUser();
  } catch {
    connectionFailed = true;
  }

  if (!user) {
    root.replaceChildren();
    const title = document.createElement("h1");
    title.className = "app-title";
    title.textContent = "Handwriting Notes Digitizer";
    root.appendChild(title);

    if (connectionFailed) {
      const status = document.createElement("p");
      status.setAttribute("role", "status");
      status.textContent =
        "Cannot reach the server. Reconnect to reopen your web notebooks, then retry.";
      const retry = document.createElement("button");
      retry.textContent = "Retry connection";
      retry.onclick = () => {
        retry.disabled = true;
        void start(root);
      };
      root.append(status, retry);
    }

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

  await bootstrap(root, user.username);
}
