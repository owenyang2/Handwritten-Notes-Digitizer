"""Validate an OpenAPI v1 document and compare it with a prior contract.

This is intentionally dependency-free so CI can run it before optional
production integrations are installed.  It checks the compatibility rules that
matter to the generated web/native clients: paths and operations cannot
disappear, existing parameters cannot become newly required, response status
codes/media types cannot disappear, and referenced object fields/types/enums
cannot be narrowed incompatibly.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _schema_issues(
    old: Any,
    new: Any,
    location: str,
    issues: list[str],
    seen: set[tuple[int, int]],
) -> None:
    if not isinstance(old, dict) or not isinstance(new, dict):
        if old != new:
            issues.append(f"{location}: schema changed incompatibly")
        return
    marker = (id(old), id(new))
    if marker in seen:
        return
    seen.add(marker)

    if old.get("$ref"):
        if new.get("$ref") != old.get("$ref"):
            issues.append(f"{location}: reference changed from {old['$ref']!r}")
        return
    if new.get("$ref"):
        issues.append(f"{location}: schema became a reference unexpectedly")
        return

    old_type = old.get("type")
    new_type = new.get("type")
    if old_type is not None and old_type != new_type:
        issues.append(f"{location}: type changed from {old_type!r} to {new_type!r}")

    old_enum = old.get("enum")
    new_enum = new.get("enum")
    if isinstance(old_enum, list) and isinstance(new_enum, list):
        removed = [value for value in old_enum if value not in new_enum]
        if removed:
            issues.append(f"{location}: enum values removed: {removed!r}")
    elif old_enum is not None and old_enum != new_enum:
        issues.append(f"{location}: enum constraint changed incompatibly")

    old_required = set(old.get("required", []))
    new_required = set(new.get("required", []))
    missing_required = sorted(old_required - new_required)
    newly_required = sorted(new_required - old_required)
    if missing_required:
        issues.append(f"{location}: required fields removed: {missing_required!r}")
    if newly_required:
        issues.append(f"{location}: fields became newly required: {newly_required!r}")

    old_properties = old.get("properties", {})
    new_properties = new.get("properties", {})
    if isinstance(old_properties, dict) and isinstance(new_properties, dict):
        for name, old_property in old_properties.items():
            if name not in new_properties:
                issues.append(f"{location}.{name}: property removed")
            else:
                _schema_issues(
                    old_property,
                    new_properties[name],
                    f"{location}.{name}",
                    issues,
                    seen,
                )

    if "items" in old and "items" in new:
        _schema_issues(old["items"], new["items"], f"{location}[]", issues, seen)

    for keyword in ("allOf", "anyOf", "oneOf"):
        old_values = old.get(keyword)
        new_values = new.get(keyword)
        if isinstance(old_values, list) and isinstance(new_values, list):
            if len(new_values) < len(old_values):
                issues.append(f"{location}: {keyword} branch removed")
            for index, old_value in enumerate(old_values):
                if index < len(new_values):
                    _schema_issues(
                        old_value,
                        new_values[index],
                        f"{location}.{keyword}[{index}]",
                        issues,
                        seen,
                    )


def _content_issues(
    old_content: Any,
    new_content: Any,
    location: str,
    issues: list[str],
) -> None:
    if not isinstance(old_content, dict) or not isinstance(new_content, dict):
        issues.append(f"{location}: content contract is malformed")
        return
    for media_type, old_media in old_content.items():
        if media_type not in new_content:
            issues.append(f"{location}: media type removed: {media_type}")
            continue
        new_media = new_content[media_type]
        if isinstance(old_media, dict) and isinstance(new_media, dict):
            if "schema" in old_media and "schema" in new_media:
                _schema_issues(
                    old_media["schema"],
                    new_media["schema"],
                    f"{location}.{media_type}",
                    issues,
                    set(),
                )


def _parameter_issues(
    old_parameters: Any,
    new_parameters: Any,
    location: str,
    issues: list[str],
) -> None:
    def index(parameters: Any) -> dict[tuple[str, str], dict[str, Any]]:
        if not isinstance(parameters, list):
            return {}
        result: dict[tuple[str, str], dict[str, Any]] = {}
        for parameter in parameters:
            if isinstance(parameter, dict) and "name" in parameter and "in" in parameter:
                result[(str(parameter["name"]), str(parameter["in"]))] = parameter
        return result

    old_index = index(old_parameters)
    new_index = index(new_parameters)
    for key, old_parameter in old_index.items():
        parameter_location = f"{location}.{key[1]}:{key[0]}"
        if key not in new_index:
            issues.append(f"{parameter_location}: parameter removed")
            continue
        new_parameter = new_index[key]
        if old_parameter.get("required", False) is False and new_parameter.get(
            "required", False
        ):
            issues.append(f"{parameter_location}: parameter became required")
        if "schema" in old_parameter and "schema" in new_parameter:
            _schema_issues(
                old_parameter["schema"],
                new_parameter["schema"],
                parameter_location,
                issues,
                set(),
            )


def _operation_issues(
    old_operation: dict[str, Any],
    new_operation: dict[str, Any],
    location: str,
    issues: list[str],
) -> None:
    _parameter_issues(
        old_operation.get("parameters", []),
        new_operation.get("parameters", []),
        location,
        issues,
    )

    old_body = old_operation.get("requestBody")
    new_body = new_operation.get("requestBody")
    if isinstance(old_body, dict):
        if not isinstance(new_body, dict):
            issues.append(f"{location}: request body removed")
        else:
            if old_body.get("required", False) is False and new_body.get("required", False):
                issues.append(f"{location}: request body became required")
            _content_issues(
                old_body.get("content"),
                new_body.get("content"),
                f"{location}.requestBody",
                issues,
            )

    old_responses = old_operation.get("responses", {})
    new_responses = new_operation.get("responses", {})
    if not isinstance(old_responses, dict) or not isinstance(new_responses, dict):
        issues.append(f"{location}: responses contract is malformed")
    else:
        for status, old_response in old_responses.items():
            if status not in new_responses:
                issues.append(f"{location}: response {status} removed")
                continue
            new_response = new_responses[status]
            if isinstance(old_response, dict) and isinstance(new_response, dict):
                if "content" in old_response:
                    _content_issues(
                        old_response["content"],
                        new_response.get("content"),
                        f"{location}.responses.{status}",
                        issues,
                    )

    old_security = old_operation.get("security")
    new_security = new_operation.get("security")
    if old_security is None and new_security:
        issues.append(f"{location}: operation gained a security requirement")


def validate_schema(document: dict[str, Any], label: str) -> list[str]:
    issues: list[str] = []
    if not isinstance(document.get("openapi"), str) or not document["openapi"].startswith("3."):
        issues.append(f"{label}: missing or unsupported OpenAPI version")
    info = document.get("info")
    if not isinstance(info, dict) or not info.get("version"):
        issues.append(f"{label}: info.version is required")
    paths = document.get("paths")
    if not isinstance(paths, dict):
        issues.append(f"{label}: paths must be an object")
    else:
        for path, item in paths.items():
            if not isinstance(path, str) or not path.startswith("/"):
                issues.append(f"{label}: invalid path key {path!r}")
            if not isinstance(item, dict):
                issues.append(f"{label}: path {path!r} must be an object")
                continue
            for method, operation in item.items():
                if method in HTTP_METHODS and not isinstance(operation, dict):
                    issues.append(f"{label}: {method.upper()} {path} must be an object")
    return issues


def check_compatibility(baseline: dict[str, Any], candidate: dict[str, Any]) -> list[str]:
    issues = validate_schema(candidate, "candidate")
    issues.extend(validate_schema(baseline, "baseline"))
    old_paths = baseline.get("paths", {})
    new_paths = candidate.get("paths", {})
    if not isinstance(old_paths, dict) or not isinstance(new_paths, dict):
        return issues

    for path, old_item in old_paths.items():
        if path not in new_paths:
            issues.append(f"paths.{path}: path removed")
            continue
        new_item = new_paths[path]
        if not isinstance(old_item, dict) or not isinstance(new_item, dict):
            continue
        _parameter_issues(old_item.get("parameters", []), new_item.get("parameters", []), f"paths.{path}", issues)
        for method, old_operation in old_item.items():
            if method not in HTTP_METHODS:
                continue
            if method not in new_item:
                issues.append(f"paths.{path}: {method.upper()} operation removed")
                continue
            new_operation = new_item[method]
            if isinstance(old_operation, dict) and isinstance(new_operation, dict):
                _operation_issues(old_operation, new_operation, f"{method.upper()} {path}", issues)

    old_schemas = baseline.get("components", {}).get("schemas", {})
    new_schemas = candidate.get("components", {}).get("schemas", {})
    if isinstance(old_schemas, dict) and isinstance(new_schemas, dict):
        for name, old_schema in old_schemas.items():
            if name not in new_schemas:
                issues.append(f"components.schemas.{name}: schema removed")
                continue
            _schema_issues(
                old_schema,
                new_schemas[name],
                f"components.schemas.{name}",
                issues,
                set(),
            )
    return issues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument(
        "--allow-missing-baseline",
        action="store_true",
        help="validate the candidate and pass when this is the first v1 release",
    )
    args = parser.parse_args(argv)
    try:
        candidate = _load(args.candidate)
        candidate_issues = validate_schema(candidate, "candidate")
        if args.baseline is None or not args.baseline.exists():
            if not args.allow_missing_baseline:
                print("OpenAPI compatibility check requires a baseline.", file=sys.stderr)
                return 1
            if candidate_issues:
                for issue in candidate_issues:
                    print(f"- {issue}", file=sys.stderr)
                return 1
            print("OpenAPI schema is structurally valid; no prior v1 baseline exists yet.")
            return 0
        issues = check_compatibility(_load(args.baseline), candidate)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"OpenAPI compatibility check failed: {exc}", file=sys.stderr)
        return 1
    if issues:
        print("OpenAPI compatibility check failed:", file=sys.stderr)
        for issue in issues:
            print(f"- {issue}", file=sys.stderr)
        return 1
    print("OpenAPI compatibility check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
