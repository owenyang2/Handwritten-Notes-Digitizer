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
  CalibrationSampleResponse,
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

  return response.json() as Promise<T>;
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
  words: RawRecognizedWordWire[];
}

/** Wire shape of POST /images/{image_id}/recognize. */
interface RawRecognizeResponse {
  outcome: "success" | "no_text" | "timeout" | "failed";
  image_id: string;
  lines?: RawRecognizedLineWire[];
  error_message?: string | null;
}

/** Wire shape of GET /images/{image_id}/result. */
interface RawStoredResultResponse {
  image_id: string;
  status: "accepted" | "recognized" | "no_text" | "timeout" | "error";
  lines: RawRecognizedLineWire[];
}

function mapWireLines(lines: RawRecognizedLineWire[]): RecognitionResultDto["lines"] {
  return lines.map((line) => ({
    index: line.line_index,
    region: line.region,
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
    status: statusByImageStatus[raw.status],
  };
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
export async function uploadImage(file: File): Promise<UploadImageResponse> {
  const formData = new FormData();
  formData.append("file", file);
  return fetchJson<UploadImageResponse>("/images", {
    method: "POST",
    body: formData,
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
): Promise<RecognitionResultDto> {
  const raw = await fetchJson<RawRecognizeResponse>(`/images/${imageId}/recognize`, {
    method: "POST",
  });
  return normalizeRecognizeResponse(raw);
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
  return fetchJson<ExportResponse>("/export", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
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
 * prompt by this index (the field name and type must match exactly: a
 * mismatch here previously surfaced as a confusing 422 "[object Object]").
 */
export async function submitCalibrationSample(
  sessionId: string,
  promptIndex: number,
  file: File,
): Promise<CalibrationSampleResponse> {
  const formData = new FormData();
  formData.append("prompt_index", String(promptIndex));
  formData.append("file", file);
  return fetchJson<CalibrationSampleResponse>(
    `/calibration/${sessionId}/samples`,
    {
      method: "POST",
      body: formData,
    },
  );
}
