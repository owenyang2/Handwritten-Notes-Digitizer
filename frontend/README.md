# Handwriting Notes Digitizer — Web Frontend

TypeScript web frontend for the Handwriting Notes Digitizer. Built with Vite,
tested with Vitest and fast-check (property-based testing).

## Requirements

- Node >= 20
- npm >= 10

All dependency versions are pinned exactly in `package.json` for reproducible
builds (Req 11.1).

## Setup

```bash
npm install
```

## Scripts

| Command | Description |
|---------|-------------|
| `npm run dev` | Start the Vite dev server |
| `npm run build` | Type-check and produce a production build |
| `npm run typecheck` | Type-check only (no emit) |
| `npm test` | Run the test suite once (`vitest --run`) |
| `npm run test:watch` | Run tests in watch mode |

## Module layout

```
src/
  main.ts                     Entry point; composes the UI modules
  types.ts                    Shared domain types (Region, RecognitionResult, ...)
  modules/
    uploadUI.ts               Upload UI — submit handwriting images (Req 1, 3)
    editor.ts                 Editor — display + inline correction (Req 3, 4)
    calibrationUI.ts          Calibration UI — optional cold-start flow (Req 9)
    beforeAfterView.ts        Before/After accuracy view (Req 7)
```

## Property-based tests

Property tests use fast-check and run a minimum of 100 iterations, tagged
`Feature: handwriting-notes-digitizer, Property {number}: {property_text}`
per the implementation plan.
