# Review fixes — October 11, 2026

Date: **2026-10-11**

All six findings from the `platform-sync-uploads` review against `origin/main`
have been addressed. These results cover the working changes after commit
`51016918b493515b7fd261f70d74a7f3e2d194be`.

| Issue | Fixed behavior |
| --- | --- |
| Remote ink overwrites an edit made during download | Remote installs share the local commit queue and recheck the expected local revision and hash after downloading. First drawings on previously empty pages are protected too. Upload acknowledgements cannot mark a newer drawing as uploaded. |
| Open canvas ignores synced ink and navigation saves old ink | The canvas observes drawing revisions on the same page, preserves pending/submitted edits until their save is acknowledged, and only flushes changed drawings. |
| Replacing a source twice deletes its new raster | Source rasters and page images use new object keys. Cleanup removes superseded files; failed transactions retain the old source bytes. |
| Website metadata changes never reach existing iPad copies | REST metadata, trash, and restore mutations publish owner-scoped sync events in the canonical state transaction. Native projection updates title, archive, favorite, language, context, trash, and version. |
| Identical detected lines collapse into one region | Crop reuse also checks position, coordinate space, and revision. Distinct positions retain distinct region IDs; retries reuse the correct IDs. |
| Source replacement leaves old recognition current | Replacements advance page revisions and invalidate retained region crops. Old completed results become stale, old queued jobs fail before inference, and stale prediction reviews are rejected. Fresh selected-region conversions regenerate current crops; frozen sources and review history remain available. |

The native tests also exposed PencilKit changing serialized bytes during a
decode/encode round trip. Local saves now preserve the original snapshot bytes
in both the native drawing and canonical bundle. Canvas comparisons use drawings
to distinguish actual edits from serialization differences.

## Validation

| Check | Result |
| --- | --- |
| Full backend regression | **906 passed, 2 skipped**; 68.78 seconds |
| Final focused backend regressions | **5 passed** |
| Website regression | **175 passed** |
| Swift package tests | **30 passed** |
| Native app tests on iPad Pro 13-inch simulator, iOS 18.6 | **33 passed** |
| Final signed simulator app/test build | Passed |
| Swift API/OpenAPI boundary | Passed for 31 models |
| Whitespace/error check | Passed |

The two backend skips require disposable PostgreSQL and S3/MinIO services.
The suite emitted 299 existing/dependency/fixture warnings. Recognition tests
exercise the real API, storage, layout, and worker flow with deterministic
model output; this fix validation does not measure recognition accuracy.

Source fingerprint: `b385efbf4474292e89def7829a6a61d54bba2cb084ca120d4c20c7a7d2dd65a4`
over 322 nonignored files under `backend`, `frontend`, `ios`, `contracts`,
`scripts`, and `benchmarks`, using sorted UTF-8 path, NUL, and each file's binary
SHA-256.

The earlier live demo and real-model acceptance recording were not repeated.
Physical iPad/Pencil, representative handwriting accuracy, and the broader live
two-client acceptance journey remain in the [dated TODO](../archive/remaining-work-2026-10-11.md).
