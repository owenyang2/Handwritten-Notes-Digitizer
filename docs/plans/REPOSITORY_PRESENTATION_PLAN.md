# Repository presentation and documentation plan

Drafted October 12, 2026. Implementation authorized October 12; see the
[presentation check report](../release-evidence/PRESENTATION_2026-10-12.md) for
delivered work and remaining publication checks. The findings below describe
the repository before this presentation pass.

## Goal

Make Handwriting Notes Digitizer easy to understand, explore, and run as a
public repository. A visitor should understand the problem, see the working
application, and find its architecture, setup, and current limitations within
one minute, without installing anything.

The lead story is **iPad note-taking and web review, connected by a shared
backend, with prose/formula conversion, human correction, and export**. Use
“Handwriting Notes Digitizer” consistently in public documentation. Explain
once that “Personalized Notes” is the current native app/target name; changing
Swift targets, signing identifiers, or storage names is outside this cleanup.

Suggested introductory copy:

> An iPad and web app for turning handwritten notes and PDFs into reviewed text
> and editable LaTeX while preserving the original source.
>
> Local alpha: real-model conversion, correction, reopening, and Markdown export
> have been demonstrated on the website and iPad simulator. Physical iPad
> testing, representative handwriting evaluation, and broader live sync testing
> remain in progress.

## Findings that shape the plan

- The [root README](../../README.md) opens with a dated TODO and describes a web
  app. It does not introduce the iPad client in its opening or repository map.
- The [demo page](../../demos/README.md) links to an 84-second video that exists
  locally but is excluded by `demos/.gitignore`. That link will break for a
  visitor to a clean clone or the remote repository.
- Three useful screenshots are already tracked in `demos/`. The current formula
  sample is printed, and the prose prediction needs correction; these are
  workflow examples, not evidence of handwriting accuracy.
- The [feature matrix](../FEATURE_STATUS_MATRIX.md) and the final section of the
  [history/runbook](../archive/IMPLEMENTATION_HISTORY_AND_RUNBOOK.md) describe a
  September state with absent models. [October's acceptance report](../release-evidence/END_TO_END_LOCAL_2026-10-11.md)
  records real-model website and simulator workflows. These claims need dates
  and one maintained current status page.
- A clean checkout has no model weights. The [model setup guide](../MODEL_AND_IPAD_SETUP.md)
  also explains that the demonstrated macOS recognition environment is not
  included in the Linux Docker image. Starting containers and reproducing
  recognition need separate instructions.
- The current workspace contains unrelated verification and migration changes.
  Its untracked main-verification report records a PostgreSQL migration failure
  on merged main and a locally tested fix. Resolve that branch state before
  describing the production profile as verified.
- No root license file is tracked. Publication needs an explicit license
  decision and a review of included media, fonts, and third-party material.

## 1. Rebuild the first minute of the README

Make the root README the product introduction. Move detailed environment,
migration, training, and diagnostic instructions into linked guides.

Recommended order:

1. Product name and the short introduction above.
2. One clear visual showing original source beside a saved correction/rendered
   result. Include a compact iPad screenshot so both interfaces are visible.
3. Working demo link and a one-sentence explanation of what it demonstrates.
4. Three user outcomes: capture/import notes; review prose and equations;
   export corrected material while retaining source references.
5. “Engineering highlights”: offline local storage and sync recovery;
   durable recognition jobs and versioned corrections; shared contracts and
   meaningful automated coverage.
6. A small architecture diagram: iPad and browser → FastAPI → database/object
   storage and recognition worker. Show recognition models behind the worker;
   label the current worker accurately as process-local.
7. A short alpha-status table with links to evidence and remaining work.
8. A concise setup entry point, repository map, and documentation links.

Explain which parts were built for this project and which use pretrained
UniMERNet/TrOCR models. Keep the custom compact-model research and legacy CRNN
personalization in deeper documentation, with their actual evidence limits.

**Done when:** the first screen explains the product and shows it working; a
reader can reach the demo, architecture, status, and setup without searching
through a runbook.

## 2. Make the demo public-viewable and visually coherent

- Use the tracked screenshots immediately, with captions identifying the
  website, simulator, source, and saved output. Crop empty space and account
  details while retaining enough context to understand the interaction.
- Publish the existing video to a stable shareable location, such as a release
  asset, and replace current broken video links across the active docs. Verify
  playback while signed out. A video file link is a fallback; a tracked preview
  image should make the demo discoverable in the README.
- Preserve the existing explanation that the browser portions are still
  screenshots and the iPad recording uses a simulator.
- Improve the next recording around one small, legitimately shareable note:
  import/capture → convert → correct a mistake → save/reopen → export. Let the
  viewer see the original and the final usable result. Show model mistakes and
  the correction step honestly.
- Keep one visual style for preview images and captions. A useful product
  screenshot takes priority over a new logo or decorative banner.

**Done when:** a visitor can watch without cloning, requesting access, or
installing models, and every displayed result has understandable context.

## 3. Give the documentation one clear route

Add `docs/README.md` as an index and maintain the existing feature matrix as the
current status source. Refresh the matrix from dated evidence, distinguishing
code coverage, live simulator workflows, physical-device testing, and deployment
validation. Describe installed local models separately from clean-checkout
requirements.

Keep the root focused on a small set of destinations:

| Destination | Reader's question |
| --- | --- |
| Root README | What is this, and why should I care? |
| Demo page | What does it look like in use? |
| `docs/CASE_STUDY.md` | How are the main engineering problems addressed? |
| Setup guides | How can I reproduce the demonstrated workflow? |
| Feature matrix and active TODO | What works, and what remains? |
| Dated release evidence | What supports those claims? |

Move obsolete plans and chronological history under a clearly marked archive,
preserving useful design rationale and updating incoming links. Separate current
setup from historical machine-specific handoff notes. Do not rewrite old test
reports to make them appear current.

Refresh `ios/README.md` and `frontend/README.md`, including their module maps and
evidence dates. Add `ios/`, `contracts/`, and `demos/` to the root repository map.
Keep application source directories in place.

**Done when:** active documentation agrees about the product and its status,
and older plans are clearly identifiable as historical.

## 4. Add a compact engineering case study

Write a short `docs/CASE_STUDY.md` around three decisions with concrete behavior
and links to implementation/tests:

- **Protect notes during sync.** Explain local persistence, restoring exact ink
  on another client, and preserving pending local edits. Separate automated
  coverage from the broader live two-client journey still to be completed.
- **Keep recognition reviewable.** Explain frozen source crops, durable jobs,
  model availability checks, immutable review revisions, and export consistency.
- **Integrate native, web, and ML boundaries.** Explain shared wire contracts,
  distinct prose/formula engines, consent boundaries, and recovery behavior.

For each, use “problem → decision → resulting behavior → evidence → remaining
limit.” Distinguish project implementation from third-party models and
libraries used. Date any test counts and link to the corresponding committed
report; add a CI badge only after checking the public branch's actual result.
Do not turn a smoke fixture, single latency sample, or large test count into an
accuracy or production-reliability claim.

Keep the technical overview consistent with the demonstrated native/web
workflow and its evidence. Add measured quality/performance numbers only after
a documented evaluation supports them.

**Done when:** a technical reviewer can connect each prominent claim to a
specific design choice and inspect its evidence.

## 5. Complete the public-repository preflight

- Review the tracked tree and publishable Git history for credentials, private
  notes, account details, and sensitive automation logs. Use a history-aware
  secret scanner plus manual review; ignored files alone do not establish that
  history is clear. Address any actual findings before changing visibility.
- Choose the project license and document attribution/redistribution boundaries
  for bundled fonts, demo inputs, dependencies, models, and datasets. Keep model
  weights and restricted training data outside the repository.
- Test the documented setup from a clean checkout. State prerequisites, model
  downloads, expected unavailable capabilities, and the supported environment.
  Do not promise working inference from `make docker-up` without verifying it.
- Resolve or explicitly document the known PostgreSQL migration issue before
  promoting that deployment path. Keep unverified S3/MinIO and production
  deployment claims out of the summary.
- Check relative links, screenshot rendering, signed-out video access, and the
  target branch's checks. Ensure evidence cited as shipped is actually committed
  on that branch.
- Set a matching repository description, relevant topics, and the demo link.
  Pin the repository on the author's profile once it is public.

**Done when:** the intended public branch is understandable from a clean view,
its claims match its evidence, and publication review is complete.

## Sequence and scope

Start with the README and existing screenshots, then repair demo access and
status contradictions. Follow with the documentation index and case study.
Complete setup, history, licensing, and public-link checks before publication.
Treat the first four sections as a bounded documentation/media pass; setup or
publication findings may require separate fixes.

The next optional credibility improvement is a small, clearly described
evaluation of real handwritten notes, followed by physical iPad and complete
two-client testing. Report the sample size, inputs, model versions, errors, and
limits. Those checks strengthen the product evidence but need not block publishing an
accurately labeled local alpha.

New features, a redesign of the application, production hosting, renaming native
targets, and broad source-code reorganization are outside this presentation
plan. Success is a cohesive public introduction with visible product behavior,
clear implementation boundaries, reproducible setup, and defensible claims.
