# Local end-to-end demo — 2026-10-11

[Watch the 84-second demo](end-to-end-demo.mp4).

The video starts with an actual iPad simulator recording, driven by the opt-in
`LiveConversionDemoUITests` acceptance test against the running local backend
and installed models. It shows sign-in, restoring the shared library, formula
conversion, source-linked review, saving, Markdown export to the share sheet,
reopening, and the equivalent handwriting-prose journey. No recognition results
were substituted with a test engine. Startup and final idle time were trimmed;
the recorded journey runs at its original speed. There is no audio.

The final twelve seconds show clearly labelled, actual website screenshots:

- [Formula source and saved correction](website-formula-review.png).
- [Handwriting source and raw prediction](website-prose-review.png).

These browser sections are still images, rather than a continuous browser
recording. The formula review was corrected to `x + y = 2` on the website and
appears in the native review. The prose prediction `indus the` is inaccurate and
needs human correction. Saving it during this workflow test verifies persistence
and export only; it does not certify the transcription.

The formula input is a generated printed smoke fixture. The prose input is a
public IAM-derived OCR fixture, not an independent accuracy holdout. This demo
does not establish handwriting quality, Apple Pencil hardware behavior,
personalization, or full two-device interruption/conflict acceptance.

The video is a local deliverable and is ignored by Git. The source screenshots
and [test report](../docs/release-evidence/END_TO_END_LOCAL_2026-10-11.md) remain
reviewable in the workspace.
