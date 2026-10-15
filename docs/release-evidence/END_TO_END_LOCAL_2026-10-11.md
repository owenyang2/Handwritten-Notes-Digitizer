# Local conversion acceptance — 2026-10-11

## Result

The bounded local workflow passes with both installed real recognition models:
website import/review, native sign-in and library restore, page conversion,
source-linked review, saved review reopening, and converted Markdown export.
The [84-second visual demo](../../demos/end-to-end-demo.mp4) contains the actual
simulator journey plus labelled website screenshots. The complete end-to-end
plan retains unchecked broader acceptance gates.

## Source and environment

- Branch: `platform-sync-uploads`, already current with its remote and six local
  commits ahead when fetched in this chat. Existing uncommitted work was retained.
- Commit: `4b6ebb5152b2eaf43138f34784abf0befa1da492`.
- Final source fingerprint: `b82f62b1f229ff96c8979e89396b227ed1042c64f6ef9e893d66c30d6f76bf21`.
  Computed over 317 nonignored files under `backend`, `frontend`, `ios`,
  `contracts`, and `scripts`: sorted UTF-8 path, NUL, then each file's binary
  SHA-256. Documentation, model weights, environments and generated build outputs
  are outside this fingerprint.
- macOS arm64; Xcode 16.4; iPad Pro 13-inch (M4), iOS 18.6 simulator
  `EE5A4848-3E5B-415A-B7A0-C36D91A6A369`; local ad-hoc signing.
- Website: `http://127.0.0.1:5173`; backend: `http://127.0.0.1:8000`.
- UniMERNet-small revision `82cb85440138c2038680fca410c8f70bb7343d6d`;
  TrOCR handwritten revision `eaacaf452b06415df8f10bb6fad3a4c11e609406`.
  Both run offline on CPU in the isolated model environment. Final health reports
  both task models loaded. Legacy CRNN remains unavailable.

## Validation

| Check | Actual result |
| --- | --- |
| Full backend regression with isolated model store | 900 passed, 2 skipped; 125.18 seconds |
| Website tests, typecheck and production build | 174 passed; build succeeded |
| Swift package tests, including actual backend timestamp formats | 29 passed |
| Final native app unit tests | 27 passed |
| Earlier native UI regression | All 6 passed at 17:08; this predates the final live-demo fixes |
| Final original PDF/export-menu UI regression | 1 passed at 17:56 against the final build |
| Opt-in real-model native acceptance | 1 passed at 17:55; formula and prose paths in one test, 74.619 seconds |
| Generated API contracts and Swift model boundary checks | Passed; 31 Swift model checks |
| Live API conversion, review, reopening and Markdown exports | Both real models passed; retained JSON evidence |
| Delivered video | 768 × 1024 H.264, 24 fps, 84 seconds; entire output decoded successfully |

The two skipped backend checks need separate PostgreSQL and S3/MinIO services.
Warnings remain in the backend test output; no test failures remain in these
checks. Raw local UI logs are not published because login automation can log
credential keystrokes.

## Live observations and fixes

- Native runs now execute through the durable worker using frozen source crops.
  Saved canonical strokes compose with imported backgrounds; results retain
  source revision identity and stale sources are rejected.
- Formula and prose availability are checked separately. Cold model preparation
  happens before the inference deadline, including recovered queued runs.
- The iPad review supports editing, saving, source highlighting, region actions,
  exclusion, and converted Markdown, LaTeX and HTML exports. The final live UI
  test exercises Markdown; the other format paths have API/code coverage.
- Live sign-in exposed a Keychain query bug. Data-return flags are now limited
  to reads. Native date decoding accepts the fractional, timezone-less UTC
  timestamps actually emitted by the local backend.
- Native sync restores website-created imports that predate the operation log.
  Legacy imported documents expose source metadata and the correct image MIME
  type. The transparent PencilKit canvas now shows imported backgrounds.
- Website review now loads the actual source crop, including after reopening.
  Prose review renders literal text. The formula correction `x + y = 2` was
  saved, reopened and downloaded through the browser, then seen in native review.
- Review action controls are accessible in the sheet. Export is a regular
  button opening a format chooser; conversion actions appear at the top of the
  editor menu. Saving reports completion after the refreshed review is ready.

The final opt-in UI test used a disposable local account. It signed in through
the app, restored the library, requested new recognition runs, viewed the frozen
source, saved each block, opened Markdown sharing, closed and reopened review,
and asserted identical saved text for both tasks. The model/backend connections
were real; normal CI skips this test unless demo credentials are supplied.
Local Xcode result bundle: `/private/tmp/hnd-live-demo-10.xcresult`.

## Limits and remaining work

The printed formula probe and public IAM-derived word establish functioning
inference, not representative handwriting accuracy. TrOCR predicted `indus the`;
that transcription needs correction. No accuracy or personalization improvement
claim is supported by this sample.

Physical iPad installation still needs the user's connected device and Apple
signing account. Hardware Pencil/palm rejection, rotation, multitasking and
physical share-extension behavior need device testing. Full live offline drawing
to a second independent native installation, interrupted uploads, concurrent
edits, production service integration and broader mixed-document acceptance
remain unchecked in the plan. Focused automated tests cover parts of those
behaviors; they do not replace live acceptance.

Optional personalization additionally needs a verified compatible trained CRNN
artifact and separate real-data quality evaluation. Installed TrOCR does not
enable that legacy training workflow. No further downloads are required for the
local formula/prose conversion demonstrated here.

## Evidence

- [Real-model API results](LIVE_CONVERSION_2026-10-11.json).
- [Initial model/simulator setup evidence](MODEL_AND_IPAD_SETUP_2026-10-11.md)
  (historical setup stage).
- [Current setup and physical-device instructions](../guides/recognition-and-ipad.md).
- [Acceptance plan](../archive/plans/END_TO_END_TESTING_PLAN.md).
- [Demo explanation and screenshots](../../demos/README.md).
- [Machine-readable source, model and video manifest](END_TO_END_LOCAL_2026-10-11.json).

At handoff, the regular simulator app was open and signed into the disposable
demo account. Manual Sync completed with "Online" and no pending operations.
The backend and website were left running. Test runner stores remain separate
from the normal local library.
