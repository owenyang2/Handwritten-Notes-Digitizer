# Handwriting Notes Digitizer

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users and purpose

This overhaul targets the web workspace first. The user chose a focused research
workspace with original handwriting and corrected notes at its center. Import
pages, review prose and formulas against their exact source, save corrections,
and export a usable document.

## Capabilities and constraints

The existing React/TypeScript client and API contracts remain in place. Library,
Favorites, archived notebooks, trash, search, page selection, recognition,
source-linked editing, explicit saving/export, account recovery, sync and
optional training consent must remain reachable. Model availability is a real
capability boundary. Unsaved web drafts last only while the page remains open.
Native iPhone/iPad work is outside this web-first change.

## Product principles

- Keep source and correction visibly connected.
- Make the next task clear without expanding every advanced control.
- Preserve drafts, recovery, explicit consent and keyboard access.
- Use real notebook data; never fabricate recognition or persistence evidence.

## Brand commitments

Quiet Notebook is the existing workspace name. Product terms are Library,
Notebook, Page, Convert, Review and Export.

## Accessibility and evidence

Preserve System/Light/Dark appearance, reduced motion, visible keyboard focus,
44px touch targets and responsive review. Automated browser evidence uses
synthetic notes and mocked APIs. It does not establish recognition accuracy,
physical-device usability or native acceptance. See [status](docs/STATUS.md)
and the [design reference](docs/reference/design.md).
