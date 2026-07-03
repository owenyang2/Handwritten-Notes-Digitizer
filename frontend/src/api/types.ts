// API request/response shapes that mirror the FastAPI backend.
// These are the wire formats; the domain types in src/types.ts are used inside
// the frontend after mapping from these shapes.

// ---------------------------------------------------------------------------
// Health / capability registry
// ---------------------------------------------------------------------------

/** Name of a capability reported by GET /health. */
export type CapabilityName =
  | "upload"
  | "recognition"
  | "correction"
  | "calibration"
  | "personalization"
  | "export";

/** Component status reported inside a capability entry. */
export interface CapabilityStatus {
  available: boolean;
  /** Human-readable reason when unavailable. */
  reason?: string;
}

/** Response shape for GET /health (Req 11.5, 11.6). */
export interface HealthResponse {
  status: "ok" | "degraded" | "unavailable";
  capabilities: Record<CapabilityName, CapabilityStatus>;
}

// ---------------------------------------------------------------------------
// Image upload  (POST /images)
// ---------------------------------------------------------------------------

export interface UploadImageResponse {
  image_id: string;
}

// ---------------------------------------------------------------------------
// Recognition  (POST /images/{image_id}/recognize)
// ---------------------------------------------------------------------------

export interface RegionDto {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface RecognizedWordDto {
  index: number;
  text: string;
  region: RegionDto;
  confidence?: number;
}

export interface RecognizedLineDto {
  index: number;
  region: RegionDto;
  text?: string;
  alignment_status?: "aligned" | "uncertain";
  alignment_reason?: string | null;
  words: RecognizedWordDto[];
}

export interface RecognitionResultDto {
  lines: RecognizedLineDto[];
  recognized_any_text: boolean;
  image_width?: number;
  image_height?: number;
  status: "ok" | "no_text" | "timeout" | "error";
  message?: string;
}

// ---------------------------------------------------------------------------
// Corrections  (POST /images/{image_id}/corrections)
// ---------------------------------------------------------------------------

export interface SubmitCorrectionRequest {
  word_index?: number;
  line_index?: number;
  region: RegionDto;
  recognized_text: string;
  corrected_text: string;
  store_if_unchanged?: boolean;
}

export interface SubmitCorrectionResponse {
  result: "created" | "updated" | "no_change";
  pair_id?: string;
}

// ---------------------------------------------------------------------------
// Export  (POST /export)
// ---------------------------------------------------------------------------

export interface ExportRequest {
  image_id: string;
  format: "plain" | "markdown";
}

export interface ExportSuccessResponse {
  status: "success";
  text: string;
  format: "plain" | "markdown";
}

export interface ExportEmptyResponse {
  status: "empty";
  message: string;
}

export interface ExportErrorResponse {
  status: "error";
  message: string;
}

export type ExportResponse = ExportSuccessResponse | ExportEmptyResponse | ExportErrorResponse;

// ---------------------------------------------------------------------------
// Calibration  (POST /calibration/session, POST /calibration/{session_id}/samples)
// ---------------------------------------------------------------------------

/**
 * Wire shape of `POST /calibration/session`.
 * The backend returns bare prompt sentences (no id) — the frontend assigns an
 * id based on array position (see `main.ts`) since submissions reference a
 * prompt by its 0-based index (`prompt_index`), not a stable identifier.
 */
export interface CalibrationSessionResponse {
  session_id: string;
  prompts: string[];
}

/** Wire shape returned on success by `POST /calibration/{session_id}/samples`.
 * Rejections (invalid image, no handwriting detected, etc.) are not returned
 * in the body — they surface as a 422 `ApiError` instead.
 */
export interface CalibrationSampleResponse {
  status: "accepted";
  pair_id: string;
}
