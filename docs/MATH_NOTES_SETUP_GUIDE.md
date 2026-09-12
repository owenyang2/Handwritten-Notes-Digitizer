# Math Notes setup and operations guide

This guide answers two separate questions:

1. Is the implementation complete? The application and experiment plumbing for
   the plan is implemented and tested.
2. Is the repository self-contained for useful recognition? No. Model weights,
   licensed handwriting data, and production infrastructure are intentionally
   external to this repository.

The shortest useful path is to run the web application with Docker, then install
the verified formula model artifact if you want automatic LaTeX recognition.
Manual crop selection and LaTeX review work without a formula model.

## 1. Implementation status

| Area | Status | What you still supply or decide |
| --- | --- | --- |
| PDF/image import and page rasterization | Implemented | Nothing for local use |
| Manual formula crops, editable LaTeX, KaTeX preview | Implemented | A note/PDF to review |
| Region ordering, split/merge/exclude, immutable review revisions | Implemented | Nothing for local use |
| Markdown, HTML, LaTeX, LaTeX bundle, and controlled PDF export | Implemented | Nothing for local use |
| Owner-scoped durable recognition jobs, bounded retries, startup recovery | Implemented | A persistent database/object-store deployment |
| Native compact image-to-LaTeX model and training CLI | Implemented | Real formula images/labels and training time, or a checkpoint |
| UniMERNet-small adapter | Code path implemented; not installed here | Download the approved snapshot, complete license review, and verify its artifact digest |
| Legacy prose CRNN and personalization worker | Code path implemented; not trained here | Supply licensed IAM data, a matched manifest, a trained base model, and verified eval images |
| Formula metrics and benchmark runner | Implemented | A verified prediction file and held-out image/label split |
| Automatic page layout | Implemented as a conservative, reviewable heuristic | Human review remains required; a learned layout/VLM model is not bundled |
| Production operations | Compose/local deployment is implemented | HTTPS, secrets, backups, managed database/object storage, and deployment policy |

The plan deliberately forbids claiming recognition accuracy from the checked-in
placeholder/synthetic fixtures. Until you provide real data and a verified model
artifact, the /health endpoint should report formula recognition as unavailable
and the UI will let you enter LaTeX manually.

## 2. Prerequisites

For the application:

- Git
- Docker Desktop with the Compose plugin, or Python 3.11/3.12 and Node.js 20+
  for local development
- About 4 GB of free disk space for the CPU Docker image and dependencies, plus
  space for uploaded documents and model artifacts
- A browser

For compact-model training:

- An NVIDIA machine with a compatible CUDA/PyTorch installation is recommended;
  an RTX 3060 is the documented pilot target.
- CPU training is supported but slow. The web Docker image is CPU-only; it does
  not use the host GPU.
- A real, licensed formula dataset. MathWriting is the primary planned source;
  HME100K and CROHME are complementary sources subject to their own terms.

For legacy prose recognition and personalization:

- A licensed IAM Handwriting Database download, including line images and
  transcriptions
- A separately verified, held-out general-English image/label set for promotion
  guardrails

Never commit private notes, licensed pixels, credentials, model weights, or
generated checkpoints to Git.

## 3. Recommended Docker setup

### 3.1 Prepare the checkout

From the repository root:

~~~bash
git clone <repository-url>
cd Handwritten-Notes-Digitizer
cp .env.example .env
~~~

The defaults are suitable for a local single-host run. If port 80 is already in
use, edit .env and choose another host port, for example:

~~~dotenv
FRONTEND_PORT=8080
~~~

In that case use http://localhost:8080 below. Keep the Docker object-store path
as /app/var/objectstore; that path is inside the container.

### 3.2 Start the stack

For an attached development run:

~~~bash
make docker-up
~~~

This builds both images and keeps logs attached. Open:

~~~text
http://localhost/
~~~

For a detached run:

~~~bash
docker compose up -d --build --wait --wait-timeout 120
docker compose ps
curl http://localhost/api/health
~~~

If you chose FRONTEND_PORT=8080, use localhost:8080. The backend is also bound
to 127.0.0.1:8000 by default:

~~~bash
curl http://127.0.0.1:8000/health
~~~

The health endpoint can return HTTP 200 while reporting unavailable recognition.
That means the web service is alive; it does not mean a model is installed.

Useful commands:

~~~bash
make docker-logs
docker compose ps
docker compose logs --tail=200 backend
docker compose logs --tail=200 frontend
~~~

Stop the stack without deleting data:

~~~bash
make docker-down
~~~

Do not use docker compose down -v unless you intentionally want to delete the
Compose database and object-store volumes. That removes uploaded documents,
regions, review history, and local personalization artifacts.

### 3.3 Use the application

1. Register an account in the login screen.
2. Upload a multipage PDF, PNG, or JPEG.
3. For a PDF, open the imported document and select a complete formula crop.
   Auto-detect page regions is a deterministic starting point, not a final
   layout decision.
4. Select a region to see the original crop, editable LaTeX, and KaTeX preview.
5. Run recognition when a formula model is installed. Otherwise enter LaTeX
   manually; an unavailable model is not replaced with random output.
6. Save the review. Check the training-consent box only if this crop may enter
   your personal formula dataset.
7. Reopen the document from History and export the active revision.

Supported export formats are Markdown, HTML, LaTeX, LaTeX bundle (a zip with
document.tex and retained figure assets), and controlled PDF.

Default import limits are 10 MiB per file, 50 PDF pages, and 40 million decoded
pixels per page. Password-protected PDFs are rejected. Adjust these limits in
.env only after considering memory and denial-of-service risk:

~~~dotenv
HND_MAX_UPLOAD_BYTES=10485760
HND_PDF_RASTER_DPI=200
HND_PDF_MAX_PAGES=50
HND_PDF_MAX_PIXELS=40000000
~~~

## 4. Local development without Docker

Use this path when changing code or running the frontend against a local API.

### 4.1 Install the backend

From the repository root:

~~~bash
python3 -m venv backend/.venv
backend/.venv/bin/python -m pip install --upgrade pip
backend/.venv/bin/pip install -r backend/requirements.txt
~~~

The backend requirements install CPU PyTorch wheels. For local backend
development, leave backend/.env absent to use the documented defaults, or
create it with only local overrides:

~~~dotenv
HND_ENVIRONMENT=development
HND_DATABASE_URL=sqlite:///./hnd.db
HND_OBJECT_STORE_PATH=./var/objectstore
HND_FORMULA_ENGINE=auto
~~~

The backend reads backend/.env, not the repository-root .env. The root .env is
for Docker Compose and contains container paths.

### 4.2 Install the frontend

~~~bash
cd frontend
npm ci
cd ..
~~~

Start the two processes in separate terminals:

~~~bash
make dev-backend
~~~

~~~bash
make dev-frontend
~~~

Open http://127.0.0.1:5173/. The Vite development server proxies /api to
127.0.0.1:8000. Check the API directly with:

~~~bash
curl http://127.0.0.1:8000/health
~~~

### 4.3 Run migrations and tests

Application startup upgrades the configured database. To run the migration
checks explicitly:

~~~bash
cd backend
.venv/bin/alembic upgrade head
.venv/bin/alembic check
cd ..
~~~

Run the complete test/validation set:

~~~bash
make test
cd frontend && npm run typecheck && npm run build && cd ..
PYTHONPATH=backend backend/.venv/bin/python -m py_compile $(find backend/app training benchmarks scripts -name '*.py' -print)
backend/.venv/bin/python scripts/doctor.py
~~~

make docker-smoke additionally waits for Compose health checks, but it uses the
local backend/.venv to run scripts/doctor.py; install the local backend
environment first if you use that target.

## 5. Install automatic formula recognition

The setting is:

~~~dotenv
HND_FORMULA_ENGINE=auto
~~~

auto tries UniMERNet-small first and the compact checkpoint second. Use
unimernet or compact when you want an explicit backend and no fallback.

### 5.1 UniMERNet-small (fastest route to a formula model)

The downloader resolves a branch/tag to an immutable Hub commit, downloads the
snapshot, and writes model_artifact.json with a tree digest. Run it from the
repository root:

~~~bash
(cd backend && .venv/bin/python ../scripts/download_unimernet.py --destination var/objectstore/models/unimernet_small --revision main)
~~~

This requires network access and the upstream model's terms must be reviewed
before use. For reproducibility, record the resolved commit and printed digest;
for a release, pass a reviewed immutable commit instead of a moving branch.

Do not rename this directory to base_model; the legacy prose model uses a
different artifact contract.

For local development, restart the backend process after installation. For
Docker, the host directory backend/var/objectstore/models is mounted into the
backend container read-only, so install the snapshot there and restart the
backend:

~~~bash
docker compose restart backend
curl http://localhost/api/health
~~~

Look for a loaded formula_model in the health response. If it is unavailable,
run make doctor and inspect the model-loading reason.

### 5.2 Native compact model

Use this route when you need a fully native PyTorch training path or want to
train on a permitted formula dataset.

Each JSONL record needs a stable ID, an image or InkML source, a normalized
LaTeX label, and an explicit split. A minimal shape is:

~~~json
{"sample_id":"mw-0001","image":"crops/mw-0001.png","rawLabel":"x^2","normalizedLabel":"x^2","split":"train","source_document_id":"page-group-1","page_id":"page-1"}
~~~

The loader rejects unsupported labels, missing sources, duplicate IDs, and a
source document that crosses train/dev/test splits. It rasterizes InkML
deterministically and includes source bytes and split metadata in the dataset
digest. The training command refuses to train by falling back to dev/test data.

Put the manifest and images outside Git, then run a CPU smoke/pilot from the
repository root:

~~~bash
PYTHONPATH=backend backend/.venv/bin/python -m training.cli --manifest data/mathwriting/manifest.jsonl --output backend/var/objectstore/models/compact_math/checkpoint.pt --epochs 1 --batch-size 2 --device cpu
~~~

For a CUDA training environment, keep it separate from the CPU web runtime.
Install the application dependencies needed by the training modules, then
replace the CPU PyTorch packages with the CUDA-matched versions. For example:

~~~bash
python3 -m venv training/.venv
training/.venv/bin/pip install -r backend/requirements.txt
training/.venv/bin/pip uninstall -y torch torchvision torchaudio
training/.venv/bin/pip install -r training/requirements-cuda.txt --index-url https://download.pytorch.org/whl/cu124
~~~

Verify CUDA before starting a long run:

~~~bash
training/.venv/bin/python -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU')"
~~~

Train with dynamic-width batches, AMP, gradient accumulation, and a resumable
checkpoint:

~~~bash
PYTHONPATH=backend training/.venv/bin/python -m training.cli --manifest data/mathwriting/manifest.jsonl --output backend/var/objectstore/models/compact_math/checkpoint.pt --epochs 16 --batch-size 8 --accumulate 4 --workers 0 --device cuda --amp --patience 5
~~~

If interrupted, resume from the checkpoint:

~~~bash
PYTHONPATH=backend training/.venv/bin/python -m training.cli --manifest data/mathwriting/manifest.jsonl --output backend/var/objectstore/models/compact_math/checkpoint.pt --resume backend/var/objectstore/models/compact_math/checkpoint.pt --epochs 16 --device cuda --amp
~~~

The checkpoint records the tokenizer/configuration, optimizer, scheduler, AMP
scaler, RNG state, epoch/data position, manifest path, and dataset digest.
After copying a completed checkpoint into the Docker-mounted model directory,
restart the backend and check /health.

Profile a completed checkpoint separately from accuracy:

~~~bash
PYTHONPATH=backend training/.venv/bin/python training/profile.py --checkpoint backend/var/objectstore/models/compact_math/checkpoint.pt --device cuda --steps 20
~~~

## 6. Legacy prose model and personalization

These steps are not required for formula-only Math Notes review. They are
needed for the original line-level English recognizer and its per-user adapter
workflow.

### 6.1 Train the shared prose model on licensed IAM data

Obtain IAM under its terms and extract it outside version control:

~~~text
backend/datasets/
├── ascii/       # forms.txt and lines.txt
└── lines/       # IAM line PNG hierarchy
~~~

Build a writer-disjoint manifest:

~~~bash
cd backend
PYTHONPATH=.. .venv/bin/python -m app.ml.build_real_iam_manifest --ascii-dir datasets/ascii --images-dir datasets/lines --output datasets/iam_real_manifest.json --max-resized-width 800
~~~

Run the real training driver. It is CPU-bound and may take a long time over
the full dataset:

~~~bash
HND_IAM_DATASET_ROOT="$PWD/datasets/lines" PYTHONPATH=.. .venv/bin/python -m app.ml.run_real_training --manifest datasets/iam_real_manifest.json --max-epochs 12 --checkpoint-every 2 --output-dir var/objectstore/models/base_model
~~~

The final artifact is base_model.pt plus base_model.meta.json. Restart the
backend after training. The checked-in IAM manifest and synthetic fallback are
only plumbing fixtures; they do not produce a useful recognizer.

### 6.2 Personalization requirements

Personalization examples are created only from explicit user consent on saved
reviews. A real deployment also needs:

- a trained/frozen base prose artifact;
- enough consented examples to cross HND_PERSONALIZATION_TRIGGER_THRESHOLD;
- a held-out personal split;
- a verified general-English image/label fixture set for the catastrophic-
  forgetting guardrail.

Leave this setting false in production:

~~~dotenv
HND_ALLOW_SYNTHETIC_EVAL=false
~~~

The checked-in general evaluation manifest contains references but deliberately
does not contain licensed handwriting pixels. Do not pair arbitrary IAM images
with those references. Supply a separately verified fixture manifest with image
hashes and keep the test images out of training. Until that exists, adapter
promotion must remain unavailable rather than being justified by synthetic
noise.

The API surface includes status, training-example review, and a durable run
request at POST /personalization/runs; compact formula training remains the
offline training.cli workflow described above.

## 7. Evaluation and release evidence

For formula recognition, prepare a verified prediction JSONL file with at least
reference and prediction per row. Prefer also recording sample_id, page_id,
region_id, predicted_region_id, truncated, latency_ms, and peak_memory_bytes.

Run the benchmark report:

~~~bash
PYTHONPATH=backend backend/.venv/bin/python -m benchmarks.runner --predictions results/formula-test.predictions.jsonl --output results/formula-test.report.json --version 1.0.0 --split test
~~~

Use a development split for model selection and a separate final test split for
release claims. For personal notes, bootstrap by page rather than by crop so a
page with many regions cannot dominate the confidence interval. The report
includes normalized exact match, LaTeX token error rate, renderability,
critical-structure errors, truncation, page-level layout/order, and
end-to-end summaries.

Do not call the tiny compact smoke run or the synthetic fixtures an accuracy
result. They only prove that the pipeline can load data, train, checkpoint, and
run inference.

## 8. Production checklist

Before exposing the service beyond a private development machine:

- Install and verify a real model artifact; record its immutable revision, hash,
  license, processor configuration, and benchmark version.
- Use licensed datasets and keep train/dev/test or whole-document splits
  isolated. Store the dataset version and digest with every experiment.
- Keep HND_ALLOW_SYNTHETIC_EVAL=false.
- Set explicit upload/page/raster limits appropriate for your hardware.
- Replace the default SQLite deployment with a backed-up managed PostgreSQL
  database for multi-user or multi-process use.
- Move high-volume object storage to a backed-up, access-controlled store if
  the single-host filesystem is insufficient.
- Put the frontend/API behind HTTPS and an authenticated network boundary.
- Plan backups for both the database and object-store files; review restore
  procedures before accepting private notes.
- Separate CUDA training workers from the CPU-only web container and promote
  only artifacts with verified evaluation evidence.
- Monitor container health, job failures/timeouts, disk growth, and model
  latency/memory.

These operational controls are deployment work around the implemented code;
they are not supplied as a managed cloud service by this repository.

## 9. Troubleshooting

### Health is 200 but formula recognition is unavailable

This is expected without a model. Check:

~~~bash
make doctor
curl http://localhost/api/health
~~~

Install UniMERNet or place the compact checkpoint at the configured model path,
then restart the backend. A corrupt or config-only artifact is rejected.

### Docker cannot bind port 80

Set FRONTEND_PORT=8080 in the root .env, recreate the frontend, and use
http://localhost:8080.

### Local runs cannot find a model installed for Docker

Docker uses /app/var/objectstore and mounts host
backend/var/objectstore/models. Local backend defaults use
backend/var/objectstore when started through the Make target. Put artifacts
under the host path and do not set a local HND_OBJECT_STORE_PATH to the
container path /app/var/objectstore.

### A training manifest is rejected

Check that each row has normalizedLabel (or normalized_label), an image or
InkML source relative to the manifest, a supported LaTeX vocabulary, and a
split. Every source_document_id must belong to exactly one split, and at least
one row must be in train.

### CUDA reports false

The web container is CPU-only. Verify the separate training environment, the
NVIDIA driver, the CUDA-matched PyTorch wheel, and that the process is running
on the GPU host. Use --device cpu for a correctness smoke test.

### Compose data disappeared

Check whether docker compose down -v was run. That removes named volumes.
Normal docker compose down preserves the database and object store.

## Related files

- [Implementation plan](MATH_NOTES_IMPLEMENTATION_PLAN.md)
- [Existing runbook](MATH_NOTES_RUNBOOK.md)
- [Data card](MATH_NOTES_DATA_CARD.md)
- [Model card](MATH_NOTES_MODEL_CARD.md)
- [Benchmark guide](../benchmarks/README.md)
- [Legacy prose training procedure](../backend/app/ml/BASE_MODEL_TRAINING.md)
- [General evaluation set](../backend/app/ml/GENERAL_EVAL_SET.md)
