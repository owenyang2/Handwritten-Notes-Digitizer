# Handwriting Notes Digitizer — web client

The React/TypeScript/Vite client imports notes, displays original pages and recognition
crops, reviews prose or editable LaTeX, and exports saved corrections through
the shared FastAPI backend. KaTeX renders formula previews; prose stays literal
text. This is the web interface to the same documents used by the native iPhone/iPad client.

[Current UI and workflow](../docs/guides/using-the-app.md) · [Current status](../docs/STATUS.md) ·
[First-time setup](../docs/guides/getting-started.md)

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
| `components/AppShell.tsx`, `components/ui/` | React Library, routes, responsive navigation, appearance, and owned shadcn primitives |
| `modules/authGate.ts` | Session entry and in-place reauthentication |
| `modules/mathNotes.ts`, `modules/mathEditor.ts` | Document regions, source-linked formula/prose review, save/export |
| `modules/pageViewer.ts`, `modules/reviewQueue.ts` | Source pages, selected crops, review navigation |
| `modules/batchImportUI.ts`, `modules/uploadQueue.ts` | PDF/image import and queued/resumable transfers |
| `modules/syncOrchestrator.ts`, `modules/syncUI.ts` | Synchronization coordination and status |
| `modules/consentUI.ts` | Explicit training consent controls |
| `modules/editor.ts`, `modules/calibrationUI.ts`, `modules/beforeAfterView.ts` | Legacy CRNN review and optional personalization/calibration |
| `serviceWorker.ts`, `public/sw.js` | Service-worker registration and web asset caching |

The current document-conversion paths use UniMERNet/TrOCR when installed. Legacy
CRNN calibration/personalization needs its own compatible trained artifact;
TrOCR availability does not enable it. See [model setup](../docs/guides/recognition-and-ipad.md).

## Checks

| Command | Coverage |
| --- | --- |
| `npm run typecheck` | TypeScript |
| `npm test` | Vitest and fast-check, one run |
| `npm run test:watch` | Watch mode |
| `npm run build` | Typecheck and production Vite assets |
| `npm run test:e2e` | Production-build workflows in Chromium, Firefox, and WebKit (install engines with `npx playwright install`) |

Browser checks include automated WCAG scans in both appearances at 1280px and
320px, keyboard focus and source selection, failed-save retry, and session recovery.
The default suite uses synthetic API fixtures. Its WebKit service-worker scenario
is covered by the opt-in live API check because worker requests bypass interception.
The Chromium-only performance sample is not field INP or representative p75 evidence.

To run live offline/reconnect checks, start an **isolated local test API** on port
8000, then run from this directory:

```bash
HND_LIVE_API_E2E=1 npm run test:e2e -- live-offline.spec.ts
```

This creates disposable accounts and notebooks from a checked-in synthetic PNG;
it runs without model weights. The web client retains unsaved drafts while the
page remains open. Reopening web notebooks needs a connection; unsaved drafts
are not durable across reload. API JSON requests bypass the HTTP cache, and private API resources
are excluded from the service-worker asset cache. Native offline storage is a
separate capability.

The [UI revamp evidence](../docs/release-evidence/UI_REVAMP_2026-10-14.md) records
the new shell, native alignment, browser checks, and remaining physical-device gates.

Use [status](../docs/STATUS.md) for current acceptance gaps and dated reports.
Property-test conventions are in the [testing guide](../docs/development/testing.md).
Refresh public screenshots after implementation changes with `make docs-screenshots`
from the repository root; see the [documentation standard](../docs/development/documentation.md).

[Project overview](../README.md) · [Documentation index](../docs/README.md)
