#!/usr/bin/env python3
"""Check stored Swift Codable API fields against the committed OpenAPI v1.

The native client intentionally uses Foundation's snake-case strategies instead
of a generated networking runtime. This check keeps that lightweight boundary
honest by validating the model declarations that are used on the wire against
the corresponding OpenAPI component schemas.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SWIFT = ROOT / "ios" / "Sources" / "PersonalizedNotesKit" / "APIModels.swift"
DEFAULT_OPENAPI = ROOT / "contracts" / "openapi-v1.json"

# Some response types use a more specific native name than the shared server
# schema. Keeping aliases here makes the mapping explicit and reviewable.
MODEL_SCHEMA_MAP = {
    "LoginRequest": "LoginRequest",
    "RegisterRequest": "RegisterRequest",
    "PasswordResetRequest": "PasswordResetRequest",
    "PasswordResetConfirmRequest": "PasswordResetConfirm",
    "LoginResponse": "LoginResponse",
    "MeResponse": "MeResponse",
    "AccountSessionInfo": "AccountSessionResponse",
    "PageSummary": "PageSummary",
    "DocumentRecord": "DocumentResponse",
    "CreateDocumentRequest": "CreateDocumentRequest",
    "UpdateDocumentRequest": "UpdateDocumentRequest",
    "SyncOperationRequest": "SyncOperationRequest",
    "SyncPushRequest": "SyncPushRequest",
    "SyncAcceptedOperation": "SyncAcceptedOperation",
    "SyncConflict": "SyncConflictResponse",
    "SyncPushResponse": "SyncPushResponse",
    "SyncChange": "SyncChangeResponse",
    "SyncPullResponse": "SyncPullResponse",
    "ResolveSyncConflictRequest": "ResolveConflictRequest",
    "InkBundleRevision": "InkBundleResponse",
    "InkBundleManifestResponse": "InkBundleManifestResponse",
    "InkBundleContentResponse": "InkBundleContentResponse",
    "ResumableUploadPart": "UploadPartSummary",
    "ResumableUploadSession": "UploadSessionResponse",
    "CreateResumableUploadRequest": "CreateUploadRequest",
    "RecognitionRunRequest": "RecognitionRunRequest",
    "RecognitionRun": "RecognitionRunResponse",
    "ImportedPageResponse": "ImportedPageResponse",
    "DocumentSourceUploadResponse": "SourceUploadResponse",
    "AccountExportResponse": "AccountExportResponse",
    "DeleteAccountRequest": "DeleteAccountRequest",
}


def snake_case(name: str) -> str:
    # This matches Foundation's useful behavior for acronym suffixes such as
    # `pageID`, `imageIDs`, and `expectedSHA256`.
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name).lower()


def schema_allows_null(schema: dict) -> bool:
    if schema.get("type") == "null":
        return True
    return any(
        isinstance(option, dict) and option.get("type") == "null"
        for option in schema.get("anyOf", [])
    )


def swift_struct_bodies(source: str) -> dict[str, str]:
    result: dict[str, str] = {}
    pattern = re.compile(r"public\s+struct\s+(\w+)[^{]*\{")
    for match in pattern.finditer(source):
        depth = 1
        index = match.end()
        while index < len(source) and depth:
            if source[index] == "{":
                depth += 1
            elif source[index] == "}":
                depth -= 1
            index += 1
        if depth == 0:
            result[match.group(1)] = source[match.end() : index - 1]
    return result


def swift_fields(body: str) -> dict[str, bool]:
    """Return wire-relevant stored fields and whether each is optional."""

    fields: dict[str, bool] = {}
    for line in body.splitlines():
        match = re.search(
            r"\bpublic\s+(?:let|var)\s+(\w+)\s*:\s*([^\s{]+)", line
        )
        if not match or "{" in line[match.start() :]:
            # Computed properties such as `id`, `isTerminal`, and `isSuccessful`
            # are not Codable storage and must not become wire fields.
            continue
        name, type_name = match.groups()
        fields[name] = type_name.endswith("?")
    return fields


def check(swift_path: Path, openapi_path: Path) -> list[str]:
    swift = swift_path.read_text(encoding="utf-8")
    document = json.loads(openapi_path.read_text(encoding="utf-8"))
    schemas = document.get("components", {}).get("schemas", {})
    structs = swift_struct_bodies(swift)
    errors: list[str] = []

    for swift_name, schema_name in MODEL_SCHEMA_MAP.items():
        body = structs.get(swift_name)
        if body is None:
            errors.append(f"{swift_name}: struct not found in {swift_path}")
            continue
        schema = schemas.get(schema_name)
        if schema is None:
            errors.append(f"{swift_name}: OpenAPI schema {schema_name!r} is missing")
            continue

        fields = swift_fields(body)
        wire_fields = {snake_case(name): optional for name, optional in fields.items()}
        properties = set(schema.get("properties", {}))
        required = set(schema.get("required", []))

        unknown = sorted(set(wire_fields) - properties)
        if unknown:
            errors.append(
                f"{swift_name} -> {schema_name}: Swift fields not in OpenAPI: {', '.join(unknown)}"
            )
        missing_required = sorted(required - set(wire_fields))
        if missing_required:
            errors.append(
                f"{swift_name} -> {schema_name}: required OpenAPI fields missing in Swift: "
                + ", ".join(missing_required)
            )
        optional_required = sorted(
            field
            for field in required
            if field in wire_fields
            and wire_fields[field]
            and not schema_allows_null(schema["properties"].get(field, {}))
        )
        if optional_required:
            errors.append(
                f"{swift_name} -> {schema_name}: required OpenAPI fields are optional in Swift: "
                + ", ".join(optional_required)
            )

    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--swift", type=Path, default=DEFAULT_SWIFT)
    parser.add_argument("--openapi", type=Path, default=DEFAULT_OPENAPI)
    args = parser.parse_args()
    errors = check(args.swift, args.openapi)
    if errors:
        print("Swift API model contract check failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(f"Swift API model contract check passed for {len(MODEL_SCHEMA_MAP)} models.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
