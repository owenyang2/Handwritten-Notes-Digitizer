# Production Compose profile

This repository's default path is still a one-command local/demo stack:

```bash
docker compose up
```

That uses `docker-compose.yml` with SQLite (`HND_DATABASE_URL=sqlite:///./var/hnd.db`) and the filesystem object store. Do not replace that file with this profile.

`docker-compose.prod.yml` is the optional production-shaped layout: PostgreSQL 16, Redis 7, MinIO (S3-compatible), the API, and the nginx frontend.

## Run the production profile

From the repository root:

```bash
docker compose -f docker-compose.prod.yml up --build
```

Then open `http://localhost/` (or `FRONTEND_PORT` if you override it). The API is bound to `127.0.0.1:8000`. MinIO is on `127.0.0.1:9000` (API) and `127.0.0.1:9001` (console).

Stop with Ctrl-C, then:

```bash
docker compose -f docker-compose.prod.yml down
```

Add `-v` only if you intend to delete Postgres, Redis, and MinIO volumes.

## Production Python extras

CI and `Dockerfile.backend` stay on `backend/requirements.txt` so the lean image does not pull Redis/S3/Postgres drivers.

The production image is `Dockerfile.backend.prod`, which also installs `backend/requirements-prod.txt`:

- `redis==5.2.1` — shared rate-limit buckets
- `boto3==1.35.76` — S3-compatible object store
- `psycopg[binary]==3.2.4` — PostgreSQL driver (`postgresql+psycopg://...`)

Local (non-Docker) use:

```bash
backend/.venv/bin/pip install -r backend/requirements.txt
backend/.venv/bin/pip install -r backend/requirements-prod.txt
```

## What the compose file sets

| Variable | Production default | Role |
| --- | --- | --- |
| `HND_DATABASE_URL` | `postgresql+psycopg://hnd:hnd@postgres:5432/hnd` | Relational store |
| `HND_REDIS_URL` | `redis://redis:6379/0` | Rate-limit `INCR`/`EXPIRE` buckets |
| `HND_OBJECT_STORE_BACKEND` | `s3` | User image/crop/upload bytes |
| `HND_S3_ENDPOINT_URL` | `http://minio:9000` | MinIO S3 API |
| `HND_S3_BUCKET` | `hnd-objects` | Created by `minio-init` |
| `HND_ENABLE_METRICS` | `true` | JSON request logs + `GET /metrics` |

Unset `HND_REDIS_URL` and the API falls back to in-process rate limits. Unset the S3 backend and it stays on the filesystem store (the local compose default).

## Volumes

- `postgres_data` — PostgreSQL cluster under `/var/lib/postgresql/data`
- `redis_data` — Redis AOF/RDB under `/data`
- `minio_data` — object bytes under `/data`
- `./backend/var/objectstore/models` — **bind-mounted read-only** so trained formula/HTR artifacts are still supplied by the operator (they are not uploaded to MinIO by this profile)

## Healthchecks

| Service | Probe |
| --- | --- |
| postgres | `pg_isready` |
| redis | `redis-cli ping` |
| minio | `mc ready local` |
| minio-init | one-shot `mc mb --ignore-existing` (must complete successfully) |
| backend | `GET /health` |
| frontend | `wget http://127.0.0.1/` |

The backend waits for Postgres, Redis, and successful MinIO bucket creation before it starts. Alembic migrations run automatically the first time the API process opens a database session (`upgrade_database` in `app.services.auth_api`). You can also run them explicitly:

```bash
docker compose -f docker-compose.prod.yml exec backend python -c "from app.data.migrations import upgrade_database; from app.config import get_settings; upgrade_database(get_settings().database_url)"
```

## MinIO bucket

`minio-init` creates `hnd-objects` (or `HND_S3_BUCKET`) once MinIO accepts S3 credentials. Re-run the one-shot job if you need to recreate the bucket:

```bash
docker compose -f docker-compose.prod.yml run --rm minio-init
```

Or from the console at `http://127.0.0.1:9001` with `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` (defaults are `minioadmin` / `minioadmin` and must be changed).

## What is still required before a real deployment

This profile is production-*shaped*, not production-complete.

- **Real model artifacts.** Recognition reports `unavailable` until verified UniMERNet/compact/CRNN weights are present under the models bind-mount. Do not treat a green `/health` liveness field as recognition quality.
- **TLS.** Terminate HTTPS in front of nginx. `HND_ENVIRONMENT=production` marks the session cookie `Secure`, so browser login over plain HTTP will not persist. Bearer tokens still work for API clients.
- **Secrets.** Replace Compose placeholder passwords and MinIO keys. Do not publish Postgres or Redis ports.
- **Backups.** Snapshot `postgres_data` and `minio_data` (and Redis if you rely on it beyond rate limits). Test restore.
- **Workers.** Recognition and training still run as in-process threads on the API. The commented `worker` service in `docker-compose.prod.yml` is a placeholder for an isolated worker; it is not wired.
- **OpenTelemetry.** `HND_OTEL_EXPORTER_OTLP_ENDPOINT` is accepted and exported as `OTEL_EXPORTER_OTLP_ENDPOINT`. This repo does not bundle an OTEL SDK or collector.
- **Rate limits / metrics.** Redis-backed limits cover auth, image upload/recognize, `/documents/import`, `/uploads`, `/pages/*/recognition`, `/sync/push`, and `/export`. `/metrics` is a Prometheus counter of method/route/status only — no note text, tokens, LaTeX, or filenames.

Keep using `docker-compose.yml` whenever you want the original SQLite local/demo defaults.
