# Implementation Plan: Handwriting Notes Digitizer

## Overview

This plan converts the design into incremental, test-driven coding tasks. The implementation language is **Python** (FastAPI backend, PyTorch HTR/training, Hypothesis property tests) and **TypeScript** (web frontend, fast-check property tests), as specified in the design.

Tasks build on each other: scaffolding and reproducibility foundation first, then data/persistence and isolation, then upload/validation, the HTR base model and inference engine, the recognition service, the Editor and correction loop (the hero feature), calibration, the asynchronous personalization service, evaluation, export, partial-deployment health, full wiring, and Docker packaging.

Property-based tests follow the design's Correctness Properties section: each of Properties 1–28 is implemented as a **single** property-based test running a **minimum of 100 iterations**, tagged with the comment `Feature: handwriting-notes-digitizer, Property {number}: {property_text}`. Property tests use Hypothesis (Python) or fast-check (TypeScript). Test sub-tasks are marked optional with `*`.

## Tasks

- [x] 1. Set up project structure and reproducibility foundation
  - [x] 1.1 Scaffold the Python backend project
    - Create the backend package layout (FastAPI app entry, `services/`, `ml/`, `data/`, `config/`) and a pinned dependency manifest (FastAPI, PyTorch, Pillow, SQLAlchemy/ORM, Hypothesis, pytest) with exact versions
    - Add a central config module with fixed random seeds and deterministic settings used across recognition and training
    - _Requirements: 11.1_
  - [x] 1.2 Scaffold the TypeScript web frontend project
    - Create the frontend project (upload UI, Editor, Calibration UI, Before/After view modules) with a pinned dependency manifest including fast-check and a test runner (vitest/jest with `--run`)
    - _Requirements: 11.1, 11.4_
  - [x] 1.3 Configure test frameworks for both stacks
    - Wire up pytest + Hypothesis (default profile of ≥100 iterations) for Python and fast-check + vitest for TypeScript, with a shared tagging convention for property tests
    - _Requirements: 11.1_

- [x] 2. Implement data models, persistence, and object store
  - [x] 2.1 Define relational data models and schema
    - Implement `User`, `Image`, `RecognizedWord`, `TrainingPair` (with unique constraint on `(user_id, source_region_key)`), `Adapter` (unique per `user_id`), `TrainingRun`, `EvaluationResult` per the design's data model, including enums and `consumed_by_run_id`
    - _Requirements: 1.6, 4.1, 4.7, 5.3, 6.1, 7.3_
  - [x] 2.2 Implement the object/image store abstraction
    - Create a storage interface for image bytes, region crops, and adapter weight files, referenced by path from the relational records
    - _Requirements: 1.6, 4.3_
  - [x] 2.3 Implement the per-user-scoped data-access layer
    - Implement repositories where every query is scoped by authenticated `user_id` at the data-access layer (defense in depth), not just in route handlers
    - _Requirements: 10.2, 10.5_
  - [x] 2.4 Write unit tests for data-access scoping
    - Verify repository queries cannot return rows owned by another user
    - _Requirements: 10.5_

- [x] 3. Implement authentication and per-user isolation
  - [x] 3.1 Implement the Auth + Session layer
    - Implement login/session-or-token auth, password hashing, and an auth dependency that gates image, correction, and calibration submissions; reject unauthenticated requests with an auth-required indication storing nothing
    - _Requirements: 10.1, 10.4, 1.5_
  - [x] 3.2 Enforce cross-user isolation across services
    - Ensure requests for resources owned by another user return 404 (not 403) so existence is not disclosed; deny access to other users' training pairs, recognized text, and adapters
    - _Requirements: 10.5_
  - [x] 3.3 Write property test for the authentication gate (Property 2)
    - **Property 2: Authentication gate stores nothing on unauthenticated requests**
    - Use Hypothesis; assert persisted-data state is identical before/after any unauthenticated image/correction/calibration submission
    - **Validates: Requirements 1.5, 10.1, 10.4**
  - [x] 3.4 Write property test for cross-user access denial (Property 28)
    - **Property 28: Cross-user access is denied without disclosure**
    - **Validates: Requirements 10.5**

- [x] 4. Implement image upload and validation
  - [x] 4.1 Implement the image validator
    - Validate by decoded content (PNG/JPEG), size (>0 and ≤10,485,760 bytes), and decodability; return a reason identifying the violated rule (unsupported format / size limit / invalid image)
    - _Requirements: 1.1, 1.2, 1.3, 1.4_
  - [x] 4.2 Implement `submit_image` endpoint with ownership persistence
    - Wire auth → validation → persist accepted image associated with the submitting user (`POST /images`)
    - _Requirements: 1.1, 1.6, 10.4_
  - [x] 4.3 Write property test for image validation (Property 1)
    - **Property 1: Image validation accepts exactly the valid set**
    - Use a generator producing payloads with controllable magic bytes, decodability, and size around the 10 MB boundary
    - **Validates: Requirements 1.1, 1.2, 1.3, 1.4**
  - [x] 4.4 Write property test for image ownership (Property 3)
    - **Property 3: Accepted images are owned by their submitter**
    - **Validates: Requirements 1.6**

- [x] 5. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. Implement the HTR base model training procedure (reproducible)
  - [x] 6.1 Implement the CNN + BiLSTM + CTC model architecture
    - Define the PyTorch network (CNN feature extractor → BiLSTM → CTC) with a pinned configuration file (layer sizes, learning rate, batch size, epochs, augmentation)
    - _Requirements: 11.2_
  - [x] 6.2 Implement the IAM dataset pipeline and fixed split
    - Build deterministic data loading for the IAM dataset pinned to a documented version with a checked-in train/validation/test split definition
    - _Requirements: 11.2_
  - [x] 6.3 Implement the deterministic base-model training script
    - Train the base model with fixed seeds and deterministic data ordering; document dataset version, split, and training configuration; produce the shared frozen base model artifact
    - _Requirements: 11.2, 11.3_
  - [x] 6.4 Prepare the general English evaluation set
    - Document and version a held-aside fixed evaluation subset used by the personalization guardrails
    - _Requirements: 6.2_
  - [x] 6.5 Implement optional ONNX export of the base model
    - Export the trained base model to ONNX for portable inference, ensuring adapters still compose with the frozen base at load time
    - _Requirements: 11.2_
  - [x] 6.6 Write reproducibility smoke test for base-model training
    - Re-run training with fixed seed/config and assert CER/WER on the fixed eval set are within the configured tolerance
    - _Requirements: 11.3_

- [x] 7. Implement the HTR inference engine
  - [x] 7.1 Implement preprocessing and layout/word segmentation
    - Decode, grayscale, deskew, normalize, and segment into lines (top-to-bottom) and words (left-to-right) producing crops with coordinates
    - _Requirements: 2.1, 2.2_
  - [x] 7.2 Implement recognition and result assembly with regions
    - Run crops through the model + CTC decoder and assemble ordered lines → ordered words, each word carrying source region coordinates and a `recognized_any_text` flag
    - _Requirements: 2.1, 2.2, 2.4, 2.5_
  - [x] 7.3 Implement adapter composition in the engine
    - Compose a user's adapter weights with the frozen base for an inference when an adapter is provided; use the base alone when absent
    - _Requirements: 5.4, 5.5_
  - [x] 7.4 Write property test for recognized-word regions (Property 4)
    - **Property 4: Every recognized word carries a valid region**
    - **Validates: Requirements 2.4**
  - [x] 7.5 Write property test for adapter application (Property 14)
    - **Property 14: Recognition applies the adapter exactly when one exists**
    - Use small fakes for base/adapter so 100+ iterations are cheap
    - **Validates: Requirements 5.4, 5.5**
  - [x] 7.6 Write unit tests for HTR output on fixed sample images
    - Assert lines/words are produced and segmented into words (not isolated characters), and a no-text fixture yields a no-text result
    - _Requirements: 2.1, 2.2, 2.5_

- [x] 8. Implement the Recognition_Service
  - [x] 8.1 Implement recognition orchestration with timeout and error handling
    - Enforce a 10-second budget (cancel + timeout error on overrun, retaining the image); catch engine failures returning a recognition-failed error without modifying stored data; return a structured no-text result when nothing is detected
    - _Requirements: 2.3, 2.5, 2.6, 2.7_
  - [x] 8.2 Implement recognition endpoints with per-user adapter selection
    - Implement `POST /images/{image_id}/recognize` and `GET /images/{image_id}/result`, selecting only the requesting user's adapter when one exists
    - _Requirements: 2.1, 5.4, 5.5, 10.3_
  - [x] 8.3 Write property test for recognition timeout (Property 5)
    - **Property 5: Recognition timeout retains the image**
    - **Validates: Requirements 2.6**
  - [x] 8.4 Write property test for engine failure (Property 6)
    - **Property 6: Engine failure leaves stored data unchanged**
    - **Validates: Requirements 2.7**
  - [x] 8.5 Write property test for adapter isolation at recognition (Property 27)
    - **Property 27: Recognition uses only the requesting user's adapter**
    - **Validates: Requirements 10.3**
  - [x] 8.6 Write integration test for recognition latency
    - Assert ~100-word fixtures return within 10 seconds (1–3 representative runs)
    - _Requirements: 2.3_

- [x] 9. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 10. Implement the Editor frontend
  - [x] 10.1 Implement recognized-text display alongside the image
    - Render text in reading order (lines top-to-bottom, words left-to-right) with the original image visible at the same time; show a no-text message with the image when applicable
    - _Requirements: 3.1, 3.2, 3.3, 3.4_
  - [x] 10.2 Implement inline editing with confirm
    - Allow editing any word (up to 10,000 chars per word), update the display on confirm, and call the Correction_Pipeline only for changed words
    - _Requirements: 4.1, 4.2_
  - [x] 10.3 Write property test for display ordering (Property 7)
    - **Property 7: Display order follows reading order**
    - Use fast-check (TypeScript)
    - **Validates: Requirements 3.2**
  - [x] 10.4 Write unit tests for the Editor component
    - Render within budget, show image + text together, no-text message, update on confirm, 10,000-char edit boundary
    - _Requirements: 3.1, 3.3, 3.4, 4.1, 4.2_

- [x] 11. Implement the Correction_Pipeline (hero feature)
  - [x] 11.1 Implement create-on-change training-pair capture
    - On a confirmed edit, create a `Training_Pair` (region crop + corrected text) only when corrected text differs from recognized text; persist within 5 seconds; leave pairs unchanged when text is identical
    - _Requirements: 4.3, 4.4, 4.5, 4.8_
  - [x] 11.2 Implement idempotent region-keyed upsert
    - Re-correcting the same region upserts (keyed by `(user, source_region_key)`) to the latest text with no duplicates
    - _Requirements: 4.7_
  - [x] 11.3 Implement storage-failure handling and training enqueue trigger
    - On storage failure return an error, keep the corrected text in the Editor, and leave prior pairs unchanged (transactional write); enqueue personalization when the user reaches ≥20 unused pairs
    - _Requirements: 4.6, 5.1_
  - [x] 11.4 Write property test for create-on-change (Property 8)
    - **Property 8: A training pair is created exactly when a confirmed edit changes a word**
    - **Validates: Requirements 4.3, 4.4, 4.8**
  - [x] 11.5 Write property test for idempotent re-correction (Property 9)
    - **Property 9: Re-correcting a region is idempotent and last-write-wins**
    - Use a generator of correction sequences targeting the same region
    - **Validates: Requirements 4.7**
  - [x] 11.6 Write property test for correction storage failure (Property 10)
    - **Property 10: Correction storage failure preserves prior pairs**
    - **Validates: Requirements 4.6**
  - [x] 11.7 Write integration test for correction persistence latency
    - Assert a created Training_Pair is stored within 5 seconds
    - _Requirements: 4.5_

- [x] 12. Implement the optional Calibration flow
  - [x] 12.1 Implement calibration session prompt generation
    - `POST /calibration/session` presents between 3 and 10 prompts, each 1–200 characters
    - _Requirements: 9.1, 9.2_
  - [x] 12.2 Implement calibration sample submission
    - Validate each submission (non-empty, ≤10 MB, detectable handwriting region); on success create a `Training_Pair` with `origin=calibration` using the known prompt text, stored in the same store as corrections; reject invalid submissions with a reason and create no pair
    - _Requirements: 9.3, 9.4, 9.5_
  - [x] 12.3 Write property test for calibration prompt bounds (Property 24)
    - **Property 24: Calibration prompt-set bounds**
    - **Validates: Requirements 9.2**
  - [x] 12.4 Write property test for calibration submission validity (Property 25)
    - **Property 25: Valid calibration submission creates a labeled pair; invalid does not**
    - **Validates: Requirements 9.3, 9.4**
  - [x] 12.5 Write unit tests for calibration optionality and shared store
    - Assert the system functions without calibration and that calibration pairs land in the same store as correction pairs
    - _Requirements: 9.1, 9.5_

- [x] 13. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 14. Implement the asynchronous Personalization_Service
  - [x] 14.1 Implement the unused-pair threshold trigger
    - `maybe_enqueue_training` enqueues a job iff the count of pairs not yet consumed by a completed training is ≥20
    - _Requirements: 5.1_
  - [x] 14.2 Implement held-out split sizing
    - Reserve a held-out set of size `max(round(0.2 × N), 5)` bounded by N, disjoint from the training set and partitioning the user's pairs
    - _Requirements: 6.1_
  - [x] 14.3 Implement the PEFT adapter trainer with early stopping
    - Train only adapter parameters (LoRA-style/last-layer) keeping the base frozen; store exactly one adapter per user; stop when held-out CER fails to improve by ≥0.5 pp over 3 consecutive cycles and never exceed 50 epochs; use fixed seeds for determinism
    - _Requirements: 5.2, 5.3, 6.5_
  - [x] 14.4 Implement the per-user training lock
    - Hold a per-user mutex so no two adapter trainings run concurrently for the same user
    - _Requirements: 5.6_
  - [x] 14.5 Implement accept/reject guardrails, isolation, and outcome handling
    - Apply the general-set guardrail (accept iff adapter CER ≤ base CER + 1.0 pp AND adapter WER ≤ base WER + 2.0 pp), otherwise reject; train only from the user's own pairs across both origins; on failure or rejection retain the prior active configuration, record the outcome, and notify the user on rejection; mark consumed pairs only on acceptance
    - _Requirements: 5.7, 6.3, 6.4, 6.6, 9.6, 10.2_
  - [x] 14.6 Write property test for the threshold trigger (Property 11)
    - **Property 11: Training triggers exactly at the unused-pair threshold**
    - **Validates: Requirements 5.1**
  - [x] 14.7 Write property test for held-out split (Property 17)
    - **Property 17: Held-out set sizing and disjointness**
    - **Validates: Requirements 6.1**
  - [x] 14.8 Write property test for base-model freezing (Property 12)
    - **Property 12: Base model parameters are unchanged by training**
    - Use a small fake trainer/model so iterations are cheap
    - **Validates: Requirements 5.2**
  - [x] 14.9 Write property test for adapter cardinality (Property 13)
    - **Property 13: At most one adapter per user**
    - **Validates: Requirements 5.3**
  - [x] 14.10 Write property test for no concurrent training (Property 15)
    - **Property 15: No concurrent training per user**
    - **Validates: Requirements 5.6**
  - [x] 14.11 Write property test for training-failure retention (Property 16)
    - **Property 16: Training failure retains the prior active configuration**
    - **Validates: Requirements 5.7**
  - [x] 14.12 Write property test for the guardrail decision (Property 18)
    - **Property 18: Adapter accept/reject guardrail decision**
    - **Validates: Requirements 6.3, 6.4**
  - [x] 14.13 Write property test for early stopping (Property 19)
    - **Property 19: Early-stopping criterion**
    - Use a generator of held-out CER trajectories (improving, plateauing, oscillating)
    - **Validates: Requirements 6.5**
  - [x] 14.14 Write property test for per-user training-set selection (Property 26)
    - **Property 26: A user's training set is exactly that user's pairs across all origins**
    - Use multi-user pair collections with mixed origins
    - **Validates: Requirements 9.6, 10.2**
  - [x] 14.15 Write integration tests for training trigger timing and end-to-end adapter use
    - Assert enqueue within 60 s of reaching threshold, adapter applied end-to-end at inference, and general-set measurement recorded post-training
    - _Requirements: 5.1, 5.4, 6.2_
  - [x] 14.16 Write unit test for guardrail-rejection side effects
    - Assert a notification and a "did not improve" record are produced on rejection
    - _Requirements: 6.6_

- [x] 15. Implement the Evaluation_Service
  - [x] 15.1 Implement CER and WER metrics
    - Compute character/word-level Levenshtein distance over reference length, yielding a non-negative ratio; treat an empty reference as unavailable rather than dividing by zero
    - _Requirements: 7.4, 7.5, 7.6_
  - [x] 15.2 Implement before/after evaluation reporting
    - Measure base-model and adapter CER/WER on the user's held-out set and report before, after, and delta (before − after) plus availability
    - _Requirements: 7.1, 7.2, 7.3_
  - [x] 15.3 Implement partial-failure handling for measurements
    - Report a failed measurement as unavailable while retaining and reporting any successfully computed measurement
    - _Requirements: 7.7_
  - [x] 15.4 Write property test for CER/WER definition (Property 21)
    - **Property 21: CER and WER metric definition and non-negativity**
    - Use a generator including empty, whitespace, Unicode, and long strings
    - **Validates: Requirements 7.4, 7.5, 7.6**
  - [x] 15.5 Write property test for delta arithmetic (Property 20)
    - **Property 20: Reported delta equals before minus after**
    - **Validates: Requirements 7.3**
  - [x] 15.6 Write property test for partial measurement failure (Property 22)
    - **Property 22: Partial measurement failure retains successful measurements**
    - **Validates: Requirements 7.7**

- [x] 16. Implement the Export_Service
  - [x] 16.1 Implement plain-text and markdown export
    - Plain text preserves line breaks; markdown preserves line and paragraph structure; output reflects every confirmed correction at request time and never waits for training; empty text returns a nothing-to-export message with no output; on failure return a failure indication leaving text unchanged
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6_
  - [x] 16.2 Write property test for export fidelity (Property 23)
    - **Property 23: Export fidelity preserves structure and current corrections**
    - **Validates: Requirements 8.1, 8.2, 8.3**
  - [x] 16.3 Write unit tests for export edge cases
    - Empty-text message, export-failure handling, and export-while-training-pending uses current text
    - _Requirements: 8.4, 8.5, 8.6_

- [x] 17. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 18. Implement partial-deployment capability registry and health
  - [x] 18.1 Implement the capability registry and `/health`
    - Map each capability to its required components and expose availability via `GET /health`
    - _Requirements: 11.5_
  - [x] 18.2 Implement capability-unavailable responses
    - Endpoints whose backing component is unavailable return a structured "capability unavailable" response while available capabilities continue serving
    - _Requirements: 11.5, 11.6_
  - [x] 18.3 Write integration test for partial-deployment routing
    - Disable a component and assert available capabilities serve while the unavailable one is flagged
    - _Requirements: 11.5, 11.6_

- [x] 19. Integration and frontend wiring
  - [x] 19.1 Wire the frontend to the backend capabilities
    - Connect upload → recognize → Editor display → inline correction → export, plus the optional calibration UI, through the web user interface
    - _Requirements: 11.4_
  - [x] 19.2 Wire the Before/After accuracy view and personalization status
    - Surface `GET /personalization/status` and `GET /personalization/evaluation` (before/after CER/WER, deltas, unavailable states) in the UI
    - _Requirements: 7.3, 5.6, 5.7_
  - [x] 19.3 Write end-to-end integration test for the full flow
    - Assert a full deployment exposes upload, recognition, correction, personalization, and export through the UI
    - _Requirements: 11.4_

- [x] 20. Docker packaging and reproducibility
  - [x] 20.1 Create the containerized deployment definition
    - Add a Dockerfile/compose that builds frontend, backend, and recognition runtime from pinned dependency versions with no manual code modification, with independently togglable components for partial deployment
    - _Requirements: 11.1, 11.4, 11.5_
  - [x] 20.2 Write build/reproducibility smoke test
    - Assert the container builds from a clean checkout with pinned dependencies and no manual edits, and that the documented base-model training procedure exists and is runnable
    - _Requirements: 11.1, 11.2_

- [x] 21. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional test tasks and can be skipped for a faster MVP; core implementation tasks are never optional.
- Each task references specific requirement sub-clauses for traceability.
- Property tests (Properties 1–28) each implement a single design property at ≥100 iterations, tagged `Feature: handwriting-notes-digitizer, Property {number}: {property_text}`, using Hypothesis (Python) or fast-check (TypeScript).
- For invariants that touch training (Properties 12, 13, 15, 16), the base model and trainer are replaced with small fakes/mocks so 100+ iterations stay cheap.
- Checkpoints ensure incremental validation at natural boundaries.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2", "1.3"] },
    { "id": 1, "tasks": ["2.1", "2.2", "6.1", "6.2"] },
    { "id": 2, "tasks": ["2.3", "6.3", "6.4"] },
    { "id": 3, "tasks": ["2.4", "3.1", "3.2", "6.5", "7.1"] },
    { "id": 4, "tasks": ["3.3", "3.4", "4.1", "6.6", "7.2", "7.3"] },
    { "id": 5, "tasks": ["4.2", "4.3", "4.4", "7.4", "7.5", "7.6", "10.1"] },
    { "id": 6, "tasks": ["8.1", "10.2", "14.2", "15.1"] },
    { "id": 7, "tasks": ["8.2", "10.3", "10.4", "11.1", "15.2", "16.1"] },
    { "id": 8, "tasks": ["8.3", "8.4", "8.5", "8.6", "11.2", "12.1", "14.1", "15.3", "16.2", "16.3"] },
    { "id": 9, "tasks": ["11.3", "11.4", "11.5", "11.6", "12.2", "14.3", "15.4", "15.5", "15.6"] },
    { "id": 10, "tasks": ["11.7", "12.3", "12.4", "12.5", "14.4", "14.5"] },
    { "id": 11, "tasks": ["14.6", "14.7", "14.8", "14.9", "14.10", "14.11", "14.12", "14.13", "14.14", "18.1"] },
    { "id": 12, "tasks": ["14.15", "14.16", "18.2", "19.1", "19.2"] },
    { "id": 13, "tasks": ["18.3", "19.3", "20.1"] },
    { "id": 14, "tasks": ["20.2"] }
  ]
}
```
