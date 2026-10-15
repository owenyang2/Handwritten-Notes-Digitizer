# Recorded verification

Each report describes its recorded source and environment. Results do not
certify the current checkout, remote CI, model accuracy or a production release.
Use [current status](../STATUS.md) for the maintained acceptance summary.

| Report | Scope |
| --- | --- |
| [Final verification, October 14](VERIFICATION_AND_PONYTAIL_AUDIT_2026-10-14.md) | Regression/native suites, production stack, storage encryption and remaining limits |
| [UI revamp, October 14](UI_REVAMP_2026-10-14.md) | Web/native redesign, browser checks and measurements; later fixes are in final verification |
| [Local conversion, October 11](END_TO_END_LOCAL_2026-10-11.md) | Real-model web/simulator review, save/reopen/export and historical recording |
| [Review fixes, October 11](REVIEW_FIXES_2026-10-11.md) | Sync/source races and regressions |
| [Commit validation, October 11](COMMIT_VALIDATION_2026-10-11.md) | Automated checks at the recorded commit batch |
| [Main verification, October 11](MAIN_VERIFICATION_2026-10-11.md) | Earlier application/model checks and then-open deployment failures |
| [Model/iPad setup, October 11](MODEL_AND_IPAD_SETUP_2026-10-11.md) | Recorded model environment and simulator setup |
| [Presentation, October 12](PRESENTATION_2026-10-12.md) | Earlier documentation/media and publication checks |
| [iPad acceptance, September 12](IPAD_ALPHA_ACCEPTANCE_2026-09-12.md) | Earlier simulator/offline acceptance |

Associated JSON manifests and screenshots remain beside their reports. Add new
evidence with revision, command/journey, date, environment, input/model identity,
outcome and limits; preserve existing reports. Normal tests write to ignored
output. Current public UI images are generated separately from the current
build; see [the media standard](../development/documentation.md#screenshots-and-videos).
