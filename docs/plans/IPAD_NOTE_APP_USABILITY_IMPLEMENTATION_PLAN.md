# iPad Note App Usability Implementation Plan

Status: proposed implementation plan

Audit date: 2026-09-12

Audited branch: `platform-sync-uploads`

Audited baseline: `67150d9`

Related roadmap: [`PERSONALIZED_NOTES_PLATFORM_REMAINING_TASKS.md`](PERSONALIZED_NOTES_PLATFORM_REMAINING_TASKS.md)

## 1. Executive decision

The iPad target is a working PencilKit shell and useful architectural spike, not
yet a dependable note-taking alpha. It can authenticate an existing account,
show locally cached notebooks, create a local notebook, persist a native
`PKDrawing`, and enqueue a few document lifecycle operations. It cannot yet
guarantee that a newly created notebook, its pages, or its ink reach another
device. The current share-import path also removes its staged source after
creating only a local placeholder.

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
| App shell | `RootView` routes between loading, login, library, and one notebook. | Simple SwiftUI state flow that can be split into feature coordinators. |
| Authentication | Username/password sign-in and Keychain-backed session restore. | `SharedBackendClient.register(...)` already implements the backend registration call. |
| Library | Local list, title search, active/archive/trash picker, create action, lifecycle swipe actions, and sync summary. | SQLite library projections and pending operation storage exist. |
| Drawing | `PKCanvasView`, system `PKToolPicker`, Pencil-only drawing, pan/zoom, and local native `PKDrawing` persistence. | PencilKit provides low-latency capture and a native lossless representation. |
| Sync | Durable document-operation queue, idempotency keys, cursors, and local conflict records. | A sound foundation for metadata sync once creation and content sync are added. |
| Share extension | Accepts PDFs and images into an App Group inbox. | Correct process boundary for share-sheet ingestion. |
| Account | Current user display, session list, and session revocation. | Useful security management surface. |
| Settings | Wi-Fi-only upload toggle, personalization consent acknowledgement, client ID, and API host display. | Initial privacy and network controls. |
| Tests | Ink golden test, local-store persistence test, and fake-service sync tests. | Demonstrates that the shared package is testable without a device. |

### 3.2 Confirmed usability and integrity gaps

#### Account entry

- `RootView` exposes only **Sign In**. There is no registration form, even
  though `SharedBackendClient.register(username:email:password:)` exists.
- Registration is therefore not conceptually web-only; it is merely missing
  from the iPad UI and `AppModel`.
- There is no forgot-password, email-verification, password visibility,
  password rules, field-level validation, request progress, or recovery path
  for an expired/revoked session.
- The login footer says sign-in is required only for sync, but `AppModel.start()`
  routes users without a session back to login and prevents local library use.
- Account deletion/export, password change, and privacy controls are not
  available in the native account screen.

#### Notebook creation and cross-device identity

- `AppModel.createNotebook` writes a randomly identified `DocumentRecord`
  directly to SQLite and does not call `POST /documents` or enqueue a create
  operation.
- `POST /sync/push` rejects operations for documents absent from the server.
- `POST /documents` currently generates the server document ID rather than
  accepting the client's durable ID. A local notebook therefore needs an
  explicit ID reconciliation mechanism or a new client-assigned-ID contract.
- Pulled operations are projected only onto an already-present local document.
  Pulling a change is not sufficient to discover a remotely created notebook.
- Result: archive/trash/rename sync cannot make a locally created notebook
  whole on another client until creation and snapshot/bootstrap semantics are
  defined.

#### Ink durability and portability

- Drawing changes are debounced for 50 ms and written to native `PKDrawing`
  and an `ink_v1` file.
- The `ink_v1` bundle is currently written with `strokes: []` while claiming
  pressure, azimuth, altitude, and timing capture. The app-neutral canonical
  data is therefore not an actual representation of the drawing.
- Drawing-save errors are swallowed. There is no save-state indicator,
  persistent failure record, retry action, or recovery UI.
- Ink is never placed in a durable upload queue and `uploadInkBundle` is not
  called by the app.
- There is no revision protocol tying native bytes, canonical strokes,
  thumbnail, upload digest, and server acknowledgement together.

#### Pages and editing

- `NotebookCanvasView` always opens the first page. It has no page navigation,
  insertion, deletion, duplication, reordering, or thumbnails.
- The page is a fixed white 768×1024 canvas. There are no paper templates,
  page size/orientation controls, covers, PDF underlay, or per-page metadata.
- Drawing is hardcoded to `.pencilOnly`. This is good for accidental-touch
  prevention but makes the simulator and users who draw by touch appear
  broken, and there is no setting or contextual explanation.
- The system tool picker supplies basic tools, but the app has no explicit
  undo/redo, reusable pen presets, custom palette, lasso command surface,
  eraser mode control, ruler/shape affordance, zoom reset, or left-handed
  toolbar layout.
- There are no typed text boxes, images, links, checklists, shapes, attachments,
  or audio. These are later parity items, not data-integrity blockers.

#### Share and import

- The extension writes a manifest and source bytes to the App Group.
- The host app creates one placeholder document and then calls
  `markConsumed`, which deletes the staged manifest and source file.
- The source bytes are not copied to app-owned durable storage, uploaded, or
  turned into real pages before deletion. This can lose the only imported copy
  known to the app.
- There is no in-app Files picker, Photos picker, document scanner, import
  preview, multi-page progress, cancellation, retry, or explanation of format
  limits.

#### Library and discovery

- The library is a flat text list. Search matches titles only.
- There are no folders/sections, tags, favorites, pinning, recent items, grid
  view, covers/thumbnails, sorting, multi-select, batch actions, rename,
  duplicate, move/copy, or permanent-delete flow.
- Pagination fields exist in the shared model but are not exposed as an
  infinite or paged library experience.
- Failed queued operations and conflicts are stored but not shown. The visible
  count covers pending operations only, so work can disappear from the user's
  mental model after a failed attempt.

#### Conversion and personalization

- The iPad app has no selected-stroke, region, page, or notebook conversion
  action and no review UI.
- There is no handwriting/full-content search, converted-block editor,
  uncertainty queue, source highlighting, or native export.
- Recognition model artifacts are not present in the current development
  environment. The UI must show recognition as unavailable rather than imply
  that empty or fallback output is a successful conversion.

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

- The package has seven tests and no app UI test target.
- There are no automated registration, import, canvas restore, multipage,
  conflict, background transfer, token-expiry, or real-backend integration
  tests.
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

Development settings should permit a validated API URL and “Test Connection.”
Release builds must use an HTTPS allowlisted endpoint and must not expose an
arbitrary-host credential exfiltration path.

## 6. Phased implementation plan

### Milestone 0 — Lock contracts and protect existing data (P0)

Goal: establish data invariants before changing the user interface.

#### Backend and shared contracts

- [ ] Choose and document the client-assigned document/page ID contract from
  AD-1.
- [ ] Add an idempotent offline document-create path and include it in sync
  pull/snapshot history.
- [ ] Define page create/rename/reorder/duplicate/delete operations and their
  conflict behavior.
- [ ] Define immutable ink revision upload and manifest responses, including
  digest deduplication, maximum size, resumable state, and active revision.
- [ ] Add capability/model-readiness response and structured error codes.
- [ ] Generate or contract-test the Swift API models against OpenAPI.

#### Local persistence

- [ ] Add schema migrations and normalized pages/revisions/transfer queues.
- [ ] Implement crash-safe dual-format ink commits.
- [ ] Convert real PencilKit strokes into non-empty canonical `ink_v1`.
- [ ] Add a persistent save-failure journal and recovery on launch.
- [ ] Copy share-extension sources into app-owned durable storage before
  acknowledging/removing the inbox item.
- [ ] Add quota/free-space checks and preserve the staged source on failure.

#### Automated proof

- [ ] Add cross-language non-empty `ink_v1` golden fixtures.
- [ ] Test document creation offline, app termination, reconnect, idempotent
  retry, second-client bootstrap, and ID equality.
- [ ] Test interrupted dual-file commits at every boundary and prove the last
  complete revision reopens.
- [ ] Test share-import crashes before copy, after copy, during upload, after
  server acceptance, and before inbox cleanup.
- [ ] Run at least 10,000 randomized save/crash/recovery sequences with zero
  confirmed stroke loss.

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

- [ ] Replace the inline login form with an `AuthFlow` containing Welcome,
  Create Account, Sign In, and recovery states.
- [ ] Add username, email, password, confirmation, show/hide password, submit
  progress, inline validation, keyboard submit, and server-field error mapping.
- [ ] Wire `AppModel.register(...)` to the existing client method, install the
  returned session in Keychain, and transition into onboarding/library.
- [ ] Preserve user-entered fields after retryable network failures; clear
  password fields after successful authentication.
- [ ] Add forgot-password request/reset and email verification only after the
  backend contract is implemented. Do not link to a dead web route.
- [ ] Decide and implement local guest mode. Prompt for account creation when
  the user first asks to sync, convert, or open notes on the web.
- [ ] Provide a deliberate guest-to-account merge that attaches local stable
  IDs without duplication.
- [ ] Add session-expired reauthentication that leaves local notes available.
- [ ] Add Account actions for data export and account deletion with explicit
  consequences and reauthentication as required.
- [ ] Keep session list/revocation and label the current device clearly.
- [ ] Make onboarding optional and contextual: create a notebook, write a
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

- [ ] Unit-test form validation and state transitions.
- [ ] Integration-test register → authenticated `me` → session restore → revoke.
- [ ] UI-test create account, duplicate username/email, offline retry, invalid
  credentials, expired token, sign out, and guest upgrade.
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

- [ ] Replace `NotebookCanvasView` with `NotebookEditorView` and a
  `NotebookEditorModel` scoped to one notebook.
- [ ] Add page thumbnail navigation, current-page indicator, add, duplicate,
  reorder, rotate, and delete-with-undo.
- [ ] Autosave on drawing changes, page transitions, scene backgrounding, memory
  warnings, and editor close.
- [ ] Show separate local-save and sync states near the notebook title; expose
  details and retry when attention is required.
- [ ] Add explicit undo/redo with disabled state and keyboard shortcuts.
- [ ] Support Pencil and optional touch drawing, with a discoverable toggle.
- [ ] Preserve pan/zoom and add fit-page/reset zoom.
- [ ] Support portrait/landscape, split view, and Stage Manager without losing
  tool state or changing stroke coordinates.

#### Drawing tools

- [ ] Keep the system tool picker where it provides a complete experience;
  supplement it with app commands instead of cloning it.
- [ ] Add three to five persistent pen/highlighter presets with color and width.
- [ ] Add lasso selection with move, resize if safely representable, duplicate,
  cut/copy/paste, delete, and convert-selection actions.
- [ ] Expose object/pixel eraser behavior supported by the deployment target.
- [ ] Add ruler and shape recognition only if PencilKit support and round-trip
  behavior meet the iOS 17 compatibility floor.
- [ ] Respect Apple Pencil double-tap/squeeze capabilities when available and
  provide ordinary toolbar alternatives.
- [ ] Add left/right palette placement and palm-rejection guidance.

#### Paper and pages

- [ ] Add blank, ruled, grid, dot-grid, and Cornell templates as derived
  backgrounds, not flattened ink.
- [ ] Add common page sizes plus orientation and a simple custom size path.
- [ ] Add notebook covers and first-page thumbnails.
- [ ] Keep templates changeable without modifying stroke coordinates.

#### Tests and performance

- [ ] UI-test every page operation and app relaunch.
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

- [ ] Build one `ImportCoordinator` used by share extension, Files,
  `PhotosPicker`, drag-and-drop, and optional document scanner entry points.
- [ ] Use security-scoped resource access correctly for selected files and copy
  into the durable import store before parsing.
- [ ] Validate media type, signature, size, page count, dimensions, and storage
  availability before expensive decoding.
- [ ] Generate a real page per PDF page/image with source dimensions and
  thumbnails.
- [ ] Display queued/importing/uploading/processing/failed/complete states with
  cancel, retry, and safe cleanup.
- [ ] Let the user choose new notebook versus insert into the current notebook.
- [ ] Keep the source PDF/image as a non-destructive page underlay and draw ink
  in a separate layer.

#### Export and sharing

- [ ] Export the current page or notebook as PDF with background, source asset,
  and ink rendered consistently.
- [ ] Export original source, PNG/JPEG page image, native archive, canonical
  `ink_v1`, and converted Markdown/LaTeX where available.
- [ ] Add standard share sheet and Files exporter with progress/cancellation.
- [ ] Define a versioned native notebook archive containing manifests, assets,
  native drawings, canonical ink, and checksums for backup/migration.
- [ ] Add print support only after PDF export is deterministic.

#### Tests

- [ ] Test 1-page/100-page PDFs, rotated pages, large images, malformed files,
  password-protected PDFs, cancellation, low storage, and app termination.
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

- [ ] Add grid/list display with persisted preference and useful cover/page
  thumbnails.
- [ ] Add sort by updated, created, title, and manual/favorite order.
- [ ] Add rename, duplicate, favorite, move, archive, trash, restore, and
  permanent delete to both context menus and multi-select actions.
- [ ] Add folders with nesting limited to a tested depth; support drag-and-drop
  and a move sheet.
- [ ] Add tags and smart views for favorites, recent, imported, unsynced, and
  needs-attention items.
- [ ] Define trash retention and communicate when permanent deletion affects
  every synced device.
- [ ] Add pagination/incremental loading, stable selection, and thumbnail cache
  eviction.

#### Search

- [ ] Phase search as title/metadata → recognized text → handwriting index →
  attachment OCR/object metadata, depending on available models.
- [ ] Add in-library scopes and in-notebook search with page/result previews.
- [ ] Selecting a result must open the exact page and highlight source strokes
  or source crop without modifying ink.
- [ ] Make offline index availability and stale/incomplete results explicit.

#### Tests

- [ ] Verify folder/tag/favorite mutations offline and across two clients.
- [ ] Test library performance at 1,000 notebooks and thumbnail-cache pressure.
- [ ] Test Unicode titles/tags, empty states, duplicate names, and search result
  navigation.

Exit gate:

- A user can find a target page in a 100-page notebook and a target notebook in
  a 1,000-item library using accessible controls.
- All organizational operations survive offline edits and converge or surface
  a resolvable conflict.

### Milestone 5 — Source-linked conversion and review (P1)

Goal: deliver the differentiated workflow only after original notes are safe.

- [ ] Add Convert Selection, Convert Page, and bounded Convert Notebook actions.
- [ ] Upload/reuse the exact immutable ink revision being converted and record
  its digest and coordinate transform.
- [ ] Show queued/running/partial/failed/cancelled/complete status per region or
  page and resume after relaunch.
- [ ] Gracefully use raster fallback for unsupported ink and show model
  unavailability as a real error state.
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
- [ ] Add keyboard shortcuts for new/open/search, undo/redo, page navigation,
  tool selection, zoom, and export.
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

## 10. Recommended implementation sequence

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
