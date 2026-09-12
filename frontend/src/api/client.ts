// API client for the Handwriting Notes Digitizer backend.
//
// Every method maps to one HTTP endpoint (see design.md API Surface table).
// The client is intentionally thin — it handles network I/O and surfaces typed
// responses; error handling / UX decisions belong to the calling modules.
//
// Base URL is configurable via the VITE_API_BASE_URL environment variable
// (defaults to "" so that relative paths work when frontend + backend are
// served from the same origin).

import type {
  HealthResponse,
  UploadImageResponse,
  RecognitionResultDto,
  RegionDto,
  SubmitCorrectionRequest,
  SubmitCorrectionResponse,
  ExportRequest,
  ExportResponse,
  CalibrationSessionResponse,
  RecognitionJobDto,
} from "./types.js";

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

/** Resolved base URL (no trailing slash).
 *
 * When `VITE_API_BASE_URL` is set (e.g. an absolute origin for a remote
 * backend) it is used verbatim.  Otherwise the client targets the relative
 * `/api` prefix, which nginx proxies to the backend container (see nginx.conf)
 * and the Vite dev server proxies to http://localhost:8000 (see vite.config.ts).
 */
function getBaseUrl(): string {
  // Vite exposes import.meta.env at build time.
  const envUrl =
    typeof import.meta !== "undefined" &&
    (import.meta as { env?: { VITE_API_BASE_URL?: string } }).env
      ?.VITE_API_BASE_URL;
  if (typeof envUrl === "string" && envUrl.trim() !== "") {
    return envUrl.replace(/\/$/, "");
  }
  return "/api";
}

// ---------------------------------------------------------------------------
// Low-level fetch helpers
// ---------------------------------------------------------------------------

/** A typed API error with status code and optional backend detail message. */
export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/**
 * Normalize a FastAPI error `detail` payload into a human-readable string.
 *
 * FastAPI's `detail` is not always a string: automatic request-validation
 * failures (422) send an array of `{loc, msg, type}` objects, and some of
 * this app's handlers (e.g. calibration rejection) send a `{reason, message}`
 * object. Passing any of those straight into `Error(message)` causes the
 * message to be coerced to the literal string "[object Object]", which is
 * confusing to see in the UI — so every shape is flattened to text here.
 */
function detailToMessage(detail: unknown): string | undefined {
  if (typeof detail === "string") return detail;

  if (Array.isArray(detail)) {
    const parts = detail.map((item) => {
      if (item && typeof item === "object" && "msg" in item) {
        const loc = "loc" in item && Array.isArray((item as { loc?: unknown[] }).loc)
          ? (item as { loc: unknown[] }).loc.filter((p) => p !== "body").join(".")
          : undefined;
        const msg = String((item as { msg: unknown }).msg);
        return loc ? `${loc}: ${msg}` : msg;
      }
      return JSON.stringify(item);
    });
    return parts.join("; ");
  }

  if (detail && typeof detail === "object") {
    if ("message" in detail && typeof (detail as { message?: unknown }).message === "string") {
      return (detail as { message: string }).message;
    }
    if ("reason" in detail && typeof (detail as { reason?: unknown }).reason === "string") {
      return (detail as { reason: string }).reason;
    }
    return JSON.stringify(detail);
  }

  return undefined;
}

async function fetchJson<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const { data } = await fetchJsonWithMeta<T>(path, init);
  return data;
}

interface FetchJsonMeta<T> {
  data: T;
  headers: Headers;
}

/**
 * Fetch JSON while preserving response headers used by paginated endpoints.
 * Most callers only need the decoded value and should use fetchJson(); library
 * screens use this variant to honor the server's exact pagination contract.
 */
async function fetchJsonWithMeta<T>(
  path: string,
  init?: RequestInit,
): Promise<FetchJsonMeta<T>> {
  const url = `${getBaseUrl()}${path}`;
  const response = await fetch(url, {
    credentials: "include", // send session cookie
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.headers ?? {}),
    },
  });

  if (!response.ok) {
    let message = `HTTP ${response.status}`;
    try {
      const body = (await response.json()) as { detail?: unknown };
      const readable = detailToMessage(body?.detail);
      if (readable) message = readable;
    } catch {
      // ignore JSON parse errors on error responses
    }
    throw new ApiError(response.status, message);
  }

  return {
    data: (await response.json()) as T,
    headers: response.headers,
  };
}

// ---------------------------------------------------------------------------
// Public API surface (mirrors design.md API Surface table)
// ---------------------------------------------------------------------------

/** Raw wire shape of GET /health as emitted by the FastAPI backend.
 * The backend reports each capability as a plain boolean and names the upload
 * capability `image_upload`; the frontend normalizes this into HealthResponse.
 */
interface RawHealthResponse {
  status: string;
  capabilities: Record<string, boolean>;
  model_status?: "loaded" | "unavailable" | "trained" | "untrained" | "loading" | "unknown";
}

/** Map backend capability keys → frontend CapabilityName. */
const CAPABILITY_KEY_MAP: Record<string, string> = {
  image_upload: "upload",
  recognition: "recognition",
  correction: "correction",
  calibration: "calibration",
  personalization: "personalization",
  export: "export",
};

/**
 * GET /health
 * Returns component availability for partial-deployment gating (Req 11.5, 11.6).
 *
 * Normalizes the backend wire shape (`{ image_upload: true, ... }`) into the
 * frontend's HealthResponse (`{ capabilities: { upload: { available: true } } }`).
 */
export async function getHealth(): Promise<HealthResponse> {
  const raw = await fetchJson<RawHealthResponse>("/health");
  const capabilities = {} as HealthResponse["capabilities"];
  let allAvailable = true;

  for (const [backendKey, available] of Object.entries(raw.capabilities ?? {})) {
    const frontendKey = CAPABILITY_KEY_MAP[backendKey] ?? backendKey;
    (capabilities as Record<string, { available: boolean }>)[frontendKey] = {
      available,
    };
    if (!available) allAvailable = false;
  }

  return {
    status: allAvailable ? "ok" : "degraded",
    capabilities,
    model_status: raw.model_status ?? "unknown",
  };
}

// ---------------------------------------------------------------------------
// Recognition wire-shape normalization
// ---------------------------------------------------------------------------

/** Raw line/word shapes emitted by the FastAPI recognition endpoints. */
interface RawRecognizedWordWire {
  word_id?: string;
  line_index: number;
  word_index: number;
  text: string;
  region: RegionDto;
  confidence?: number;
}

interface RawRecognizedLineWire {
  line_index: number;
  region: RegionDto;
  text?: string;
  alignment_status?: "aligned" | "uncertain";
  alignment_reason?: string | null;
  excluded?: boolean;
  reviewed?: boolean;
  words: RawRecognizedWordWire[];
}

/** Wire shape of POST /images/{image_id}/recognize. */
interface RawRecognizeResponse {
  outcome: "success" | "no_text" | "timeout" | "failed";
  image_id: string;
  lines?: RawRecognizedLineWire[];
  image_width?: number | null;
  image_height?: number | null;
  error_message?: string | null;
}

/** Wire shape of GET /images/{image_id}/result. */
interface RawStoredResultResponse {
  image_id: string;
  status: "accepted" | "recognized" | "no_text" | "timeout" | "error";
  image_width?: number | null;
  image_height?: number | null;
  lines: RawRecognizedLineWire[];
}

function mapWireLines(lines: RawRecognizedLineWire[]): RecognitionResultDto["lines"] {
  return lines.map((line) => ({
    index: line.line_index,
    region: line.region,
    text: line.text,
    alignment_status: line.alignment_status,
    alignment_reason: line.alignment_reason,
    excluded: line.excluded,
    reviewed: line.reviewed,
    words: line.words.map((word) => ({
      index: word.word_index,
      text: word.text,
      region: word.region,
      confidence: word.confidence,
    })),
  }));
}

function linesHaveText(lines: RecognitionResultDto["lines"]): boolean {
  return lines.some((line) => line.words.some((word) => word.text.length > 0));
}

/** Map the backend recognize envelope into the frontend RecognitionResultDto. */
function normalizeRecognizeResponse(raw: RawRecognizeResponse): RecognitionResultDto {
  const lines = mapWireLines(raw.lines ?? []);
  const hasText = linesHaveText(lines);
  const statusByOutcome: Record<RawRecognizeResponse["outcome"], RecognitionResultDto["status"]> = {
    success: "ok",
    no_text: "no_text",
    timeout: "timeout",
    failed: "error",
  };
  return {
    lines,
    recognized_any_text: raw.outcome === "success" && hasText,
    image_width: raw.image_width ?? undefined,
    image_height: raw.image_height ?? undefined,
    status: statusByOutcome[raw.outcome],
    message: raw.error_message ?? undefined,
  };
}

/** Map GET /result into the same frontend shape used by the editor. */
function normalizeStoredResultResponse(raw: RawStoredResultResponse): RecognitionResultDto {
  const lines = mapWireLines(raw.lines ?? []);
  const hasText = linesHaveText(lines);
  const statusByImageStatus: Record<
    RawStoredResultResponse["status"],
    RecognitionResultDto["status"]
  > = {
    recognized: "ok",
    no_text: "no_text",
    timeout: "timeout",
    error: "error",
    accepted: "error",
  };
  return {
    lines,
    recognized_any_text: raw.status === "recognized" && hasText,
    image_width: raw.image_width ?? undefined,
    image_height: raw.image_height ?? undefined,
    status: statusByImageStatus[raw.status],
  };
}

/** URL for the backend-preprocessed image used as the region coordinate space. */
export function getProcessedImageUrl(imageId: string): string {
  return `${getBaseUrl()}/images/${encodeURIComponent(imageId)}/processed`;
}

// ---------------------------------------------------------------------------
// Auth  (POST /auth/register, /auth/login, /auth/logout, GET /auth/me)
// ---------------------------------------------------------------------------

export interface AuthResponse {
  access_token: string;
  token_type: string;
  user_id: string;
}

export interface CurrentUser {
  user_id: string;
  username: string;
  email: string;
}

/** POST /auth/register — create an account and establish a session cookie. */
export async function register(
  username: string,
  email: string,
  password: string,
): Promise<AuthResponse> {
  return fetchJson<AuthResponse>("/auth/register", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, email, password }),
  });
}

/** POST /auth/login — authenticate and establish a session cookie. */
export async function login(
  username: string,
  password: string,
): Promise<AuthResponse> {
  return fetchJson<AuthResponse>("/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
}

/** POST /auth/logout — revoke the session and clear the cookie. */
export async function logout(): Promise<void> {
  const url = `${getBaseUrl()}/auth/logout`;
  await fetch(url, { method: "POST", credentials: "include" });
}

/** GET /auth/me — return the current user, or null when unauthenticated. */
export async function getCurrentUser(): Promise<CurrentUser | null> {
  try {
    return await fetchJson<CurrentUser>("/auth/me");
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) return null;
    throw err;
  }
}

/**
 * POST /images
 * Upload a PNG/JPEG image.  Returns the new image_id.
 * Authentication is enforced by the backend (Req 1.5, 10.4).
 */
export async function uploadImage(
  file: File,
  documentId?: string,
  signal?: AbortSignal,
): Promise<UploadImageResponse> {
  const formData = new FormData();
  formData.append("file", file);
  if (documentId) {
    formData.append("document_id", documentId);
  }
  return fetchJson<UploadImageResponse>("/images", {
    method: "POST",
    body: formData,
    signal,
    // Do NOT set Content-Type — browser sets multipart boundary automatically.
  });
}

/**
 * POST /images/{image_id}/recognize
 * Trigger recognition (base model + user adapter if present).
 * Returns the recognition result immediately (synchronous, 10 s budget).
 */
export async function recognizeImage(
  imageId: string,
  onProgress?: (current: number, total: number) => void,
  signal?: AbortSignal,
): Promise<RecognitionResultDto> {
  const url = `${getBaseUrl()}/images/${imageId}/recognize?stream=true`;
  const response = await fetch(url, {
    method: "POST",
    credentials: "include",
    signal,
  });

  if (!response.ok) {
    let message = `Failed to start recognition: HTTP ${response.status}`;
    try {
      const body = (await response.json()) as { detail?: unknown };
      message = detailToMessage(body.detail) ?? message;
    } catch {
      // Preserve the status-based error for non-JSON responses.
    }
    throw new ApiError(response.status, message);
  }
  if (!response.body) {
    throw new Error("Recognition response did not include a stream.");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const consumeEvent = (chunk: string): RecognitionResultDto | undefined => {
    const data = chunk
      .split(/\r?\n/)
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).trimStart())
      .join("\n");
    if (!data) return undefined;

    let event: unknown;
    try {
      event = JSON.parse(data);
    } catch {
      throw new Error("Recognition stream contained invalid JSON.");
    }
    if (!event || typeof event !== "object" || !("type" in event)) {
      throw new Error("Recognition stream contained an invalid event.");
    }

    const typedEvent = event as Record<string, unknown>;
    switch (typedEvent.type) {
      case "progress":
        if (
          onProgress &&
          typeof typedEvent.current === "number" &&
          typeof typedEvent.total === "number"
        ) {
          onProgress(typedEvent.current, typedEvent.total);
        }
        return undefined;
      case "result":
        return normalizeRecognizeResponse(typedEvent.result as RawRecognizeResponse);
      case "error":
        throw new Error(
          typeof typedEvent.error === "string"
            ? typedEvent.error
            : "Recognition stream failed.",
        );
      default:
        return undefined;
    }
  };

  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });

    const events = buffer.split(/\r?\n\r?\n/);
    buffer = done ? "" : events.pop() ?? "";
    for (const event of events) {
      const result = consumeEvent(event);
      if (result) return result;
    }

    if (done) break;
  }

  throw new Error("Recognition stream ended before returning a result.");
}

/**
 * GET /images/{image_id}/result
 * Fetch an already-computed recognition result.
 */
export async function getRecognitionResult(
  imageId: string,
): Promise<RecognitionResultDto> {
  const raw = await fetchJson<RawStoredResultResponse>(`/images/${imageId}/result`);
  return normalizeStoredResultResponse(raw);
}

/**
 * POST /images/{image_id}/corrections
 * Submit a confirmed correction for a word (Req 4.3–4.8).
 * Only called when the corrected text differs from the recognized text.
 */
export async function submitCorrection(
  imageId: string,
  payload: SubmitCorrectionRequest,
): Promise<SubmitCorrectionResponse> {
  return fetchJson<SubmitCorrectionResponse>(
    `/images/${imageId}/corrections`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
}

/**
 * POST /export
 * Export the edited text for an image as plain text or markdown (Req 8).
 */
export async function exportText(
  payload: ExportRequest,
): Promise<ExportResponse> {
  if (payload.format === "pdf") {
    const blob = await exportPdf(payload.image_id);
    return { status: "success", format: "pdf", text: await blobToBase64(blob) };
  }
  return fetchJson<ExportResponse>("/export", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function exportPdf(imageId: string): Promise<Blob> {
  const url = `${getBaseUrl()}/export`;
  const response = await fetch(url, {
    method: "POST",
    credentials: "include",
    headers: {
      Accept: "application/pdf, application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ image_id: imageId, format: "pdf" }),
  });
  if (!response.ok) {
    let message = `HTTP ${response.status}`;
    try {
      const body = (await response.json()) as { detail?: unknown; message?: string };
      message = detailToMessage(body?.detail) || body.message || message;
    } catch {
      // ignore
    }
    throw new ApiError(response.status, message);
  }
  const contentType = response.headers.get("content-type") ?? "";
  if (contentType.includes("application/pdf")) {
    return response.blob();
  }
  const body = (await response.json()) as ExportResponse;
  if (body.status !== "success") {
    throw new ApiError(response.status, body.message || "Export failed.");
  }
  const binary = atob(body.text);
  const bytes = Uint8Array.from(binary, (ch) => ch.charCodeAt(0));
  return new Blob([bytes], { type: "application/pdf" });
}

async function blobToBase64(blob: Blob): Promise<string> {
  const buffer = await blob.arrayBuffer();
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

export interface LineReviewPayload {
  line_index: number;
  reviewed_text: string;
  excluded: boolean;
  region?: { x: number; y: number; width: number; height: number };
}

export async function saveImageReviews(
  imageId: string,
  lines: LineReviewPayload[],
): Promise<void> {
  await fetchJson(`/images/${encodeURIComponent(imageId)}/reviews`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ lines }),
  });
}

export interface DocumentPageSummary {
  page_id: string;
  page_index: number;
  image_id: string | null;
  width: number;
  height: number;
  dpi: number;
  coordinate_space: string;
}

export interface ImportDocumentResponse {
  document_id: string;
  title: string;
  page_count: number;
  pages: DocumentPageSummary[];
}

export async function importDocument(
  file: File,
  title: string,
  signal?: AbortSignal,
): Promise<ImportDocumentResponse> {
  const fd = new FormData();
  fd.append("file", file);
  fd.append("title", title);
  return fetchJson<ImportDocumentResponse>("/documents/import", {
    method: "POST",
    body: fd,
    signal,
  });
}

export interface CreateRegionResponse {
  region_id: string;
  page_id: string;
  content_type: string;
  bbox: number[];
  order_index: number;
  coordinate_space?: string;
  page_revision?: number;
  crop_sha256?: string | null;
}

export async function createPageRegion(
  pageId: string,
  bbox: { x: number; y: number; width: number; height: number },
  contentType = "math_display",
): Promise<CreateRegionResponse> {
  return fetchJson<CreateRegionResponse>(`/pages/${encodeURIComponent(pageId)}/regions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      bbox: [bbox.x, bbox.y, bbox.width, bbox.height],
      content_type: contentType,
    }),
  });
}

export interface RecognizeRegionResponse {
  region_id: string;
  prediction_id: string | null;
  engine_id: string;
  text: string;
  truncated: boolean;
  available: boolean;
  reason: string | null;
  score?: number | null;
  candidates?: Array<{ text: string; score?: number | null; truncated?: boolean; kind?: string }>;
  latency_ms?: number | null;
}

export async function recognizeRegion(regionId: string): Promise<RecognizeRegionResponse> {
  return fetchJson<RecognizeRegionResponse>(
    `/regions/${encodeURIComponent(regionId)}/recognize`,
    { method: "POST" },
  );
}

export interface RegionReviewResponse {
  review_id: string;
  region_id: string;
  reviewed_text: string;
  excluded: boolean;
  version: number;
  review_revision_id: string | null;
  document_revision_id: string | null;
  training_example_id: string | null;
}

export interface SaveRegionReviewOptions {
  predictionId?: string | null;
  expectedVersion?: number | null;
  includeInTraining?: boolean;
  split?: "train" | "dev" | "test" | "unassigned";
}

export async function saveRegionReview(
  regionId: string,
  reviewedText: string,
  excluded = false,
  predictionId?: string | null,
  options: SaveRegionReviewOptions = {},
): Promise<RegionReviewResponse> {
  return fetchJson<RegionReviewResponse>(`/regions/${encodeURIComponent(regionId)}/review`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      reviewed_text: reviewedText,
      excluded,
      prediction_id: predictionId ?? null,
      expected_version: options.expectedVersion ?? null,
      include_in_training: options.includeInTraining ?? false,
      split: options.split ?? "unassigned",
    }),
  });
}

export interface PageRegionDetail {
  region_id: string;
  page_id: string;
  content_type: string;
  bbox: number[];
  order_index: number;
  prediction_text: string | null;
  reviewed_text: string | null;
  excluded: boolean;
  unresolved: boolean;
  prediction_id?: string | null;
  review_revision_id?: string | null;
  review_version?: number | null;
  crop_revision?: number;
  score?: number | null;
  truncated?: boolean;
  crop_sha256?: string | null;
}

export async function listPageRegions(pageId: string): Promise<PageRegionDetail[]> {
  return fetchJson<PageRegionDetail[]>(`/pages/${encodeURIComponent(pageId)}/regions`);
}

export interface CreateRecognitionJobRequest {
  region_ids?: string[];
  auto_layout?: boolean;
  mode?: string;
  max_attempts?: number;
}

export async function createRecognitionJob(
  pageId: string,
  payload: CreateRecognitionJobRequest = {},
): Promise<RecognitionJobDto> {
  return fetchJson<RecognitionJobDto>(
    `/pages/${encodeURIComponent(pageId)}/recognition-jobs`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
}

export async function getRecognitionJob(jobId: string): Promise<RecognitionJobDto> {
  return fetchJson<RecognitionJobDto>(`/jobs/${encodeURIComponent(jobId)}`);
}

export async function retryRecognitionJob(jobId: string): Promise<RecognitionJobDto> {
  return fetchJson<RecognitionJobDto>(`/jobs/${encodeURIComponent(jobId)}/retry`, {
    method: "POST",
  });
}

const TERMINAL_RECOGNITION_JOB_STATES = new Set([
  "succeeded",
  "no_text",
  "failed",
  "timeout",
  "cancelled",
]);

export async function waitForRecognitionJob(
  jobId: string,
  onProgress?: (job: RecognitionJobDto) => void,
  maxWaitMs = 60_000,
): Promise<RecognitionJobDto> {
  const started = Date.now();
  let delay = 150;
  while (Date.now() - started <= maxWaitMs) {
    const job = await getRecognitionJob(jobId);
    onProgress?.(job);
    if (TERMINAL_RECOGNITION_JOB_STATES.has(job.state)) return job;
    await new Promise<void>((resolve) => window.setTimeout(resolve, delay));
    delay = Math.min(1_000, Math.round(delay * 1.35));
  }
  throw new Error("Recognition job did not finish before the client wait limit.");
}

export async function patchPageRegion(
  regionId: string,
  payload: {
    bbox?: [number, number, number, number];
    content_type?: string;
    order_index?: number;
    expected_page_revision?: number;
  },
): Promise<CreateRegionResponse> {
  return fetchJson<CreateRegionResponse>(`/regions/${encodeURIComponent(regionId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function reorderPageRegions(pageId: string, regionIds: string[]): Promise<CreateRegionResponse[]> {
  return fetchJson<CreateRegionResponse[]>(`/pages/${encodeURIComponent(pageId)}/regions/reorder`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ region_ids: regionIds }),
  });
}

export async function splitPageRegion(regionId: string, boxes: Array<[number, number, number, number]>): Promise<CreateRegionResponse[]> {
  return fetchJson<CreateRegionResponse[]>(`/regions/${encodeURIComponent(regionId)}/split`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ boxes }),
  });
}

export async function mergePageRegions(pageId: string, regionIds: string[], contentType?: string): Promise<CreateRegionResponse> {
  return fetchJson<CreateRegionResponse>(`/pages/${encodeURIComponent(pageId)}/regions/merge`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ region_ids: regionIds, content_type: contentType }),
  });
}

export interface DocumentRevisionSummary {
  revision_id: string;
  revision_number: number;
  created_at: string;
}

export async function getDocumentRevisions(documentId: string): Promise<DocumentRevisionSummary[]> {
  return fetchJson<DocumentRevisionSummary[]>(
    `/documents/${encodeURIComponent(documentId)}/revisions`,
  );
}

export function getPageRasterUrl(pageId: string): string {
  return `${getBaseUrl()}/pages/${encodeURIComponent(pageId)}/raster`;
}

export async function exportDocument(
  documentId: string,
  format: "markdown" | "latex" | "latex_bundle" | "html" | "pdf",
  revisionId?: string | null,
): Promise<{ format: string; text: string } | Blob> {
  if (format === "pdf" || format === "latex_bundle") {
    const url = `${getBaseUrl()}/documents/${encodeURIComponent(documentId)}/exports`;
    const response = await fetch(url, {
      method: "POST",
      credentials: "include",
      headers: {
        Accept: "application/pdf, application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ format, revision_id: revisionId ?? null }),
    });
    if (!response.ok) {
      throw new ApiError(response.status, "Document export failed.");
    }
    return response.blob();
  }
  return fetchJson<{ status: string; format: string; text: string }>(
    `/documents/${encodeURIComponent(documentId)}/exports`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ format, revision_id: revisionId ?? null }),
    },
  );
}

/**
 * POST /calibration/session
 * Start a calibration session; returns 3–10 prompt sentences (Req 9.2).
 */
export async function startCalibrationSession(): Promise<CalibrationSessionResponse> {
  return fetchJson<CalibrationSessionResponse>("/calibration/session", {
    method: "POST",
  });
}

/**
 * POST /calibration/{session_id}/samples
 * Submit a handwriting image for one calibration prompt (Req 9.3–9.5).
 *
 * `promptIndex` is the 0-based position of the prompt within the session's
 * `prompts` array — the backend has no separate prompt id, it identifies the
 * prompt by this index. The multipart wire field is `prompt_index`, matching
 * the FastAPI `Form` parameter exactly.
 */
export async function submitCalibrationSample(
  sessionId: string,
  promptIndex: number,
  file: File,
): Promise<void> {
  const fd = new FormData();
  fd.append("file", file);
  fd.append("prompt_index", promptIndex.toString());

  await fetchJson<unknown>(`/calibration/${sessionId}/samples`, {
    method: "POST",
    body: fd,
  });
}

export interface DocumentResponse {
  document_id: string;
  title: string;
  created_at: string;
  image_ids: string[];
  source_kind?: string;
  pages?: DocumentPageSummary[];
  active_revision_id?: string | null;
  updated_at?: string;
  archived_at?: string | null;
  trashed_at?: string | null;
  sync_version?: number;
  default_language?: string;
  course_context?: string | null;
  source_metadata?: Record<string, unknown> | null;
}

export interface DocumentListOptions {
  query?: string;
  includeArchived?: boolean;
  includeTrashed?: boolean;
  view?: "active" | "archived" | "trash";
  limit?: number;
  offset?: number;
}

export interface UpdateDocumentRequest {
  title?: string;
  archived?: boolean;
  default_language?: string;
  course_context?: string | null;
  expected_sync_version?: number;
}

export async function createDocument(
  title: string,
  signal?: AbortSignal,
): Promise<DocumentResponse> {
  return fetchJson<DocumentResponse>("/documents", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
    signal,
  });
}

export interface DocumentListPage {
  items: DocumentResponse[];
  offset: number;
  limit: number;
  total: number;
  hasMore: boolean;
}

export async function getDocumentsPage(
  options: DocumentListOptions = {},
): Promise<DocumentListPage> {
  const params = new URLSearchParams();
  if (options.query?.trim()) params.set("query", options.query.trim());
  if (options.includeArchived) params.set("include_archived", "true");
  if (options.includeTrashed) params.set("include_trashed", "true");
  if (options.view) params.set("view", options.view);
  if (options.limit !== undefined) params.set("limit", String(options.limit));
  if (options.offset !== undefined) params.set("offset", String(options.offset));
  const suffix = params.toString() ? `?${params.toString()}` : "";
  const response = await fetchJsonWithMeta<DocumentResponse[]>(`/documents${suffix}`, {
    method: "GET",
  });
  const offset = options.offset ?? Number(response.headers.get("X-Document-Offset") ?? 0);
  const limit = options.limit ?? Number(response.headers.get("X-Document-Limit") ?? response.data.length);
  const totalHeader = Number(response.headers.get("X-Document-Total"));
  const hasMoreHeader = response.headers.get("X-Document-Has-More");
  return {
    items: response.data,
    offset: Number.isFinite(offset) ? Math.max(0, offset) : 0,
    limit: Number.isFinite(limit) && limit > 0 ? limit : response.data.length,
    total: Number.isFinite(totalHeader) && totalHeader >= 0
      ? totalHeader
      : (options.offset ?? 0) + response.data.length,
    hasMore: hasMoreHeader === null
      ? response.data.length >= (options.limit ?? response.data.length + 1)
      : hasMoreHeader.toLowerCase() === "true",
  };
}

export async function getDocuments(options: DocumentListOptions = {}): Promise<DocumentResponse[]> {
  return (await getDocumentsPage(options)).items;
}

export async function getDocument(documentId: string): Promise<DocumentResponse> {
  return fetchJson<DocumentResponse>(`/documents/${encodeURIComponent(documentId)}`);
}

export async function updateDocument(
  documentId: string,
  update: UpdateDocumentRequest,
): Promise<DocumentResponse> {
  return fetchJson<DocumentResponse>(`/documents/${encodeURIComponent(documentId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(update),
  });
}

export async function restoreDocument(documentId: string): Promise<DocumentResponse> {
  return fetchJson<DocumentResponse>(
    `/documents/${encodeURIComponent(documentId)}/restore`,
    { method: "POST" },
  );
}

export async function trashDocument(documentId: string): Promise<DocumentResponse> {
  return fetchJson<DocumentResponse>(`/documents/${encodeURIComponent(documentId)}`, {
    method: "DELETE",
  });
}

// ---------------------------------------------------------------------------
// Cross-device synchronization
// ---------------------------------------------------------------------------

export interface SyncOperationRequest {
  client_id: string;
  client_operation_id: string;
  document_id: string;
  base_sync_version: number;
  entity_type: string;
  entity_id?: string | null;
  operation_type: string;
  payload: Record<string, unknown>;
}

export interface SyncAcceptedOperation {
  client_id: string;
  client_operation_id: string;
  operation_id: string;
  server_sequence: number;
  document_id: string;
  sync_version: number;
  idempotent: boolean;
}

export interface SyncConflict {
  conflict_id: string;
  document_id: string;
  client_id: string;
  client_operation_id: string;
  base_sync_version: number;
  current_sync_version: number;
  entity_type: string;
  entity_id: string | null;
  operation_type: string;
  payload: Record<string, unknown>;
  status: string;
  resolution: Record<string, unknown> | null;
  is_tombstone?: boolean;
  created_at: string;
  resolved_at: string | null;
}

export interface SyncPushResponse {
  accepted: SyncAcceptedOperation[];
  conflicts: SyncConflict[];
  current_versions: Record<string, number>;
}

export interface SyncChange {
  server_sequence: number;
  operation_id: string;
  document_id: string;
  client_id: string;
  client_operation_id: string;
  base_sync_version: number;
  sync_version: number;
  entity_type: string;
  entity_id: string | null;
  operation_type: string;
  payload: Record<string, unknown>;
  created_at: string;
  contract_version?: string;
  is_tombstone?: boolean;
}

export interface SyncPullResponse {
  items: SyncChange[];
  next_cursor: number;
  has_more: boolean;
}

export async function pushSyncOperations(
  operations: SyncOperationRequest[],
): Promise<SyncPushResponse> {
  return fetchJson<SyncPushResponse>("/sync/push", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ operations }),
  });
}

export async function pullSyncOperations(
  cursor = 0,
  limit = 50,
): Promise<SyncPullResponse> {
  const params = new URLSearchParams({
    cursor: String(cursor),
    limit: String(limit),
  });
  return fetchJson<SyncPullResponse>(`/sync/pull?${params.toString()}`, {
    method: "GET",
  });
}

export async function listSyncConflicts(
  includeResolved = false,
): Promise<SyncConflict[]> {
  const suffix = includeResolved ? "?include_resolved=true" : "";
  return fetchJson<SyncConflict[]>(`/sync/conflicts${suffix}`, {
    method: "GET",
  });
}

export async function resolveSyncConflict(
  conflictId: string,
  decision: "keep_server" | "retry_as_new",
  details: Record<string, unknown> = {},
): Promise<SyncConflict> {
  return fetchJson<SyncConflict>(
    `/sync/conflicts/${encodeURIComponent(conflictId)}/resolve`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision, details }),
    },
  );
}

export interface SyncClientInfo {
  client_id: string;
  platform: string | null;
  device_name: string | null;
  cursor: number;
  last_seen_at: string;
  revoked_at: string | null;
  created_at: string;
}

export async function registerSyncClient(body: {
  client_id: string;
  platform?: string;
  device_name?: string;
}): Promise<SyncClientInfo> {
  return fetchJson<SyncClientInfo>("/sync/clients", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function acknowledgeSyncCursor(
  clientId: string,
  cursor: number,
): Promise<SyncClientInfo> {
  return fetchJson<SyncClientInfo>(
    `/sync/clients/${encodeURIComponent(clientId)}/ack`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cursor }),
    },
  );
}

export async function listSyncClients(): Promise<SyncClientInfo[]> {
  return fetchJson<SyncClientInfo[]>("/sync/clients", { method: "GET" });
}

export interface ResumableUploadSession {
  upload_id: string;
  filename: string;
  content_type: string | null;
  expected_size: number;
  expected_sha256: string;
  received_size: number;
  part_size: number;
  status: string;
  document_id: string | null;
  assembled_ref: string | null;
  parts: Array<{ part_number: number; size_bytes: number; sha256: string }>;
  expires_at: string;
  error_code: string | null;
  error_message: string | null;
}

export async function createResumableUpload(body: {
  filename: string;
  expected_size: number;
  expected_sha256: string;
  content_type?: string;
  part_size?: number;
  idempotency_key?: string;
  document_id?: string;
}): Promise<ResumableUploadSession> {
  return fetchJson<ResumableUploadSession>("/uploads", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function uploadResumablePart(
  uploadId: string,
  partNumber: number,
  data: Blob,
  sha256: string,
  signal?: AbortSignal,
): Promise<ResumableUploadSession> {
  return fetchJson<ResumableUploadSession>(
    `/uploads/${encodeURIComponent(uploadId)}/parts/${partNumber}`,
    {
      method: "PUT",
      headers: { "X-Part-SHA256": sha256 },
      body: data,
      signal,
    },
  );
}

export async function completeResumableUpload(
  uploadId: string,
): Promise<ResumableUploadSession> {
  return fetchJson<ResumableUploadSession>(
    `/uploads/${encodeURIComponent(uploadId)}/complete`,
    { method: "POST" },
  );
}

export async function getResumableUpload(
  uploadId: string,
): Promise<ResumableUploadSession> {
  return fetchJson<ResumableUploadSession>(
    `/uploads/${encodeURIComponent(uploadId)}`,
    { method: "GET" },
  );
}

export interface ConsentResponse {
  event_id: string;
  policy_version: string;
  scope: string;
  action: string;
  created_at: string;
  personal_training_allowed: boolean;
}

export async function getConsent(): Promise<ConsentResponse | null> {
  try {
    return await fetchJson<ConsentResponse>("/personalization/consent");
  } catch (err) {
    if (err instanceof ApiError && (err.status === 404 || err.status === 403)) {
      return null;
    }
    throw err;
  }
}

export async function recordConsent(action: "grant" | "withdraw"): Promise<ConsentResponse> {
  return fetchJson<ConsentResponse>("/personalization/consent", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, scope: "personal_model" }),
  });
}
