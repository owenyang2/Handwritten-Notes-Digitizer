---
name: Quiet Notebook
description: Quiet Notebook visual specification for web and native notes workspaces.
colors:
  canvas: "#F7F7F5"
  sidebar: "#F1F0EE"
  surface: "#FFFFFF"
  ink: "#202523"
  muted: "#626B66"
  border: "#DDE2DE"
  accent: "#272725"
  accent-soft: "#ECEBE8"
  warning: "#855700"
  danger: "#B42318"
  dark-canvas: "#191919"
  dark-sidebar: "#202020"
  dark-surface: "#242424"
  dark-ink: "#F1F1EF"
  dark-muted: "#B0AFAB"
  dark-border: "#3A3A38"
  dark-accent: "#E8E7E4"
  dark-accent-soft: "#30302E"
  on-accent: "#FFFFFF"
  dark-on-accent: "#191919"
typography:
  title:
    fontFamily: system-ui
    fontSize: 1.75rem
  body:
    fontFamily: system-ui
    fontSize: 1rem
  metadata:
    fontFamily: system-ui
    fontSize: 0.875rem
rounded:
  control: 8px
  panel: 12px
  overlay: 16px
spacing:
  xs: 4px
  sm: 8px
  md: 16px
  lg: 24px
  xl: 32px
---

# Quiet Notebook

Implemented foundation dated October 14, 2026. Web tokens, the React shell, and native appearance follow this guide. See [implementation evidence](docs/release-evidence/UI_REVAMP_2026-10-14.md) for verified behavior and remaining device/release gates. This document borrows the token-plus-rationale approach of [Google's DESIGN.md format](https://github.com/google-labs-code/design.md); schema conformance has not been tested.

## Product character

A Notion-inspired note-taking workspace with quiet navigation and a document-first layout. Handwriting, page previews, and useful results provide the visual interest. Use a warm gray sidebar, clean document surfaces, dark text, and restrained neutral actions. This supersedes the green-accent Paper and Ink proposal and its earlier mockup. Avoid decorative gradients, glass panels, oversized empty dashboard cards, and repeated promotional copy inside the app.

The signature interaction is selecting recognized text and immediately seeing its exact source highlighted. Preserve this relationship across desktop, phone, and iPad.

## Hierarchy and language

Start in Library. A collapsible sidebar contains Library, Favorites, and recent notebooks, with account/settings at the bottom. Use actual notebook titles and breadcrumbs in the workspace; omit promotional headings. Open a notebook to view pages, convert content, review results, and export. Use the same vocabulary on all platforms: Library, Notebook, Page, Convert, Review, Export. Keep calibration, training details, and API configuration in relevant advanced settings.

One visually dominant action per task area. Primary buttons use the neutral accent and its matching on-accent text; secondary actions use text, icons, or subtle neutral surfaces; destructive actions use danger styling only in the relevant menu or confirmation. Icons supplement labels; icon-only controls require accessible names.

Use direct, factual status copy: “Saved on this device”, “Syncing”, “Changes not saved”, “Ready to review”, “Source changed — convert again”. Only display states supported by actual persistence and server responses. Saving corrections never implies consent to training.

## Color and surfaces

Map tokens to CSS variables and named SwiftUI colors. Use semantic names in components rather than hardcoded hex values. Provide one Appearance selector: System / Light / Dark, defaulting to System. Preserve one layout and component system across appearances. Persist the choice locally; System follows live OS changes, while explicit overrides remain stable. Apply the web preference before first paint to avoid a theme flash. Custom accent palettes and named visual themes are deferred. Keep scanned pages and ink fidelity intact in either appearance; do not blindly invert source content.

Paper background is a separate notebook setting where supported; changing interface appearance must not alter stored ink, original scans, or export content.

Neutral borders divide structural areas. Use dividers and whitespace instead of a card around every editor region. Selected navigation uses a soft neutral fill; selected source regions also need a clear outline or label. Use shadows only for floating overlays and minimal page elevation. Success, warning, and error each need a readable text label or icon, not color alone. Dark mode needs separately checked warning, danger, focus, and selection tokens during implementation.

## Typography and density

Use system sans-serif fonts for navigation and controls, native Dynamic Type on Apple platforms, and rem-based web sizing. Body text starts at 16px, metadata at 14px, screen titles at 28px. Use a comfortable 1.5 body line height. Do not shrink essential controls to fit more actions.

Use monospace only for editable LaTeX or technical identifiers where needed. Render formulas through the existing math renderer. Keep readable transcription lines around 65–75 characters where the layout permits. Use 4/8/12/16/24/32px spacing increments; native layouts adapt to system metrics and accessibility sizes.

## Adaptive composition

Desktop: collapsible library sidebar; compact breadcrumb, notebook title and toolbar; optional notebook page rail; source and transcription side by side during review. Allow panels to collapse so content keeps adequate width.

Tablet: collapse library navigation first. Use source/result comparison when space permits; otherwise switch between them without losing the selected region or draft.

Phone: one primary pane, compact header, a notebook navigation drawer, reachable task actions, and sheets for secondary tools. Review shows the selected source crop above the editable result. An Original/Text switch preserves context. Do not stack every desktop panel into a long page.

Start web layout testing at 640px and 1024px transition points, then adjust based on actual content. Native UI uses available width and size classes rather than device names.

## Components

Web: React, TypeScript, Vite, Tailwind, and a small owned set of shadcn/ui components. Begin with buttons, fields, menus, dialogs, sheets, tooltips, tabs, badges, and progress. Add resizable panes only for the review screen. Customize shadcn primitives to these tokens rather than shipping their defaults unchanged. Wrap only recurring product patterns such as save status and notebook rows/cards. Retain existing prose/formula editing: a block editor, slash commands, and draggable content blocks are outside this release.

Native: SwiftUI navigation, menus, sheets, SF Symbols, and PencilKit. Share visual roles, labels, and behavior with web, while retaining native interaction conventions.

Every component must define relevant default, hover, focus, pressed, disabled, loading, empty, and error behavior. The implementation must keep content visible during background work and preserve focus and drafts across updates.

## Motion and feedback

Proposal: 120–180ms for small state changes, up to 220ms for overlays. Animate opacity and transform selectively. Remove the current global button lift and transition-all styling. Respect reduced motion. Native Pencil input must never wait on decorative animation or network work.

Use skeletons only for unknown content and real progress when available. Do not invent recognition percentages. Completion feedback should be quiet; save failures and conflicts must remain visible until resolved. Toasts cannot be the sole location of important errors.

## Accessibility and consistency checks

Target WCAG 2.2 AA on web. Product touch targets should be at least 44×44 CSS px on web and 44×44 points on native touch layouts; this is deliberately more generous than WCAG's 24px minimum target criterion and its exceptions. Check contrast, keyboard order, focus restoration, zoom/reflow, VoiceOver, and Dynamic Type. Provide accessible alternatives to drag-only crop selection and reordering.

Keep a small component preview screen with both appearances and realistic long labels. Check all three preference choices, OS appearance changes, persistence after reload, and unchanged drafts/source content during theme switches. Every UI change should identify its token/component choices and capture the affected desktop and phone states. Update this guide when a deliberate design decision changes; do not accumulate undocumented one-off styling.

## References

The user-supplied “Notion — Style Reference” is visual inspiration, not an implementation instruction set. Its marketing hero sizes, colorful feature panels, decorative characters, and prompt examples do not define the app UI. Use the note-taking direction agreed in conversation: compact navigation, readable documents, restrained controls.

- [Google DESIGN.md](https://github.com/google-labs-code/design.md): structured tokens and rationale.
- [shadcn/ui](https://ui.shadcn.com/docs): owned, composable UI components.
- [Vercel Web Interface Guidelines](https://vercel.com/design/guidelines): interaction review checklist.
- [Apple Human Interface Guidelines](https://developer.apple.com/design/human-interface-guidelines): native platform guidance.
- [W3C target-size guidance](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html): accessibility reference.
