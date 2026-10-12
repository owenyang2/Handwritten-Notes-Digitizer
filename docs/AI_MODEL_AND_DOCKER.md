# Legacy prose recognition and Docker wiring

This document describes the original CRNN line/word workflow. The current
document-conversion demo uses separate UniMERNet formula and TrOCR prose
adapters. Start with [getting started](GETTING_STARTED.md),
[current model setup](MODEL_AND_IPAD_SETUP.md), and
[feature status](FEATURE_STATUS_MATRIX.md) for the maintained product state.

## Runtime Path

The legacy web workflow uses the shared FastAPI backend and Vite/nginx frontend.

1. The frontend uploads an image with `POST /api/images`.
2. nginx or the Vite dev server strips `/api` and forwards the request to the backend.
3. The backend stores image bytes in the object store and creates an `Image` row.
4. The frontend calls `POST /api/images/{image_id}/recognize`.
5. `Recognition_Service` loads the stored image bytes, resolves any active user adapter, and calls `app.ml.recognition.recognize_bytes`.
6. The HTR engine preprocesses the page, segments text regions, recognizes each line with the CRNN base model, distributes decoded tokens across word boxes, and returns editable words with source regions.

## Model Artifact

When an operator supplies a verified trained base model, the runtime expects it
at:

```text
backend/var/objectstore/models/base_model/base_model.pt
backend/var/objectstore/models/base_model/base_model.meta.json
```

Docker mounts that host directory read-only into:

```text
/app/var/objectstore/models
```

The backend looks for:

```text
<HND_OBJECT_STORE_PATH>/models/base_model/base_model.pt
```

In Docker, `HND_OBJECT_STORE_PATH=/app/var/objectstore`, so the paths line up. In local development, run backend commands from `backend/` so the default `./var/objectstore` points at `backend/var/objectstore`.

## Previous Failure Mode

The UI used to collapse every non-successful recognition response into `recognizedAnyText=false`, so engine failures and timeouts were displayed as `No text recognized`. The editor now distinguishes:

- `no_text`: the engine ran successfully but found no decodable text.
- `timeout`: recognition exceeded the backend time budget.
- `error`: storage/model/engine failure.

That distinction matters because a missing or mis-mounted model artifact can
otherwise look like a handwriting problem. A clean checkout intentionally has
no model weights; `/health` and the doctor script must report recognition as
unavailable until an artifact is installed and probed.

## Line/Word Alignment

The shared base model is trained on line images, so recognition decodes a full
line first. The UI still needs editable chunks, so the backend compares:

- decoded text token count
- detected word-region count

When the counts match, the line is marked `aligned` and rendered as individual
editable word spans. Confirmed word edits can safely create region-cropped
training pairs.

When the counts differ, the line is marked `uncertain` and rendered as a
full-line editable textarea. These edits are kept in the editor and local
export path, but they are not submitted as region-based correction pairs because
the system cannot truthfully say which token belongs to which image box yet.

This handles cases like a decoded line containing:

```text
quaintly but kept driving their oxen forward
```

while segmentation only found one or a few large regions. The UI no longer
pretends that the whole line belongs to one word box.

The region coordinates are backend-preprocessed image pixels, not guessed CSS
coordinates. Recognition responses include the processed image dimensions, and
the frontend displays `/api/images/{image_id}/processed` in the review pane so
the boxes and pixels share the same coordinate space.

The next improvement is a richer image-region review tool for uncertain lines:
delete false positives, split/merge regions, and approve or disable saved
training examples before an epoch starts.

## Review Workspace UI

The frontend is now organized as a review console:

- left rail: upload -> review -> train -> export workflow
- main workspace: upload plus image/text recognition review
- image stage: region overlays distinguish aligned and uncertain detections
  on the same preprocessed image used by backend segmentation
- text panel: word spans save region-backed corrections; uncertain lines render
  as text-only review areas
- side panel: export controls, personalization status, training timeline,
  collected sample queue, and calibration

Current correction semantics:

- Saving a reviewed line submits the current line region and line transcription
  as a region-backed training pair. This is the preferred path for the current
  line-trained CTC model.
- Editing an aligned word can still submit a precise word correction, but word
  chips are secondary because word segmentation is less reliable.
- Marking a line as `Not text` is how users reject false-positive regions,
  such as a lone dot or scanner speck. It removes that line from export and
  disables saving it as training data; `Restore line` reverses the choice.
- Collected training examples can be managed from the training sample queue:
  edit the corrected text label, or delete a bad crop/sample entirely. Editing
  a label marks that sample unused again so the corrected version can feed a
  future training round. Deleting or editing does not retroactively change an
  adapter that has already finished training.
- The image overlay is the crop editor: drag a line box to move it, and drag
  its lower-right handle to resize the crop before saving it for training.
  Raw `x/y/width/height` coordinates are internal implementation details.

The missing backend layer for a full pre-epoch audit is mutating training-pair
management after samples are saved: edit label, disable/delete bad sample,
approve sample, and trigger training only from approved examples.

## One-Command Checks

Run:

```bash
make doctor
```

This checks:

- host model artifact and metadata
- `.env` local-vs-Docker path pitfalls
- backend health at `http://127.0.0.1:8000/health`
- Vite proxy health at `http://127.0.0.1:5173/api/health`
- Docker/nginx proxy health at `http://127.0.0.1/api/health`
- Alembic migration drift against the active runtime database (the Compose
  database when Docker is running)
- Python model loading through the backend code
- Docker container visibility of the mounted model, when Compose is running

The command returns a non-zero exit code for failed required checks. Missing
optional local services remain warnings. `make docker-smoke` waits for the
Compose health checks before invoking this diagnostic.

Common run commands:

```bash
make dev-backend
make dev-frontend
make docker-up
make docker-smoke
```
