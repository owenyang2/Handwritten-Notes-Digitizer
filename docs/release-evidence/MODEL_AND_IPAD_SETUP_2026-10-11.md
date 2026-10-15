# Model and iPad setup evidence — 2026-10-11

Historical setup stage: later conversion implementation, TrOCR installation,
live browser/native validation and the recorded demo are documented in
[the final local report](END_TO_END_LOCAL_2026-10-11.md). Outstanding items in
this initial report describe the state at that earlier stage.

Scope: install a working formula model, prepare the iPad simulator, and identify
the remaining inputs for physical-device/prose testing. This does not close the
complete end-to-end acceptance plan.

## Source and environment

- Branch: `platform-sync-uploads`.
- HEAD: `4b6ebb5152b2eaf43138f34784abf0befa1da492`.
- The earlier fetch/pull confirmed the branch was current with its remote and
  six local commits ahead. Existing uncommitted app/backend work was preserved.
- Working source fingerprint after setup code changes:
  `fd5f2a8a4d2d8dc6ab9e9b8df33dad780ac5184ef00f14d709019641a41f54ef`.
  Computed over 307 existing tracked/non-ignored untracked files beneath
  `backend/`, `frontend/`, `ios/`, `contracts/`, and `scripts/`: sort paths,
  then feed each UTF-8 path, NUL byte, and raw SHA-256 file digest to SHA-256.
  Ignored local weights, environments, database, and generated Xcode project
  are excluded. Documentation is outside this source fingerprint.
- Mac arm64, Python 3.12.6, Xcode 16.4, XcodeGen 2.46.
- Simulator: iPad Pro 13-inch (M4), iOS 18.6,
  `EE5A4848-3E5B-415A-B7A0-C36D91A6A369`.

## Model installation and compatibility

Downloaded [the official UniMERNet-small snapshot](https://huggingface.co/wanderkid/unimernet_small)
using `scripts/download_unimernet.py`. The requested branch `main` resolved to
immutable revision `82cb85440138c2038680fca410c8f70bb7343d6d`.

- Location: `backend/var/objectstore/models/unimernet_small`.
- Tree digest: `3c4b36213bb7c7f773e04ef0f5022fd1b7934d5a489869116010a63961476859`.
- Recomputed tree digest matched the installed artifact manifest after inference.
- Snapshot uses `unimernet_small.pth`, so the generic Transformers loader alone
  could not provide inference. Added an optional cached subprocess runtime and
  offline worker with strict checkpoint loading and an actual generation probe.
- Optional environment: `backend/var/objectstore/models/unimernet_runtime/.venv`;
  system-site-package inheritance disabled. Exact installed dependency pins are
  in `backend/requirements-unimernet-macos.txt`.
- Tested core versions: UniMERNet 0.2.3, Torch 2.5.1, Transformers 4.42.4,
  NumPy 1.26.4. `pip check`: no broken requirements. Fresh standalone runtime
  probe after disabling inheritance: `{"ready": true}`.
- Startup failures, request deadlines, worker termination/restart, cache reuse,
  and token-limit/truncation forwarding are covered by six focused new tests.
- Installed snapshot occupies approximately 775 MB; the runtime occupies 1.3 GB.
  Both are ignored by Git. The API environment was not replaced.

## Live formula workflow

Backend started with `HND_FORMULA_ENGINE=unimernet` on `127.0.0.1:8000`;
website on `127.0.0.1:5173`. Health was checked directly and through the
website's `/api` proxy. `formula_model.loaded` is true and the backend is
`unimernet-small`; the prose CRNN is unavailable because its checkpoint is absent.

The smoke used a disposable local account and a generated 360 × 100 PNG of the
printed expression `x + y = 2`. No credentials or tokens are included in evidence.

1. Registered through the website API proxy.
2. Imported the PNG into a document/page.
3. Created a full-page crop in the returned `normalized-raster-v1` coordinate space.
4. Submitted a persisted formula recognition job and polled to `succeeded`.
5. Reopened the region list: prediction was
   `\mathbf { x } + \mathbf { y } = 2`, engine `unimernet-small`, no truncation.
6. Saved reviewed text `x + y = 2` against the prediction and reopened it.
7. Exported LaTeX using the saved immutable document revision; exported content
   included the reviewed expression.

Retained response evidence is in
[MODEL_SETUP_FORMULA_SMOKE_2026-10-11.json](MODEL_SETUP_FORMULA_SMOKE_2026-10-11.json).
The final job completed on its first attempt in approximately 0.88 seconds.
An earlier standalone CPU smoke measured about 8.8 seconds loading and
0.55 seconds inference. These are single measurements on a printed generated
fixture, not a handwriting benchmark or proof of whole-page quality. This
validation exercised the running HTTP workflow, not browser button interactions.

## Simulator build and verification

Generated the existing Xcode project with `xcodegen generate`. Built and installed
`PersonalizedNotes` with Derived Data at `/private/tmp/hnd-ipad-setup-derived`.
The initial unsigned build passed automated tests but normal app startup showed
a Keychain error. Rebuilt with local ad-hoc signing:

```sh
cd ios
xcodebuild -project PersonalizedNotes.xcodeproj -scheme PersonalizedNotes \
  -configuration Debug \
  -destination 'platform=iOS Simulator,id=EE5A4848-3E5B-415A-B7A0-C36D91A6A369' \
  -derivedDataPath /private/tmp/hnd-ipad-setup-derived \
  build CODE_SIGNING_ALLOWED=YES CODE_SIGN_IDENTITY=-
```

Build succeeded. Installed this signed build without deleting app data and
launched it normally. A fresh screenshot showed the welcome screen with
Create Account, Sign In, and Continue Offline and no Keychain error. Simulator
and Xcode were left open. No Apple developer account is required for this
simulator signing step.

The fresh Xcode test run passed 27 native app tests and six iPad UI tests with
zero failures. Test result bundle is under
`/private/tmp/hnd-ipad-setup-derived/Logs/Test/`; raw test log is
`/private/tmp/hnd-ipad-setup-tests.log`. The test run preceded the final signing
rebuild; the signed build additionally passed the normal-launch visual check.

## Regression

Ran the full backend suite with a separate empty temporary model store, so tests
of unavailable models were independent of installed weights:

```sh
cd backend
HND_OBJECT_STORE_PATH=/private/tmp/hnd-model-setup-regression-store \
  PYTHONPATH=.. .venv/bin/python -m pytest -q --disable-warnings
```

Result: **891 passed, two skipped**, 286 warnings, 65.52 seconds. The skipped
checks require disposable PostgreSQL and S3 services. Live inference above
separately validated the real installed model. Website regression/build and
Swift package/contract checks from preparation remain recorded in the testing
plan; this setup made no website or Swift source changes.

## Remaining acceptance gates

- `devicectl list devices` returned no physical devices. There were zero valid
  development signing identities. Physical installation needs a connected,
  unlocked/trusted iPad and the user's Xcode signing/App Group setup.
- Real Pencil, palm rejection, multitasking, local-network and share-extension
  hardware acceptance remains outstanding.
- Prose recognition and personalization need the project's compatible trained
  CRNN checkpoint and metadata, or licensed real data and training.
- Native recognition-run execution, website viewing of native ink, and native
  converted-result review/export still require the stage-3 implementation.
- Representative handwritten notes and labels are required to measure quality.
- This Mac environment does not install the optional model runtime in Docker.

The [setup guide](../guides/recognition-and-ipad.md) contains restart and device steps.
