# Handwriting Notes Digitizer: Implementation History and Runbook

> **Historical document.** Retained for design rationale and development history.
> Its checkboxes, machine paths, and capability statements describe an earlier
> stage; use the [current status](../STATUS.md),
> [active remaining work](remaining-work-2026-10-11.md), and
> [setup guide](../guides/getting-started.md) for the maintained project state.

This document logs the major debugging, fixes, and operational decisions made during the end-to-end bring-up of this project. It is intended as a future reference for both users and agents.

## 1) What was fixed (chronological highlights)

### Base model/training correctness
- Root cause discovered: base model training was running with synthetic fallback data when IAM path/config was missing.
- Added/supporting workflow for real IAM training:
  - Real manifest generation from IAM metadata.
  - Real training driver with epoch logging/checkpointing.
- Historical note: an earlier working session reported a 12-epoch real IAM run
  and deployment. That artifact is not present in this checkout and has not
  been independently reverified; treat recognition as unavailable until a
  legally usable manifest, artifact digest, and evaluation report are supplied.

### Recognition pipeline correctness
- Root cause discovered: training and inference granularity mismatch.
  - Model trained on line-level samples but inference assembled results from word crops.
- Fix applied: recognize full line crops and then distribute text across word regions for editor/export compatibility.
- Added line/word alignment metadata:
  - If decoded token count matches detected word-region count, the UI renders
    word-level correction spans.
  - If counts differ, the UI renders a full-line editor and keeps edits out of
    region-based training pairs until a region review tool exists.

### Personalization/calibration trigger wiring
- Confirmed calibration submissions create `TrainingPair` entries and store crop image bytes in object store.
- Ensured calibration submissions trigger enqueue checks for personalization training threshold logic.
- Added/verified background worker pipeline for queued personalization runs.

### Frontend/backend contract mismatches
- Fixed calibration prompt submission payload mismatch (`prompt_index` expectation).
- Fixed response normalization mismatches in recognition flow.
- Improved API error rendering so user sees readable details instead of `[object Object]`.
- Fixed recognition status rendering after the UI overhaul: backend `failed` and
  `timeout` outcomes now render as explicit recognition errors/timeouts instead
  of being collapsed into the benign `No text recognized` empty-state.
- Added frontend handling for uncertain line alignment so tokenization mistakes
  are editable as line text instead of misleading word boxes.

### Docker/runtime wiring
- Added `.dockerignore` so Docker builds do not copy local `.venv`, object-store
  data, SQLite DBs, datasets, node_modules, or stale frontend build output.
- Fixed the frontend container healthcheck to probe `127.0.0.1` instead of
  `localhost`, matching the nginx listener in this image.
- Verified Docker nginx `/api/health` proxies to the backend and the backend
  container sees `/app/var/objectstore/models/base_model/base_model.pt`.

### Tooling/documentation
- Added `Makefile` targets for local dev, tests, Docker, logs, and diagnostics.
- Added `scripts/doctor.py` to check model artifacts, local/Docker health,
  common `.env` path traps, and Docker model mounts.
- Added `docs/AI_MODEL_AND_DOCKER.md` as the compact model/data-flow reference.

### Frontend redesign
- Replaced the old stacked basic sections with a two-column review console:
  workflow rail, recognition review workspace, image region overlays, explicit
  region-backed vs text-only edit states, export controls, personalization
  timeline, training example queue, and calibration panel.
- Word edits now show pending/saved/failed visual state for training-pair
  submission. Uncertain line edits remain clearly text-only.
- Pivoted correction UX to line-region review for the current line-trained
  model: users can edit line text, adjust line crop coordinates, save reviewed
  line crops as training examples, or ignore false-positive lines.
- Renamed the false-positive action to `Not text`, disabled line saving while
  marked, dimmed the matching overlay box, and filtered marked lines out of
  local export.
- Added training-pair management: users can edit a stored sample label or
  delete a bad sample from the collected examples panel. Label edits reset the
  pair to unused so corrected data can participate in a future training run.
- Replaced raw `x/y/width/height` region inputs with direct line-box editing in
  the image pane. Users drag the line crop or resize it with the lower-right
  handle; save uses the visual crop region.
- Fixed the overlay coordinate contract: recognition responses now include
  processed image dimensions, and the frontend uses the authenticated processed
  image endpoint for review so boxes are scaled against the same pixels the
  backend segmented.

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

### Repo-level shortcuts
```bash
make doctor
make dev-backend
make dev-frontend
make docker-up
make docker-smoke
```

### Verify both quickly
```bash
curl -i http://127.0.0.1:8000/health
curl -i http://127.0.0.1:5173/api/health
curl -i http://127.0.0.1/api/health
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
- The root `.env` is for Docker and uses `/app/var/objectstore`; local backend
  commands should be run from `backend/` so they use `backend/var/objectstore`.

## 7) Recommended next improvements

- Add a dedicated `docs/` section and move this runbook there.
- Add health panel in UI with live backend reachability and last successful API call timestamps.
- Add integration test that verifies frontend export handles all backend export `status` variants.
- Add CI check for frontend-backend contract drift (schema snapshot/type generation).

## 8) Refactor follow-up

- Repaired document repository scoping and exposed the document model/repository
  through the persistence package.
- Document creation, grouping, and latest-page reopening are now wired into the
  frontend review workflow.
- Uploads validate the selected document before object storage writes, and
  compensate by deleting stored bytes if the database transaction fails.
- Recognition SSE parsing now handles CRLF event boundaries and reliably rejects
  malformed, failed, or prematurely closed streams.
- Repaired the empty initial Alembic revision with a static schema snapshot and
  made application startup upgrade to the checked-in Alembic head revision.
- Aligned Docker and local PyTorch installs on the matching CPU wheel index so
  torchvision loads its compiled operators reliably.
- Anchored application dotenv loading to `backend/.env`; the repository-root
  `.env` remains Docker Compose-only and can no longer leak container paths
  into local tests.
- Made `make docker-smoke` wait for Compose health checks and made
  `make doctor` return a failing exit status when a required check fails.

## Current checkout truth (2026-09-12)

The checked-in code contains model loaders, training/evaluation tooling, and
fail-closed capability checks, but it intentionally does not contain licensed
handwriting pixels or model weights. `scripts/doctor.py` is the authoritative
local check for the current artifact directories; on this checkout it reports
the legacy prose, UniMERNet-small, and compact-math artifacts as absent. A
historical runbook entry is not deployment evidence.
