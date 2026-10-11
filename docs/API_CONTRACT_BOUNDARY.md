# API contract boundary

The backend OpenAPI document is the source of truth for the versioned HTTP
surface. It is generated from the FastAPI application and committed at
`contracts/openapi-v1.json`. `contracts/error_codes.json` and the `ink_v1`
golden files are part of the same checked-in contract set.

## Client boundary

The web and native clients currently use a deliberately small, manual,
transport-neutral DTO boundary. They do not share browser `File`/`Blob`
objects, FastAPI classes, SQLAlchemy models, or a generated client runtime.

- TypeScript wire DTOs and request functions live in
  `frontend/src/api/types.ts` and `frontend/src/api/client.ts`.
- Swift wire DTOs and the URLSession transport live in
  `ios/Sources/PersonalizedNotesKit/APIModels.swift` and
  `ios/Sources/PersonalizedNotesKit/SharedBackendClient.swift`.
- Swift uses `APIJSON.encoder()` and `APIJSON.decoder()` for the shared JSON
  rules in `ios/Sources/PersonalizedNotesKit/JSONValue.swift`.
- `ink_v1` is a separate canonical JSON/byte contract. Its cross-language
  fixture is `contracts/ink_v1/simple_stroke.json`.

This is an intentional compatibility boundary, not a claim that the clients
are generated from OpenAPI. Every DTO change must therefore update the OpenAPI
artifact, both client representations when applicable, and a contract or
round-trip test. A field added to a response must be optional on clients until
the minimum supported backend is guaranteed to emit it. Removing or changing
the meaning of an existing field requires a new version or an explicit
compatibility decision.

## Wire rules

- JSON field names are `snake_case`.
- UUIDs are JSON strings in the standard hyphenated representation.
- Date/time values are ISO-8601 strings with an explicit UTC offset.
- Error responses use the structured `code` vocabulary in
  `contracts/error_codes.json`.
- Sync cursors are monotonically increasing server sequences; client
  operation IDs are idempotency keys.
- Imported PDF/image bytes use the documented multipart or resumable-upload
  endpoints. Canonical `ink_v1` is JSON and may contain its versioned base64
  native-drawing envelope; immutable ink downloads revalidate that envelope's
  page ID and content hash before local installation.

## Required checks

From the repository root:

```bash
PYTHONPATH=backend python scripts/generate_ink_v1_golden.py
PYTHONPATH=backend python scripts/export_openapi.py
PYTHONPATH=backend python scripts/check_generated_contracts.py
python scripts/check_swift_api_models.py
PYTHONPATH=backend python scripts/check_openapi_compatibility.py \
  --candidate contracts/openapi-v1.json --allow-missing-baseline
```

CI runs the generators from the backend working directory, checks all four
generated artifacts for drift, and compares OpenAPI against the previous
version when a baseline exists. The repository also contains a regression test
that proves stale generated output is rejected and a source-level test that
guards this manual boundary document.

Do not mark the client contract as staging- or device-validated based on these
checks. They prove schema and serialization consistency only; live backend,
offline, token-expiry, accessibility, and physical-device evidence remains a
separate release gate.

## Dependency warning decision

The current FastAPI release supports Starlette 0.41.3, whose test client still
looks up AnyIO's compatibility alias. Starlette 0.41.3 and AnyIO 4.15.1 are
therefore direct, exact pins in `backend/requirements.txt`. The warning is a
known upstream compatibility warning, not an ignored application warning; the
pins keep CI reproducible and the FastAPI/Starlette/AnyIO stack must be upgraded
as one compatibility change.
