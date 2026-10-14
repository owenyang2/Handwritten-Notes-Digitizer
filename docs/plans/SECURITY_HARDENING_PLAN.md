# Security Hardening Plan

Review date: 2026-10-14. Scope: `backend/app`, `frontend/`, `ios/`, `nginx.conf`,
Dockerfiles, compose files, CI, git history, and pinned dependencies.

## Summary

The core access-control design is sound. Every data route depends on
`get_current_user`; the only unauthenticated routes are `/health`, `/auth/*`,
and the password-reset pair. Repositories take `user_id` as a mandatory first
argument, cross-user lookups return 404, and no IDOR was found across
documents, pages, regions, jobs, uploads, runs, assets, or exports. There is no
raw SQL, no outbound fetch of user-supplied URLs, no client-supplied `user_id`
or role, and object-store paths are segment-validated and resolve-contained.
Sessions are opaque random tokens stored as SHA-256 hashes, cookies are
`HttpOnly` + `SameSite=Lax` (+ `Secure` in production), passwords use salted
PBKDF2, and no secrets were found in tracked files or git history.

The real gaps are in password reset, rate limiting, account deletion, default
infrastructure credentials, public operational endpoints, and dependency CVEs.

## P0 — Fix before any internet-facing deployment

### 1. Password reset can hand out account-takeover tokens
`backend/app/services/consent_api.py:136-158`

- `debug_token` is returned whenever `environment != "production"`, and
  `Settings.environment` defaults to `"development"`
  (`backend/app/config/settings.py:50`). A deployment that forgets or
  misspells `HND_ENVIRONMENT` lets anyone reset any account knowing only its
  email. The non-empty `debug_token` also makes the response reveal which
  emails exist.
- Confirming a reset does not revoke existing sessions or older unused reset
  tokens.
- `/auth/password-reset/*` has no rate limit.
- Production has no mail sender, so reset does not actually work there.

Plan:
- Remove `debug_token` from the HTTP response entirely. Tests read the token
  from the database, or through an explicit `HND_EXPOSE_RESET_TOKENS=true`
  test-only flag that startup refuses when `environment == "production"`.
- On confirm: revoke all `UserSession` rows for the user and mark every other
  outstanding `PasswordResetToken` for that user as used.
- Add rate-limit rules for both reset routes (per IP and per target email).
- Either wire a real mail sender or hide/disable reset in production until one
  exists, and document that.

### 2. Login/register rate limit is trivially bypassable
`backend/app/middleware/rate_limiter.py:152-162`

`_client_identity` uses the hash of *any* presented bearer token or session
cookie as the bucket key, without validating it. Sending a random
`Authorization: Bearer <random>` header with each request gives every login
attempt a fresh bucket, so brute force is unlimited.

Plan:
- For `/auth/login`, `/auth/register`, and `/auth/password-reset/*`, always
  key on client IP, plus a second bucket keyed on the normalized
  username/email in the request body.
- Add progressive per-account lockout or backoff after repeated failures.
- Test: N logins with distinct fake bearer tokens from one IP get 429.

### 3. Account deletion is incomplete and reversible
`backend/app/services/account_api.py:220-340`

`DELETE /account` only soft-deletes documents (`deleted_at`), deletes image
rows and upload objects, and revokes sessions. The `User` row and password
remain, so the user can log back in and `POST /documents/{id}/restore` the
"deleted" documents. Page rasters, PDF originals, region crops, ink bundles,
assets, training pairs, adapters, jobs/runs, sync history, consent and
vocabulary rows all stay behind.

Plan:
- Disable the account immediately (set `deleted_at`/`disabled_at` on `User`,
  reject it in `get_current_user` and `AuthService.authenticate`).
- Run a background purge job that hard-deletes every user-owned row and object
  (walk every model with a `user_id` column; delete the `users/<user_id>/`
  object prefixes), recording progress on the existing `DeletionJob`.
- Add a test that creates one of every user-owned object type, deletes the
  account, and asserts nothing with that `user_id` remains in the database or
  object store, and that login fails.

### 4. Production compose falls back to default credentials
`docker-compose.prod.yml:37,66-67,119,127-128`

Postgres defaults to `hnd`/`hnd` and MinIO to `minioadmin`/`minioadmin`. If the
operator forgets to set them, the stack starts with well-known credentials.

Plan:
- Use `${VAR:?must be set}` for `HND_POSTGRES_PASSWORD`, `MINIO_ROOT_USER`,
  `MINIO_ROOT_PASSWORD`, `HND_S3_ACCESS_KEY`, `HND_S3_SECRET_KEY`, and the
  database URL so compose refuses to start without them.
- Add a startup check: when `environment == "production"`, refuse to boot if
  any credential equals a known default (`hnd`, `minioadmin`, empty).
- Give the backend a dedicated MinIO user with a policy limited to its bucket,
  instead of the root key.

### 5. Fail closed on environment
`backend/app/config/settings.py:50`

Change `environment` to `Literal["development", "test", "production"]` so a
typo fails validation, and have the production compose profile and the prod
Dockerfile set it explicitly. Audit every `environment != "production"` branch
(the reset token, cookie `Secure`, metrics) so the unsafe behavior needs an
explicit opt-in rather than being the default.

## P1 — High-value hardening

### 6. Operational endpoints are public through nginx
nginx proxies all of `/api/` to the backend, so these are reachable from the
internet:
- `/api/docs`, `/api/redoc`, `/api/openapi.json` (FastAPI defaults).
- `/api/metrics` (enabled by default in the prod profile).
- `/api/health`, which returns model artifact filesystem paths and raw
  exception text (`backend/app/main.py:117-147`,
  `backend/app/ml/recognition.py:226-310`).

Plan: set `docs_url=None, redoc_url=None, openapi_url=None` in production; add
`location = /api/metrics { deny all; }` (scrape from the internal network
only); reduce public `/health` to `status` + capability booleans, and move the
detailed model report to an internal-only route.

### 7. Security headers are dropped on static assets; no HSTS
`nginx.conf:52-56`

nginx does not inherit `add_header` into a `location` block that declares its
own `add_header`. The static-asset location adds `Cache-Control`, so JS, CSS,
SVG, and images ship without CSP, `nosniff`, frame, or referrer headers.

Plan: move the security headers into an `include` snippet used at server level
and in every location that adds headers. Add
`Strict-Transport-Security: max-age=31536000; includeSubDomains` at the TLS
terminator. Consider adding `Cross-Origin-Opener-Policy: same-origin`.

### 8. Dependency vulnerabilities
`pip-audit` against `backend/requirements*.txt` (npm audit is clean):

| Package | Pinned | Advisories | Fixed in |
|---|---|---|---|
| starlette | 0.41.3 | 7 | 1.3.1 (requires FastAPI upgrade) |
| torch | 2.5.1 | 19 | 2.9.1 |
| transformers | 4.47.1 | 26 | 5.5.0 |
| pillow | 11.0.0 | 17 | 12.3.0 |
| onnx | 1.17.0 | 9 | 1.22.0 |
| pytest | 8.3.4 | 1 | 9.0.3 (dev only) |

Plan: upgrade FastAPI + Starlette + AnyIO together first (they are on the
upload/request path), then Pillow (decodes untrusted images), then
torch/transformers/onnx with the model reproducibility tests as the gate. Add
`pip-audit` and `npm audit --omit=dev` jobs to `.github/workflows/ci.yml`, plus
Dependabot or Renovate.

Related: `backend/app/ml/backends/compact.py:142` uses
`torch.load(..., weights_only=False)`. The artifact is operator-supplied, not
user-supplied, but it is arbitrary code execution if a checkpoint is swapped.
Switch to `weights_only=True` with an allow-listed safe-globals set, or verify
a pinned SHA-256 before loading.

### 9. Input bounds and upload validation
- `LoginRequest` / `RegisterRequest` (`auth_api.py:177-197`) have no length
  limits. A multi-megabyte password makes every PBKDF2 call expensive. Add
  `max_length` (username 64, email 320, password 128) and a minimum password
  length of 8 on register, matching reset.
- Validate email format on register (`pydantic.EmailStr`) and normalize case.
  Login and reset already try a lowercase lookup, but register stores the raw
  form.
- `ReorderRegionsRequest.region_ids` (`math_regions_api.py:130`) and
  `ResolveConflictRequest.details` (`sync_api.py:140-144`) are unbounded. Add
  `max_length` / a serialized-size cap.
- Resumable `purpose=asset` completion (`uploads_api.py:509-523`) stores bytes
  as PDF/JPEG/PNG based only on client `content_type`/filename. Run the same
  magic-byte checks (`validate_image`, `%PDF` header) used by `/images` and
  `/documents/import`.
- `RequestSizeLimitMiddleware` (`middleware/request_limits.py:44-46`) only
  checks `Content-Length`. Also count streamed bytes for chunked bodies. nginx's
  `client_max_body_size` covers this behind the proxy, but not direct access.

### 10. Storage quota covers only assets
`max_account_bytes` is enforced only for `Asset` rows (`assets_api.py:141-151`).
Images, PDF originals, rasters, and upload parts bypass it, so one account can
fill the disk 10 MiB at a time. Compute usage across all user-owned objects
(or keep a running `users.bytes_used` counter) and enforce it in every write
path.

### 11. Rate-limit coverage for expensive routes
Not covered today: `POST /documents/{id}/exports`, `POST /personalization/runs`
(adapter training), `POST /calibration/*`, `POST /images/{id}/corrections`,
`POST /jobs/{id}/retry`, and the password-reset routes. Add rules for them. Also
make the limiter fail closed for auth routes when Redis is configured but
unreachable in production. The in-memory fallback is per-process, so limits
multiply with worker count.

### 12. Least-privilege database role
The compose `POSTGRES_USER` is a superuser and the app connects as it. Create a
migration owner role (DDL) and a runtime role with only
`SELECT/INSERT/UPDATE/DELETE` on app tables; run Alembic with the owner and the
API with the runtime role.

## P2 — Defense in depth and operations

- **CSRF:** cookie auth relies on `SameSite=Lax` alone, which does not protect
  against same-site subdomains, and multipart routes (`/images`,
  `/documents/import`) accept simple cross-site form posts. Add a middleware
  that rejects unsafe methods carrying the session cookie unless
  `Origin`/`Referer` matches the configured site origin. Bearer-token clients
  (iOS) are unaffected.
- **Error detail leakage:** stop returning raw exception text in `detail`
  (`jobs.py:480`, `math_regions_api.py:222,353,498`, `document_export.py:451,455`,
  `sync_api.py:852,903`, `pdf_import.py:413,475,605`). Map known errors to
  stable `error_codes` and log the rest server-side with the request ID.
- **Cross-tenant ID oracle:** `sync_api.py:575-577` returns 409 when a
  client-chosen `document_id` belongs to any user, and client-chosen `page_id`
  inserts (`sync_api.py:449-458`) can collide across tenants. This is low risk
  with UUIDv4s, but consider namespacing client IDs per user, or mapping
  collisions to the same response as "not found".
- **Defense-in-depth scoping:** `UploadRepository.list_parts/get_part`
  (`repositories.py:1577-1597`) are not user-scoped. Add `user_id` so they are
  safe to reuse.
- **Session management:** add a "sign out everywhere" action and revoke all
  sessions on password change/reset (see P0-1). Consider shortening the 7-day
  TTL with sliding renewal, and purge expired sessions on a schedule
  (`SessionStore.purge_expired` exists but nothing calls it).
- **Audit log:** add an append-only `security_events` table for login
  success/failure, registration, password-reset request/confirm, session
  revoke, account export, and account deletion (user ID, hashed IP, user agent,
  timestamp; no emails or tokens).
- **Log redaction:** `RedactingFilter` is installed only when metrics are
  enabled (`observability.py:151`). Install it unconditionally and add an
  email-address pattern.
- **Monitoring/alerting:** metrics and OTLP exist, but no alert rules. Add
  alerts for 5xx rate, 429 spikes on auth routes, failed-login rate, job
  failure rate, and disk/bucket usage.
- **Backups:** `docs/PRODUCTION_PROFILE.md:114` only says to snapshot volumes.
  Add a scripted `pg_dump` + bucket mirror, retention policy, and a documented
  restore drill that is actually run.
- **Encryption at rest:** S3 `put_object` (`s3_object_store.py:112`) sets no
  server-side encryption. Enable bucket default SSE (or pass
  `ServerSideEncryption`) and use encrypted volumes for Postgres.
- **Onboarding:** registration has no email verification, so anyone can
  register someone else's address and block it. Add verification before the
  email is usable for password reset.

## Checklist coverage

Items reviewed with no action needed:

- **Secrets:** `.env` and `*.db` are gitignored and untracked; the git history
  scan found no keys, tokens, or private keys; release-evidence files contain
  no tokens.
- **Frontend:** the only build-time variable is `VITE_API_BASE_URL`; Vite
  builds without source maps by default; there are no `innerHTML` sinks; KaTeX
  renders with `trust: false`; auth stays in the HttpOnly cookie (no token in
  `localStorage`); the service worker never caches `/api/` responses.
- **iOS:** session tokens are stored in the Keychain; ATS permits only local
  networking.
- **CORS:** no CORS middleware is configured, so only the same origin can read
  responses.
- **JWT:** not used; sessions are opaque tokens hashed at rest.
- **Injection, SSRF, path traversal:** none found (see Summary).
- **Admin/debug routes:** none exist apart from the docs/metrics/health
  exposure in P1-6.
- **Not applicable:** Firebase, Supabase, row-level security, webhooks,
  payments/subscriptions, third-party analytics, and LLM features. The "AI" is
  local OCR/formula models with no tool use, so prompt injection does not
  apply. Recognized text is rendered as text or with KaTeX `trust: false`.
- **Build logs:** CI does not echo secrets and uses none.

## Suggested order

1. P0-1, P0-2, P0-5: small, mostly in `consent_api.py`, `rate_limiter.py`,
   `settings.py`, each with regression tests.
2. P0-4 and P1-6/P1-7: config-only changes to compose and nginx.
3. P0-3: account disable + purge job + full-coverage deletion test.
4. P1-8: FastAPI/Starlette upgrade, then Pillow, then the ML stack; add
   audit jobs to CI.
5. Remaining P1, then P2 as ongoing hardening.
