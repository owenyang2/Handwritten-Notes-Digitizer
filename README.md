# Handwriting Notes Digitizer

A local web app for uploading handwriting images, reviewing recognized text, and turning corrected line crops into personalization training data.

The project is built around a FastAPI backend, a Vite/TypeScript frontend, and a line-trained CRNN/CTC handwriting recognition model. The current review workflow treats line regions as the primary training unit because the base model was trained on line images. Word boxes are shown when segmentation and token alignment are reliable, but reviewed line crops are the safest source of clean training examples.

## What It Does

- Upload PNG/JPEG handwriting samples.
- Run OCR-style handwriting recognition through the backend model pipeline.
- Review detected line regions directly on the processed image.
- Drag or resize line boxes before saving them as training crops.
- Edit recognized line text before adding it to the training queue.
- Mark false-positive detections, such as dots or non-text marks, as `Not text`.
- Manage collected training samples by editing labels or deleting bad crops.
- Export reviewed text as plain text or Markdown.
- Personalize recognition with per-user adapter training when enough clean examples are collected.

## Quick Start

The recommended path is Docker.

```bash
make docker-up
```

Then open:

```text
http://localhost/
```

`make docker-up` runs `docker compose up --build`, so it builds the app and leaves container logs attached in the terminal. Stop it with `Ctrl-C`, then cleanly shut containers down with:

```bash
make docker-down
```

Useful Docker commands:

```bash
make docker-logs
make docker-smoke
docker compose ps
```

## Diagnostics

Run the project health check with:

```bash
make doctor
```

The doctor script checks the local model artifact, migrations for the active
runtime (the Compose database when Docker is running), backend health,
frontend/API proxy health, common `.env` path mistakes, model loading, and
Docker model visibility. It returns a non-zero exit code for failed checks;
unavailable optional local endpoints are warnings.

`make docker-smoke` builds the Compose stack, waits for its health checks, then
runs the same diagnostic.

## Local Development

Install backend dependencies if needed:

```bash
python3 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements.txt
```

Install frontend dependencies if needed:

```bash
cd frontend
npm install
```

Start the backend:

```bash
make dev-backend
```

Start the frontend in another terminal:

```bash
make dev-frontend
```

Local dev URLs:

```text
Frontend: http://127.0.0.1:5173/
Backend:  http://127.0.0.1:8000/health
Proxy:    http://127.0.0.1:5173/api/health
```

For local backend commands, run from the repo root through the Make targets or from `backend/` directly. The Docker `.env` uses `/app/var/objectstore`, while local backend defaults expect `backend/var/objectstore`.

## Database Migrations

Application startup upgrades the configured database to the checked-in Alembic
head revision before serving requests. To inspect migration drift directly:

```bash
cd backend
.venv/bin/alembic check
```

For an explicit upgrade outside application startup:

```bash
cd backend
.venv/bin/alembic upgrade head
```

## Testing

Run both suites:

```bash
make test
```

Run one side at a time:

```bash
make test-frontend
make test-backend
```

Frontend-specific commands:

```bash
cd frontend
npm run typecheck
npm run build
npm test
```

Backend-specific commands:

```bash
cd backend
.venv/bin/python3 -m pytest -q
```

## How Recognition Works

1. The frontend uploads an image to the backend.
2. The backend stores the original image and preprocesses it for recognition.
3. Segmentation detects line regions and possible word regions in processed-image coordinates.
4. The CRNN/CTC model decodes each line crop.
5. The frontend displays the same processed image used by the backend so overlay boxes match the pixels being reviewed.
6. Users correct the line text and crop area before saving clean training examples.

If a detected line is just a dot, scanner mark, or bad crop, use `Not text`. That excludes it from export and prevents it from being saved as training data.

## Training Data Workflow

Reviewed line crops and calibration examples become `TrainingPair` records. The personalization worker can train a small per-user adapter after enough unused samples are available.

Important behavior:

- Editing text in the review box changes the reviewed/exported text.
- `Save line for training` adds the current line crop plus the corrected label to the training sample queue.
- Editing a stored sample label updates future training data.
- Deleting a stored sample removes that bad crop from future training.
- Editing or deleting a sample does not retroactively change an adapter that has already finished training.
- New adapters are accepted only when evaluation says they improve recognition.

## Project Layout

```text
backend/
  app/                 FastAPI app, services, data models, ML pipeline
  tests/               Backend unit, integration, and property tests
  var/objectstore/     Local model artifacts and stored runtime data

frontend/
  src/                 Vite/TypeScript frontend modules
  README.md            Frontend-specific setup and module notes

docs/
  AI_MODEL_AND_DOCKER.md

scripts/
  doctor.py            Runtime and wiring diagnostic script

docker-compose.yml     Backend/frontend container orchestration
Makefile               Common local, test, Docker, and diagnostic commands
```

## Configuration

Start with:

```bash
cp .env.example .env
```

Most settings have safe defaults. Common knobs:

- `FRONTEND_PORT`: host port for the nginx frontend, default `80`.
- `VITE_API_BASE_URL`: leave empty for same-origin `/api` proxying.
- `HND_OBJECT_STORE_PATH`: object-store root for images, crops, adapters, and models.
- `HND_RECOGNITION_TIMEOUT_SECONDS`: recognition request time budget.
- `HND_PERSONALIZATION_TRIGGER_THRESHOLD`: unused training-pair count needed to trigger personalization.

## More Documentation

- [AI model and Docker wiring](docs/AI_MODEL_AND_DOCKER.md)
- [Implementation history and runbook](IMPLEMENTATION_HISTORY_AND_RUNBOOK.md)
- [Frontend README](frontend/README.md)
- [Property testing notes](PROPERTY_TESTING.md)
