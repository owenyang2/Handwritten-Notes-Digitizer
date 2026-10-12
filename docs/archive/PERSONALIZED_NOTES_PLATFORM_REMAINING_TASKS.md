# Personalized Notes Platform: Remaining Work and Agent Handoff

> **Historical document.** Retained for design rationale and development history.
> Its checkboxes, machine paths, and capability statements describe an earlier
> stage; use the [current status](../FEATURE_STATUS_MATRIX.md),
> [active remaining work](../../TODO-2026-10-11.md), and
> [setup guide](../GETTING_STARTED.md) for the maintained project state.

Last audited: September 12, 2026
Audited branch: `platform-sync-uploads` at `5fa39dc`
Source plan: [archived platform plan](PERSONALIZED_NOTES_PLATFORM_IMPLEMENTATION_PLAN.md)

This is the execution checklist for finishing the plan. It records only work that
is incomplete, unverified, or known to be broken. A checked box must mean that the
acceptance evidence described here exists; adding a class, endpoint, or UI shell is
not enough when the plan requires a staging, device, security, accessibility, or
user-research result.

## Current verified baseline

- [x] At the start of the audit, the fetched branch matched
  `origin/platform-sync-uploads`; the audit changes are now committed locally
  on top of that remote tip.
- [x] Branch contains `origin/main` and every prior remote feature branch.
- [x] Backend suite: 872 tests passed, with 16 warnings (including the
  consciously pinned Starlette/AnyIO compatibility warning and ONNX export
  warnings).
- [x] Frontend suite: 173 tests passed; typecheck and production build passed.
- [x] Swift package suite: 7 tests passed.
- [x] Empty SQLite database upgraded through every migration; `alembic check`
  reported no drift.
- [x] Development and production Compose files pass `docker compose config`.
- [x] Generated `ink_v1`, OpenAPI, and error-code artifacts reproduce without
  drift when invoked with the correct Python path.
- [x] `pip check` reports no broken Python requirements.
- [x] The last successful production npm dependency audit reported zero known
  vulnerabilities; the September 12 refresh was blocked by npm registry DNS
  in this sandbox and therefore remains a pending external verification.
- [x] Full local Docker smoke test on Apple Silicon: both images built, both
  containers became healthy, nginx served the frontend, and `/api/health`
  reached the backend. Recognition remained honestly unavailable without a
  model artifact.
- [ ] Useful recognition smoke test. No prose, UniMERNet, or compact-math model
  artifact is installed in the audited checkout.

## Execution evidence from this pass

The following checks were run on September 12, 2026 from the working tree. They
prove repository behavior only; they do not replace staging, legal, research,
accessibility, or physical-device evidence.

- `OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONPATH=.. .venv/bin/python -m pytest -q`
  from `backend/`: **872 passed, 2 skipped**.
- `npm test`, `npm run typecheck`, and `npm run build` from `frontend/`:
  **173 passed**, typecheck passed, production build passed.
- `swift test --filter PersonalizedNotesKitTests` from `ios/`: **7 passed**.
- A disposable empty SQLite database upgraded through Alembic `head`, then
  `alembic check` reported no drift.
- Generated-contract freshness and first-release OpenAPI structural checks
  passed; the stale-artifact regression test fails as designed.
- ONNX export parity passed for widths 64, 96, 160, and 192 at batch sizes 1
  and 2. The optional MinIO and PostgreSQL tests were added but skipped because
  no disposable services were configured.
- `make docker-smoke` passed on an ARM64 Docker Desktop host after adding
  architecture-aware PyTorch wheel pins. The frontend and backend containers
  were healthy and both direct and nginx-proxied health checks passed.
- `npm audit --omit=dev --audit-level=high` could not refresh its prior result in
  this sandbox because the npm registry DNS endpoint was unavailable; no npm
  dependency files changed during this pass.
- Real model recognition, real PostgreSQL/Redis/MinIO staging, interviews,
  consented corpus collection, licensing review, signed iPad
  testing, and production approval remain unverified.

## P0: Repair the branch's validation pipeline

These tasks come first because the current GitHub Actions workflow can report a
failed or misleading result.

### CI-1: Fix contract-generation imports

- [x] Fix `.github/workflows/ci.yml` lines 39-40. The backend job runs from
  `backend/`, but the scripts are launched with `PYTHONPATH=..`. The exact checked-in
  command fails with `ModuleNotFoundError: No module named 'app'`.
- [x] Either run the scripts from the repository root with `PYTHONPATH=backend`, or
  keep the backend working directory and use `PYTHONPATH=.`.
- [x] Reproduce both commands locally in the same working directory used by CI.

Acceptance:

- The two generator commands exit zero in a clean checkout using the workflow's
  exact commands.
- The backend GitHub Actions job reaches the contract-drift step.

### CI-2: Make generated-contract drift checks effective

- [x] Fix the line-41 pathspecs. From `backend/`, `contracts/...` does not address
  the repository-root `contracts/` directory.
- [x] Check every generated artifact, not only the hash and error codes:
  `contracts/ink_v1/simple_stroke.json`,
  `contracts/ink_v1/simple_stroke.sha256`, `contracts/openapi-v1.json`, and
  `contracts/error_codes.json`.
- [x] Add a regression check that intentionally changes a generated artifact and
  proves the workflow command fails.

Acceptance:

- Regeneration followed by `git diff --exit-code` succeeds on an untouched tree.
- A deliberately stale generated file causes a non-zero result.

### CI-3: Close remaining automated-validation gaps

- [x] Add an OpenAPI compatibility check, not only snapshot regeneration.
- [x] Compile or generate the transport-neutral TypeScript and Swift DTO clients
  from the checked-in schema, or explicitly document and test the chosen manual
  contract boundary.
- [ ] Add PostgreSQL migration coverage, including upgrades from every released
  supported schema. An opt-in current-head PostgreSQL test exists, but no
  disposable PostgreSQL service was available and the full released-revision
  matrix still needs to be exercised.
- [ ] Add production S3-compatible object-store contract coverage against MinIO.
  An opt-in test exists, but it was not run against a live disposable MinIO
  service.
- [x] Add queue duplicate-delivery, crash, retry, timeout, and stale-lease tests.
- [x] Add sync state-machine property tests covering reordering, duplicates,
  conflicts, tombstones, and compaction.
- [x] Add export goldens for Unicode, long equations, matrices, figures,
  pagination, and malicious LaTeX.
- [x] Add eager-versus-ONNX parity tests across widths and batch sizes before
  enabling ONNX in production.
- [x] Consciously pin the Starlette/AnyIO deprecation warning with exact,
  compatible dependency versions and document the planned stack upgrade.

## P0: Complete Phase 0 evidence and governance

These tasks require product owners, researchers, legal/privacy review, or real
participants. An autonomous coding agent may create tooling and document
templates, but must not claim these tasks are complete without real evidence.

- [ ] Conduct 12-20 interviews across integrated-notebook, external-app, and
  paper-note users.
- [ ] Collect 30-50 representative pages with explicit research consent and a
  documented retention/deletion policy.
- [ ] Benchmark the current product and approved competitor/vendor trials on the
  same pages.
- [ ] Record import success, region recall, formula exact match, critical-symbol
  errors, and human correction minutes per page.
- [ ] Create and approve a privacy threat model and complete data inventory.
- [ ] Create a model/dataset license register with commercial-use decisions.
- [ ] Approve product principles, non-goals, and the native-notebook decision gate.

Exit evidence:

- Users value the source-linked correction/personalization workflow enough to
  justify native notebook work.
- A benchmark and legally usable model/data route exist.
- No mismatched, unlicensed, or non-consented data can enter training or promotion.

## P0: Real recognition data, models, and evaluation

The code currently fails closed when real artifacts are missing, which is correct.
The product is not useful for recognition until the following work is completed.

- [ ] Replace the authored placeholder IAM labels with a cryptographically pinned
  manifest of real, matched image/transcription pairs, or keep that training path
  disabled permanently.
- [ ] Add a human spot-check tool that renders random image/label pairs before a
  dataset can be marked verified.
- [ ] Populate real prose and math evaluation fixtures with provenance, consent,
  licenses, split integrity, and digests.
- [ ] Install and pin at least one real prose baseline and one real formula
  baseline in staging.
- [ ] Produce signed evaluation reports containing artifact digest, dataset digest,
  environment, latency, accuracy, critical-symbol errors, and limitations.
- [ ] Demonstrate deterministic or bounded-reproducibility training from the
  documented configuration.
- [ ] Keep capability endpoints and UI honest when an artifact is absent,
  incompatible, corrupt, or too slow.
- [ ] Decide whether UniMERNet/MathWriting-related licenses permit the intended
  deployment; do not infer permission from the code license.

Acceptance:

- A real PDF/image can complete import -> recognition -> review -> export in
  staging using installed, verified artifacts.
- No synthetic fixture or missing model can produce a promotion or success claim.
- Performance and error baselines are recorded on a private representative holdout.

## P1: Production architecture and operational safety

The checked-in profile is production-shaped, not production-complete.

### Workers and queues

- [ ] Choose and document Celery, Dramatiq, or another production queue after a
  reliability spike.
- [ ] Move recognition and training out of FastAPI process-local threads.
- [ ] Implement durable leases, heartbeats, retry/backoff, idempotent completion,
  cancellation, stale-worker recovery, and poison-job handling.
- [ ] Add separately scalable CPU and GPU worker entrypoints to
  `docker-compose.prod.yml`.
- [ ] Prove two API replicas and multiple workers do not duplicate or lose work.

### Storage, database, and restore

- [ ] Run the complete suite against PostgreSQL, Redis, and MinIO rather than only
  SQLite/in-memory substitutes.
- [ ] Test migrations from every supported released database revision.
- [ ] Define lifecycle rules for originals, crops, ink, exports, models, and
  deletion tombstones.
- [ ] Implement encrypted backups and perform documented PostgreSQL/MinIO restore
  drills.
- [ ] Ensure account deletion remains durable and visibly retryable when a storage
  target or worker is temporarily unavailable.

### Edge security and observability

- [ ] Add real TLS termination and secure production-domain configuration.
- [ ] Remove all placeholder database, Redis, and MinIO credentials; integrate a
  secret manager or documented deployment-secret mechanism.
- [ ] Add the OpenTelemetry SDK/collector or remove the unsupported configuration
  claim.
- [ ] Define dashboards and alerts for job age, queue depth, upload failures,
  storage growth, model failures, latency, and deletion retries without logging
  note content.
- [ ] Define SLOs, budgets, incident response, rollback, and on-call procedures.
- [ ] Complete restore, deletion, failover, and model-rollback drills.

## P1: Upload and parser hardening

- [x] Bound every legacy image, calibration, and PDF upload before parsing; the
  routes now use bounded readers and do not call `read()`/`body()` on an
  unbounded request. Isolated malware/CDR quarantine remains a separate gate
  below.
- [x] Enforce the default proxy (11 MiB request envelope) and application
  declared-length limits consistently; route-level readers still enforce the
  10 MiB decoded upload and resumable-part limits for chunked requests.
- [ ] Validate extensions, MIME declarations, magic signatures, decoded dimensions,
  page counts, compression ratios, and final hashes.
- [ ] Add hostile fixtures for truncation, polyglots, malformed files,
  decompression bombs, oversized images/pages, and parser timeouts.
- [ ] Add isolated malware/CDR handling appropriate to the deployment threat model.
- [ ] Sandbox PDF and TeX/math rendering with CPU, memory, time, filesystem, and
  network restrictions.
- [ ] Safely support HEIC if it remains a promised input format.
- [ ] Connect completed resumable sessions to import ingestion without duplicating
  stored assets.
- [ ] Test pause, resume, cancellation, retry, expiration, cleanup, quota races,
  and concurrent completion.

## P1: Authentication, privacy, and deletion

- [x] Stop storing the usable session token as the `UserSession` primary key;
  store and resolve only a one-way token hash. The Alembic migration preserves
  existing sessions by hashing them and assigns a non-secret session UUID;
  downgrade is intentionally blocked because a one-way hash cannot restore a
  raw credential.
- [ ] Implement short-lived access sessions plus rotating refresh credentials, or
  integrate a vetted OIDC provider.
- [ ] Add idle and absolute expiry, credential rotation, replay detection, device
  naming, session history, and remote revocation.
- [ ] Deliver password-reset messages through a real provider without returning a
  debug token outside test/development.
- [ ] Add verified email and decide whether MFA/passkeys are required.
- [ ] Store iOS credentials in Keychain and test revoke/expiry/recovery against a
  live backend.
- [ ] Verify account export includes all promised user data without leaking secrets
  or other tenants.
- [ ] Verify deletion removes or tombstones every eligible database row, object,
  crop, export, ink bundle, adapter, model assignment, cache, log reference, and
  backup according to policy.
- [ ] Add deletion failure injection and retry tests.
- [ ] Complete tenant-isolation fuzzing across every object, document, sync,
  upload, model, account, and export endpoint.

## P1: Web product completeness and accessibility

- [ ] Replace or formally validate the handwritten browser API contract against
  the OpenAPI schema on every CI run.
- [ ] Complete durable batch import across browser reload, offline transitions,
  duplicate selection, pause/cancel, and expired upload sessions.
- [ ] Add mobile capture quality checks for rotation, perspective, blur, glare,
  crop confirmation, and retake.
- [ ] Test `Permissions-Policy: camera=(self)` on a real HTTPS mobile deployment.
- [ ] Complete library behavior for search, pagination, rename, archive, trash,
  restore, permanent delete, sorting, and concurrent updates.
- [ ] Add accessible progress/status announcements, keyboard-only operation,
  200% zoom/reflow, reduced motion, high contrast, and source-crop alternatives.
- [ ] Run automated accessibility tests and manual screen-reader/keyboard testing.
- [ ] Test service-worker upgrades and stale-cache recovery.

## P1: Complete Phase 2 sync and iPad alpha

The Swift package currently proves only macOS-safe kit behavior. It does not prove
the signed iPad app, PencilKit, Keychain, App Group, or real networking.

The detailed native product audit, usability requirements, dependency order, and
milestone exit gates are maintained in
[`IPAD_NOTE_APP_USABILITY_IMPLEMENTATION_PLAN.md`](../plans/IPAD_NOTE_APP_USABILITY_IMPLEMENTATION_PLAN.md).

- [ ] Generate and build the Xcode project with the real Apple team and final
  bundle/App Group identifiers.
- [ ] Replace the placeholder App Group and verify host app/share-extension access.
- [ ] Wire share-extension inbox items into durable host-app import and resumable
  upload, including failure recovery.
- [ ] Implement real background `URLSession` transfers and `BGTaskScheduler`
  scheduling; declaring background modes is not completion.
- [ ] Preserve both canonical `ink_v1` and native `PKDrawing` across save, reopen,
  sync, conflict, and migration.
- [ ] Wire selected-stroke and page conversion from the canvas to backend jobs.
- [ ] Surface sync conflicts, retries, cancellation, offline state, token expiry,
  and storage/quota errors in accessible UI.
- [ ] Add content-free native analytics and privacy controls.
- [ ] Complete VoiceOver, Dynamic Type, keyboard, pointer, switch-control, and
  left/right-handed palette behavior.
- [ ] Run cross-language golden and round-trip tests between Python and Swift.
- [ ] Run at least 10,000 randomized crash/recovery sequences with zero confirmed
  stroke loss.
- [ ] Verify conflict tests preserve both edits and converge after user resolution.
- [ ] Verify the same account sees the same documents and revisions on web/iPad.

Physical-device matrix:

- [ ] Older supported iPad/Pencil, current base iPad, and current iPad Pro.
- [ ] Portrait, landscape, split view, and Stage Manager where supported.
- [ ] Low storage, low-battery mode, thermal pressure, and long sessions.
- [ ] Wi-Fi loss, captive portal, cellular restriction, backgrounding, system
  termination, force-quit, and token expiry.
- [ ] Very long pages, hundreds of strokes, and 100+ page notebooks.
- [ ] Left/right-handed users and relevant accessibility configurations.
- [ ] Record pen latency, jank, memory, battery, and sync/thumbnail interference.

## P2: Complete Phase 3 conversion workflow

- [ ] Implement and validate a real ink-recognition baseline; retain raster fallback
  for every input.
- [ ] Complete selected-stroke, selected-region, page, and notebook conversion.
- [ ] Ensure recognition never mutates original ink or uploaded assets.
- [ ] Stream progressive page/region results with cancellation and restart-safe
  recovery.
- [ ] Complete the uncertainty/critical-symbol queue for minus/plus, `1/l/I`,
  `0/O`, `x/times`, inequalities, scripts, brackets, fractions, and truncation.
- [ ] Ensure every converted block highlights its exact source strokes or crop.
- [ ] Verify block edits, reorder/split/merge/exclude, and all exports use the same
  immutable active revision.
- [ ] Ensure users can finish review/export when any one recognizer is unavailable.
- [ ] Decide whether to add Apple's on-device prose/search baseline on supported
  systems.
- [ ] Measure median correction time against the external-app/manual-copy baseline.

Exit evidence:

- Correction time improves on representative users/pages.
- Critical errors are visible and reviewable.
- Partial failure is recoverable and original evidence is unchanged.

## P2: Complete Phase 4 truthful personalization

- [ ] Expand the current vocabulary reranker into a versioned dictionary and
  confusion learner with auditable reasons.
- [ ] Add similar-example retrieval and inspectable source examples.
- [ ] Add per-note/per-example inclusion and exclusion, reset, and delete controls.
- [ ] Build chronological personal holdouts that prevent document/page leakage.
- [ ] Run candidates in shadow mode before activation.
- [ ] Implement versioned assignment, compatibility checks, rollback, and base-only
  fallback for prose, math raster, and ink tasks.
- [ ] Replace the legacy final-classifier-only adapter as the claimed solution;
  evaluate reranking, conditioning/prompting, and architecture-appropriate LoRA or
  adapters.
- [ ] Add global, per-user, and critical-symbol promotion guardrails.
- [ ] Show real before/after evidence and sample counts without implying that
  collection alone means improvement.
- [ ] Prove deleting personal learning removes all eligible artifacts and restores
  base behavior.
- [ ] Run comprehension testing showing users understand what was learned and used.

Exit evidence:

- A prospective holdout demonstrates improvement in correction time or a predefined
  recognition metric.
- Global and critical-symbol guardrails pass.
- Rollback and personal-data deletion are tested end to end.

## P3: Complete Phase 5 integrations and scale

- [ ] Select export/integration destinations from research rather than assumption.
- [ ] Implement only approved share-sheet, webhook, API, LMS, or cloud-drive scopes,
  with explicit authorization, audit, and revocation.
- [ ] Add autoscaling CPU/GPU workers, batching, account quotas, rate controls, and
  cost dashboards.
- [ ] Implement model shadow/canary deployment and rehearse rollback.
- [ ] Commission external accessibility and security audits and close findings.
- [ ] Build support tools that hide raw note content by default and audit access.
- [ ] Meet approved SLO, cost, restore, deletion, and incident-runbook gates.

## P3: Phase 6 Android decision

- [ ] Measure demand and iPad retention before authorizing Android work.
- [ ] If authorized, create the Android app using Kotlin, Compose, and AndroidX Ink.
- [ ] Reuse OpenAPI, sync semantics, `ink_v1`, fixtures, and design tokens.
- [ ] Repeat offline, crash-recovery, device, accessibility, and release gates on
  Android.

Do not implement Android merely to make the roadmap look complete; the plan makes
this phase conditional on validated demand.

## Documentation cleanup

- [x] Update this handoff's assessment and evidence section so fixed validation
  gaps are not reported as current defects.
- [x] Reconcile `IMPLEMENTATION_HISTORY_AND_RUNBOOK.md`, which recorded a real
  IAM model deployment, with the current checkout and doctor output showing no
  installed artifacts.
- [x] Add a maintained feature/status matrix: implemented, unit-tested,
  integration-tested, device-tested, staging-validated, and production-approved.
- [x] Document exactly which artifacts are intentionally excluded from Git and how
  staging obtains and verifies them.
- [x] Keep “alpha,” “beta,” and “production-ready” claims tied to exit evidence
  in the matrix, runbook, and model/data boundary documentation.

## Required final release gate

Do not mark the plan complete until all applicable items below have evidence:

- [ ] GitHub Actions is green from a clean checkout, including real contract drift
  detection.
- [x] Full local Docker smoke succeeds on the audited Apple Silicon host.
- [ ] Full PostgreSQL/Redis/MinIO staging smoke succeeds.
- [ ] Real image/PDF and ink workflows complete through consistent exports.
- [ ] Recognition artifacts and benchmark reports are verified and pinned.
- [ ] Web and signed iPad clients pass offline/conflict/token-expiry scenarios.
- [ ] Security, privacy, accessibility, backup/restore, and deletion gates pass.
- [ ] Performance/SLO and cost targets pass.
- [ ] User-research, device, licensing, and comprehension gates are signed off.
- [ ] The repository is clean, generated artifacts match, and release documentation
  reflects the actual deployed system.

## Commands the finishing agent should run

```bash
git fetch --prune origin
git status --short --branch
git rev-list --left-right --count origin/platform-sync-uploads...HEAD

cd frontend
npm ci
npm run typecheck
npm test
npm run build
npm audit --omit=dev --audit-level=high

cd ../backend
python -m pip install -r requirements.txt
PYTHONPATH=.. python -m pytest -q
python -m alembic upgrade head
python -m alembic check

cd ../ios
swift test --filter PersonalizedNotesKitTests

cd ..
docker compose config --quiet
docker compose -f docker-compose.prod.yml config --quiet
make docker-smoke
```

Use a temporary empty database for the clean-migration test. Run the production
integration suite against disposable PostgreSQL, Redis, and MinIO services. Never
run destructive account/deletion/restore tests against user data.

## Copy/paste handoff prompt

> Continue the Personalized Notes implementation from
> `docs/PERSONALIZED_NOTES_PLATFORM_REMAINING_TASKS.md` on branch
> `platform-sync-uploads`. Start with the P0 CI defects and work in dependency
> order. Inspect the existing implementation before changing it, preserve user
> changes, add regression tests for every bug, and run the relevant verification
> commands after each milestone. Do not mark research, licensing, staging,
> production, accessibility, or physical-device gates complete without actual
> evidence. Keep missing-model behavior fail-closed and never use synthetic data to
> support an accuracy or promotion claim. At each stopping point, update the
> checklist with evidence, unresolved blockers, exact test results, and the next
> safe task.
