# Feature and evidence status matrix

Last audited: 2026-09-12. This matrix separates code presence from the evidence
needed to ship it. “Implemented” means the repository contains the behavior;
“tested” refers to automated checks in this checkout; staging, device, and
production columns require separate deployment evidence.

| Capability | Implemented | Unit/property tested | Integration tested | Device tested | Staging validated | Production approved |
| --- | --- | --- | --- | --- | --- | --- |
| Image/PDF import and bounded upload reads | Yes | Yes | SQLite/API fixtures | No | No | No |
| Resumable upload manifest, digest, and retry contract | Yes | Yes | Filesystem object-store fixture | No | MinIO check is opt-in and not run | No |
| OpenAPI, error-code, and `ink_v1` generated contracts | Yes | Yes | CI commands documented | No | No | No |
| Manual TypeScript/Swift DTO boundary | Yes | Yes | Swift package tests pass, including missing-field compatibility | No | No | No |
| Document sync, idempotency, conflicts, tombstones, compaction | Yes | Yes, including property tests | SQLite/API fixtures | No | No | No |
| Durable recognition-job lifecycle | Yes, process-local worker | Yes, including crash/retry/timeout/stale lease tests | No production queue | No | No | No |
| Session-token hashing and migration | Yes | Yes | Disposable SQLite migration path | No | PostgreSQL check is opt-in and not run | No |
| Export formats and malicious-LaTeX handling | Yes | Yes, export goldens | Filesystem/PDF fixtures | No | No | No |
| ONNX export parity | Optional | Yes across tested widths/batches | CPU runtime fixture | No | No | No |
| Real prose/formula recognition | Fail-closed code path only | Synthetic/structural tests only | No real artifact installed | No | No | No |
| Personalization improvement | Plumbing only | Training/property coverage exists | No consented representative corpus | No | No | No |
| iPad app, PencilKit, Keychain, background transfers | Swift package only | Package tests only | No signed app/network run | No | No | No |
| TLS, secret management, backups, restore, deletion drills | Not complete | Partial application tests | No deployment drill | No | No | No |

The matrix must be updated with a command, artifact digest, environment, and
review owner when an evidence level changes. A green automated test does not
advance a feature to staging or production approval by itself.
