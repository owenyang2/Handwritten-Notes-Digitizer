# Documentation

Start with the [project overview](../README.md) and [82-second demo](../demos/README.md).
Handwriting Notes Digitizer is a local alpha with web and iPad simulator
workflows demonstrated using real recognition models. “Personalized Notes” is
the native app's current target/display name.

## Explore the engineering

- [Case study](CASE_STUDY.md): note preservation, source-linked review, and
  native/web/ML integration, with implementation and test references.
- [Feature and evidence matrix](FEATURE_STATUS_MATRIX.md): maintained summary
  of implemented behavior, live checks, and outstanding validation.
- [Remaining work](../TODO-2026-10-11.md): active device, sync, accuracy, and
  deployment checklist.
- [Testing guide](../PROPERTY_TESTING.md): backend, frontend, Swift, simulator,
  and shared-contract checks.

## Run or develop the application

- [Getting started](GETTING_STARTED.md): prerequisites, Docker/local setup,
  configuration, migrations, and diagnostics.
- [Recognition and iPad setup](MODEL_AND_IPAD_SETUP.md): pinned models, the
  verified macOS runtime, simulator instructions, and physical-device setup.
- [Frontend guide](../frontend/README.md) and [iPad guide](../ios/README.md).
- [Formula training and operations](MATH_NOTES_SETUP_GUIDE.md) and
  [runbook](MATH_NOTES_RUNBOOK.md).
- [Optional deployment profile](PRODUCTION_PROFILE.md): PostgreSQL/Redis/S3
  configuration and current deployment gaps.

## Contracts, models, and data

- [API contract boundary](API_CONTRACT_BOUNDARY.md).
- [Model artifact and data boundary](MODEL_ARTIFACTS_AND_DATA_BOUNDARY.md).
- [Formula model card](MATH_NOTES_MODEL_CARD.md) and [data card](MATH_NOTES_DATA_CARD.md).
- [Benchmark runner](../benchmarks/README.md) and [training notebooks](../notebooks/README.md).
- [Third-party notices](../THIRD_PARTY_NOTICES.md).

## Dated evidence

These reports describe their recorded source/environment, rather than promising
that the same models or services are present in a fresh checkout.

| Report | What it establishes |
| --- | --- |
| [Local conversion, October 11](release-evidence/END_TO_END_LOCAL_2026-10-11.md) | Real-model website/simulator review, reopening, and export; recorded demo |
| [Review fixes, October 11](release-evidence/REVIEW_FIXES_2026-10-11.md) | Sync-race and stale-source regressions; backend, web, and native test results |
| [Commit validation, October 11](release-evidence/COMMIT_VALIDATION_2026-10-11.md) | Application checks at the documented commit batch |
| [iPad alpha acceptance, September 12](release-evidence/IPAD_ALPHA_ACCEPTANCE_2026-09-12.md) | Earlier simulator/offline-storage acceptance; historical limitations |
| [Presentation checks, October 12](release-evidence/PRESENTATION_2026-10-12.md) | Documentation, media, secret-scan scope, and publication follow-up |

## Plans and history

The [end-to-end acceptance plan](plans/END_TO_END_TESTING_PLAN.md) and
[iPad usability roadmap](plans/IPAD_NOTE_APP_USABILITY_IMPLEMENTATION_PLAN.md)
retain future verification work. The [presentation plan](plans/RECRUITER_PRESENTATION_PLAN.md)
records the scope of this documentation pass.

[Archived plans and implementation history](archive/README.md) explain how the
project evolved. Historical checklists and machine-specific runbooks are not
the current status or first-time setup instructions.
