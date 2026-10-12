"""Regression coverage for generated-contract freshness and compatibility."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "contracts"
CHECK_GENERATED = ROOT / "scripts" / "check_generated_contracts.py"
CHECK_OPENAPI = ROOT / "scripts" / "check_openapi_compatibility.py"
CHECK_SWIFT_MODELS = ROOT / "scripts" / "check_swift_api_models.py"
CONTRACT_BOUNDARY = ROOT / "docs" / "API_CONTRACT_BOUNDARY.md"


def _run(script: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_generated_contracts_are_current() -> None:
    result = _run(CHECK_GENERATED)
    assert result.returncode == 0, result.stdout + result.stderr


def test_swift_api_models_match_openapi() -> None:
    result = _run(CHECK_SWIFT_MODELS)
    assert result.returncode == 0, result.stdout + result.stderr


def test_generated_contract_checker_rejects_a_stale_artifact(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    shutil.copytree(CONTRACTS, checkout / "contracts")
    error_codes = checkout / "contracts" / "error_codes.json"
    payload = json.loads(error_codes.read_text(encoding="utf-8"))
    payload["__intentional_stale_fixture__"] = "must be rejected"
    error_codes.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    result = _run(CHECK_GENERATED, "--root", str(checkout))
    assert result.returncode != 0
    assert "error_codes.json" in result.stdout + result.stderr


def test_openapi_compatibility_checker_rejects_breaking_changes(tmp_path: Path) -> None:
    baseline = {
        "openapi": "3.1.0",
        "info": {"title": "test", "version": "v1"},
        "paths": {
            "/notes": {
                "get": {
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/Note"
                                    }
                                }
                            }
                        }
                    }
                }
            }
        },
        "components": {
            "schemas": {
                "Note": {
                    "type": "object",
                    "required": ["id"],
                    "properties": {
                        "id": {"type": "string"},
                        "title": {"type": "string"},
                    },
                }
            }
        },
    }
    candidate = json.loads(json.dumps(baseline))
    del candidate["components"]["schemas"]["Note"]["properties"]["title"]
    baseline_path = tmp_path / "baseline.json"
    candidate_path = tmp_path / "candidate.json"
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
    candidate_path.write_text(json.dumps(candidate), encoding="utf-8")

    result = _run(
        CHECK_OPENAPI,
        "--baseline",
        str(baseline_path),
        "--candidate",
        str(candidate_path),
    )
    assert result.returncode != 0
    assert "property removed" in result.stdout + result.stderr


def test_openapi_compatibility_checker_accepts_first_release_without_baseline(
    tmp_path: Path,
) -> None:
    candidate = tmp_path / "candidate.json"
    candidate.write_text(
        json.dumps(
            {
                "openapi": "3.1.0",
                "info": {"title": "test", "version": "v1"},
                "paths": {},
            }
        ),
        encoding="utf-8",
    )
    result = _run(
        CHECK_OPENAPI,
        "--candidate",
        str(candidate),
        "--allow-missing-baseline",
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_manual_client_contract_boundary_is_present_and_explicit() -> None:
    document = CONTRACT_BOUNDARY.read_text(encoding="utf-8")
    required_phrases = (
        "manual",
        "contracts/openapi-v1.json",
        "frontend/src/api/client.ts",
        "ios/Sources/PersonalizedNotesKit/APIModels.swift",
        "snake_case",
        "ISO-8601",
    )
    missing = [phrase for phrase in required_phrases if phrase not in document]
    assert not missing, f"contract boundary document is missing: {missing}"
