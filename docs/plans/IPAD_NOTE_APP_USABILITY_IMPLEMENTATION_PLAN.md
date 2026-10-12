# iPad Note App Usability Implementation Plan

Status: engineering alpha acceptance pass complete — iPad alpha foundation, resumable transfer durability, destination-aware import, mixed-source archive portability, normalized page-title search navigation, Pencil hardware actions, bounded notebook conversion, raster conversion fallback, contextual onboarding, normalized local revision/transfer persistence, and guarded second-client ink restoration completed

Audit date: 2026-09-12

Audited branch: `platform-sync-uploads`

Audited baseline: `67150d9`

Related roadmap: [`PERSONALIZED_NOTES_PLATFORM_REMAINING_TASKS.md`](PERSONALIZED_NOTES_PLATFORM_REMAINING_TASKS.md)

## 1. Executive decision

The current branch now has a dependable local-first writing foundation: native
registration/sign-in and guest mode, account export/deletion actions, stable
offline document/page identities, idempotent remote bootstrap, a multipage
PencilKit editor, crash-safer native and `ink_v1` commits, real PencilKit
stroke conversion, durable PDF/image/native-archive imports, source
upload/download, visible
sync recovery, durable local import-failure retry, and a source-linked
page-conversion request/status path.
The remaining milestones below are still required before calling the target
beta-ready; conversion review, model readiness, and the broader
selection/region/notebook workflow remain open.

Verified in this implementation slice:

- iPad simulator build and launch on iPad Pro 13-inch (M4); the latest full
  run used the iOS 18.6 simulator runtime.
- Swift package tests pass, including durable local-store reopen, failed-sync
  retry, page lifecycle, `ink_v1` golden coverage, DTO wire-shape checks, and
  HTTP-client contract tests for health, authenticated recognition, and
  unauthenticated password-recovery routes, and resumable ink-upload wire
  contracts (28 tests total), including register → `me` → restore → revoke
  session lifecycle coverage.
- The native `PersonalizedNotesUITests` target passes on the iPad Pro 13-inch
  (M4) simulator for auth-control accessibility, offline notebook creation
  across relaunch, accessible page/canvas/tool actions, add/duplicate page
  actions, and page/notebook export affordances for PDF, PNG, JPEG, canonical
  `ink_v1`, and native archive, including opening and closing the live PDF
  share controller, plus favoriting a notebook and opening the Favorites view;
  the full run executes 6 UI tests and 27 app tests, including page-number and
  diacritic-insensitive page filtering plus library page-title search opening
  the exact matching page.
- Backend suite passes: 885 passed, 2 skipped; generated OpenAPI is current.
- Server ink revisions are immutable, content-hash deduplicated, linked by
  parent revision, exposed through a page manifest, and bound explicitly to
  recognition requests. Direct and resumable bounded uploads are verified;
  resumable retries preserve the page binding and publish through the same
  immutable revision writer.
- Native archive import tests pass on the iPad simulator for page identity
  remapping, safe-path rejection, checksum mismatch detection, supported v1
  round-trip metadata, and mixed PDF/image page-source preservation.
- A user can import into the current notebook or create a new notebook from
  Files or Photos, with page-specific source metadata retained for mixed
  notebooks; native archive export/import preserves those per-page source
  bindings.
- Imported source bytes are copied into app-owned storage, attached to the
  existing stable document/page aggregate, hash-idempotent on retry, restored
  on another signed-in client, and composited into page PDF export with ink.
- The host app can import selected Photos images through the same durable Files
  pipeline, export a complete notebook PDF, and retain/retry staged sources
  that fail before local notebook creation.
- The editor exposes native iPad printing from the same deterministic notebook
  PDF artifact; the print affordance is covered by the full UI run.
- Development settings accept a validated API endpoint and can probe the
  backend capability/model-readiness health response; the app keeps the
  loopback default for Simulator and requires HTTPS for non-loopback release
  endpoints.
- The library exposes an optional Getting Started guide from the empty state
  and More menu, covering local save versus sync, first Pencil input, page
  creation, and retry-safe sync/conversion behavior without blocking offline
  writing.
- The local SQLite store now records schema version 3, migrates old blob-backed
  notebooks additively to normalized `local_pages`, immutable local revision,
  and transfer-queue tables, keeps the legacy page blob as a rollback/read-
  repair source, dual-writes page edits, and defers page-row cleanup until
  purge acknowledgement; migration, reopen/retry, and queue cleanup behavior
  is covered by package tests.
- Import preflight rejects empty, mislabeled, unsupported, and structurally
  invalid files before raster decoding; iPad runtime tests cover valid PDF/PNG
  inputs and a 100-page PDF with alternating portrait/landscape dimensions.
- Focused two-client proof now covers a failed first reconnect followed by a
  successful retry, stable document/page identity bootstrap, active immutable
  ink-manifest discovery, owner-scoped byte-identical revision download, local
  content-hash validation, and exact native/canonical pair installation. A
  reconnect never overwrites a newer unsynced local revision.

These checks prove an engineering alpha foundation, not the full beta exit
gate: physical-device Pencil behavior, accessibility, quota/fault campaigns,
search/organization, conversion review, and model-backed recognition remain
open.

The next implementation must prioritize trust before feature breadth:

1. No lost notes, strokes, pages, or imports.
2. A complete first-run and account flow, including sign-up on iPad.
3. A practical multi-page notebook editor with understandable save and sync
   state.
4. Library organization, import, search, and export expected from a modern
   note-taking app.
5. The product's differentiator: source-linked handwriting conversion and
   review, without ever replacing the original ink.

Do not begin with collaboration, audio recording, AI chat, or decorative
parity features. Those features should follow a measured iPad alpha in which
offline edits survive crashes and converge across devices.

### Immediate next move (re-scoped after the alpha implementation)

The current branch is a large, uncommitted alpha slice. Stop adding features
until it is reviewable and its main user journey is understandable. Work in
this order:

1. Review and consolidate the current changes into coherent slices. Confirm
   migrations, generated contracts, and app/backend boundaries before merging.
2. Run one bounded acceptance pass covering first launch, local notebook
   creation, writing, force-quit/reopen, page actions, import, annotation,
   export, and visible recovery from an ordinary sync failure. Record confusing
   interactions and fix those before expanding the feature set.
3. Prove the primary trust gap: create and edit a notebook on one
   client, then bootstrap the same document, page IDs, and ink revision on a
   second client. Cover retry and reconnect behavior with focused tests.
   **Completed in this acceptance pass; physical multi-device validation is
   still required before beta.**
4. Test Apple Pencil writing and the core accessibility path on a physical
   iPad. Treat simulator UI coverage as supporting evidence only.
5. Once those checkpoints are green, move to source-linked conversion review:
   show output beside the original ink, expose uncertainty, and support
   accept/edit/retry without replacing source strokes. Do this only against a
   useful recognition model or an explicit deterministic test fixture.

Randomized crash campaigns, 1,000-notebook performance runs, and exhaustive
fault matrices are beta-hardening work. They must not block this alpha
checkpoint or replace hands-on product validation.

## 2. Scope and success definition

This plan covers the native iPad host app, share extension, shared Swift
package, backend contracts needed by the iPad app, and iPad-specific test and
release work. It does not replace the broader platform plan. Backend security,
model availability, production deployment, web parity, and personalization
evaluation remain governed by the related roadmap.

The iPad usability initiative is complete when a new user can:

1. Install the app, understand its value, and either start locally or create an
   account without visiting the web app.
2. Create a notebook while offline, write with Apple Pencil, add and reorder
   pages, leave the app, force-quit it, and reopen with every stroke intact.
3. Import a multi-page PDF or image from Files, Photos, or the share sheet and
   see durable progress, retry, and failure states.
4. Organize, find, rename, duplicate, move, archive, trash, restore, and export
   notes without relying on hidden swipe gestures.
5. Sign in on a second client and receive the same notebooks, pages, and
   revisions after sync.
6. Select strokes, a region, or a page for conversion; review uncertain output
   against exact source ink; and finish even when one recognition provider is
   unavailable.
7. Use the core flows with VoiceOver, Dynamic Type, a hardware keyboard, a
   pointer, either hand, and supported iPad multitasking modes.

## 3. Evidence-based audit of the current app

### 3.1 What exists today

| Area | Current implementation | Useful foundation |
| --- | --- | --- |
| App shell | `RootView` routes loading, welcome/auth, library, and a multipage notebook editor. | Simple SwiftUI state flow that can be split into feature coordinators. |
| Authentication | Welcome, sign-in, registration, guest mode, Keychain-backed session restore, session expiry fallback, and typed password-reset request/confirm UI. | Email verification, guest merge, refresh-token rotation, and richer field validation remain open. |
| Library | Local list, title search, active/archive/trash picker, create/import actions, lifecycle actions, sync summary, and sync issue recovery. | SQLite library projections and pending operation storage exist. |
| Drawing | `PKCanvasView`, system `PKToolPicker`, Pencil/touch toggle, pan/zoom, source underlay, and local native `PKDrawing` persistence. | PencilKit provides low-latency capture and a native lossless representation. |
| Sync | Durable document-operation queue, idempotency keys, cursors, stable page IDs, source transfer, immutable ink manifests, resumable ink sessions, guarded active-ink download, and local retry/conflict records. | Exact conversion binding and focused two-client reconnect/bootstrap are implemented; broader physical multi-device and conflict campaigns remain open. |
| Share extension | Accepts PDFs and images into an App Group inbox. | Correct process boundary for share-sheet ingestion. |
| Account | Current user display, session list/revocation, export receipt, confirmation-gated deletion, and local native archive export/import. | Recovery password policy, email verification, and complete account-rights delivery remain open. |
| Settings | Wi-Fi-only upload toggle, personalization consent acknowledgement, client ID, validated API endpoint editing, and capability/model-readiness test. | Initial privacy and network controls. |
| Tests | Ink golden test, local-store persistence test, organization metadata persistence, fake-service sync tests, DTO wire-shape checks, URLProtocol transport contract tests, app-unit tests, and a native iPad UI test target. | Import, drawing-restore, fault, and physical-device campaigns remain open. |

### 3.2 Confirmed usability and integrity gaps

#### Account entry

- `AuthFlowView` now provides welcome, registration, sign-in, and guest entry;
  session restore remains Keychain-backed and local notes remain available
  after session expiry.
- Password recovery now uses the backend request/confirm contract with generic
  request messaging, one-time token entry, and development-only display of a
  server-returned debug token. Email verification and password policy/field-level
  validation remain open; guest-to-account attachment now requires an explicit
  stable-ID merge decision.
- Account export currently exposes the backend's metadata receipt contract;
  the notebook editor also provides a local versioned native archive export.
  Deletion is explicit and documents that the local iPad copy is retained.

#### Notebook creation and cross-device identity

- `document.create` now carries client-assigned document/page UUIDs, is
  idempotent by client operation identity, and atomically creates the server
  aggregate on first push.
- Pulling a create change bootstraps a remote local projection; page add,
  delete, reorder, and duplicate use stable IDs and ordered indexes.
- Direct document creation remains a separate server API path, so the broader
  normalized-page/snapshot contract still needs a formal versioned migration.
- A newly bootstrapped client now follows the active page manifest to an
  owner-scoped immutable content endpoint, revalidates the canonical hash, and
  installs the exact native/canonical pair. Pending offline local ink is never
  replaced by a reconnect download.

#### Ink durability and portability

- Drawing changes are debounced for 50 ms and written to native `PKDrawing`
  and an `ink_v1` file.
- `PencilKitInkAdapter` now writes non-empty canonical strokes and preserves
  supported point timing, force, azimuth, altitude, size, color, and tool data.
- Native and canonical files are committed through a versioned manifest with
  SHA-256 validation and a serialized commit queue; the editor exposes local
  save state and retryable sync failures.
- A failed local commit records a durable page-scoped recovery snapshot and
  journal entry; the editor and sync-issues screen expose retry across relaunch.
- Ink uploads are performed after metadata/source sync and deduplicated by
  content hash. The server now retains immutable page revisions with parent
  lineage, an explicit active revision, a page manifest, bounded-size
  validation, and exact revision binding for conversion requests. Thumbnail
  generation remains open; resumable ink transfer now uses the authenticated
  multipart session protocol with a stable local idempotency key.

#### Pages and editing

- `NotebookEditorView` now provides page thumbnails, add, duplicate, delete,
  reorder, page navigation, explicit move/delete actions with confirmation,
  explicit undo/redo, fit/reset zoom, and scene background flushes.
- Pencil-only capture is the default, with a discoverable touch-drawing toggle;
  source PDF/image underlays are rendered in the PencilKit content layer and
  page export composites source plus retained ink.
- Paper templates, custom page sizes/orientation, persistent presets, and a
  native PencilKit lasso affordance are implemented. Cut/copy/paste commands
  are exposed for the native selection; selection extraction/transform,
  convert-selection, shape recognition, left-handed layout, and
  delete-with-undo remain open.
- There are no typed text boxes, images, links, checklists, shapes, attachments,
  or audio. These are later parity items, not data-integrity blockers.

#### Share and import

- The extension writes a manifest and source bytes to the App Group.
- The host app copies the extension source into app-owned durable storage
  before acknowledging the inbox item, creates real pages from PDF/image
  dimensions, and keeps the source metadata for retry.
- Files import and share-sheet ingestion are present. The backend now accepts
  a hash-idempotent source upload against the existing stable document and
  exposes owner-scoped download for second-client hydration.
- PhotosPicker entry points, drag-and-drop, and the local retry path are
  present; scanner support and remote upload/processing progress remain open.
  The current import path now routes Files, share-sheet, PhotosPicker,
  drag-and-drop, and `.hndnote` notebook sources through one
  `ImportCoordinator`, exposes per-file
  importing progress with cancellation, enforces a 10 MB client-side cap,
  checks free space before staging, and keeps a durable failure journal for
  sources that fail before a notebook is created.

#### Library and discovery

- The library has title/source-metadata search, lifecycle actions, notebook
  duplication, favorite state with a synced Favorites smart view, bounded
  folder paths, tags, recent/imported/unsynced/needs-attention views, a
  persisted list/grid choice, page-preview cards, updated/created/title
  sorting, multi-select bulk actions, and explicit permanent-delete confirmation.
- Pagination is exposed as incremental loading with stable selection for the
  current view. Local search now includes page titles and opens the matching
  page from list, grid, and context-menu results; recognized-text search,
  drag-and-drop folder placement, thumbnail-cache eviction, pinning, and
  copy-to-folder remain open.
- Failed queued operations and conflicts now appear in a sync-issues sheet with
  retry, keep-server, and retry-as-new actions. Folder depth/path validation,
  metadata limits, and server-side ownership checks are covered; two-client
  organization convergence and 1,000-item performance remain open.

#### Conversion and personalization

- The iPad editor now has a page-conversion action that first requires the
  current canonical ink revision to be uploaded, then displays the real
  queued/running/partial/failed/complete server state. The original ink is
  never replaced; selection, region, notebook, and review UI remain open.
- There is no handwriting/full-content search, converted-block editor,
  uncertainty queue, source highlighting, or conversion-result export.
- Recognition model artifacts are not present in the current development
  environment. The UI must show recognition as unavailable rather than imply
  that empty or fallback output is a successful conversion; the page action now
  maps model_unavailable to that explicit state.

#### Device, networking, and release configuration

- The default API URL is `http://127.0.0.1:8000`. That works only when the
  backend is reachable in the same simulator/Mac network context; on a physical
  iPad, loopback means the iPad itself.
- The Docker development backend is currently bound to Mac loopback. Physical
  iPad testing needs a LAN-reachable, TLS-capable development endpoint or a
  controlled tunnel and explicit development transport policy.
- The App Group and signing identifiers are placeholders until a real Apple
  team and final bundle identifiers are configured.
- Background modes are declared, but there is no background transfer session
  or scheduled task implementation.
- The full app and embedded share extension build in the simulator, but that
  does not validate Pencil latency, palm rejection, Keychain/App Group behavior,
  memory pressure, background execution, or signing on a physical iPad.

#### Quality and accessibility

- The shared package now has local-store, sync, and ink golden coverage plus
  backend contract/integration tests. A native UI test target covers auth
  controls, offline notebook persistence across relaunch, and initial page
  operations; an iPad unit-test target covers native archive round-trip and
  integrity failures.
- Automated registration, import, local and second-client canvas restore,
  conflict, and transport contract coverage exists; background transfer,
  token-expiry UI, and full live-backend iPad campaigns remain open.
- VoiceOver labels/rotors, Dynamic Type layouts, keyboard commands, pointer
  behavior, reduced motion, increased contrast, switch control, and left-handed
  layouts have not been verified.
- There are no measured pen-latency, long-page, 100-page, memory, battery,
  thermal, thumbnail-contention, or low-storage baselines.

## 4. Product direction and information architecture

### 4.1 Product principles

1. **Ink is the source.** Conversion, search indexes, thumbnails, and exports
   are derived. Recognition never mutates or replaces original ink.
2. **Local action is immediate.** Drawing, page turns, undo, and local saves
   never wait for the network.
3. **State is honest.** Use explicit `Saved on iPad`, `Waiting to sync`,
   `Syncing`, `Needs attention`, and `Synced` states. Do not collapse durable
   local save and remote synchronization into one checkmark.
4. **Recovery is visible.** Failed uploads, conflicts, expired sessions, and
   unavailable recognition have a destination, explanation, and retry path.
5. **Progressive complexity.** A first notebook should feel simple. Folders,
   metadata, conversion controls, and advanced settings appear where needed.
6. **System conventions first.** Prefer PencilKit, standard document/photo
   pickers, share sheets, keyboard commands, drag-and-drop, and native
   accessibility semantics.
7. **Account value is explicit.** Local writing should be possible before
   sign-up unless the product deliberately chooses server-required storage.
   Sign-up unlocks sync, backup, web access, conversion, and personalization.

### 4.2 Proposed iPad navigation

Use a `NavigationSplitView` that adapts from regular to compact width:

```text
Sidebar                     Library/content                  Editor
├─ All Notes                ├─ Search                        ├─ Page thumbnails
├─ Recent                   ├─ Sort/filter/view controls     ├─ Canvas
├─ Favorites                ├─ Notebook grid/list            ├─ Drawing toolbar
├─ Shared (future)          └─ Multi-select actions          └─ Inspector/review
├─ Folders
├─ Tags
├─ Archive
├─ Trash
└─ Sync & Account
```

On a notebook screen:

- Leading edge: collapsible page navigator with thumbnails, page numbers, and
  add/reorder actions.
- Center: distraction-free canvas with persistent page bounds and background.
- Top/near-pencil edge: drawing tools, undo/redo, selection, insert, and
  conversion action.
- Trailing sheet/inspector: page settings, recognition status, source-linked
  review, and document metadata.
- Compact width: page navigator and inspector become sheets; the canvas remains
  primary.

### 4.3 First-run and authentication flow

Recommended default:

```text
Welcome → Try a sample/local notebook
        ↘ Create account → verify if required → library
        ↘ Sign in → restore/sync → library
```

The first screen should explain the exchange clearly:

- Write locally without an account.
- Create an account to sync, back up, use the web app, convert handwriting, and
  personalize recognition.
- Existing users can sign in.

If product or backend policy requires an account for all data, remove the
current “sign-in is only required to sync” promise and state the requirement
before requesting credentials. Do not ship the contradictory state.

## 5. Architecture decisions required before UI expansion

### AD-1: Stable client-assigned identities

Adopt client-assigned UUIDs for offline-created documents, pages, and immutable
ink revisions. Extend `POST /documents` with an optional idempotent
`client_document_id`, or add a first-class `document.create` operation that can
create the canonical row. The server must:

- accept the same owner/client/idempotency tuple repeatedly;
- return the same canonical document;
- reject cross-owner UUID collisions without disclosing ownership;
- create the first page or accept a bounded initial page payload atomically;
- append a create/snapshot change that another client can bootstrap;
- define whether the canonical ID equals the client ID. Prefer equality to
  avoid an alias table and rewriting foreign keys.

Do not “fix” this only by creating online first. That would break the stated
offline-first contract.

### AD-2: Dual ink representation with one commit record

Preserve both:

- Native `PKDrawing` bytes for lossless PencilKit reopening.
- Canonical `ink_v1` strokes for backend recognition and non-Apple clients.

One page revision record must bind:

```text
revision_id
document_id
page_id
local_sequence
pkdrawing_path + SHA-256
ink_v1_path + SHA-256
thumbnail_path + SHA-256 (optional/derived)
created_at / committed_at / uploaded_at
upload_state / attempt_count / last_error
base_server_revision
```

Enumerate each `PKStroke` and its `PKStrokePath` points into `InkStroke` and
`InkPoint`, preserving location, time offset, force, azimuth, altitude, ink
type/color/width, and transforms supported by the canonical schema. Round-trip
tests must use non-empty multi-stroke fixtures and tolerances defined by the
format, not screenshot similarity.

Perform a crash-safe commit:

1. Snapshot the drawing on the main actor.
2. Serialize native and canonical data off the UI thread into temporary files.
3. `fsync`/atomically replace both files.
4. Commit the SQLite revision and dirty-upload record in one transaction after
   both files exist.
5. Retain the previous known-good revision until the new commit is complete.
6. Generate thumbnails at low priority and never block ink capture.

### AD-3: Distinguish local save, metadata sync, and file transfer

Use separate durable queues and visible states:

- `document_operations`: small ordered metadata/page operations.
- `ink_uploads`: immutable page revision files and resumable upload state.
- `asset_imports`: PDFs/images from extension, Files, Photos, or scanner.
- `conversion_jobs`: submitted backend jobs and resumable result state.

Each queue needs pending/running/retryable-failed/permanent-failed/cancelled/
complete states, attempt count, next retry time, structured error code, and
user-visible recovery action. A global “Sync” operation coordinates these
queues but does not erase their identity.

### AD-4: Remote bootstrap and reconciliation

Sync pull operations alone do not describe a notebook that is absent locally.
Implement one of the following, in preference order:

1. Sync snapshots that contain complete library/page metadata at a cursor.
2. Paginated `GET /documents` reconciliation followed by operation pull.
3. A create operation whose payload is sufficient to instantiate the local
   projection, followed by periodic reconciliation as a safety net.

On login/new device, register the sync client, reconcile the library, download
page manifests lazily, then pull operations. Acknowledge the cursor only after
changes are durably committed locally. Handle stale cursors by applying server
snapshots rather than presenting an opaque 409 error.

### AD-5: Page and asset model

Normalize pages locally instead of storing the entire page array as one JSON
blob. Add tables for:

- notebooks/documents;
- pages with stable IDs, fractional/order keys, dimensions, template, rotation,
  source asset, and active local/server ink revision;
- immutable page revisions;
- folders and notebook-folder relationships;
- tags and notebook-tag relationships;
- favorites/pins and local display preferences;
- transfer/import/conversion jobs;
- operation errors and conflicts.

Provide additive, versioned SQLite migrations with backup/recovery tests. Do
not delete existing `local_documents.pages` until migration verification and
rollback behavior are proven.

### AD-6: Server capability and environment reporting

Add a lightweight authenticated/unauthenticated capability response describing
API version, upload limits, supported recognition paths, model readiness, and
feature flags. The native app should distinguish:

- backend unreachable;
- authentication required/expired;
- sync available;
- upload unavailable;
- conversion endpoint available but model not ready;
- incompatible API contract.

- [x] Development settings permit a validated API URL and “Test Connection.”
  Release builds require HTTPS for non-loopback endpoints and the health probe
  does not attach account credentials. A production allowlist is still a
  deployment responsibility.

## 6. Phased implementation plan

### Milestone 0 — Lock contracts and protect existing data (P0)

Goal: establish data invariants before changing the user interface.

#### Backend and shared contracts

- [x] Choose and document the client-assigned document/page ID contract from
  AD-1.
- [x] Add an idempotent offline document-create path and include it in sync
  pull/snapshot history.
- [x] Define page create/rename/reorder/duplicate/delete operations and their
  conflict behavior through stable IDs, optimistic local projection, server
  versioning, and durable conflict records.
- [x] Define immutable ink revision upload and manifest responses, including
  digest deduplication, maximum size, resumable state, and active revision.
  Direct and resumable bounded revision uploads, manifest responses,
  deduplication, stable page binding, and exact active-revision conversion
  binding are implemented and contract-tested.
- [x] Add capability/model-readiness response and structured error codes; the
  iPad health probe and unavailable-model state are contract-tested.
- [x] Contract-test the Swift `Codable` API models against OpenAPI with
  `scripts/check_swift_api_models.py`, including explicit schema aliases and
  Foundation-compatible acronym handling for `ID`, `IDs`, and `SHA256`.

#### Local persistence

- [x] Add schema migrations and normalized pages/revisions/transfer queues.
  Schema version 3 creates normalized page rows, immutable local revision
  records, and stable transfer jobs additively; page edits soft-delete stale
  projection rows, revision commits and their ink transfer intent are written
  in one transaction, source/ink upload state updates reuse stable IDs, and
  purge acknowledgement removes dependent rows only after cleanup. Package
  tests cover legacy backfill, reopen/retry transitions, and page-purge
  cleanup.
- [x] Implement crash-safe dual-format ink commits.
- [x] Convert real PencilKit strokes into non-empty canonical `ink_v1`.
- [x] Add a persistent save-failure journal and recovery on launch.
- [x] Copy share-extension sources into app-owned durable storage before
  acknowledging/removing the inbox item.
- [x] Add client-side size/free-space checks and preserve the staged source on
  failure; server account quotas remain authoritative.
- [x] Queue acknowledged document/page deletion cleanup so local ink revisions,
  recovery journals, and app-owned imported sources are removed only after the
  server operation is accepted.

#### Automated proof

- [x] Add cross-language non-empty `ink_v1` golden fixtures.
- [x] UI-test document creation offline, app termination, and relaunch
  persistence. Focused tests also cover reconnect failure/retry, idempotent
  immutable ink download, second-client bootstrap, ID equality, and exact ink
  restoration; a signed physical two-device run remains open.
- [x] Test interrupted dual-file commits at every boundary with debug-only
  fault injection and prove the last complete revision (or the fully published
  new revision) reopens without a hybrid native/canonical pair.
- [ ] Test share-import crashes before copy, after copy, during upload, after
  server acceptance, and before inbox cleanup.
- [ ] Before beta release, run a bounded randomized save/crash/recovery
  campaign. Start with 100 sequences and inspect every failure; scale toward
  10,000 only after the smaller run is clean and disk/time limits plus automatic
  artifact cleanup are enforced. This is deferred during alpha iteration.

Exit gate:

- A locally created notebook and non-empty drawing appear on a second client
  with stable document/page IDs.
- Force-quit at any tested commit step preserves the prior or new complete
  drawing, never a partial hybrid.
- An imported source remains recoverable until durable local ingestion and
  server acknowledgement satisfy the retention policy.

### Milestone 1 — Complete account entry and first-run experience (P0)

Goal: let users start and recover without visiting the web app.

#### Native UI

- [x] Replace the inline login form with an `AuthFlow` containing Welcome,
  Create Account, Sign In, and password-recovery states.
- [x] Add username, email, password, confirmation, show/hide password, submit
  progress, inline validation, keyboard submit, and safe retryable error
  display; field-specific server mapping remains open.
- [x] Wire `AppModel.register(...)` to the existing client method, install the
  returned session in Keychain, and transition into onboarding/library.
- [x] Preserve user-entered fields after retryable network failures and clear
  password fields after successful authentication.
- [x] Add password-reset request/confirm UI against the typed backend
  contract. Keep request responses non-enumerating, accept one-time tokens,
  and expose a development debug token only when the server returns it.
- [ ] Add email verification against a real delivery and token policy before
  release; do not link to a dead web route.
- [x] Decide and implement local guest mode. Prompt for account creation when
  the user first asks to sync, convert, or open notes on the web.
- [x] Provide a deliberate guest-to-account merge that attaches local stable
  IDs without duplication after the user confirms the upload; deferring keeps
  the pending operations local for a later explicit sync.
- [x] Add session-expired reauthentication that leaves local notes available.
- [x] Add Account actions for data export and account deletion with explicit
  consequences and reauthentication as required.
- [x] Keep session list/revocation and label the current device clearly.
- [x] Make onboarding optional and contextual: create a notebook, write a
  stroke, add a page, and explain local-save versus sync state.

#### Security and policy

- [ ] Keep tokens in Keychain and add refresh-token rotation/device metadata
  according to the platform security roadmap.
- [ ] Rate-limit and normalize register/recovery errors without account
  enumeration.
- [ ] Decide whether passkeys and Sign in with Apple are launch requirements or
  post-alpha enhancements. If third-party sign-in is added, implement Sign in
  with Apple consistently with App Store rules.
- [ ] Link actual Terms and Privacy documents before release; record consent
  only where legally/product-required.

#### Tests

- [x] Unit-test username/email/password/reset-token validation; full SwiftUI
  state-transition and backend-auth integration coverage remains open.
- [x] Integration-test register → authenticated `me` → session restore → revoke
  through the native transport and durable session vault.
- [x] UI-test the welcome/auth controls and offline notebook creation path;
  duplicate username/email, invalid credentials, expired token, sign out, and
  guest upgrade remain open.
- [ ] Verify Password AutoFill content types and hardware-keyboard navigation.

Exit gate:

- A new user can create an account entirely on iPad and relaunch authenticated.
- A returning user can sign in and recover from an expired session without
  losing local access.
- The app either offers functional guest mode or truthfully explains why an
  account is required.

### Milestone 2 — Trustworthy multipage writing alpha (P0/P1)

Goal: make the app useful for a real class or meeting before adding conversion.

#### Editor shell

- [x] Replace `NotebookCanvasView` with `NotebookEditorView` and a
  `NotebookEditorModel` scoped to one notebook.
- [x] Add page thumbnail navigation, current-page indicator, add, duplicate,
  reorder, and explicit delete with confirmation. Rotate and delete-with-undo
  remain open.
- [x] Autosave on drawing changes, page transitions, scene backgrounding, and
  editor close; memory warnings trigger the same local flush boundary.
- [x] Show separate local-save and sync states near the notebook title; expose
  details and retry when attention is required.
- [x] Add explicit undo/redo with disabled state and keyboard shortcuts.
- [x] Support Pencil and optional touch drawing, with a discoverable toggle.
- [x] Preserve pan/zoom and add fit-page/reset zoom.
- [x] Keep the editor usable in regular and compact horizontal size classes:
  compact windows move the page navigator above the canvas, while the canvas
  keeps its page coordinate space. Physical rotation, Stage Manager, and
  Pencil coordinate verification remain open.

#### Drawing tools

- [x] Keep the system tool picker where it provides a complete experience;
  supplement it with app commands instead of cloning it.
- [x] Add five persistent pen/highlighter/eraser presets with color and width.
- [ ] Add lasso selection with move, resize if safely representable, duplicate,
  cut/copy/paste, delete, and convert-selection actions. The native PencilKit
  lasso tool and native cut/copy/paste commands are exposed and UI-tested;
  selection extraction/transform/delete/convert behavior remains open.
- [x] Expose vector eraser behavior supported by the deployment target; pixel
  erasing remains open until its round-trip semantics are verified.
- [x] Add an explicit Ruler command backed by the supported PencilKit ruler;
  shape recognition remains open until its round-trip behavior meets the iOS
  17 compatibility floor.
- [x] Respect Apple Pencil double-tap/squeeze capabilities when available and
  provide ordinary toolbar alternatives. System-configured eraser/previous
  tool actions and native picker visibility are bridged; physical Pencil
  validation remains part of device testing.
- [ ] Add left/right palette placement and palm-rejection guidance.

#### Paper and pages

- [x] Add blank, ruled, grid, dot-grid, and Cornell templates as derived
  backgrounds, not flattened ink.
- [x] Add common page sizes plus orientation and a simple custom size path.
- [ ] Add notebook covers and first-page thumbnails.
- [x] Keep templates changeable without modifying stroke coordinates.

#### Tests and performance

- [x] UI-test add/duplicate/reorder/delete page actions, offline notebook
  creation, and app relaunch persistence; drawing restore and force-quit fault
  coverage remain open.
- [ ] Test undo/redo across autosave boundaries and define whether history is
  session-only or persisted.
- [ ] Measure Pencil latency and frame hitches while save, thumbnail, and upload
  work occurs.
- [ ] Test hundreds of strokes per page and 100+ page notebooks on the device
  matrix in the platform roadmap.

Exit gate:

- Zero lost strokes across ordinary drawing, page switching, backgrounding,
  force-quit, low storage, and reconnect scenarios.
- Page operations converge across two clients and conflicts preserve both
  recoverable outcomes.
- Drawing stays responsive while save/thumbnail/upload work is active.

### Milestone 3 — Durable import, annotation, and export (P1)

Goal: support the workflows that move real school/work material through a note
app.

#### Import

- [x] Build one `ImportCoordinator` used by share extension, Files,
  `PhotosPicker`, and drag-and-drop; optional document scanner entry points
  remain open.
- [x] Use security-scoped resource access correctly for selected files and copy
  into the durable import store before parsing.
- [x] Validate the selected source's readable size and local storage
  availability before staging; signature/media/page/dimension validation still
  needs to move ahead of expensive decoding for the remaining import sources.
- [x] Generate a real page per PDF page/image with source dimensions and
  thumbnails.
- [x] Display local importing progress with per-file counts, cancel, durable
  local failure records, retry, and safe cleanup. Queued/uploading/processing
  reconciliation and remote retry state remain open.
- [x] Let the user choose new notebook versus insert into the current notebook
  from Files or Photos. Imported pages retain page-specific PDF/image source
  bindings, and native archive round trips preserve mixed-source notebooks.
- [x] Keep the source PDF/image as a non-destructive page underlay and draw ink
  in a separate layer.

#### Export and sharing

- [x] Export the current page or notebook as PDF with background, source asset,
  and ink rendered consistently.
- [x] Export original source when available, PNG/JPEG page images, a native
  notebook archive, and canonical `ink_v1`; converted Markdown/LaTeX remains
  dependent on recognition output.
- [x] Present local exports through the standard iPad share sheet.
- [x] Add a Files exporter for PDF and native archive artifacts; cancellation
  is handled by the system document exporter and errors remain user-visible.
- [x] Define a versioned native notebook archive containing manifests, source
  assets, native drawings, canonical ink, and SHA-256 checksums for
  backup/migration.
- [x] Import `.hndnote` packages through the shared coordinator with schema and
  version checks, safe-path validation, checksum/size validation, page identity
  remapping, source validation, per-page source preservation, and iPad
  round-trip tests; future archive schema versions remain explicitly rejected
  until supported.
- [x] Add print support after PDF export became deterministic; the editor sends
  the generated notebook PDF to `UIPrintInteractionController`.

#### Tests

- [x] UI-test that PDF, PNG, JPEG, canonical `ink_v1`, and native archive
  export actions are discoverable from the editor and that PDF opens/closes the
  system share controller with a generated artifact.
- [ ] Test 1-page/100-page PDFs, rotated pages, large images, malformed files,
  password-protected PDFs, cancellation, low storage, and app termination.
- [x] Runtime-test each export artifact, including source-unavailable and
  partially-saved-ink failures; iPad-targeted tests open generated PDFs,
  verify deterministic PNG/JPEG dimensions/signatures, decode canonical ink,
  round-trip native archives, preserve original source bytes, and assert
  explicit failure behavior; the focused iPad run also verifies a 100-page
  notebook PDF and alternating rotated page dimensions.
- [ ] Pixel-diff representative PDF exports and validate page count, dimensions,
  searchable text, and source/ink alignment.
- [ ] Verify cleanup never removes the last durable copy of an import.

Exit gate:

- Every accepted import either becomes a complete notebook or remains visibly
  retryable with its source intact.
- Exported PDFs match the editor's page order, backgrounds, source assets, and
  ink alignment.

### Milestone 4 — Library organization and findability (P1)

Goal: keep hundreds of notebooks and pages manageable.

#### Library

- [x] Add grid/list display with persisted preference and useful cover/page
  thumbnails.
- [x] Add persisted sort by updated, created, and title. Manual/favorite order
  remains open with the organization model.
- [x] Add rename, duplicate, favorite, archive, trash, and restore to the
  single-notebook context menu and lifecycle actions. Favorite state is stored
  locally, synced through `document.favorite`, included in the server document
  contract, and exposed through the Favorites library view.
- [x] Add move and permanent delete to context menus and add multi-select
  actions. Permanent deletion is server-acknowledged and drains a durable local
  purge queue for ink, recovery, and app-owned import files.
- [x] Add folders with nesting limited to a tested depth and a move sheet;
  drag-and-drop placement remains open.
- [x] Add tags and smart views for favorites, recent, imported, unsynced, and
  needs-attention items.
- [x] Define trash retention as manual-only and communicate that permanent
  deletion is an explicitly confirmed, account-wide action after server
  acceptance; automatic retention is intentionally not enabled.
- [x] Add pagination/incremental loading and stable selection; thumbnail cache
  eviction remains open.

#### Search

- [x] Search local notebook title, source metadata, and page titles; show the
  matching page and open it as the editor's initial page from library results.
- [ ] Phase search as title/metadata → recognized text → handwriting index →
  attachment OCR/object metadata, depending on available models.
- [ ] Add in-library scopes and in-notebook search with page/result previews.
- [ ] Selecting a result must open the exact page and highlight source strokes
  or source crop without modifying ink.
- [ ] Make offline index availability and stale/incomplete results explicit.

#### Tests

- [ ] Verify folder/tag/favorite mutations offline and across two clients.
- [ ] Test library performance at 1,000 notebooks and thumbnail-cache pressure.
- [x] Test Unicode page titles and search-result navigation, including
  diacritic-insensitive lookup; Unicode tags, empty states, and duplicate-name
  coverage remain open.

Exit gate:

- A user can find a target page in a 100-page notebook and a target notebook in
  a 1,000-item library using accessible controls.
- All organizational operations survive offline edits and converge or surface
  a resolvable conflict.

### Milestone 5 — Source-linked conversion and review (P1)

Goal: deliver the differentiated workflow only after original notes are safe.

- [ ] Add Convert Selection from lasso regions.
- [x] Convert Page and bounded Convert Notebook actions reuse the per-page
  exact-revision path and cap notebook conversion at 100 pages; imported pages
  without local ink use the attached raster source as a safe fallback.
- [x] Upload/reuse the exact immutable ink revision being converted and record
  its digest and coordinate transform through the page manifest request.
- [x] Show queued/running/partial/failed/cancelled/complete status per page and
  persist/resume non-terminal runs after relaunch.
- [x] Gracefully use raster fallback for imported pages without local ink and
  show model unavailability as a real error state; unsupported ink formats
  remain open.
- [ ] Present converted blocks beside or over the source with one-tap source
  highlighting.
- [ ] Add critical-symbol and uncertainty review for signs, digits/letters,
  inequalities, scripts, brackets, fractions, and truncation.
- [ ] Add accept, edit, split, merge, reorder, exclude, and retry-region actions.
- [ ] Ensure review edits create immutable revisions and all exports consume the
  same active revision.
- [ ] Add handwriting/full-content search only from reviewed or explicitly
  labeled provisional output.
- [ ] Add personalization consent, learned-example inspection, per-note
  exclusion, reset/delete, and truthful evidence of model assignment.

Exit gate:

- Conversion never changes original ink and every output block can identify
  its source strokes/crop.
- Users can finish review/export if any one recognizer is unavailable.
- Private holdout evaluation shows a defined improvement before the UI claims
  that recognition is personalized.

### Milestone 6 — Advanced note-app parity, only after measured demand (P2)

Candidates, in recommended order:

1. Typed text boxes, images, links, and shapes on a page.
2. Checklists and simple structured typed pages.
3. Reusable elements/stickers made from selected ink or images.
4. Audio recording with permission, waveform, transcript, and timestamped
   playback linked to ink events.
5. Quick Note/shortcut/widget entry points.
6. Collaboration, comments, sharing permissions, and live presence.
7. Study sets, flashcards, spaced repetition, and presentation mode.

Each candidate needs a product metric and data/conflict model before entering
an implementation milestone. Audio and collaboration materially expand privacy,
storage, background, and synchronization risk; they are not harmless toolbar
additions.

### Milestone 7 — Accessibility, reliability, and release hardening (cross-cutting)

Run this work throughout earlier milestones and close all gates before beta:

- [ ] Add semantic labels, values, hints, headings, focus order, and custom
  actions for canvas-adjacent controls.
- [ ] Provide non-canvas alternatives for page management and conversion review.
- [ ] Verify VoiceOver, Full Keyboard Access, Switch Control, Voice Control,
  pointer, 200% text, increased contrast, reduced motion, and color-independent
  status.
- [x] Add keyboard shortcuts for new/import, undo/redo, page navigation,
  zoom, export, and in-app pen/pencil/marker/eraser selection; search/open
  focus shortcuts remain open.
- [ ] Instrument content-free launch, save latency, sync queue age, transfer
  failures, recognition state, and crashes. Never log note text, ink coordinates,
  filenames, tokens, or source images.
- [ ] Add diagnostics export with explicit redaction and user consent.
- [ ] Verify Data Protection file classes, Keychain accessibility, backup
  exclusions for rebuildable caches, and backup inclusion for durable local
  notes according to product policy.
- [ ] Finalize bundle ID, App Group, Apple team, icons, privacy manifest,
  permission strings, versioning, and release configuration.
- [ ] Test clean install, migration from every shipped schema, update during
  pending transfers, account switch, account deletion, and reinstall.
- [ ] Complete physical-device testing across the matrix in the platform plan.

Release gates:

- Zero confirmed note/stroke loss in the defined fault campaign.
- Same-account cross-client convergence meets the sync SLO.
- No high-severity accessibility blocker in the core flow.
- No credential or note content in logs/analytics.
- Recognition availability and personalization claims match deployed evidence.
- App Store account-deletion, privacy, and signing requirements are satisfied.

## 7. File-level implementation map

The exact split may evolve, but keep feature state out of a single expanding
`AppModel`.

```text
ios/Sources/PersonalizedNotesApp/
├─ App/
│  ├─ PersonalizedNotesApp.swift
│  ├─ AppCoordinator.swift
│  └─ AppEnvironment.swift
├─ Authentication/
│  ├─ AuthFlowView.swift
│  ├─ AuthViewModel.swift
│  ├─ RegistrationView.swift
│  ├─ SignInView.swift
│  └─ AccountRecoveryView.swift
├─ Library/
│  ├─ LibrarySplitView.swift
│  ├─ LibraryViewModel.swift
│  ├─ NotebookGridItem.swift
│  ├─ LibrarySidebar.swift
│  └─ MoveNotebookView.swift
├─ Editor/
│  ├─ NotebookEditorView.swift
│  ├─ NotebookEditorModel.swift
│  ├─ PageNavigatorView.swift
│  ├─ PencilCanvasRepresentable.swift
│  ├─ DrawingToolbarView.swift
│  └─ SaveSyncStatusView.swift
├─ Import/
│  ├─ ImportCoordinator.swift
│  ├─ ImportProgressView.swift
│  └─ ImportSourcePicker.swift
├─ Review/
│  ├─ ConversionCoordinator.swift
│  ├─ ConversionReviewView.swift
│  └─ SourceHighlightView.swift
└─ Settings/
   ├─ AccountView.swift
   ├─ SyncIssuesView.swift
   └─ SettingsView.swift

ios/Sources/PersonalizedNotesKit/
├─ API/                  # generated/checked transport contracts
├─ Domain/               # notebook, page, revision, import, transfer
├─ Ink/                  # PKDrawing adapter kept in iOS-capable target
├─ Persistence/          # migrations and repositories
├─ Sync/                 # operation, bootstrap, conflict, scheduler
└─ Transfer/             # foreground/background uploads and downloads
```

Recommended ownership changes:

- Keep `SharedBackendClient` as transport; do not put orchestration in it.
- Split `AppModel` into an app/session coordinator and feature-specific models.
- Make repositories the only code allowed to mutate SQLite/file commit state.
- Introduce protocols around clock, reachability, filesystem, transfer session,
  and API transport to make failure testing deterministic.
- Add a dedicated iOS unit/UI test target in `project.yml`; do not rely only on
  macOS-compatible Swift Package tests.
- Keep the share extension small: stage and report. Parsing, document creation,
  upload, and cleanup belong to the host app's durable import coordinator.

## 8. Required backend/API additions

This is a planning contract, not a final endpoint specification:

| Need | Contract change |
| --- | --- |
| Offline create | Idempotent client-assigned document/page IDs and create change/snapshot. |
| Page lifecycle | Add/create/reorder/duplicate/delete/restore operations with stable order keys. |
| Ink sync | Immutable revision manifest plus resumable file upload, digest dedup, activation, and tombstone behavior. |
| New-device bootstrap | Snapshot or document/page reconciliation plus cursor acknowledgement. |
| Import | Durable upload completion followed by idempotent PDF/image ingest job and progress. |
| Search | Scoped result endpoint returning document/page/source coordinates and index status. |
| Conversion | Immutable source revision, region/stroke selection, job state, partial results, cancellation, and capability errors. |
| Export | Revision-bound export job and deterministic artifact metadata. |
| Auth recovery | Password reset, email verification policy, rotated refresh credentials, and structured errors. |
| App readiness | Capability/version/model-readiness endpoint and supported limits. |
| Account rights | Native-compatible data export and account deletion with status/retention. |

Every mutating request needs an idempotency key, owner scoping, bounded payload,
structured error code, retry classification, and contract test in Python and
Swift.

## 9. Test strategy and evidence matrix

| Layer | Required coverage |
| --- | --- |
| Pure/unit | Form validation, reducers/state machines, order-key generation, ink conversion, digesting, coordinate transforms, error mapping. |
| Persistence | Every migration, atomic commit recovery, queue transitions, retry schedules, quota errors, file/row consistency. |
| Contract | OpenAPI fixtures, Python↔Swift JSON, non-empty ink golden files, capability/version negotiation. |
| Integration | Real local backend: register, create, draw, upload, pull to second client, conflict, import, convert, export, delete. |
| UI | First run, signup, guest upgrade, library organization, drawing restore, pages, import retry, sync issues, review, export. |
| Fault injection | Kill app/process around each commit/transfer boundary; network loss, 401, 409, 413, 429, 5xx, corrupt cache, low disk. |
| Performance | Pencil latency, main-thread stalls, save/thumbnail/upload contention, memory/battery/thermal, large library/notebook. |
| Accessibility | Automated identifiers/audits plus manual VoiceOver, keyboard, pointer, Switch Control, Dynamic Type, contrast, handedness. |
| Security/privacy | Tenant isolation, token storage/rotation, malicious import, URL allowlist, log/analytics redaction, deletion/export. |

Maintain a release evidence folder containing test command, commit SHA, device/
OS, fixture set, result, known deviations, and owner. Simulator success is not
physical-device evidence.

For local Xcode campaigns, reuse one task-specific DerivedData directory, keep
generated test artifacts below 2 GB, and clean them after recording the result.
Abort the campaign if free disk space falls below 10 GB. Do not retain a result
bundle for a high-iteration passing run unless it is needed to diagnose a
failure.

## 10. Recommended implementation sequence

Current handoff priority:

1. Make the existing alpha branch reviewable and run the bounded user journey.
2. Close share-import crash-retention gaps; focused second-client
   bootstrap/reconnect proof is complete, with physical multi-device validation
   retained as a beta gate.
3. Validate Pencil behavior and accessibility on a physical iPad.
4. Build conversion review around a useful model or deterministic fixture.
5. Defer scale, soak, and broad fault campaigns to beta hardening with resource
   limits and cleanup in place.

The dependency order is intentional:

```text
Stable IDs + normalized pages
        ↓
Atomic native/canonical ink revisions
        ↓
Durable queues + remote bootstrap
        ↓
Signup/guest onboarding ──→ multipage editor
        ↓                         ↓
Durable imports ───────────→ library/search/export
                                  ↓
                         source-linked conversion
                                  ↓
                    advanced parity + collaboration
```

Suggested pull-request slices:

1. Contract decision record and OpenAPI tests for offline create/bootstrap.
2. SQLite migration framework and normalized page/revision repositories.
3. PencilKit-to-`ink_v1` adapter plus atomic dual-format commit.
4. Durable ink transfer queue and sync coordinator.
5. Remote reconciliation/new-device bootstrap.
6. Auth flow and native registration.
7. Guest mode/account attachment, if selected.
8. Editor shell and page navigator.
9. Save/sync status and issue center.
10. Drawing-tool/paper-template improvements.
11. Durable import coordinator and share-extension handoff.
12. PDF/image underlays and deterministic export.
13. Library split view, folders/tags/favorites, and search scopes.
14. Conversion selection and resumable job state.
15. Review/source highlighting and native conversion export.
16. Physical-device hardening and beta release evidence.

Each slice should include migrations/contracts, implementation, tests, and a
small operator/user note. Avoid a large visual redesign PR before persistence
and sync invariants are covered.

## 11. Caveats and explicit product decisions

Resolve these decisions before their dependent milestone; do not silently make
them in view code:

1. **Guest mode:** recommended because local writing already exists and account
   gating contradicts current copy. If rejected, document why an account is
   essential before first use.
2. **Document identity:** recommended client UUID equals canonical UUID.
3. **Minimum OS:** retain iPadOS 17 unless a measured PencilKit/API benefit
   justifies raising it; gate newer Pencil features by availability.
4. **Storage policy:** define whether durable local-only notes are included in
   device backups and what happens after sign-out/account deletion.
5. **Trash retention:** define time window, server tombstone duration, local
   cache cleanup, and permanent-delete warning.
6. **Folder model:** single parent is simpler; tags cover cross-cutting groups.
   Do not build multi-parent folders unless research proves the need.
7. **PDF semantics:** imported PDF is an immutable source layer; annotations are
   separate and export composites them.
8. **Undo persistence:** session-only is acceptable if explicitly tested and
   communicated; note content itself must persist.
9. **Recognition availability:** never return an empty successful result when
   models are missing. Expose capability and retry/fallback state.
10. **Collaboration:** defer until operation semantics preserve independent
    page/ink edits and permissions are fully designed.
11. **Audio:** defer until privacy, background recording, synchronization,
    transcript retention, and storage costs are approved.
12. **Physical device endpoint:** define a secure development endpoint; never
    ship arbitrary HTTP or loopback defaults in release configuration.

## 12. Benchmarks used, without copying competitor scope

The plan uses current first-party product documentation as expectation-setting,
not as a mandate to clone every feature:

- Apple Notes supports handwriting, drawings, Scribble/transcription, media,
  scans, checklists, search through handwriting and attachments, tags, and
  Smart Folders.
- Goodnotes documents folders/subfolders, favorites, grid/list organization,
  PDF annotation, lasso/elements, templates, handwriting search, and audio
  linked to notes.
- Notability's current feature catalog includes folders, handwriting/math
  conversion, PDF outlines, media, audio/transcripts, shortcuts, themes, and
  locked folders.
- OneNote's iPad information architecture uses notebooks, sections, and pages
  and exposes touch-drawing/palm-orientation controls.

Primary references:

- [Apple: PencilKit](https://developer.apple.com/documentation/pencilkit)
- [Apple: Managing accounts](https://developer.apple.com/design/human-interface-guidelines/managing-accounts)
- [Apple: Onboarding](https://developer.apple.com/design/human-interface-guidelines/onboarding)
- [Apple: Write and draw with Markup](https://support.apple.com/en-gb/guide/ipad/ipad6350b8dc/ipados)
- [Apple: Handwriting in Notes](https://support.apple.com/en-my/guide/ipad/ipada87a6078/ipados)
- [Apple: Search in Notes](https://support.apple.com/en-ca/guide/ipad/ipad64863a98/ipados)
- [Apple: Tags and Smart Folders](https://support.apple.com/en-gb/guide/ipad/ipadf688557f/26/ipados/26)
- [Apple: Background Tasks](https://developer.apple.com/documentation/backgroundtasks)
- [Apple: SwiftUI file importer/exporter](https://developer.apple.com/documentation/swiftui/view-presentation)
- [Apple: PhotosPicker](https://developer.apple.com/documentation/photosui/photospicker)
- [Goodnotes: Features](https://www.goodnotes.com/features/)
- [Goodnotes: Organize folders and documents](https://support.goodnotes.com/hc/en-us/articles/15303674514191-Organize-folders-and-documents-in-Goodnotes)
- [Goodnotes: Audio recordings](https://support.goodnotes.com/hc/en-us/articles/7352688559631-Add-Audio-Recordings-to-your-documents)
- [Notability: Feature catalog](https://support.gingerlabs.com/hc/en-us/categories/200299988-Features)
- [Microsoft: OneNote notebooks, sections, and pages](https://support.microsoft.com/en-us/onenote/onenote-for-ipad-or-iphone-help-and-learning/create-pages-sections-or-notebooks-in-onenote-for-ipad-or-iphone)
- [Microsoft: OneNote drawing mode](https://support.microsoft.com/en-US/OneNote/onenote-for-ipad-or-iphone-help-and-learning/select-a-drawing-mode-in-onenote-for-ipad-or-iphone)

## 13. Definition of done for the iPad beta

The iPad app is beta-ready only when all of the following are evidenced:

- Native signup/sign-in/recovery policy is complete and truthful.
- Guest/local mode is implemented or intentionally removed with matching copy.
- Every accepted stroke and page mutation has crash/relaunch proof.
- Native and canonical ink contain the same non-empty drawing meaning.
- New notebooks and pages bootstrap correctly on another client.
- Imports retain a recoverable source until durable completion.
- Failed sync/import/conversion work is visible and retryable.
- Multipage navigation, basic tools, organization, search, and PDF export work.
- Conversion is source-linked, non-destructive, and honest about model status.
- Core flows pass the accessibility and physical-device matrix.
- Bundle/App Group/signing, HTTPS environment, privacy disclosures, data export,
  and account deletion are release-ready.
- The broader platform roadmap's P0 and relevant iPad P1 gates remain green.

Until these conditions are met, describe the iPad target as an engineering
alpha rather than a finished note-taking app.
