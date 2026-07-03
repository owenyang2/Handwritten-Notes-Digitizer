// Shared domain types for the Handwriting Notes Digitizer frontend.
// These mirror the "Domain Value Objects" and data models in design.md so the
// frontend modules and the backend API agree on shapes.

/** A region in image pixel coordinates. */
export interface Region {
  x: number;
  y: number;
  width: number;
  height: number;
}

/** A single recognized word with its source region. */
export interface RecognizedWord {
  index: number;
  text: string;
  region: Region;
  confidence?: number;
}

/** A recognized line containing one or more words (left-to-right). */
export interface RecognizedLine {
  index: number;
  region: Region;
  text?: string;
  alignmentStatus?: "aligned" | "uncertain";
  alignmentReason?: string;
  words: RecognizedWord[];
}

/** The recognition result: ordered lines, each with ordered words. */
export interface RecognitionResult {
  lines: RecognizedLine[];
  recognizedAnyText: boolean;
  /** Pixel dimensions of the processed image whose coordinate space owns regions. */
  imageWidth?: number;
  imageHeight?: number;
  /** Backend recognition outcome, when rendered from an API response. */
  status?: "ok" | "no_text" | "timeout" | "error";
  /** Human-readable backend message for timeout/error outcomes. */
  message?: string;
}

/** Before/after CER and WER report surfaced in the accuracy view. */
export interface BeforeAfterReport {
  availability: "available" | "unavailable";
  beforeCer: number | null;
  beforeWer: number | null;
  afterCer: number | null;
  afterWer: number | null;
  deltaCer: number | null;
  deltaWer: number | null;
}

/** Supported export formats (Req 8). */
export type ExportFormat = "plain" | "markdown";

/** A calibration prompt sentence the user is asked to handwrite (Req 9.2). */
export interface CalibrationPrompt {
  id: string;
  text: string;
}
