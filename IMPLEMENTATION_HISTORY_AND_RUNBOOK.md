# Handwriting Notes Digitizer: Implementation History and Runbook

This document logs the major debugging, fixes, and operational decisions made during the end-to-end bring-up of this project. It is intended as a future reference for both users and agents.

## 1) What was fixed (chronological highlights)

### Base model/training correctness
- Root cause discovered: base model training was running with synthetic fallback data when IAM path/config was missing.
- Added/supporting workflow for real IAM training:
  - Real manifest generation from IAM metadata.
  - Real training driver with epoch logging/checkpointing.
- Completed full real IAM run (12 epochs) and deployed resulting base model artifact to object store path used by app runtime.

### Recognition pipeline correctness
- Root cause discovered: training and inference granularity mismatch.
  - Model trained on line-level samples but inference assembled results from word crops.
- Fix applied: recognize full line crops and then distribute text across word regions for editor/export compatibility.

### Personalization/calibration trigger wiring
- Confirmed calibration submissions create `TrainingPair` entries and store crop image bytes in object store.
- Ensured calibration submissions trigger enqueue checks for personalization training threshold logic.
- Added/verified background worker pipeline for queued personalization runs.

### Frontend/backend contract mismatches
- Fixed calibration prompt submission payload mismatch (`prompt_index` expectation).
- Fixed response normalization mismatches in recognition flow.
- Improved API error rendering so user sees readable details instead of `[object Object]`.

### Export bug
- Root cause: frontend expected `{ content, format }`, backend actually returns discriminated `status` variants:
  - `{ status: "success", text, format }`
  - `{ status: "empty", message }`
  - `{ status: "error", message }`
- Fix: frontend now branches by `status`, downloads only on `"success"`, and surfaces message for `"empty"`/`"error"`.

### Personalization transparency UX
- Added clearer pipeline guide in UI.
- Added progress indicators from backend status fields.
- Added defensive handling for non-finite progress values (prevents `HTMLProgressElement.max` runtime crash).
- Added training timeline section in UI.
- Added list of collected training examples with source/type/status and sample thumbnail.

### New personalization transparency APIs
- `GET /personalization/status` now includes:
  - `unused_training_pairs`
  - `training_trigger_threshold`
  - `max_training_epochs`
- Added:
  - `GET /personalization/training-pairs`
  - `GET /personalization/training-pairs/{pair_id}/image`

## 2) Why users saw HTTP 500 at frontend

Two recurring causes occurred during the session:
- Frontend TypeScript compile/runtime breakages during iterative changes.
- Vite proxy errors when backend was not actually reachable (`ECONNREFUSED 127.0.0.1:8000`), which surfaced as frontend request failures.

Current known-good check:
- Frontend dev server: `http://127.0.0.1:5173/`
- Backend health: `http://127.0.0.1:8000/health`
- Vite proxy health path: `http://127.0.0.1:5173/api/health`

## 3) Operational runbook (local dev)

### Start backend
```bash
cd backend
.venv/bin/python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

### Start frontend
```bash
cd frontend
npm run dev -- --host 127.0.0.1 --port 5173
```

### Verify both quickly
```bash
curl -i http://127.0.0.1:8000/health
curl -i http://127.0.0.1:5173/api/health
```

## 4) Test commands used

### Frontend
```bash
cd frontend
npm test
npx tsc --noEmit
```

### Backend (personalization API focus)
```bash
cd backend
python3 -m pytest tests/test_personalization_api.py -q
```

## 5) Data flow reference (personalization)

1. User submits correction or calibration sample.
2. Crop image is stored in object store.
3. `TrainingPair` row is upserted/created with label text and origin.
4. Unused pair count drives threshold check.
5. Worker processes queued runs and evaluates adapter.
6. Adapter accepted/rejected/failed outcome updates user status and UI.

## 6) Known pitfalls and guardrails

- Restart backend after API schema changes; stale process can serve old response shapes.
- Keep frontend/backend response types synchronized to avoid silent runtime failures.
- If Vite shows proxy `ECONNREFUSED`, backend is not reachable (or wrong host/port).
- This workspace currently has no git metadata in `MLTraining`, `backend`, or `frontend`; commits require a valid git repository.

## 7) Recommended next improvements

- Add a dedicated `docs/` section and move this runbook there.
- Add health panel in UI with live backend reachability and last successful API call timestamps.
- Add integration test that verifies frontend export handles all backend export `status` variants.
- Add CI check for frontend-backend contract drift (schema snapshot/type generation).

