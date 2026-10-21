# Status and roadmap

Reviewed October 20, 2026 against source and the recorded reports below.
**Local alpha:** implemented behavior, automated tests, simulator journeys,
physical hardware and deployment acceptance are separate evidence levels.
Installed models and services on a test machine are not included in a clone.

## Capabilities and limits

| Area | Available behavior / evidence | Remaining acceptance |
| --- | --- | --- |
| Web workspace | Warm paper/charcoal folio, stronger selected states, source-preview shelf/list, search, compact conversion controls, source-linked review, visible Theme control, System/Light/Dark before/after sign-in, explicit save/export; [UI consistency fixes](release-evidence/ui-fixes-2026-10-20.md) cover Settings type, selects, phone spacing, warning contrast, forms and dialogs with synthetic browser checks | Assistive technology, physical devices, representative performance/usability and non-Mac font rendering; overhaul is web-only |
| Native notebooks | Universal iPhone/iPad target, PencilKit, local SQLite/ink, import, page edits and appearance; app/UI simulator suites recorded green | Physical Pencil/palm rejection, signing/App Groups, share import, accessibility, battery and thermals |
| Sync and recovery | Pending operations, conflicts, tombstones, exact ink restore, revision checks; automated races/recovery and partial live journeys | Complete live two-client offline/interruption/concurrent-edit journey |
| Recognition | UniMERNet-small formulas and TrOCR prose; real-model local web/simulator review → save → reopen → Markdown demonstrated | Representative handwritten prose/formula evaluation; Linux inference runtime |
| Review and export | Immutable version-checked reviews; Markdown, LaTeX, HTML and controlled PDF; API goldens and selected live formats | Varied mixed notes and full cross-client source/format fidelity |
| Personalization / compact model | Consent, isolation, training and promotion tooling | Compatible CRNN artifact and consented data, or trained compact checkpoint; no measured accuracy improvement |
| Jobs and uploads | Database-backed jobs, retries/deadlines, stale-source rejection and restart recovery; resumable-upload fixtures | Process-local recognition worker; live transfer interruption and S3 campaigns |
| Authentication and contracts | Ownership/session/property checks; generated OpenAPI/error/ink drift and Swift boundary checks | Broader live account expiry/deletion/isolation and production auth acceptance |
| Deployment | Local SQLite stack and PostgreSQL/Redis/SeaweedFS profile checked; encrypted/scoped S3, restart persistence, API workflow and Redis recovery recorded | HTTPS, backup/restore, least privilege, Linux models, capacity and release drills |

The older demo uses a printed formula fixture and an inaccurate prose prediction
(`indus the`). It proves integration, not handwriting accuracy. TrOCR does not
enable legacy CRNN personalization. Unsaved web corrections survive while the
page stays open; they do **not** survive reload. Reopening web notes needs a
connection; native offline storage is a separate capability.

## Next acceptance work

Complete items only after linking a dated result with its source revision,
environment, input/model identity, command or journey, outcome and limits.

- [ ] **Two-client journey:** create/draw offline, force-quit/reopen, sync, restore
  identical page order/source/ink on a second native installation and the web;
  exercise concurrent edits, interrupted uploads and backend restarts.
- [ ] **Client recovery and isolation:** live registration/session expiry,
  cross-account denial, account export/deletion, missing models, malformed files,
  timeouts and rate limits with disposable accounts.
- [x] **Web UI consistency:** applied the [October 20 fixes](release-evidence/ui-fixes-2026-10-20.md)
  for all nine audit findings. Synthetic browser checks now include expanded
  Settings, the older image-review warning and live System appearance before
  sign-in. Physical-device, screen-reader and broader real-content acceptance
  remain separate.
- [ ] **Import/review/export fidelity:** multipage PDF and image imports through
  actual clients; mixed pages, split/merge/reorder/exclude/retry, saved reviews
  reopened on another client, and comparison of all export formats.
- [ ] **Physical iPad:** provision both targets/App Group, configure a reachable
  backend, then test Pencil, palm rejection, gestures, rotation/multitasking,
  share import, VoiceOver/Dynamic Type and long sessions.
- [ ] **Recognition quality:** representative notes and verified transcriptions,
  disjoint train/dev/test documents, agreed quality/resource targets, retained
  predictions and benchmark reports. Correct the historical prose transcription
  before reusing it as an example.
- [ ] **Optional model research:** real CRNN/compact artifacts and licensed data;
  measure independent quality and guardrail/rollback/consent behavior before
  claiming personalization improvement.
- [ ] **Deployment:** HTTPS login, Linux model runtime, backups and tested restore,
  least privilege, large libraries, low disk, background transfers and capacity.

The compact iPhone Tools-menu failure in earlier reports was resolved in the
final October 14 verification. It is not an open defect. Simulator results
still do not establish physical-device readiness.

## Evidence

| Report | Scope |
| --- | --- |
| [October 20 UI fixes](release-evidence/ui-fixes-2026-10-20.md) | Implemented audit corrections, browser checks, Settings screenshots and refreshed current web media; synthetic inputs |
| [October 20 web UI audit](release-evidence/ui-audit-2026-10-20.md) | Source `76a78ea`; cross-browser typography/control geometry, synthetic screenshots, expanded/older screen accessibility findings and local lab performance; audit only, fixes outstanding |
| [Final October 14 verification](release-evidence/VERIFICATION_AND_PONYTAIL_AUDIT_2026-10-14.md) | Latest recorded regression/native suites and local production/storage checks |
| [October 14 UI report](release-evidence/UI_REVAMP_2026-10-14.md) | Redesign, browser accessibility/recovery, lab measurements and native limits; read alongside final fixes above |
| [October 11 real-model conversion](release-evidence/END_TO_END_LOCAL_2026-10-11.md) and [provenance](release-evidence/END_TO_END_LOCAL_2026-10-11.json) | Website/simulator conversion, saved review/reopen/export and historical recording |
| [October 11 review regressions](release-evidence/REVIEW_FIXES_2026-10-11.md) | Sync races, stale-source rejection and automated regression evidence |

[All dated reports](release-evidence/README.md) ·
[Setup](guides/getting-started.md) · [Documentation](README.md)
