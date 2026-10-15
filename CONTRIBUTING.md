# Contributing

Start with [local setup](docs/guides/getting-started.md) and the source maps for
[web](frontend/README.md) or [native](ios/README.md). Keep changes focused and run
the [checks appropriate to the behavior](docs/development/testing.md).

## Before a change is complete

1. Implement the behavior and verify the affected success and recovery paths.
2. Update the existing canonical guide/reference in the same change. Describe
   observable behavior, exact configuration and relevant limits. Update
   [status](docs/STATUS.md) when capability or acceptance changes.
3. For frontend implementation changes, run `make docs-screenshots`, inspect the
   generated images, and include the screenshots and manifest. Documented native
   screenshots/videos must also be replaced or removed when their UI changes.
4. Run `make docs-check`. To check a branch's documentation impact, run
   `python3 scripts/docs.py check --base <base-ref>`.
5. Fill in the PR template with the documentation impact and validation results.
   A change with no reader-visible effect may use
   `Documentation impact: none: <specific reason>` in its PR description.

## Documentation

Use [the documentation standard](docs/development/documentation.md) for the
canonical topic map, media requirements and research rationale. Update existing
pages before creating a new one, and link every new maintained page from the
[documentation index](docs/README.md). Keep implementation plans, dated test
counts and recordings out of the current setup and workflow pages.

CI checks local links, maintained-page navigation, screenshot provenance and PR
documentation impact. Reviewers still check wording, commands and media against
the implementation; automation cannot establish factual accuracy.
