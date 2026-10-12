# Feature and evidence status

Updated October 12, 2026 from dated October 11 reports. This is the maintained
status summary. “Implemented” describes source behavior; automated coverage,
live simulator checks, physical hardware, and deployment acceptance are separate
levels of evidence. A recorded local environment is not supplied by a clean clone.

## Product workflow

| Capability | Automated evidence | Live local evidence | Remaining validation |
| --- | --- | --- | --- |
| PDF/image import, source retention, page rasterization | API/PDF/image fixtures | Website import and imported-library restore in simulator | Broader mixed-document/client import acceptance; physical share import |
| Native notebooks, page edits, local ink persistence | Swift store/recovery and native app tests | Simulator offline creation/relaunch and page controls | Physical Pencil/palm rejection; long-session, accessibility, and resource checks |
| Document sync, conflicts, tombstones, exact ink restoration | API/property tests and second-client/canvas-race tests | Website corrections visible in simulator; shared library restored | Complete live two-client offline/interruption/concurrent-edit journey |
| Formula recognition with UniMERNet-small | Adapter, capability, job, and stale-source tests | Installed real model; printed formula conversion/review/export passed | Representative handwritten formulas and Linux model runtime |
| Prose recognition with TrOCR | Prose/conversion API coverage | Installed real model; source review/save/reopen/Markdown passed | Representative handwriting evaluation; demo's raw prose prediction is incorrect |
| Immutable corrections and document revisions | Version-conflict, stale-source, and review regression tests | Website and simulator save/reopen passed | Broader live region-edit and concurrent-review acceptance |
| Markdown, LaTeX, HTML, controlled PDF export | Export goldens and native runtime checks | Simulator Markdown sharing; website Markdown download; other formats checked through local APIs | Full cross-client format/source fidelity on varied notes |
| Training consent and personalization | Consent, isolation, training, and promotion guardrail tests | No measured personalization improvement | Compatible CRNN artifact, consented data, independent quality evaluation |
| Custom compact formula model | Model/training/data-validation tooling | No evaluated real checkpoint claimed | Licensed data, training, and held-out evaluation |

## Reliability and deployment boundaries

| Boundary | Current evidence | Limit |
| --- | --- | --- |
| Recognition jobs | Database-backed state, retries/deadlines, stale-source rejection, restart recovery tests | Worker remains process-local; no independent production queue |
| Resumable uploads | Digest/manifest/retry contracts with filesystem fixtures | Live interruption/kill campaigns and S3 integration remain |
| Owner isolation and authentication | Ownership/property checks, session hashing, simulator sign-in | Broader live account separation, expiry, deletion, and production auth policy remain |
| Shared contracts | Generated OpenAPI/error/ink artifacts and Swift boundary checks | Drift checks do not replace client/device acceptance |
| SQLite | Fresh migration and schema checks; local app workflow | Local validation only |
| PostgreSQL | Fresh migration failed on merged main; native-ALTER fix and integrity test prepared/tested locally | Fix is still uncommitted in this workspace; do not claim released production compatibility |
| S3/MinIO | Filesystem/fake-client coverage; real service check blocked by registry downloads | No passing real S3 integration or production-stack acceptance |
| Production operations | Optional Compose configuration exists | HTTPS/Linux deployment, secrets policy, backup/restore, and release drills incomplete |
| Physical iPad | No physical-device evidence recorded | Simulator results do not establish Pencil, signing/App Group, battery, or thermal behavior |

## Evidence register

| Evidence | Environment / source | What it supports |
| --- | --- | --- |
| [Local conversion report](release-evidence/END_TO_END_LOCAL_2026-10-11.md) and [manifest](release-evidence/END_TO_END_LOCAL_2026-10-11.json) | macOS arm64, iOS 18.6 simulator; source fingerprint and pinned model revisions recorded | Real-model workflow and demo, including source/output limits |
| [Review fixes](release-evidence/REVIEW_FIXES_2026-10-11.md) | Source fingerprint `b385efbf…`; local backend/web/Swift/simulator checks | Sync races, source invalidation, and regression coverage |
| [Commit validation](release-evidence/COMMIT_VALIDATION_2026-10-11.md) | Documented application commit batch, October 11 | Dated local automated validation |

The pre-existing workspace report `release-evidence/MAIN_VERIFICATION_2026-10-11.md`
records the later main verification and PostgreSQL/S3 findings. It and the
migration fix are not yet committed; the table above records that limitation
without promoting them to shipped evidence. Remote legacy status and PR-workflow
queries returned no results during this presentation pass; no CI-passing claim
is made from those queries.

Model weights and corpora are intentionally external. The demo's real-model
conversion does not establish accuracy, and installed TrOCR does not enable
the legacy CRNN personalization workflow.

When changing an evidence level, record the date, command/journey, source
revision, environment, model/dataset identity where relevant, result, and
remaining limits in a dated report. Keep historical reports intact.

[Remaining checks](../TODO-2026-10-11.md) · [Setup](GETTING_STARTED.md) ·
[Documentation index](README.md)
