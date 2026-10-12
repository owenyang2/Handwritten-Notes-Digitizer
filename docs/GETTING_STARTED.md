# Getting started

Handwriting Notes Digitizer has a web/API stack and an optional native iPad
client. Start with the [demo](../demos/README.md) to inspect the product without
installing dependencies.

## Choose a setup

| Goal | Requirements and expected result |
| --- | --- |
| Run the web/API stack | Docker with Compose; SQLite and filesystem storage; recognition unavailable until models are installed |
| Reproduce the recorded recognition | macOS arm64, Python 3.12, Node 20+/npm 10+, separate model downloads/runtime; follow the linked model guide |
| Run the iPad simulator | macOS, Xcode, XcodeGen, backend; local ad-hoc signing |
| Install on a physical iPad | Connected iPad, Apple signing/team setup, reachable backend; hardware acceptance remains |

## Docker: web/API stack

From the repository root:

```bash
cp .env.example .env
HND_ENVIRONMENT=development FRONTEND_PORT=8080 make docker-up
```

Open <http://localhost:8080/>. The frontend serves the website and forwards
`/api` to FastAPI. For example, <http://localhost:8080/api/health> reports
component and model availability. `make docker-up` builds the images and keeps
logs attached. Stop with `Ctrl-C`, then use:

```bash
make docker-down
```

The environment/port overrides above are for a local development machine.
The example configuration defaults to production cookies, which require HTTPS
for browser login; use production settings only with an HTTPS deployment.
Change `FRONTEND_PORT` if 8080 is occupied. Volumes retain runtime data unless
explicitly removed.

### Recognition requires additional setup

A clean clone contains **no weights, handwriting corpus, or trained adapters**.
Model absence should appear as unavailable; a healthy HTTP endpoint alone does
not establish that recognition is ready.

The recorded demo used UniMERNet-small and TrOCR in a separate macOS arm64
Python 3.12 environment. Follow [recognition and iPad setup](MODEL_AND_IPAD_SETUP.md)
for pinned snapshots, runtime installation, and model-health checks. The Linux
Docker image does not install that optional runtime, and a macOS virtual
environment cannot be reused inside a Linux container. Linux inference still
needs a compatible runtime and verification.

## Local development

Use Python **3.11 or 3.12** for the API and Node **20+** with npm **10+** for the
website. The optional recorded model environment specifically uses Python 3.12.
From the repository root:

```bash
python3.12 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements.txt
cd frontend
npm ci
cd ..
```

Use `python3.11` instead for API-only development if needed. Start each service
in its own terminal, from the repository root:

```bash
make dev-backend
```

```bash
make dev-frontend
```

Open <http://127.0.0.1:5173/>. Backend health is at
<http://127.0.0.1:8000/health>; the web proxy is at
<http://127.0.0.1:5173/api/health>.

The root `.env` configures Docker Compose. The API reads `backend/.env` for
local settings, and otherwise uses its local defaults. Keep Docker's object
store path `/app/var/objectstore` out of local API configuration; the default
local path is `backend/var/objectstore` when started through the Make target.
Use ignored local configuration for credentials or private service addresses.

## Native iPad client

The current Xcode target/app name is `PersonalizedNotes`. See the
[iPad guide](../ios/README.md) for module layout and test boundaries, then the
[simulator setup](MODEL_AND_IPAD_SETUP.md#simulator-setup) or
[physical-device setup](MODEL_AND_IPAD_SETUP.md#your-physical-ipad).
The simulator's default backend is `http://127.0.0.1:8000`. A physical device
needs a reachable host address and correctly configured signing/App Groups.

## Configuration and diagnostics

| Setting | Purpose |
| --- | --- |
| `HND_ENVIRONMENT` | Local development versus production cookie/observability behavior |
| `FRONTEND_PORT` | Docker's website host port |
| `HND_OBJECT_STORE_PATH` | Runtime originals, crops, ink, and model directory |
| `HND_FORMULA_ENGINE` | `auto`, `unimernet`, or `compact` |
| `HND_RECOGNITION_TIMEOUT_SECONDS` | Recognition inference deadline |
| `VITE_API_BASE_URL` | Keep empty for the same-origin `/api` proxy |

After installing the API dependencies, run:

```bash
make doctor
```

The diagnostic checks model artifacts, active database migrations, backend and
proxy health, and Docker/local path mismatches. Failed required checks produce
a nonzero exit status; unavailable optional endpoints are warnings. Missing
models are expected on a clean checkout and must be installed before passing
recognition checks.

`make docker-smoke` builds/starts Compose, waits for health checks, then calls
the host's `backend/.venv/bin/python` diagnostic. It therefore also needs the
local API environment; it is not a Docker-only first-install command.

## Migrations and tests

API startup applies the checked-in Alembic migration chain. For a local check,
run from `backend/`:

```bash
.venv/bin/alembic upgrade head
.venv/bin/alembic check
```

See the [testing guide](../PROPERTY_TESTING.md) for test and contract commands.
Use the [feature matrix](FEATURE_STATUS_MATRIX.md) to distinguish local checks
from deployment acceptance. The [optional production profile](PRODUCTION_PROFILE.md)
still has service-integration and release gaps.
