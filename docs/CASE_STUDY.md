# Engineering case study

Handwriting Notes Digitizer connects an iPad notebook client and a web review
workspace to a shared backend. The user captures or imports notes, requests
prose/formula recognition, reviews the result beside its original source, and
exports the saved correction.

The engineering focus is keeping source material and user edits trustworthy
across local storage, synchronization, asynchronous inference, and export.
The application is a **local alpha**. Real-model website/simulator workflows
were demonstrated on October 11, 2026; representative accuracy and physical
iPad acceptance remain outstanding.

## 1. Preserve a drawing while remote sync is in flight

**Problem.** Restoring notebook metadata alone can leave a second client with
an empty canvas. Downloading remote ink also risks replacing a drawing made
locally while the download is running.

**Decision.** Store edits locally in SQLite and ink files, enqueue pending
operations, and restore the exact native drawing plus its canonical ink bundle.
Validate the page identity and content hash, then recheck the local revision
on the local commit queue before installing a remote revision. An upload
acknowledgement must refer to the submitted revision, not a newer local edit.

**Result.** Tests cover restoring identical ink, retrying downloads, rejecting
tampered/cross-account data, and protecting pending drawings. The canvas also
observes revision changes so synced ink becomes visible without navigation
silently committing an old drawing.

**Inspect:** [SQLite local store](../ios/Sources/PersonalizedNotesKit/SQLiteLocalStore.swift),
[sync engine](../ios/Sources/PersonalizedNotesKit/SyncEngine.swift),
[native ink restoration](../ios/Sources/PersonalizedNotesApp/AppModel.swift),
[second-client tests](../ios/Tests/PersonalizedNotesAppTests/SecondClientBootstrapTests.swift),
[canvas race tests](../ios/Tests/PersonalizedNotesAppTests/PencilCanvasSyncTests.swift).
The [dated review report](release-evidence/REVIEW_FIXES_2026-10-11.md) records the
race conditions and fixes. Full live two-client interruption/conflict acceptance
and hardware drawing tests remain in the [active checklist](../TODO-2026-10-11.md).

## 2. Keep predictions and corrections attached to the right source

**Problem.** An asynchronous prediction can finish after its source page changes.
An export can also diverge from the correction that the user just saved if it
reads mutable state instead of a reviewed revision.

**Decision.** Recognition runs use frozen crops, hashes, page revisions, and
model provenance. Persist job state in the database with retries/deadlines and
restart recovery. Reject stale sources, and save corrections as immutable
review revisions with a version check. Exports assemble the selected reviewed
document snapshot.

**Result.** Replacing a source invalidates current recognition while retaining
history. A stale review reports a conflict instead of overwriting another
edit. Missing or incompatible models report unavailable. The real-model demo
shows source-linked review, save/reopen, and Markdown export; automated tests
cover additional export formats and failure paths.

**Inspect:** [recognition jobs](../backend/app/services/jobs.py),
[review revisions](../backend/app/services/document_review.py),
[document export](../backend/app/services/document_export.py),
[conversion regressions](../backend/tests/test_conversion_runs.py),
[job recovery tests](../backend/tests/test_jobs_lifecycle.py),
[review regressions](../backend/tests/test_review_regressions.py),
[export goldens](../backend/tests/test_document_export_goldens.py).
See [recorded acceptance](release-evidence/END_TO_END_LOCAL_2026-10-11.md).
The database makes job state durable, but the worker is currently process-local;
an independently deployed production queue has not been delivered.

## 3. Connect native, web, and ML components without hiding their limits

**Problem.** Swift and TypeScript clients must agree on errors, timestamps,
document identity, and ink representation. Prose and formula recognizers also
have different capabilities and model requirements.

**Decision.** Keep versioned OpenAPI, error-code, and canonical `ink_v1`
artifacts with regeneration/drift checks and Swift compatibility checks.
Use separate model adapters and health/capability reporting for prose and
formulas. Corrections enter training data only through explicit consent;
TrOCR conversion does not silently enable legacy CRNN personalization.

**Result.** The clients use a shared document/review model while surfacing
missing capabilities. A signed simulator journey against the local backend
exercised authentication, imported-source restoration, conversion, review,
reopening, and sharing with real model connections.

**Inspect:** [OpenAPI](../contracts/openapi-v1.json),
[ink contract](../contracts/ink_v1/simple_stroke.json),
[contract boundary](API_CONTRACT_BOUNDARY.md),
[Swift contract checks](../scripts/check_swift_api_models.py),
[frontend API client tests](../frontend/src/api/client.test.ts),
[real-model simulator test](../ios/UITests/LiveConversionDemoUITests.swift),
[training consent](../backend/app/services/document_review.py).
The runtime demonstration uses public/synthetic fixtures, so it establishes
functioning integration rather than handwriting accuracy.

## What this project builds and what it uses

| Project implementation | Third-party components |
| --- | --- |
| Native notebook/editor, local persistence, pending operations, ink restore | SwiftUI, PencilKit, SQLite |
| Website review, import, source previews, saved corrections | TypeScript, Vite, KaTeX |
| Owner-scoped APIs, jobs, immutable reviews, exports, contracts | FastAPI, SQLAlchemy/Alembic, PyTorch |
| Formula/prose adapters and model availability/provenance | Pretrained UniMERNet-small and Microsoft TrOCR |
| Compact formula model, training CLI, evaluation and promotion checks | PyTorch and externally supplied licensed datasets |

The pretrained recognizers were integrated into the product; their base weights
were not trained by this project. The compact-model research path and legacy
CRNN adapter training exist in source, but neither has a representative quality
result supporting a portfolio accuracy claim.

## Evidence and portfolio wording

The [October 11 regression report](release-evidence/REVIEW_FIXES_2026-10-11.md)
records **906 backend tests**, **175 website tests**, **30 Swift package tests**,
and **33 native app tests** passing. Two backend service-integration checks were
skipped. These are dated local checks at the report's source fingerprint, not a
claim that current remote CI or deployment acceptance passed.

Suggested resume bullet, for work the author can personally substantiate:

> Built an iPad and web notes application with local persistence, shared document
> synchronization, and source-linked prose/formula recognition review; integrated
> pretrained models with durable jobs, versioned corrections, and document export
> using SwiftUI, TypeScript, and FastAPI.

[Demo](../demos/README.md) · [Current status](FEATURE_STATUS_MATRIX.md) ·
[Setup](GETTING_STARTED.md) · [Project overview](../README.md)
