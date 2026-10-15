# Production Compose profile

The optional profile runs PostgreSQL 16, Redis 7, SeaweedFS 4.48, the FastAPI
backend and the nginx frontend. The default local/demo profile remains SQLite
and filesystem storage. Recognition models are supplied separately.

## Start an isolated deployment

Copy `.env.production.example` to `.env.production` and fill every secret with
an independent value. `openssl rand -hex 32` generates a suitable value. Keep
the encryption key with your backups: changing it makes existing encrypted
objects unreadable. Environment files containing secrets are ignored by Git.

```bash
cp .env.production.example .env.production
# Fill all required values before starting.
docker compose --env-file .env.production -f docker-compose.prod.yml up -d --build --wait
```

The website uses `FRONTEND_PORT` (8080 in the example). The direct API is bound
to `127.0.0.1:8000`. PostgreSQL, Redis and SeaweedFS have no published
ports; S3 is reachable only through the internal storage network.
Terminate HTTPS before exposing the website: production session cookies are
Secure. No public deployment is performed by the verification workflow.

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml down
```

This retains data volumes. Only use `down -v` for a disposable deployment whose
data you intend to delete.

## Storage startup and permissions

`storage-config` writes two identities into a private configuration volume:
an administrator and the backend identity, which can Read/Write/List only
`HND_S3_BUCKET`. Equal admin/backend access keys and known default credentials
are rejected. The API never receives the storage admin credentials or the
encryption key.

SeaweedFS creates the named bucket on startup. `storage-init` enables default
AES256 encryption and verifies an encrypted object can be written, inspected,
read and deleted. The backend waits for successful initialization, PostgreSQL
and Redis health. Encryption initialization fails the deployment if it cannot
verify encrypted storage. The existing S3 adapter and stored object references
remain unchanged.

The storage service and initializer use a separate internal Docker network;
only the backend joins both the storage network and the application network.
WebDAV, the Admin UI, Iceberg and Lance are disabled. This is a single-node
profile, with no storage replication.

| Required variable | Purpose |
| --- | --- |
| `HND_POSTGRES_PASSWORD` | PostgreSQL password; the role/database names default to `hnd` |
| `HND_S3_ADMIN_ACCESS_KEY`, `HND_S3_ADMIN_SECRET_KEY` | Storage administrator used for setup |
| `HND_S3_ACCESS_KEY`, `HND_S3_SECRET_KEY` | Separate bucket-scoped backend credentials |
| `HND_S3_ENCRYPTION_KEY` | Independent secret of at least 32 characters; retain with backups |

| Optional setting | Default |
| --- | --- |
| `HND_ENVIRONMENT` | `production` |
| `HND_REDIS_URL` | `redis://redis:6379/0` |
| `HND_OBJECT_STORE_BACKEND` | `s3` |
| `HND_S3_ENDPOINT_URL` | `http://seaweedfs:8333` |
| `HND_S3_REGION` | `us-east-1` |
| `HND_S3_BUCKET` | `hnd-objects` |
| `HND_S3_PREFIX` | Empty |
| `HND_ENABLE_METRICS` | `true` |

Volumes are `postgres_data`, `redis_data`, `seaweed_data` (objects, metadata
and local storage state), and `seaweed_config` (credentials). Model artifacts
remain a read-only host mount at `backend/var/objectstore/models`.

The API applies Alembic migrations at startup. Install
`backend/requirements-prod.txt` in addition to the base requirements when
running the production integrations outside Docker.

## Existing MinIO data

MinIO's community server and client repositories are archived. The profile
now uses SeaweedFS rather than relying on the retired upstream images.
[Upstream status](https://github.com/minio/minio).

SeaweedFS does not read MinIO's disk format. Existing `minio_data` volumes
are not reused or deleted. Before replacing an existing deployment, back it
up, copy the bucket through the S3 API while retaining every object key,
verify object counts and digests, then change the endpoint. Keep the old
volume until reads and exports have been verified. No existing data migration
was performed in this pass.

## Authentication and diagnostics

Production startup rejects known default database/S3 credentials and exposed
password-reset tokens. Password-reset routes return 404 in production until
a mail sender exists. Development/test may explicitly enable
`HND_EXPOSE_RESET_TOKENS`; production refuses that flag.

Public `/api/health` reports liveness and capability booleans. nginx blocks
`/api/health/details`, `/api/metrics`, `/api/docs`, `/api/redoc`, and
`/api/openapi.json`. Operators can query detailed health and metrics directly
on the private backend interface. When configured Redis is unavailable,
authentication rate limiting returns 503 rather than bypassing the quota.

## Deployment acceptance

See the dated verification report and [feature matrix](../STATUS.md)
for actual test results. A local stack smoke test does not establish internet
or hardware readiness. Still required: TLS configuration, database least
privilege, backup/restore drills, representative model quality and Linux model
runtime checks, workload capacity, physical-device acceptance, and the remaining
items in the [deployment checklist](../STATUS.md#next-acceptance-work).
