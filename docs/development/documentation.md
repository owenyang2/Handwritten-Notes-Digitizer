# Documentation standard

Keep one maintained explanation per topic, written for the current checkout.
Readers should find the next action without reading implementation history.
Use Markdown in the repository; no separate documentation platform is required.

## Canonical pages

| Information | Home |
| --- | --- |
| Product overview, architecture diagram, engineering highlights and shortest local start | [Root README](../../README.md) |
| Navigation to maintained information | [Documentation index](../README.md) |
| Capabilities, acceptance gaps and next work | [Status and roadmap](../STATUS.md) |
| Setup, workflow, model installation, training, deployment | Existing page in `docs/guides/` |
| Architecture, UI decisions, contracts, model/data boundaries | Existing page in `docs/reference/` |
| Testing and contributor workflows | `docs/development/` and [Contributing](../../CONTRIBUTING.md) |
| Component source maps and commands | Component README beside its code |
| Results at a particular revision/environment | `docs/release-evidence/`, linked from its [index](../release-evidence/README.md) |
| Superseded plans, runbooks and old UI media | [History index](../archive/README.md) |

Update a canonical page before adding a page. New maintained pages need a link
from the documentation index. Use lowercase filenames with dashes. Keep the
root README short; leave full setup, matrices, test counts and long plans in
their canonical homes. A current instruction must not depend on an archived
runbook. History may link back to maintained instructions.

## Recruiter-facing overview

The README is also a portfolio entry point. Keep a visible system architecture
diagram, the technology stack, and a few concrete engineering highlights that
link to the [case study](../reference/architecture.md). Preserve useful technical
visuals when consolidating documentation. Put deeper diagrams, tradeoffs and
code/test evidence in the architecture reference so readers can inspect the work.

Use Mermaid for small architecture diagrams so they render on GitHub and stay
editable beside the code. Keep diagrams aligned with implementation, including
local versus optional deployment services and process boundaries. Distinguish
project-built systems from integrated pretrained models, and keep acceptance
limits visible. Prefer demonstrated decisions over promotional claims.

## Writing and evidence

Start with the outcome and prerequisites, then give commands and expected
results. Name the working directory for commands. Use actual UI labels and
verify configuration against code. Link to details instead of copying them.
Mention absent models, connectivity and data persistence when they affect use.

Record test counts only in dated evidence. Every acceptance claim needs its
source revision or fingerprint, command/journey, environment, date, input and
model/dataset identity when relevant, result and remaining limits. Keep old
reports intact; add a new report and update status when evidence changes.
Do not infer accuracy from synthetic fixtures or hardware readiness from a
simulator. Git history records normal doc edits; a new handoff report is not
needed for every fix.

## Screenshots and videos

Public current web screenshots live in `docs/assets/screenshots/`. Refresh them:

```bash
# From the repository root; install frontend dependencies first.
cd frontend
npx playwright install chromium
cd ..
make docs-screenshots
make docs-check
```

The capture command builds the production app and reuses the responsive browser
journey in `frontend/e2e/workspace.spec.ts`. It stages captures in a temporary
directory, then installs eight Library/review images at 320/1440px in light/dark
appearance after the journey succeeds. It records SHA-256 for the frontend
implementation, capture fixture and images in `manifest.json`. Synthetic notes
and a mocked API keep captures reproducible without private notes or models.

Inspect every changed image for readable labels, layout and unintended content.
Use descriptive alt text. Captions identify the platform and whether inputs/API
are synthetic. A screenshot of a mock response does not prove backend behavior.
The checker conservatively requires a refresh after any frontend implementation
change, including API wiring, dependencies and assets.

Only promote a video as current when its entire UI matches the current product.
Record source revision, date, platform, input/model identity and capture journey
beside it. If re-recording is unavailable, remove the video from current pages
and link it only as history. The October 11 recording remains available from
the history index; it predates the redesign. Current native media requires
manual recapture or removal when affected; the automatic web capture does not
validate native UI. Ordinary browser tests save output under ignored
`frontend/test-results/`, preserving dated reports.

## Checks and review

`make docs-check` uses Python's standard library. It validates local Markdown
and HTML links/images (including Markdown heading anchors), checks maintained
docs are indexed, rejects historical media in current pages, and checks capture
source/image hashes. External URLs are reviewed manually; no network is needed.

On PRs, CI compares the full diff with the PR base. Application/configuration
changes require a maintained doc change or a specific
`Documentation impact: none: <reason>` line in the PR body. Test-only changes
are exempt. Locally the same check accepts `--base <ref>` and optional
`--no-docs-impact '<reason>'`. An exception explains absence of reader-visible
changes; it does not bypass media freshness. Pushes run structural/media checks.

Reviewers confirm the documentation actually describes the change, commands
remain usable, screenshots match the UI, and remaining limits are honest.
A touched Markdown file or matching hash cannot establish semantic correctness.
Branch protection must require the CI `documentation` job to make it a merge gate.

## Research behind the standard

Reviewed October 15, 2026. These established repositories informed the scope;
the structure above is adapted to this project's size.

| Source | Practice used here |
| --- | --- |
| [uv documentation](https://docs.astral.sh/uv/) and [contributor guide](https://docs.astral.sh/uv/reference/contributing/#documentation) | Separate getting started, task guides and reference; provide explicit generation/preview commands |
| [Django: writing documentation](https://docs.djangoproject.com/en/dev/internals/contributing/writing-documentation/) | Organize around reader goals; link to reference instead of repeating it; distinguish changes and available behavior |
| [VS Code docs contribution guide](https://github.com/microsoft/vscode-docs/blob/main/CONTRIBUTING.md) | Deliberate navigation, lowercase filenames, verified links, image placement and alt text |

The source-hash media gate is a repository-specific safeguard against the stale
screenshots observed here. It refreshes provenance when code changes; human
review remains responsible for what the images and prose claim.
