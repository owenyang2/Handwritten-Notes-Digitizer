---
name: Notebook — Research Folio
description: The implemented web workspace keeps original handwriting beside corrections.
colors:
  bg: "#f3f0e9"
  sidebar: "#f3f0e9"
  panel: "#fffdf8"
  panel-soft: "#f4f1e9"
  ink: "#342e29"
  muted: "#70675d"
  line: "#e4ddd1"
  line-strong: "#b6aa99"
  accent: "#365b45"
  accent-strong: "#294836"
  on-accent: "#fffdf8"
  focus: "#38664b"
  green: "#256744"
  amber: "#855700"
  red: "#b42318"
  selection-soft: "#e3ecdf"
  source-outline: "#326448"
  source-fill: "#32644814"
  source-selected: "#32644829"
  green-soft: "#e4f0e8"
  amber-soft: "#fff3d8"
  red-soft: "#fff0ed"
  dark-bg: "#201e1a"
  dark-sidebar: "#201e1a"
  dark-panel: "#292620"
  dark-panel-soft: "#332f28"
  dark-ink: "#f3ecdf"
  dark-muted: "#bcb2a2"
  dark-line: "#464035"
  dark-line-strong: "#827766"
  dark-accent: "#b9d2b5"
  dark-accent-strong: "#d0e3ca"
  dark-on-accent: "#223126"
  dark-focus: "#b9d2b5"
  dark-green: "#92d5ad"
  dark-amber: "#f0c56a"
  dark-red: "#ffafa4"
  dark-selection-soft: "#384735"
  dark-source-outline: "#326448"
  dark-source-fill: "#32644814"
  dark-source-selected: "#32644829"
  dark-green-soft: "#1f3529"
  dark-amber-soft: "#392e1b"
  dark-red-soft: "#3c2622"
typography:
  headline:
    fontFamily: '"Iowan Old Style", "Palatino Linotype", Georgia, serif'
    fontSize: "2rem"
    fontWeight: 500
    lineHeight: 1.3
    letterSpacing: "-0.03em"
  document-title:
    fontFamily: '"Iowan Old Style", "Palatino Linotype", Georgia, serif'
    fontSize: "1.75rem"
    fontWeight: 500
    lineHeight: 1.3
    letterSpacing: "-0.03em"
  title:
    fontFamily: 'Geist, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "1.125rem"
    fontWeight: 600
    lineHeight: 1.5
  body:
    fontFamily: 'Geist, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: 'Geist, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "0.875rem"
    fontWeight: 500
    lineHeight: 1.5
  metadata:
    fontFamily: 'Geist, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "0.75rem"
    fontWeight: 400
    lineHeight: 1.5
  prose:
    fontFamily: 'Geist, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.6
  latex:
    fontFamily: "ui-monospace, monospace"
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.5
rounded:
  crop: "6px"
  navigation: "7px"
  control: "8px"
  group: "10px"
  panel: "12px"
  sheet: "18px"
  overlay: "16px"
spacing:
  xs: "4px"
  sm: "8px"
  gap: "12px"
  md: "16px"
  panel: "20px"
  lg: "24px"
  xl: "32px"
  section: "40px"
  page: "48px"
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.on-accent}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "0.5rem 0.75rem"
  button-primary-hover:
    backgroundColor: "{colors.accent-strong}"
    textColor: "{colors.on-accent}"
  button-secondary:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "0.5rem 0.75rem"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "0.5rem 0.75rem"
  button-danger:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.red}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "0.5rem 0.75rem"
  field:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "0.55rem 0.75rem"
  navigation-item:
    textColor: "{colors.muted}"
    rounded: "{rounded.navigation}"
    padding: "0.6rem 0.75rem"
  region-chip:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    typography: "{typography.metadata}"
    rounded: "{rounded.control}"
    padding: "0.5rem 0.8rem"
  notebook-card:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    rounded: "{rounded.panel}"
    padding: "0"
  correction-field:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    typography: "{typography.prose}"
    rounded: "{rounded.control}"
    padding: "1rem"
  dialog:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    rounded: "{rounded.overlay}"
    padding: "1.75rem"
    width: "640px"
---

# Design System: Notebook — Research Folio

## Overview

**Creative North Star: "Research Folio"**

Warm paper surrounds an inset notebook surface. Forest-green controls, serif
headings and compact Geist text make the workspace feel personal. Real
handwriting remains the focus; source, correction and save state stay easy to
compare. This warmer treatment follows the user’s request for a cozier workspace
and clearer selections.

The signed-in web name is “[username]’s notebook”; the sign-in screen and
installed web app use “Notebook”. Keep copy functional: no brand tagline,
Library subtitle, workspace badge or sidebar slogan. An empty Library says
“No notebooks yet” and names the accepted import formats beside the action.

This is the current **web** system. Its source of truth is the
[web stylesheet](../../frontend/src/styles.css),
[React shell](../../frontend/src/components/AppShell.tsx) and
[review workspace](../../frontend/src/modules/mathNotes.ts). The frontmatter
records implemented web tokens; `dark-` entries are the dark values of the same
CSS roles. The [surface brief](../../.impeccable/surfaces/web-workspace.md) owns
page composition. [Status](../STATUS.md) owns acceptance and remaining limits.

Native iPhone/iPad retains the October 14 **Quiet Notebook** foundation: system
fonts with Dynamic Type, warm gray canvas/sidebar, neutral prominent actions,
SwiftUI navigation, sheets and PencilKit. Its
[appearance implementation](../../ios/Sources/PersonalizedNotesApp/SettingsView.swift)
and [dated evidence](../release-evidence/UI_REVAMP_2026-10-14.md) remain applicable.
The web palette, Geist font and folio geometry below do not migrate native UI.

**Key Characteristics:**

- Warm paper, soft green accents and a comfortable notebook surface.
- Forest-green actions, visibly selected controls and labeled feedback.
- Real page previews and a visible source-to-correction relationship.
- Explicit saving, recoverable drafts and deliberate training consent.

## Colors

The palette uses warm paper in light appearance, brown-charcoal surfaces at
night and forest-green actions. Use existing semantic CSS variables in components.

### Primary

- **Forest action** (`accent`, `accent-strong`, `on-accent`): dominant task actions
  and their hover state; the matching foreground changes with appearance.
- **Evidence green** (`focus`, `selection-soft`): keyboard focus, caret, selection and
  active review-region chips. Source outlines use `source-outline` against
  original paper in either appearance; `source-fill` and `source-selected`
  distinguish available and selected regions without changing source pixels.

### Neutral

- **Warm desk** (`bg`, `sidebar`): the workspace outside the inset sheet.
- **Document sheet** (`panel`): content, controls and opaque floating surfaces.
- **Recessed surface** (`panel-soft`): source surrounds, thumbnail stages and
  secondary groups.
- **Reading ink** (`ink`, `muted`): primary text and supporting metadata.
- **Structural rules** (`line`, `line-strong`): quiet divisions and stronger
  field or hover boundaries.
- **Labeled feedback** (`green`, `amber`, `red` and their `-soft` partners):
  success, warning and failure. Always pair color with readable status text or
  an icon; reserve destructive red for the relevant action or error.

**The Original Fidelity Rule.** Appearance changes interface chrome, never
stored ink, scan pixels or export content. Original crops retain their own
colors and white paper. Notebook paper settings remain a separate concern.

System / Light / Dark is one locally persisted appearance choice. System
follows OS changes before and after sign-in; explicit choices stay stable. **Theme**
is visible in the main toolbar and on the sign-in screen, so dark mode does
not require opening Settings. The existing Settings Appearance selector stays
synchronized. Apply web appearance before first paint and preserve drafts and
credentials during a theme change.

## Typography

**Heading font:** Iowan Old Style / Palatino / Georgia, using local system
serifs. **Body and interface font:** locally served Geist with system fallbacks.
**Technical font:** platform monospace for editable LaTeX.
Rendered formulas use the existing math renderer. Native keeps system fonts.

Geist is an intentional choice from Taste Skill's Achromatic ledger direction
(seed 244730145). The design detector's overused-font exception covers Geist
only; other typefaces and design checks remain subject to review.

The hierarchy combines softer serif screen titles with practical sans controls,
normal reading
text and smaller supporting metadata. Screen headings use `headline`; notebook
headings use `document-title`; both reduce to (1.5rem) below the phone breakpoint.
Section titles use `title`, buttons use `label`, and counts/dates use `metadata`.
Muted supporting copy also uses (0.875rem). Preserve prose line height and allow
long titles and status messages to wrap. Readable transcription lines should
stay around 65–75 characters where space permits; this is a reading goal, not
an implemented width cap.

Settings uses Geist consistently for section headings (1rem, weight 600),
supporting copy and controls (0.875rem, line height 1.6). The same roles apply to
Account, Appearance, Sync and the optional sections. Serif type stays on page
titles; dense accuracy-table values use the smaller metadata role. The
[October 20 fixes](../release-evidence/ui-fixes-2026-10-20.md) verify the drift
identified in the earlier audit.

Single-choice selects share a 44px height and 0.875rem type. Their chevron sits
12px inside the right edge with 40px reserved on that side for clear text/arrow
separation. The control retains native select behavior; forced-colors mode uses
the browser decoration. Avoid local padding or font overrides that undo these
shared rules.

**The Content Type Rule.** Prose corrections use the interface face; LaTeX uses
monospace. Do not make ordinary notes look like source code.

## Layout

Use the existing spacing rhythm in the frontmatter, structural dividers and
whitespace. Desktop navigation occupies (252px); the main sheet is inset by
(12px), with a sticky (64px minimum) top bar. Content uses (3rem) vertical padding
and horizontal padding that grows from (1.5rem) to (4rem). Library content caps
at (1100px); settings and component previews cap at (920px).

Below (1024px), navigation becomes a modal surface and the main sheet meets the
viewport edge. Below (640px), content uses (1rem) horizontal padding, Library
controls wrap, settings become one column, and review shows one pane at a time.
Keep the Original / Result switch and the selected original crop above its
correction. Desktop review pairs Original and Result with a shared border;
pane balance is secondary. Do not lose the active region or draft when changing
route, pane, layout or appearance.

Library shelves use equal columns with a minimum width of (240px). At (640px)
and above a single notebook fills the shelf with a horizontal preview; list
view uses compact rows. Display real notebook titles, pages and loaded counts.
Keep archive, trash, Favorites, search and notebook actions reachable.

## Elevation & Depth

**The Flat Sheet Rule.** Resting content is flat. Borders and surface changes
establish hierarchy; shadows belong to menus, page/view popovers and dialogs.
Cards gain a stronger border rather than lifting on hover. Floating surfaces
are opaque, with restrained dark scrims behind modal dialogs. Exact shadow and
motion values live in the [design sidecar](../../.impeccable/design.json).

Button feedback lasts (150ms); thumbnail travel lasts (220ms); opening menus
move briefly over (160ms) without fading their text. Reduced motion removes
transitions and animation. No decorative motion may delay source selection,
editing or native Pencil input.

## Shapes

Use gently rounded controls, slightly broader panel corners and the broadest
corners on overlays. The frontmatter names the implemented radius roles.
Keep borders thin (1px), page previews rectangular, and crop edges faithful to
the source. The sheet has rounded corners on desktop and meets viewport edges
on compact layouts. Avoid decorative gradients and glass surfaces.

## Components

### Buttons

Owned [UI primitives](../../frontend/src/components/ui/primitives.tsx) provide
primary, secondary, ghost and destructive variants. Use one dominant action
per task area. Primary pairs an ink fill with its matching foreground; secondary is a
bordered document surface; ghost is transparent; destructive uses error ink.
Hover changes surface or ink-action tone; pressing moves a button down (1px).
Disabled controls retain context with reduced opacity and no action. Keep
visible focus (2px outline, 3px offset), readable labels and product touch
targets of at least (44×44px). Icon-only controls need accessible names.

### Inputs / Fields

Fields use a stronger structural border on the document surface, rounded
control corners and visible green focus. Labels remain available to assistive
technology. Search is a field inside the Library control group; its borderless
interior must not remove the focus outline. The prose correction area is
vertically resizable with a minimum height of (180px); LaTeX keeps monospace.

### Navigation

Navigation links use muted text and a compact icon. Selected links have a green
tonally filled surface, a visible outline and heavier text; hover alone remains
subtle. Indicate the current route semantically. Grid/list and phone review
selection use the filled action color, so the current choice is unmistakable. The collapsible desktop rail
holds Library, Favorites, recent notebooks, Settings and account controls.
Hide the recent-notebook heading when its list is empty. Show one collapse
control in the expanded desktop rail; the toolbar provides the reopen control.
Library, Favorites and Settings use the sidebar and their page heading; the
toolbar does not repeat those names as links. An open notebook has a breadcrumb
with a “Back to Library” link and its current title. On phones, retain the back
link and hide the title/chevron so the toolbar actions stay within the viewport.
The avatar and account name share a 44px row. The avatar is centered on the
navigation icon column; the name aligns with navigation labels. Log out is a
separate full-width navigation row with its own icon, on those same columns.
Long account names truncate with the full value available in the title; long
workspace names wrap.
Compact navigation uses a dialog with focus containment and restoration. Keep
the skip link, keyboard order, Escape behavior and focus restoration intact.

#### Navigation continuity

Keep the shell, recent notebooks and account footer stable while changing
sections. Recent navigation comes from the unfiltered active notebook list,
not the current search, Favorites, Archive or Trash results. Each collection
retains its last loaded query/page in memory; returning to it reuses that data
without a loading flash. Imports, edits and sync invalidate the retained data.
Search and refresh keep the scope's previous results visible with an updating
status and busy semantics until the new response arrives.
Pagination and request-generation guards still apply. Another collection's
cards never appear under the new heading while its first request is pending.

Reserve the scrollbar gutter so routes of different lengths do not move the
shell sideways. Navigation colors respond in 140ms; content changes directly,
without whole-page fades or staggered entrances. Reduced motion removes those
transitions. Select hover changes only background color so its chevron persists.
Notebook changes hide a previous notebook's review before paint. Review page
changes keep the previous controls in place while loading, block interaction
with that page, and commit the new page together; failures preserve the old
page and drafts for retry.

These choices apply the persistent shell in
[Carbon's navigation guidance](https://www.carbondesignsystem.com/building-blocks/core/components/ui-shell-header/guidelines),
the dedicated user/action footer in
[shadcn's sidebar anatomy](https://ui.shadcn.com/docs/components/base/sidebar#sidebarfooter),
and brief, restrained feedback from
[NN/g's motion guidance](https://www.nngroup.com/articles/animation-purpose-ux/).
Google's [layout-stability guidance](https://web.dev/articles/cls) explains why
delayed content and changing geometry need explicit checks. These are layout
and motion principles, not a replacement for this project's visual system.

### Chips / Region Queue

Region chips are horizontal, scrollable and text-bearing. Selection uses
evidence green and a stronger border; unresolved regions also have a visible
marker. Preserve `aria-pressed` and a readable review count. The queue is a
selection control, not a decorative tag cloud.

### Cards / Containers

Notebook cards combine real previews, titles, page/date metadata and a labeled
actions menu. Use flat borders and panel corners; thumbnail movement is the
pointer response. Source, result and settings groups use tonal separation
rather than a card around every control. Empty states explain the next action;
skeletons represent genuinely unknown content. Never invent recognition
percentages or successful persistence states.

### Source-linked Review

Selecting a recognized region connects its source outline, exact original crop
and editable correction. Maintain that link on phone and desktop. Keep zoom,
fit-to-width and the numeric crop alternative reachable; crop selection cannot
depend only on dragging. Background work must retain visible content and
drafts. Save status, conflicts and recovery stay visible near the correction.

**The Explicit Save Rule.** Editing creates an in-memory draft, not a saved
review. Drafts survive workspace navigation while the page stays open; reload
or closing the page can lose them. Preserve “Changes not saved”, copy recovery,
conflict comparison and the deliberate discard/reconvert action. Export offers
“Save and export” or “Export last saved version” when drafts exist.

Training is optional per selected crop and separate from saving. Exclusion
prevents export and training; a successful save resets the training selection.
Keep capability failures, session recovery and save errors factual and
actionable. Toasts cannot be the sole location of important errors.

The development preview removes a previous service-worker registration before
importing application modules, reloading once if an old worker still controls
that page. This startup recovery prevents cached styles or mixed module versions
without clearing appearance preferences or account data. Development does not
register another worker. Production keeps offline shell caching: hashed assets
are cache-first, mutable assets are network-first, and API/private data is
excluded. It does not automatically reload an open review to apply an update.

### Dialogs

Use opaque document surfaces, overlay corners and restrained shadows. Keep
dialogs within the viewport, allow internal scrolling, name them accessibly
and restore focus when they close. The phone navigation dialog fills the
viewport; ordinary task dialogs retain a small edge inset.

## Do's and Don'ts

### Do:

- **Do** use existing semantic colors and owned primitives in both appearances.
- **Do** keep original pixels, selected source and correction visibly connected.
- **Do** preserve explicit save, recovery, export scope and optional consent.
- **Do** retain visible focus, keyboard access, reduced motion and touch targets.
- **Do** inspect phone/desktop and light/dark captures after web UI changes.

### Don't:

- **Don't** apply the web font or palette to native without a native change.
- **Don't** invert scans or imply that an unsaved draft is durable.
- **Don't** hide save failures or conflicts in transient feedback alone.
- **Don't** add gradients, glass panels or decorative cards around every region.
- **Don't** treat synthetic browser captures as model, hardware or native proof.
