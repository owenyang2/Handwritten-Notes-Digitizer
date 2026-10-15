> Historical document. Use [current status](../../STATUS.md) and the [documentation index](../../README.md) for maintained instructions.

# Web and Mobile UI Revamp

Implementation started October 14, 2026. The web workspace and native alignment are implemented; acceptance evidence and remaining release gates are tracked in [UI_REVAMP_2026-10-14.md](../../release-evidence/UI_REVAMP_2026-10-14.md). The sections below retain the agreed scope and acceptance criteria.

## 1. Recommendation

Build the web experience with **React + TypeScript + Vite + Tailwind + selected shadcn/ui components**, retaining the existing API, recognition, and sync contracts. Keep **SwiftUI + PencilKit** for native iPhone/iPad. Use [DESIGN.md](../../reference/design.md) as the shared visual and interaction specification.

React is justified by the requested scope: a library, adaptive notebook workspace, coordinated source/result selection, forms, overlays, and asynchronous review states. It should reduce duplicated view-state handling. It does not itself make an interface fast or attractive; the redesign needs deliberate hierarchy and performance validation.

The agreed product direction is a **Notion-inspired note-taking workspace**: a collapsible warm-gray sidebar, clean document surface, actual notebook titles, compact controls, subtle dividers, and minimal shadows. The design guide calls this “Quiet Notebook”. It supersedes the earlier green-accent Paper and Ink proposal and mockup. The memorable moment should be turning a handwritten page into useful, correctable content while keeping the original close at hand.

## 2. What exists and what needs attention

Source audit: frontend/package.json, src/main.ts, styles.css, uploadUI.ts, mathNotes.ts, native RootView, LibraryView, NotebookEditorView, ConversionReviewView, and the current feature-status matrix. Also inspected the saved October 11 website screenshot. This is a source and historical-image assessment, not a fresh interactive usability or performance audit.

| Area | Current evidence | Design implication |
| --- | --- | --- |
| Web stack | Vite, TypeScript, custom DOM modules/CSS; KaTeX is the only declared runtime dependency | React/shadcn adoption is a real migration; preserve working domain code |
| Web hierarchy | Workflow rail and recognition workspace; seven utility tabs with calibration initially active; library under History | Make Library the starting point and move advanced tools out of the primary journey |
| Web styling | Dark-only root palette, multiple breakpoint overrides, global hover lift; reduced-motion rule already exists | Establish semantic themes and simplify layout rules while retaining accessibility work |
| Review | Source crop, editable result, preview, multiple export buttons, training/exclusion controls | Separate review, export, and optional training consent into clear task contexts |
| Native | SwiftUI/PencilKit, library grid/list/search, offline storage, page navigation and import/export | Refine the existing native foundation rather than replacing it with a web wrapper |
| Native conversion | Fixed 240-point source area above a result list and several actions | Make wide-screen comparison adaptive and compact review region-focused |
| Recognition quality | Current evidence explicitly records an incorrect prose demo result and incomplete handwriting evaluation | UI must support correction and uncertainty; no accuracy claims based on appearance |

Preserve the existing reliability safeguards: source retention, stale-source checks, revision conflicts, durable native ink, explicit training consent, capability gating, and recoverable uploads. Existing workspace changes predate this plan and are outside its scope.

## 3. Design references and consistency

Use the user-supplied Notion style reference as visual inspiration. Its marketing-page layouts, oversized headlines, colorful feature sections, and decorative characters are not requirements for the working app. The agreed reference is the quiet experience of a note-taking workspace. Preserve our own handwriting/source-review workflow.

[Google's DESIGN.md](https://github.com/google-labs-code/design.md) provides a useful way to keep exact tokens beside design rationale. Adopt the approach with our own product direction rather than copying another product's identity. The draft guide includes colors, typography, spacing, component behavior, responsive rules, motion, and accessibility.

[shadcn/ui](https://ui.shadcn.com/docs) supplies component source that we own and customize. Its [Vite setup](https://ui.shadcn.com/docs/installation/vite) supports a React/TypeScript and Tailwind foundation. Use one consistent component primitive family and theme; install only components needed for the current screens.

Use [Vercel's guidelines](https://vercel.com/design/guidelines) as an interaction checklist and [Apple's HIG](https://developer.apple.com/design/human-interface-guidelines) for native conventions. These references complement our product-specific guide; none determines our information architecture automatically.

Consistency comes from three concrete artifacts: DESIGN.md, shared web primitives/semantic CSS tokens, and native named colors/text styles. Avoid a token-generation service or a large internal design-system package initially. Keep matching semantic roles in two small implementations and verify them visually.

### Appearance implementation

Ship one selector in Settings: **System / Light / Dark**, defaulting to System. Use one layout, typography scale, and component set across appearances. Defer Forest/Blue accent choices, named theme packs, and custom theme editing until the default experience is established.

Map semantic surface, sidebar, text, border, action, on-action, selection, and focus tokens into Tailwind/shadcn and SwiftUI. Keep success/warning/error meanings stable. Persist the web preference locally and resolve it before first paint; listen for OS changes only in System mode. Persist native preference using the existing settings mechanism and apply it at the app root. Cross-device appearance sync is not required for this release.

Keep paper background independent of app appearance where supported. Theme switches must never recolor original scans, mutate stored ink, alter exports, remount editors, or discard drafts. Validate light and dark controls and source overlays separately.

## 4. Information architecture

Primary journey: **Library → Notebook → Convert → Review → Export**. Conversion and review are notebook tasks, not global destinations users must understand before importing.

| Destination | What belongs there |
| --- | --- |
| Library | Recent notebooks, search, supported organization, favorites, archive/trash, Import/New |
| Notebook | Page navigation, original content, current save/sync state, Convert and Export |
| Review mode | Original/result comparison, current region, correction, exclusion, next item |
| Settings | Appearance, account, sync details, privacy and optional personalization; development configuration under Advanced |

Use a collapsible desktop sidebar with Library, Favorites, and recent notebooks; place account/settings at the bottom. Notebook pages live in a separate collapsible rail within the notebook context. On phones, expose notebook navigation through a drawer rather than a persistent sidebar.

On web, use addressable library and document/page URLs with browser back/forward support. Put transient menu/dialog state outside the URL; preserve meaningful document, page, and review selection in navigation state. A lightweight established React router is reasonable for these routes; select one during foundation work and avoid a custom routing framework.

Use the same destination labels across platforms. Hide unsupported actions or explain temporary unavailability at their point of use. Do not show a nonfunctional training setup as the first experience.

## 5. Screen-by-screen experience

### Library and first use

Open to a simple “Library” heading, search, and one primary Import/New action appropriate to platform capability. Show recognizable page thumbnails, title, page count, and last edited time. A compact status marker appears only when a notebook needs attention. Reuse existing list/grid and organization capabilities; avoid introducing a new folder backend during visual work.

For an empty library, explain the outcome in one sentence, then offer Import notes and, where supported, Create notebook. A small optional sample can demonstrate review only if clearly labeled as sample content; do not silently upload user material or pretend a fixture is a live recognition result.

Put rename, duplicate, archive and trash actions in a contextual menu with discoverable alternatives to gestures. Show bulk actions only after selection. Preserve search and scroll position when returning from a notebook.

### Import and capture

Desktop supports drop, file picker, and existing batch upload. Mobile offers Photos, Files, and available camera capture in a sheet. Camera denial must leave the file picker available. Preview selected items, show destination, and let users remove mistakes before starting.

Show truthful stages: Uploading, Preparing pages, Ready, or Failed with Retry. Separate import completion from optional conversion. Preserve per-item success in partial failures. Resume from existing transfer state where supported; do not add a fake cancel button unless the operation can really be canceled.

### Notebook workspace

Use the actual notebook title and breadcrumb above the page, with page position, save/sync status, Convert and Export in a compact toolbar. Remove promotional headings such as “A little clarity for your ideas” from the earlier mockup. Give the document the dominant area; use whitespace and dividers instead of boxing every section into a card. Most actions should be quiet text/icon controls, with one emphasized action when the task requires it. Place page thumbnails in a collapsible rail. Use a neutral canvas around the document, with visible zoom controls and fit-to-width. Preserve selection and viewport when opening secondary tools.

Native writing keeps PencilKit tools close to the canvas and page management separate from conversion. Maintain existing save boundaries when backgrounding or navigating. Web editing capabilities follow the current product contracts; a browser handwriting engine is outside this redesign.

### Source-linked review: the signature experience

On desktop and wide iPad, show Original and Result side by side. Selecting a text/formula region highlights its source; selecting a source region selects the matching result. Preserve region IDs and source coordinate transforms at every zoom level.

Show a compact “Needs review” queue where supported by actual result metadata. Avoid invented confidence scores. For the current item, offer edit, save/accept, skip, and next; put split/merge/retry/exclude under contextual tools. Explain whether “accept” saves the draft and what remains unsaved.

For formulas, the formatted result is primary; editable LaTeX opens when needed. For prose, use readable text with clear correction focus. Use existing renderers and sanitization boundaries. Preserve distinctions between prediction, edited draft, saved review, and stale source.

Initially keep explicit Save review behavior with a dirty-state indicator. Autosave is a later decision requiring conflict, retry, and navigation-loss checks; visual polish must not silently change persistence semantics. Drafts survive pane switches and responsive layout changes.

When source content changes, keep the user's correction visible and explain why reconversion is required. If a save conflicts, retain the draft and offer reload/compare/retry according to supported contracts. A general error toast is insufficient.

### Export

Replace the row of format buttons with one Export entry point. Its dialog/sheet lets users choose available format and scope, shows the saved revision context, and explains exclusions. Use a sensible default based on task and remember the user's last valid choice locally.

If drafts are unsaved, offer Save and export or explicitly Export last saved version. Never silently export a different state from what the user expects. Native uses the system share sheet after export succeeds. Only show supported platform/format combinations.

### Settings, privacy, and sync

Make local save and cloud sync distinct. “Saved on this device” must not mean “Synced”, and mobile web must not inherit native offline promises without implementation evidence. Account sign-out, expiration, unavailable recognition, and sync conflicts each need a clear recovery path.

Move training enrollment and calibration to an optional personalization section. Preserve explicit consent and per-example choices where required; moving a control must not change what is collected. Retain advanced tools for existing users through a discoverable settings path.

## 6. Desktop, mobile web, iPhone and iPad

| Context | Layout | Primary interaction |
| --- | --- | --- |
| Wide desktop web | Compact navigation; page rail; adjustable source/result columns | Keyboard/mouse review, drag import, visible shortcuts |
| Narrow desktop/tablet web | Collapsed navigation; optional page rail; two panes only if readable | Touch and keyboard, optional focus mode |
| Phone web | One pane with Original/Text switch; selected crop above edit field | Reachable actions, camera/file import, keyboard-aware layout |
| Native iPhone | Navigation stack, sheets, compact library and review | System pickers, native sharing, clear back navigation |
| Native iPad | Adaptive navigation and canvas; comparison pane in review | Pencil writing, keyboard shortcuts, multitasking resize |

Start with web transitions near 640px and 1024px, but validate actual content rather than hardcoding device categories. Test 320px through 1440px, portrait/landscape, browser zoom, safe-area insets, and software keyboard opening. iPad Split View must use the compact layout when needed. A bottom action bar must not cover the focused field or final review item.

Use platform-native presentation for menus and sheets while keeping color roles, terminology, status meaning, and content hierarchy aligned. Android receives responsive web support; a new native Android app is outside scope.

## 7. React migration and library choices

Keep Vite, TypeScript, KaTeX, the API client/types, capability registry, and compatible sync/upload logic. Add React/React DOM and the Vite integration, Tailwind, selected shadcn components, and one web icon family. Keep native SF Symbols. Pin reviewed compatible versions at implementation time.

Begin with local React state and small context boundaries for session/document selection. Do not introduce Redux, a general event bus, or a second cache over the existing sync system. Add a query library only if measured server-state complexity warrants replacing a clearly identified layer.

Migrate in complete slices: application shell/library, import, notebook workspace, then review/export. While a complex DOM module remains, mount it in an isolated React host with explicit cleanup. Never let React and a legacy module own the same child elements. Verify event listeners, polling, object URLs, and subscriptions are released on unmount.

Give page/region IDs stable ownership so rerenders cannot reset drafts, zoom, or selections. Preserve error types and request semantics. Convert presentation code rather than rewriting recognition or sync logic. When a slice is accepted, remove its unused DOM renderer and CSS; avoid maintaining two permanent UIs.

Update Vitest configuration to include TSX tests; it currently includes only .ts test/spec patterns. Reuse existing API and domain tests. Add browser-level workflow coverage for migration risks. Keep backend changes in separate work only when an actual missing contract blocks a promised interaction.

A Notion-style block editor, slash commands, draggable content blocks, Next.js/SSR, an animation framework, a new rich-text editor, a new native framework, and a backend rewrite are not needed for this app revamp. A future public marketing website can be considered separately.

## 8. Delivery sequence and acceptance gates

| Phase | Deliverable | Gate before moving on |
| --- | --- | --- |
| 0: Baseline and design | Current journey recordings, state inventory, desktop/phone/iPad wireframes reflecting the agreed note-taking direction, finalized neutral tokens | Review one realistic import → correction → export journey, including a failure |
| 1: Foundation | React/Vite integration, System/Light/Dark preference, themed shadcn primitives, component preview, collapsible sidebar | Appearance persists without first-paint flash; System tracks OS changes; keyboard focus, routing and compact layout work; old flows remain reachable |
| 2: Library and import | New default landing screen, thumbnails/search, contextual actions, import/capture sheet | Existing documents open; partial import failures recover; back preserves context |
| 3: Notebook and review | Adaptive source/result workspace, region selection, correction states, unified export | Source mapping stays exact; drafts survive resize; stale/conflicting saves remain safe; exports match saved revision |
| 4: Native alignment | Library/canvas chrome, native appearance preference, adaptive conversion review, settings hierarchy, shared language | Offline writing/relaunch remains intact; compact/wide native layouts and Pencil path validated |
| 5: Polish and release | Performance/accessibility passes, recovery screens, cleanup of migrated CSS/modules | End-to-end acceptance on supported browsers/devices; recorded evidence and no unresolved data-loss failures |

Prefer a complete, convincing vertical slice before polishing every settings screen. The first milestone is a real notebook imported, opened, corrected against its source, and exported through the new desktop and phone web layouts. Native follows the same design direction without waiting for shared web code.

Indicative effort for one experienced engineer: phase 0, 2–3 days; phase 1, 3–5; phase 2, 4–6; phase 3, 6–10; phase 4, 4–7; phase 5, 3–5. Total roughly 22–36 engineering days, subject to the baseline audit and native/device access. Backend gaps or recognition-quality work are separate. These are planning estimates, not delivery commitments.

## 9. Validation and measurable quality

Accessibility: WCAG 2.2 AA target, 44px/44pt product touch targets, visible keyboard focus, semantic menus/dialogs, focus restoration, labeled controls, contrast checks, screen-reader status announcements, 200% zoom and 320px reflow, native Dynamic Type and VoiceOver. Crop and reorder operations need an alternative to precise dragging. See [W3C's target-size criterion](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html) for the distinction between the standard minimum and our product target.

Performance targets, to be measured rather than claimed: acknowledge input within 100ms; target web p75 LCP ≤2.5s, INP ≤200ms, and CLS ≤0.1 on representative usage. Record a baseline and bundle-size delta after React migration. Keep typing and page navigation responsive while recognition runs. Lazy-load heavy review/math code where helpful, size thumbnails appropriately, and prevent whole-document rerenders while typing. Add virtualization only when realistic notebook sizes show a need.

Test the high-risk journeys: import → open → select source → edit → save → export; refresh/deep link/back navigation; source change during review; save conflict; interrupted upload; offline/reconnect; account expiration; unavailable recognition; native background/relaunch with ink; keyboard opening while editing a phone review. Reuse current tests and add focused browser/native checks around these behaviors.

Verify theme switching while editing, preference persistence after relaunch, OS appearance changes in System mode, and unchanged source/ink/export content across appearances. Capture visual regression states for populated, empty, loading, error, long-title, dark, and compact screens. Verify Safari/iOS, Chromium, and Firefox against an explicit support matrix. Physical iPad testing is required for Pencil/palm rejection and real writing comfort; simulator success is not a substitute.

Run a small usability round with approximately five representative note-taking users. Proposed success criterion: at least four complete import → correction → export without coaching; all can identify whether their latest edit is saved; no participant mistakes a correction for training consent. Record baseline and redesigned task times and stumbling points. Do not send note content to analytics; begin with observed sessions and aggregate task measures.

## 10. Scope and unresolved evidence

This plan includes responsive web and native iPhone/iPad UI. It does not claim verified native iPhone readiness, full web offline parity, production recognition accuracy, or existing support for every proposed interaction. Phase 0 maps each interaction to current contracts and identifies gaps explicitly.

The highest risks are lost drafts during React remounts, broken source coordinates after layout changes, mismatched export revisions, duplicate sync subscriptions, and regressions to native ink persistence. Mitigate them with stable state ownership, incremental replacement, and the acceptance journeys above.

Keep the existing implementation recoverable through normal version control and reversible delivery slices until acceptance. Avoid retaining a permanent legacy mode or adding a feature-flag service just for this migration.

The implementation now includes the React shell and appearance, Library/import, source-linked review/export, and native alignment. The remaining acceptance work is the physical-device, accessibility, performance, and usability evidence listed in the [implementation report](../../release-evidence/UI_REVAMP_2026-10-14.md). Keep those release gates open until their results are recorded.
