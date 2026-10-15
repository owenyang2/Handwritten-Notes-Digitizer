# Commit validation — October 11, 2026

Date: **2026-10-11**

This check covers the application changes prepared for the local commit batch,
including the newer OCR, benchmark, and website transport cleanup. The earlier
[recorded acceptance report](END_TO_END_LOCAL_2026-10-11.md) remains evidence for
the simulator journey and installed models at that earlier snapshot.

## Source

- Application commit before the final documentation commit: `6fcc921c6753d195d4b1df328231be7ded53b2ad`.
- Source fingerprint: `cc3410f5e1693db6f75f9523364a7c41384d0ac0855ac6260bf6bb0d307a2ab8` over 320 files.
- Scope: nonignored files under `backend`, `frontend`, `ios`, `contracts`,
  `scripts`, and `benchmarks`.
- Method: sorted UTF-8 path, NUL, then each file's binary SHA-256.
- The only source edit after the checks was removing trailing whitespace from
  one native UI test line; it changes no behavior.

## Checks

| Check | Result |
| --- | --- |
| Backend regression | 901 passed, 2 skipped; 71.74 seconds |
| Website regression | 175 passed |
| Website typecheck and production build | Passed |
| Swift package tests | 29 passed |
| Signed simulator app/test build | Passed |
| Native app unit tests | 27 passed |
| Swift API/OpenAPI boundary | Passed for 31 models |

The initial backend run used a temporary database URL that conflicted with the
settings-default assertion. The corrected full run removed that override and
passed. No application fix was needed. The two skipped checks still require
separate disposable PostgreSQL and S3/MinIO services; 294 backend warnings
remain.

The prior real-model UI recording was not repeated for this commit batch.
Physical-device, representative handwriting quality, and full two-device
interruption/conflict acceptance remain listed in the
[dated TODO](../archive/remaining-work-2026-10-11.md).

## Commit contents

The batch groups model runtimes, ink/conversion APIs and shared wire models,
website review/transport, the native app, OCR/benchmark cleanup, and documentation.
The installed model weights, environments, generated Xcode project, databases,
raw logs, and local video recordings are excluded from Git. Public demo
screenshots and sanitized test evidence are included.
