---
name: Quiet Notebook — Research Folio
description: The implemented web workspace keeps original handwriting beside corrections.
colors:
  bg: "#f2f4f7"
  sidebar: "#f2f4f7"
  panel: "#fff"
  panel-soft: "#f2f4f7"
  ink: "#22252b"
  muted: "#606773"
  line: "#e5e8ed"
  line-strong: "#b9c0cc"
  accent: "#25282f"
  accent-strong: "#3e444e"
  on-accent: "#fff"
  focus: "#1d5fbf"
  green: "#256744"
  amber: "#855700"
  red: "#b42318"
  blue-soft: "#edf3fc"
  green-soft: "#e4f0e8"
  amber-soft: "#fff3d8"
  red-soft: "#fff0ed"
  dark-bg: "#161616"
  dark-sidebar: "#161616"
  dark-panel: "#1e1e1e"
  dark-panel-soft: "#282828"
  dark-ink: "#efefef"
  dark-muted: "#a8a8a8"
  dark-line: "#343434"
  dark-line-strong: "#666"
  dark-accent: "#e8e8e8"
  dark-accent-strong: "#fff"
  dark-on-accent: "#191919"
  dark-focus: "#a3baff"
  dark-green: "#92d5ad"
  dark-amber: "#f0c56a"
  dark-red: "#ffafa4"
  dark-blue-soft: "#222e40"
  dark-green-soft: "#1f3529"
  dark-amber-soft: "#392e1b"
  dark-red-soft: "#3c2622"
typography:
  headline:
    fontFamily: 'Geist, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "2rem"
    fontWeight: 550
    lineHeight: 1.3
    letterSpacing: "-0.03em"
  document-title:
    fontFamily: 'Geist, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "1.75rem"
    fontWeight: 550
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
  sheet: "14px"
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

# Design System: Quiet Notebook — Research Folio

## Overview

**Creative North Star: "Research Folio"**

A cool desk surrounds an inset document sheet. Neutral ink controls and compact
Geist typography provide order; real handwriting supplies character. The shell
stays quiet while source, correction and save state remain easy to compare.

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

- A cool workspace around a clean document surface.
- Neutral controls, blue focus and source selection, labeled feedback.
- Real page previews and a visible source-to-correction relationship.
- Explicit saving, recoverable drafts and deliberate training consent.

## Colors

The palette is achromatic chrome with cool paper in light appearance and neutral
dark surfaces at night. Use the existing semantic CSS variables in components.

### Primary

- **Ink action** (`accent`, `accent-strong`, `on-accent`): dominant task actions
  and their hover state; the matching foreground changes with appearance.
- **Evidence blue** (`focus`, `blue-soft`): keyboard focus, caret, selection and
  active review-region chips. It is an interaction cue, not a brand wash.

### Neutral

- **Cool desk** (`bg`, `sidebar`): the workspace outside the inset sheet.
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
follows OS changes; explicit choices stay stable. Apply web appearance before
first paint. Keep the same component and layout roles across appearances.

## Typography

**Body and interface font:** locally served Geist with the system fallbacks in
the frontmatter. **Technical font:** platform monospace for editable LaTeX.
Rendered formulas use the existing math renderer. Native keeps system fonts.

Geist is an intentional choice from Taste Skill's Achromatic ledger direction
(seed 244730145). The design detector's overused-font exception covers Geist
only; other typefaces and design checks remain subject to review.

The hierarchy is compact and editorial: medium weight titles, normal reading
text and smaller supporting metadata. Screen headings use `headline`; notebook
headings use `document-title`; both reduce to (1.5rem) below the phone breakpoint.
Section titles use `title`, buttons use `label`, and counts/dates use `metadata`.
Muted supporting copy also uses (0.875rem). Preserve prose line height and allow
long titles and status messages to wrap. Readable transcription lines should
stay around 65–75 characters where space permits; this is a reading goal, not
an implemented width cap.

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
control corners and visible blue focus. Labels remain available to assistive
technology. Search is a field inside the Library control group; its borderless
interior must not remove the focus outline. The prose correction area is
vertically resizable with a minimum height of (180px); LaTeX keeps monospace.

### Navigation

Navigation links use muted text, a compact icon and a soft neutral active/hover
surface. Indicate the current route semantically. The collapsible desktop rail
holds Library, Favorites, recent notebooks, Settings and account controls.
Compact navigation uses a dialog with focus containment and restoration. Keep
the skip link, keyboard order, Escape behavior and focus restoration intact.

### Chips / Region Queue

Region chips are horizontal, scrollable and text-bearing. Selection uses
evidence blue and a stronger border; unresolved regions also have a visible
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
