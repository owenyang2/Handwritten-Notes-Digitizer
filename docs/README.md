# Documentation

Start with [setup](guides/getting-started.md), then follow the
[import → review → export workflow](guides/using-the-app.md).
[Status and roadmap](STATUS.md) is the single maintained capability and
acceptance summary. The project is a local alpha.

## Guides

| I want to… | Read |
| --- | --- |
| Run the web/API stack or develop locally | [Getting started](guides/getting-started.md) |
| Use Library, review notes, save and export | [Using the app](guides/using-the-app.md) |
| Install recognition models or run the native app | [Recognition and iPad setup](guides/recognition-and-ipad.md) |
| Train or evaluate an optional research model | [Training and evaluation](guides/training.md) |
| Configure PostgreSQL, Redis and encrypted storage | [Deployment](guides/deployment.md) |

## Reference

- [Architecture diagram and engineering case study](reference/architecture.md)
- [UI design and tokens](reference/design.md)
- [API and shared client contracts](reference/api-contract.md)
- [Model artifacts and data boundaries](reference/model-artifacts.md)
- [Formula model card](reference/model-card.md) and [data card](reference/data-card.md)
- [Web source map](../frontend/README.md) and [native source map](../ios/README.md)
- [Benchmark runner](../benchmarks/README.md), [training notebooks](../notebooks/README.md)
  and [third-party notices](../THIRD_PARTY_NOTICES.md)

## Development

- [Contributing](../CONTRIBUTING.md): the change and review checklist.
- [Testing](development/testing.md): application, browser, native and contract checks.
- [Documentation standard](development/documentation.md): canonical pages, media
  refresh, automated checks, and the research behind this organization.

## History

[Dated verification reports](release-evidence/README.md) preserve the results at
recorded revisions. [Archived plans and demos](archive/README.md) preserve earlier
UI, implementation notes and checklists. Use the guides above for current
instructions; historical reports do not certify the current checkout.
