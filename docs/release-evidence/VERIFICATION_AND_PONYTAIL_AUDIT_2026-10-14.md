# Final verification and Ponytail audit — October 14, 2026

Scope: the complete working changes on `codex/main-verification-fixes`, based
on `aa79236`, including backend hardening, the Quiet Notebook web/native UI,
deployment, documentation, licensing and demo evidence. The user required a
passing complete production-stack check before pushing and selected SeaweedFS
as the replacement for MinIO. The branch was subsequently rebased onto
`125a658` (current main); the tested source manifest still matches every file.
Conflict resolutions affect documentation only and preserve upstream README
presentation edits. The original test baseline remains `aa79236`.

## Corrections made during verification

- Regenerated stale OpenAPI; compatibility and all four generated artifacts pass.
- Account purge now fails when stored objects cannot be removed, retaining the
  disabled account and database references for an operator retry. S3 bulk-delete
  errors and invalid pagination responses are handled explicitly.
- Request-body replay now forwards the real disconnect after delivering the
  bounded payload; observability attaches redaction after creating its handler.
- Reopened unconverted images retain the active image ID, so conversion can
  display its result. Library results use the server's normalized search contract.
- The native ruler action is reachable at the top of Tools. Page deletion captures
  its target before SwiftUI clears the confirmation binding.
- Native image fixtures use an explicit scale; tests wait for actual dismissal
  and page removal. Debug UI-test mode disables UIKit animations to avoid the
  simulator's repeated keyboard-animation waits; production interaction retains
  normal animations. Simulator tests do not establish animation performance.
- Replaced MinIO/MC with pinned SeaweedFS 4.48, private storage networking,
  separate administrator and bucket-scoped backend identities, required encryption
  secrets, and a startup check that actually writes/reads/deletes encrypted data.

## Results

| Check | Final result |
| --- | --- |
| macOS backend suite | 922 passed; the then-two opt-in integration checks skipped and subsequently run against real services |
| Linux backend suite in the production image | 922 passed, 3 opt-in integrations skipped; all three pass separately against the actual production services |
| Frontend unit/domain/property tests | 178 passed in 11 files |
| TypeScript and production frontend build | Passed; initial JavaScript chunk remains above Vite's 500 kB advisory |
| Production browser suite | 30 passed; six intentional opt-in/engine-specific skips |
| Live isolated API offline/reconnect journey | 3 passed: Chromium, Firefox, WebKit |
| Shared Swift package | 30 passed |
| iPhone 16 Pro, iOS 18.6 simulator | 33 app tests and all 8 UI tests passed |
| iPad Pro 11-inch (M4), iOS 18.6 simulator | 33 app tests and all 8 UI tests passed |
| Complete Docker production profile | PostgreSQL 16, Redis 7, SeaweedFS 4.48, API and nginx healthy; both storage setup jobs completed |
| Real production integrations | All 3 passed inside the actual API image: PostgreSQL schema/FK integrity, encrypted S3 round trip and owner-prefix purge preserving another owner |
| Production API journey through nginx | Synthetic PNG import → crop → manual correction → save/reload → revision-pinned Markdown export → completed account purge passed |
| Storage security and durability | AES256 writes verified; anonymous object reads and backend bucket administration denied; encrypted data survived storage restart |
| Authentication quotas and recovery | Rotating fake bearer tokens produced five 401s then 429; Redis outage produced 503; limiter recovered after Redis restart |
| nginx | Configuration validation plus nine page/asset/header/operational-route checks passed |
| Contracts | Four generated artifacts current; OpenAPI compatibility passed; 31 Swift API models passed |
| Frontend production dependency audit | Zero reported advisories from `npm audit --omit=dev` |
| Diff whitespace | Passed |

The browser skips comprise the three live-API checks run separately above,
two non-Chromium performance samples and one WebKit fixture/service-worker
case covered by the live WebKit journey. The opt-in native real-model demo was
excluded; no new recognition-quality result is claimed.

The initial Linux run used a read-only working checkout and hit SQLite fixture
setup errors. The final run uses a source-only writable temporary checkout
inside the same production image; real local databases, credentials, model
weights and caches are excluded. Initial native failures and interrupted keyboard
runs are diagnostics, not passing evidence.

Reproducible production setup is in [the profile guide](../PRODUCTION_PROFILE.md).
`scripts/smoke_production.py` performs the API journey against a disposable stack.
`backend/tests/test_production_integrations.py` covers real database/S3 endpoints;
set `HND_TEST_S3_REQUIRE_ENCRYPTION=1` to require AES256 metadata. Credential values
are not included in this report. The [source manifest](VERIFICATION_SOURCE_2026-10-14.json)
records hashes for the final tested source and local verification logs. Raw logs
are temporary local artifacts; this report preserves their results and limits.

## Ponytail audit

This is a complexity audit; the correctness fixes above were reviewed separately.
The existing two object-store implementations, recognition adapters, owner-scoped
repositories and editor migration hosts serve current behavior and were retained.
The following are optional simplifications; they were not applied during this pass.

- `yagni:` Four static Button variants use cva/clsx/tailwind-merge. A typed class lookup and string concatenation cover current callers while retaining Radix accessibility. [primitives.tsx](../../frontend/src/components/ui/primitives.tsx)
- `shrink:` Reset quota HTTP handling duplicates the authentication helper. Reuse `_enforce_subject_quota`. [consent_api.py](../../backend/app/services/consent_api.py)
- `delete:` `RequestSizeLimitMiddleware.dispatch` is called only by tests. Test the registered ASGI path directly and remove the compatibility shim. [request_limits.py](../../backend/app/middleware/request_limits.py)

Estimated net: -40 lines, -3 direct dependencies possible.

## Remaining boundaries

These are local functional results. HTTPS login, backup/restore drills, Linux
recognition-model installation/quality, representative workload capacity, physical
Pencil/iPhone behavior, full assistive-technology acceptance and user research
remain separate release gates. The pre-existing dependency upgrade backlog in
the security plan remains; this pass does not claim an advisory-free backend.
SeaweedFS is a single-node deployment here. Existing MinIO volumes are preserved;
no old data was copied or migrated. No public deployment or release was performed.

Historical reports retain their original dates and source fingerprints. This
report supersedes their PostgreSQL/S3 blocker and native Tools-menu failure status.
