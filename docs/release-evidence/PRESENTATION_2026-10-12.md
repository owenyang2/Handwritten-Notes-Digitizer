# Presentation and publication checks — October 12, 2026

This pass improves the repository introduction and navigation. It does not
change the application or advance physical-device, accuracy, or production
acceptance. Application source baseline: `aa79236883bb4f235439f9f75daaf1477ff5b2e2`.

## Delivered

- Product introduction with actual iPad/web screenshots, workflow, architecture,
  alpha limits, and a concise setup entry point.
- Finished 82-second video included as the sole exception to the demo-video
  ignore rules; the raw 57 MB recording remains excluded.
- Documentation index, first-time setup guide, and engineering case study with
  implementation/test references and documented engineering decisions.
- Maintained feature matrix reconciled with October's live-model evidence;
  historical plans/runbooks archived with explicit labels and repaired links.
- Native and frontend guides refreshed to reflect their current modules.
- MIT license selected by the author; bundled Noto Sans OFL text and third-party
  notices included.
- GitHub description, eight relevant topics, demo destination, and profile pin
  updated and visibly verified. The repository was already public when checked.

## Verification

Checks used an isolated HEAD snapshot plus the presentation changes, without
ignored models, environments, notes, or databases.
The pre-existing PostgreSQL application fix and its untracked main-verification
artifacts are outside this presentation snapshot.

| Check | Result |
| --- | --- |
| Markdown navigation | All 257 relative file/image links and heading anchors across 44 Markdown files resolved; two archive cross-links repaired |
| README visual preview | Both screenshots and demo navigation visible at the top; Mermaid architecture rendered without a syntax error |
| Finished video | Browser playback passed, H.264, 768 × 1024; SHA-256 matches the October 11 manifest |
| Clean Docker build/start | Web/API images built; both containers healthy with fresh SQLite/filesystem volumes and no installed models |
| Website and proxy | HTTP 200 for website and `/api/health`; formula/prose availability correctly false |
| Local browser-session behavior | Disposable registration/login, cookie-authenticated session, document create/list passed with development settings |
| Shared contracts | Four generated artifacts match; 31 Swift API model checks passed |
| History secret scan | 44 reachable commits, default Gitleaks rules, zero findings |
| Publishable snapshot secret scan | Default Gitleaks directory scan, zero findings |

The Docker check used an isolated Compose project and unused host ports
18000/18080 rather than occupying the author's running services. No local model
environment was mounted. The fresh build also ran TypeScript checking and the
production website build. Recognition quality and the full application test
suites were not rerun for documentation changes. The disposable containers and
their test volumes were removed after verification.

**Media correction:** browser playback reports **82.375 seconds** for the exact
finished file (SHA-256 `0907bb96ac6b1dad71c2d5acc4c25bdd6489c7b72057b13a59e488080ae0584f`).
Older reports call it 84 seconds. Active demo labels now say 82 seconds; the
historical reports/manifest are preserved with this correction recorded here.

## Secret-scan scope

[Gitleaks 8.30.1](https://github.com/gitleaks/gitleaks/releases/tag/v8.30.1),
downloaded from the official release and verified against its published SHA-256,
scanned **all 44 locally reachable commits** using `git --log-opts=--all` and
default detection rules, with output fully redacted. Exit status 0; **zero
findings**. A separate directory scan of the publishable snapshot also returned
zero findings. No allowlist or baseline suppressions were introduced.

Manual filename review found the checked-in `.env.example`, not tracked
credential/configuration files, private databases, model weights, or raw UI logs.
The scanner result is a bounded publication check, not a complete security audit;
it does not cover unreferenced remote history or certify application security.

## Remaining publication and product checks

- The documentation/media/license changes are prepared for a separate
  pull request. Merge them before GitHub's main README and finished-video link
  reflect this presentation pass.
- Resolve the separately prepared PostgreSQL migration fix before claiming that
  deployment path is released. Real S3/MinIO and HTTPS/Linux production
  acceptance remain incomplete.
- The demo's IAM-derived prose image has no exact original URL/redistribution
  terms recorded in the retained evidence. Confirm provenance/terms or replace
  it with author-owned notes before reusing the demo elsewhere. See
  [third-party notices](../../THIRD_PARTY_NOTICES.md).
- Physical iPad, representative recognition quality, full live two-client
  interruption/conflict, and measured personalization improvement remain in
  the [active checklist](../../TODO-2026-10-11.md).

[Project overview](../../README.md) · [Feature status](../FEATURE_STATUS_MATRIX.md)
