# iPad engineering alpha acceptance evidence — 2026-09-12

## Scope and source state

- Branch: `platform-sync-uploads`
- Starting commit: `4b6ebb5`
- Source state: large uncommitted alpha slice; no merge or release commit was
  created during this pass.
- Simulator: iPad Pro 13-inch (M4), iOS 18.6.
- Physical iPad: not exercised; Pencil latency, palm rejection, device signing,
  real App Group/Keychain behavior, and manual accessibility remain unverified.

## Acceptance results

| Boundary | Result | Evidence |
| --- | --- | --- |
| Backend | Pass | 885 passed, 2 skipped. |
| Swift package | Pass | 28 passed. |
| Native iPad app tests | Pass | 27 passed, including clean two-client reconnect/bootstrap and exact ink restoration. |
| Native iPad UI tests | Pass | 6 passed on the iOS 18.6 simulator. |
| Frontend regression | Pass | 173 tests passed; typecheck and production build passed. |
| OpenAPI | Pass | Generated `contracts/openapi-v1.json` matches the backend and 31 Swift wire models pass the contract checker. |
| Backend migrations | Pass | One linear head (`f5a6b7c8d9e0`); clean SQLite upgraded from base through all 15 revisions and created 36 tables. |
| Diff integrity | Pass | No unresolved files or whitespace errors. |

The backend run emitted 286 warnings. They are dominated by the known SQLite
test teardown warning for the `document_pages`/`ink_bundles` foreign-key cycle,
plus the pinned Starlette/AnyIO deprecation and existing ONNX tracing warnings;
no assertion failed. The two skipped tests require explicitly configured
disposable PostgreSQL and S3/MinIO services.

## User-journey evidence

The simulator UI run exercised welcome/auth controls, contextual onboarding,
offline notebook creation, app termination/relaunch persistence, page rename,
paper template selection, page add/duplicate/delete controls, import entry
points, drawing-tool alternatives, export/print affordances, live PDF share
controller presentation, favorites, and page-title search that opens the exact
page. Runtime tests separately opened export artifacts, exercised 100-page and
rotated-page PDFs, verified native archive integrity, and tested share-source
durability.

## Trust gap closed in this pass

Before this pass, sync could bootstrap notebook and page metadata plus imported
source files, but it did not download page ink. A second client could therefore
show the right notebook with an empty drawing.

The completed path now:

1. Pulls the stable document and page IDs.
2. Reads the page's active immutable ink manifest.
3. Downloads only an owner-scoped revision bound to that page.
4. Revalidates page identity and the canonical content hash on both server and
   Swift client.
5. Atomically installs the exact native `PKDrawing` bytes and canonical
   `ink_v1` bundle as one local revision.
6. Marks the restored revision complete so it is not redundantly uploaded.
7. Skips remote replacement whenever the local page contains unsynced ink.

Focused tests cover failed reconnect followed by successful retry, stable ID
equality, byte-identical download retry, cross-owner denial, tampered-content
rejection, exact native/canonical restoration, and pending-local-ink protection.

## Known deviations and remaining gates

The engineering alpha is green in this checkout, but it is not beta-ready.
Remaining gates are intentionally unchanged:

- Physical iPad Pencil, rotation/multitasking, memory/thermal, and accessibility
  testing.
- Share-import kill testing at each copy/upload/acceptance/cleanup boundary.
- Source-linked conversion review UI and a useful recognition model or an
  explicit deterministic fixture.
- Email delivery/verification policy, refresh-token rotation/device metadata,
  legal links, final bundle/App Group/team/signing, production TLS, privacy
  manifest, and release drills.
- Larger conflict, quota, low-storage, randomized crash, 1,000-notebook, and
  background-transfer campaigns.

These are external release/hardening gates, not failures in the bounded alpha
acceptance run.
