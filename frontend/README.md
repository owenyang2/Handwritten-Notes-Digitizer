# Handwriting Notes Digitizer — web client

The TypeScript/Vite client imports notes, displays original pages and recognition
crops, reviews prose or editable LaTeX, and exports saved corrections through
the shared FastAPI backend. KaTeX renders formula previews; prose stays literal
text. This is the web interface to the same documents used by the iPad client.

[Product demo](../demos/README.md) · [Current status](../docs/FEATURE_STATUS_MATRIX.md) ·
[First-time setup](../docs/GETTING_STARTED.md)

## Develop locally

Requires Node 20+ and npm 10+. From this directory:

```bash
npm ci
npm run dev
```

Start the API separately with `make dev-backend` from the repository root.
The website runs at <http://127.0.0.1:5173/> and proxies `/api` to the backend
at port 8000. Keep `VITE_API_BASE_URL` empty for this same-origin proxy.

All declared dependency versions are pinned in `package.json`; `npm ci` uses
the checked-in lockfile.

## Main modules

| Path under `src/` | Responsibility |
| --- | --- |
| `main.ts` | Application bootstrap and module wiring |
| `api/client.ts`, `api/types.ts`, `api/capabilities.ts` | Shared HTTP boundary, DTOs, availability checks |
| `modules/authGate.ts`, `modules/galleryUI.ts` | Account gate, document history, and selection |
| `modules/mathNotes.ts`, `modules/mathEditor.ts` | Document regions, source-linked formula/prose review, save/export |
| `modules/pageViewer.ts`, `modules/reviewQueue.ts` | Source pages, selected crops, review navigation |
| `modules/batchImportUI.ts`, `modules/uploadQueue.ts` | PDF/image import and queued/resumable transfers |
| `modules/syncOrchestrator.ts`, `modules/syncUI.ts` | Synchronization coordination and status |
| `modules/consentUI.ts` | Explicit training consent controls |
| `modules/editor.ts`, `modules/calibrationUI.ts`, `modules/beforeAfterView.ts` | Legacy CRNN review and optional personalization/calibration |
| `serviceWorker.ts`, `public/sw.js` | Service-worker registration and web asset caching |

The current document-conversion paths use UniMERNet/TrOCR when installed. Legacy
CRNN calibration/personalization needs its own compatible trained artifact;
TrOCR availability does not enable it. See [model setup](../docs/MODEL_AND_IPAD_SETUP.md).

## Checks

| Command | Coverage |
| --- | --- |
| `npm run typecheck` | TypeScript |
| `npm test` | Vitest and fast-check, one run |
| `npm run test:watch` | Watch mode |
| `npm run build` | Typecheck and production Vite assets |

The [October 11 review report](../docs/release-evidence/REVIEW_FIXES_2026-10-11.md)
records 175 website tests passing. The demo records live source preview,
formula correction/reopening, and Markdown download. Wider sync, accuracy, and
deployment checks remain separate. Property-test conventions are in the
[testing guide](../PROPERTY_TESTING.md).

[Project overview](../README.md) · [Documentation index](../docs/README.md)
