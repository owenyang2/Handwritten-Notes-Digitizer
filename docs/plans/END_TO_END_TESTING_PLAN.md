# End-to-end testing plan

Prepared: 2026-10-11

Scope: iPad app, website, live backend, handwriting/formula recognition, and
optional personalization. Start with the tools already installed, then complete
conversion and install real models. The baseline and subsequent setup results
are recorded below; unchecked acceptance steps remain outstanding.

## Starting point

- [x] Pull current branch: `platform-sync-uploads` is up to date with its remote
  and has six local commits ahead. Preserve the existing uncommitted alpha work.
- [x] Backend regression after conversion implementation: 900 passed, two skipped. The skipped checks require
  disposable PostgreSQL and S3/MinIO services.
- [x] Website regression: 174 passed; typecheck and production build passed.
- [x] Swift package regression: 29 passed. These use fake transports and do not
  establish a live iPad/backend journey.
- [x] Generated contracts and 31 Swift API model checks passed.
- [x] Local schema matches the models; backend and website are running on ports
  8000 and 5173. UniMERNet-small is installed and passes live formula inference;
  TrOCR handwriting prose is also installed and passes live conversion.
- [x] Fresh iOS 18.6 simulator build, 27 native app tests, and six UI tests passed.
  The app launches with local signing. Physical-device acceptance is outstanding.

These checks were run in this chat on 2026-10-11. See the
[completed local setup and remaining device inputs](../MODEL_AND_IPAD_SETUP.md)
and [final local evidence](../release-evidence/END_TO_END_LOCAL_2026-10-11.md).
The existing September simulator acceptance report remains historical evidence.

## 1. Start the website and backend

**Prerequisites:** backend environment, browser, and a small set of test notes.
The completed Mac setup uses `HND_FORMULA_ENGINE=unimernet make dev-backend` and
`make dev-frontend`, with the website at `http://127.0.0.1:5173`. Docker is an
alternative below; it may fetch images and dependencies on the first build and
needs its own Linux-compatible formula runtime. Use test accounts and a separate
test workspace/database for destructive account or deletion checks.

From the repository root:

```sh
HND_ENVIRONMENT=development FRONTEND_PORT=8080 \
  docker compose up -d --build --wait --wait-timeout 120
curl http://127.0.0.1:8080/api/health
make doctor
```

Open `http://localhost:8080`. The development setting supports local HTTP
session cookies. The doctor probes nginx on port 80, so verify the chosen port
8080 directly; its port-80 warning is expected for this configuration.

- [ ] Register, sign out, sign in, and reopen the browser session.
- [x] Local demo account: sign in, open imported formula/prose images, recognize,
  review source crops, save and reopen a formula correction, and download its
  Markdown export in the actual browser.
- [ ] Import a PDF and an image; verify retained originals and page order.
- [ ] Select a formula crop, enter LaTeX manually, save, reopen, and export it.
- [ ] Verify missing models produce an explicit unavailable state.
- [ ] Restart the services and confirm documents and saved reviews remain.

**Pass:** browser login, import, manual review, persistence, and export work
against the running backend. Missing-model warnings remain expected here.

## 2. Test the native app and live sync

**Prerequisites:** installed Xcode and XcodeGen. A missing simulator runtime may
need downloading. Physical testing needs an iPad with iOS 17 or later, an Apple
Pencil, and signing/App Group configuration.

```sh
cd ios
xcodegen generate
open PersonalizedNotes.xcodeproj
```

Select the `PersonalizedNotes` scheme and an iPad simulator. Run the app and the
native app/UI test targets. For a physical iPad, configure the signing team and
`group.com.handwrittennotes.digitizer` on both app and share extension, then run
a Debug build. Configure Settings → Backend as follows:

| Client | Backend address |
| --- | --- |
| Simulator on the backend Mac | `http://127.0.0.1:8000` |
| Physical iPad, after restarting the local backend on `0.0.0.0` | `http://<Mac-LAN-IP>:8000` |
| Physical iPad, alternative Docker setup | `http://<Mac-LAN-IP>:8080/api` |

Use Settings' connection test before signing in. Release endpoints outside
loopback require HTTPS.

- [ ] Create a notebook offline, draw, add/reorder/rename pages, then force-quit
  and reopen. Confirm every stroke and page remains.
- [ ] Import from Files, Photos, and the share extension; annotate and export.
- [ ] Sign in and sync; disconnect, make another edit, reconnect, and retry.
- [ ] On a second independent native installation, sign in with the same account
  and restore identical document/page IDs and the exact ink revision.
- [ ] Make concurrent edits and verify conflict recovery protects pending ink.
- [ ] In the website, verify shared notebook metadata and imported page sources.
- [ ] On a physical iPad, check Pencil input, palm rejection, finger pan/zoom,
  rotation, multitasking, share import, Keychain/session restore, and VoiceOver.

**Pass:** notes survive local restarts and network failures, and a second native
client restores the same ink. Record simulator and physical results separately.

## 3. Complete conversion and shared viewing

**Depends on:** a working live app/backend path from steps 1–2. This is
implementation work. Use an explicitly identified deterministic test engine
while model setup is pending; fixture results establish workflow behavior only.

Implemented 2026-10-11: native recognition runs use the durable job worker;
saved canonical ink is composed with imported backgrounds for the website and
frozen for conversion; native review offers source highlighting, editing,
region actions and exports. The focused suite uses explicitly identified
fixtures; separate live evidence records both installed real models.

- [x] Connect native recognition runs to execution, reusing existing job logic
  where practical. Persist progress, results, terminal states, bounded retries,
  deadlines, and recovery after restart; enforce per-task model availability.
- [x] Render the exact saved ink revision into a model/viewer-compatible image,
  or add a supported stroke renderer. Retain the revision digest and coordinate
  transform so regions link back to the original drawing.
- [x] Make native ink visible in the website. Any generated image must identify
  its source revision and update when that revision changes; imported backgrounds
  plus annotations must remain correctly composed.
- [x] Add the iPad review UI: source highlighting, accept/edit, uncertainty,
  split/merge, reorder, exclude, and retry actions.
- [x] Save immutable review revisions and make converted exports consume the
  same saved revision. Keep original-ink exports available independently.
- [x] Update API contracts and both clients for changed wire fields.
- [x] Add focused integration tests for run completion, restart recovery,
  duplicate requests, stale revisions, coordinate alignment, cross-account
  denial, and review/export consistency.
- [x] Real-model iPad UI acceptance: sign in, restore website imports, convert
  formula and prose pages, inspect source-linked review, save, open Markdown
  sharing, and reopen identical saved text. The recorded test passed.
- [ ] Exercise the complete live native drawing → sync → conversion journey
  and second independent installation, separately from API and unit tests.

**Pass:** a fixture conversion reaches a terminal result, output links to the
exact source, both clients can view that source, and edits survive reopening
and appear in the exported artifact. Original ink remains recoverable.

## 4. Install and verify real recognition models

**Depends on:** step 1. Model setup can proceed while step 3 is being completed;
the website's existing formula workflow can test a model independently.

| Recognition task | Required artifact | Setup choice |
| --- | --- | --- |
| Formula crops → LaTeX | UniMERNet-small snapshot with compatible inference support, or a trained compact checkpoint | Start by checking the supported pretrained snapshot's compatibility; use a trained compact artifact if needed. |
| Handwritten prose → text | Microsoft TrOCR handwritten snapshot, or a trained CRNN plus metadata | TrOCR is installed for document conversion; CRNN is still required for the existing personalization pipeline. |

Store artifacts under `backend/var/objectstore/models/`. Formula and prose
artifacts are separate; installing one does not enable the other task.

- [x] For UniMERNet-small, record the model revision, digest, tokenizer/processor configuration, and
  provenance. Confirm the downloaded snapshot is compatible with the runtime;
  the downloader alone does not provide a working inference bridge.
- [x] For UniMERNet, use `scripts/download_unimernet.py` with a selected revision.
  It resolves the revision to an immutable commit and writes an artifact manifest.
- [ ] For compact, install `compact_math/checkpoint.pt` from a real trained run.
- [x] For prose document conversion, install the pinned TrOCR snapshot with
  `scripts/download_trocr.py`; record revision, digest, license and real probe.
- [ ] For optional CRNN personalization, install `base_model/base_model.pt` and
  `base_model/base_model.meta.json` from a verified trained run.
- [x] Start the backend and inspect both model fields in `/api/health`: formula
  loaded and `prose_model.loaded` true. Legacy `model_status` remains unavailable
  without CRNN. Both models passed import/run/review/reopen/export API checks.
- [ ] Run representative real handwritten formula crops and prose lines through
  their respective APIs once the task models are available.
- [x] Check CPU smoke latency and bounded timeouts. UniMERNet inferred in about
  0.55 seconds; TrOCR in 0.8–0.9 seconds when warm. Cold setup precedes the
  inference deadline. These are individual samples, not latency distributions.

**Pass:** each installed model loads and actually infers on representative real
inputs. Missing or incompatible task models remain visibly unavailable.

Model download/training commands are in the
[setup guide](../MATH_NOTES_SETUP_GUIDE.md). Model weights and licensed datasets
are external inputs; neither is bundled in Git.

## 5. Run the complete journey with real models

**Depends on:** steps 2–4. Use two independent native clients, a browser, one
shared account, and a second account for access-isolation checks.

- [x] Bounded local imported-image journey passed for both real models in the
  simulator and API, with actual website review/source screenshots. This does
  not complete the drawing, two-client and interruption gates below.
- [ ] Create and draw offline → reopen → sign in → upload → restore on the
  second native client → view the same source on the website.
- [ ] Import mixed prose/math PDFs and photos → convert page/notebook → review
  against the source → correct → save → reopen on another client → export.
- [ ] Check page order, figures, original ink, formula structures, corrected
  text, and exported artifacts against the saved review revision.
- [ ] Repeat with network interruption, app termination during an upload,
  backend restart during conversion, expired login, and concurrent edits.
- [ ] Verify corrupt/oversized uploads, unavailable models, truncation, timeouts,
  and rate limits leave a clear recovery path and preserve notes.
- [ ] Verify account B cannot access account A's pages, ink, sources, jobs,
  predictions, reviews, or exports.
- [ ] Exercise export and account deletion on disposable test accounts.

**Pass:** the actual clients complete create/import → sync → recognize → review
→ export with real model output. Recovery paths prevent lost notes and stale
results from silently replacing newer work.

## 6. Measure model quality and optional personalization

**Depends on:** verified models and a fixed real image/transcription set.
Functional recognition from step 5 can pass before an accuracy campaign is
complete; any accuracy or improvement claim needs this evidence.

- [ ] Prepare representative clean/messy notes, prose, formulas, mixed pages,
  rotated photos, diagrams, and blank/unreadable cases with verified labels.
- [ ] Keep whole source documents separate across training, development, and
  final test splits. Record dataset versions and hashes.
- [ ] Agree accuracy, critical-symbol, latency, and memory targets before using
  the final test set. Count failed, missed, unreadable, and truncated samples.
- [ ] Report prose CER/WER and formula exact match, token errors, renderability,
  critical structure errors, page coverage/order, latency, and peak memory.
- [ ] Generate the formula report with `benchmarks.runner` and retain the exact
  prediction file and model/dataset identifiers.
- [ ] If testing prose personalization, collect explicit consented corrections,
  verify the configured training threshold (default 20), provide personal and
  verified general holdouts, and compare the adapter to the frozen base model.
- [ ] Keep `HND_ALLOW_SYNTHETIC_EVAL=false` for real promotion evidence; verify
  isolation, rejection/rollback, consent withdrawal, and reset/delete behavior.

**Pass:** quality targets are measured on held-out real data. Personalization
passes only if measured improvement and general-data guardrails justify the
adapter. Compact formula training remains a separate offline workflow.

## 7. Optional staging and beta hardening

After the bounded local journey passes, use the production Compose profile with
disposable PostgreSQL/Redis/MinIO services. Install the production Python extras
and configure the explicit test endpoints for the skipped integration checks.
Validate HTTPS login, backup/restore, longer background-transfer runs, low disk,
large notebooks/libraries, and physical-device memory/thermal behavior. This is
a separate gate from starting local testing.

## Evidence and completion

For every stage, record source commit plus a fingerprint/snapshot of uncommitted
changes, device/OS or browser, fixture set, steps/commands, actual results, and
remaining defects under `docs/release-evidence/`. Preserve existing acceptance
reports. Re-run the appropriate regression/contract suites when code changes.

The functional end-to-end milestone is step 5. Model-quality/personalization
evidence is step 6. Staging/beta readiness also requires step 7 and the broader
[iPad roadmap](IPAD_NOTE_APP_USABILITY_IMPLEMENTATION_PLAN.md).
