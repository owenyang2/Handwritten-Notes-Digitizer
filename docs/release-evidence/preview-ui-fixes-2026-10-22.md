# Preview freshness and workspace fixes — October 22, 2026

Verified on macOS 15.8 arm64 with Node 22.22.1 and local Chromium, Firefox and
WebKit. Baseline: `3e54280`; the changed frontend source is fingerprinted in the
[current capture manifest](../assets/screenshots/manifest.json), SHA-256
`2ba7208fcec33a59ded156c8d5719877890276340cf3ca26876577598a0c4ab5`.
This report records synthetic UI and cache checks, not model or native acceptance.

## Findings and changes

The earlier clean-browser checks did not reproduce the user's existing preview
cache. At `http://127.0.0.1:5173/`, an old service worker still served source
modules under stable URLs. This retained the old Theme styling and eventually
caused a missing `listenToSystemAppearance` export when old and new modules mixed.
Restarting the server alone could not replace those cached responses.

A new startup entry unregisters a legacy development worker and reloads once
before importing application modules. Development no longer registers the
worker. Production cache version 6 excludes development modules, keeps hashed
assets cache-first, and makes mutable assets network-first with offline fallback.
Private API data remains excluded. The original preview address was reloaded
and visually checked: the app loads and the Theme chevron has an inset right edge.
No account credentials or stored preferences were cleared.

The signed-in name is now “[username]’s notebook”; the signed-out name is
“Notebook”. Removed the brand tagline, Library/Settings slogans, workspace
badge and sidebar promotional text. Empty-state copy states the action and file
types. Empty recent navigation is hidden. Desktop has one collapse control, and
the account name and Log out action are left-aligned beside the avatar.

## Validation

| Check / input | Result |
| --- | --- |
| `cd frontend && npm test` | 180 tests passed |
| `cd frontend && npm run build` | TypeScript and production build passed; existing large-bundle warning remains |
| `HND_DEV_PREVIEW_URL=http://127.0.0.1:5173 npx playwright test` from `frontend/` | 45 passed, 6 skipped; synthetic APIs and notes |
| Legacy development worker serving an obsolete `appearance.ts` | Chromium, Firefox and WebKit recover on reload, retain Dark preference and lose the old controller |
| Mutable production asset seeded with a stale cache response | Network response replaces stale `appearance.js`; offline shell/reconnect journey still passes in Chromium and Firefox |
| `make docs-screenshots` | Eight current Library/review captures refreshed; all inspected at 320/1440px in light/dark appearance |
| Taste Skill app lint and browser inspection | No failures; reviewed intentional warnings below |
| `make docs-check` and `git diff --check` | Passed |

The six browser skips are the three opt-in live-API journeys, two non-Chromium
performance samples, and WebKit's mocked offline-shell journey (worker requests
bypass API interception). The new development-cache recovery test passes in all
three engines. Ordinary test artifacts were written under `/private/tmp`, keeping
historical release reports intact.

The visual pass covered desktop, tablet and phone Library, the empty Library,
and both appearances of the current Library/review captures. Search, notebook
previews and counts retain their shared edges; the phone rail is folded; original
page pixels remain unchanged. Existing Geist UI and local serif screen titles
stay in place. No direction redraw or new dependency was introduced.

Taste warnings were retained without new suppressions: warm paper is the user's
requested palette; Iowan Old Style is an installed system font rather than a web
font download; the Library's full-height sheet is workspace chrome, not a stretched
data card. Source lint flags four actual overlays' shadows and literal editable
LaTeX in monospace. The existing narrow Impeccable Geist exception is unchanged.

No physical-device, screen-reader, native, deployment, recognition-quality or
live account acceptance is claimed by these checks. Web drafts remain in memory
only while the page is open; startup recovery does not reload an already-open
review automatically.
