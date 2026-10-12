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
  /**
   * Whether the base HTR model is trained.
   * - "loaded"     — a trained artifact loaded and passed its inference check
   * - "unavailable"— missing, corrupt, or failed probe; recognition is gated
   * - "trained"    — legacy alias for loaded
   * - "untrained"  — legacy alias for unavailable
   * - "loading"    — model file exists but has not been loaded yet
   * - "unknown"    — status could not be determined
   */
  model_status?: "loaded" | "unavailable" | "trained" | "untrained" | "loading" | "unknown";
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
  excluded?: boolean;
  reviewed?: boolean;
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
// PDF/math recognition jobs and reviewed regions
// ---------------------------------------------------------------------------

export type RecognitionJobState =
  | "queued"
  | "running"
  | "succeeded"
  | "no_text"
  | "failed"
  | "timeout"
  | "cancelled";

export interface RecognitionJobDto {
  job_id: string;
  state: RecognitionJobState;
  document_id: string | null;
  page_id: string | null;
  image_id: string | null;
  region_ids: string[];
  progress_current: number;
  progress_total: number;
  attempt: number;
  max_attempts: number;
  error_message: string | null;
  result_ids: string[];
  created_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
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

export type ExportFormat = "plain" | "markdown" | "pdf";

export interface ExportRequest {
  image_id: string;
  format: ExportFormat;
}

export interface ExportSuccessResponse {
  status: "success";
  text: string;
  format: ExportFormat;
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
