# Handwriting Notes Digitizer

A local web app for importing handwritten PDFs or images, reviewing formula crops as editable LaTeX, and exporting a versioned document containing prose, mathematics, and retained figures.

The project is built around a FastAPI backend, a Vite/TypeScript frontend, durable recognition jobs, and pluggable formula engines. The original CRNN/CTC prose recognizer remains available for legacy line workflows; formula recognition uses a real, artifact-backed UniMERNet-small adapter or the native compact PyTorch image-to-LaTeX model. No missing or untrained model is presented as a successful recognition capability.

## What It Does

- Import multipage PDF, PNG, or JPEG notes while retaining the original asset and a normalized page raster.
- Select complete formula crops manually, or run deterministic page-layout detection as a reviewable starting point.
- Run recognition through a durable, owner-scoped job and keep raw output, alternatives, truncation, score, and model provenance.
- Review the original crop beside editable LaTeX and a rendered preview; corrections are immutable, version-checked revisions.
- Reorder, split, merge, exclude, and mark unresolved regions without losing the source crop.
- Export the active reviewed revision as Markdown, LaTeX, HTML, or a controlled PDF.
- Explicitly opt reviewed crops into the personal training dataset; no review is silently used for training.

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
PYTHONPATH=.. .venv/bin/python3 -m pytest -q
```

## How Recognition Works

1. The backend stores the unmodified upload and creates ordered page records. PDF pages are rasterized; photos are orientation-normalized and transparency is flattened while the original is retained.
2. A reviewer selects a complete expression, or starts with the bounded page-layout heuristic. Coordinates identify the page-raster version used for the crop.
3. A durable worker sends each crop to the configured formula engine. A missing, corrupt, incompatible, or truncated model result stays visibly unavailable or unresolved.
4. The review UI keeps the original crop, editable LaTeX, rendered preview, raw prediction, and alternatives together.
5. Saving creates an immutable review revision. Exports assemble the same active revision, so edits and exclusions cannot disappear between preview and download.

The compact model is trained with `training/cli.py`; UniMERNet-small is downloaded separately with `scripts/download_unimernet.py`. The repository intentionally contains no licensed handwriting corpus or pretrained weights, so health reports formula recognition as unavailable until a verified artifact is installed.

## Training Data Workflow

Reviewed crops become `TrainingExample` records only when the reviewer explicitly gives consent. The existing prose personalization worker can train a per-user adapter after enough eligible examples are collected; compact formula training is an offline, manifest-driven run through `training/cli.py`.

Important behavior:

- Editing LaTeX changes only the next saved review revision; a version conflict is reported instead of overwriting another edit.
- A crop enters the formula dataset only after explicit consent, with its crop hash, exact review revision, source page, and split/provenance recorded.
- Dataset loading rejects missing images, unsupported labels, duplicate IDs, and source documents crossing train/dev/test splits.
- The training checkpoint includes model/tokenizer configuration, optimizer/scheduler/scaler, RNG state, epoch and data position, manifest path, and dataset digest.
- Adapter/model promotion remains evidence-gated; unavailable evaluation data never becomes a synthetic accuracy claim.

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
  MATH_NOTES_IMPLEMENTATION_PLAN.md
  MATH_NOTES_MODEL_CARD.md
  MATH_NOTES_DATA_CARD.md
  MATH_NOTES_RUNBOOK.md

training/              Native PyTorch formula dataset, model training, and profiling
benchmarks/            Verified-manifest metrics and report runner
notebooks/             Thin Colab/Kaggle launchers

scripts/
  doctor.py            Runtime and wiring diagnostic script

docker-compose.yml     Backend/frontend container orchestration (SQLite local/demo)
docker-compose.prod.yml  Optional Postgres/Redis/MinIO profile (see docs/PRODUCTION_PROFILE.md)
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
- `HND_RECOGNITION_TIMEOUT_SECONDS`: recognition job deadline.
- `HND_PERSONALIZATION_TRIGGER_THRESHOLD`: unused training-pair count needed to trigger personalization.
- `HND_FORMULA_ENGINE`: `unimernet`, `compact`, or `auto` formula backend selection.

## More Documentation

- [AI model and Docker wiring](docs/AI_MODEL_AND_DOCKER.md)
- [Production Compose profile](docs/PRODUCTION_PROFILE.md)
- [Math-notes implementation plan](docs/MATH_NOTES_IMPLEMENTATION_PLAN.md)
- [Personalized notes platform implementation plan](docs/PERSONALIZED_NOTES_PLATFORM_IMPLEMENTATION_PLAN.md)
- [Math-notes setup and operations guide](docs/MATH_NOTES_SETUP_GUIDE.md)
- [Math-notes model card](docs/MATH_NOTES_MODEL_CARD.md)
- [Math-notes data card](docs/MATH_NOTES_DATA_CARD.md)
- [Math-notes runbook](docs/MATH_NOTES_RUNBOOK.md)
- [Benchmark runner](benchmarks/README.md)
- [Implementation history and runbook](IMPLEMENTATION_HISTORY_AND_RUNBOOK.md)
- [Frontend README](frontend/README.md)
- [Property testing notes](PROPERTY_TESTING.md)
