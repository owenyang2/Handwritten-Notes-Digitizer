"""Model-artifact provenance and integrity helpers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ArtifactManifest:
    backend_id: str
    model_id: str
    source_url: str
    revision: str
    weight_sha256: str
    tokenizer_sha256: str | None = None
    processor_sha256: str | None = None
    processor_config: dict[str, Any] = field(default_factory=dict)
    license: str | None = None
    supported_inputs: tuple[str, ...] = ("png", "jpeg", "formula_crop")
    benchmark_version: str | None = None


def sha256_file(path: str | Path) -> str:
    hasher = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def sha256_tree(root: str | Path, *, exclude: set[str] | None = None) -> str:
    root_path = Path(root)
    excluded = exclude or {"artifact_manifest.json", "model_artifact.json"}
    hasher = hashlib.sha256()
    for path in sorted(item for item in root_path.rglob("*") if item.is_file() and item.name not in excluded):
        hasher.update(str(path.relative_to(root_path)).encode("utf-8"))
        hasher.update(sha256_file(path).encode("ascii"))
    return hasher.hexdigest()


def write_artifact_manifest(path: str | Path, manifest: ArtifactManifest) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(asdict(manifest), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return destination


def read_artifact_manifest(path: str | Path) -> ArtifactManifest:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("artifact manifest must be an object")
    required = ("backend_id", "model_id", "source_url", "revision", "weight_sha256")
    missing = [key for key in required if not data.get(key)]
    if missing:
        raise ValueError(f"artifact manifest is missing: {', '.join(missing)}")
    return ArtifactManifest(
        backend_id=str(data["backend_id"]),
        model_id=str(data["model_id"]),
        source_url=str(data["source_url"]),
        revision=str(data["revision"]),
        weight_sha256=str(data["weight_sha256"]),
        tokenizer_sha256=str(data["tokenizer_sha256"]) if data.get("tokenizer_sha256") else None,
        processor_sha256=str(data["processor_sha256"]) if data.get("processor_sha256") else None,
        processor_config=dict(data.get("processor_config") or {}),
        license=str(data["license"]) if data.get("license") else None,
        supported_inputs=tuple(str(value) for value in data.get("supported_inputs", [])),
        benchmark_version=str(data["benchmark_version"]) if data.get("benchmark_version") else None,
    )


def verify_artifact_tree(root: str | Path, manifest: ArtifactManifest) -> bool:
    return sha256_tree(root) == manifest.weight_sha256


__all__ = ["ArtifactManifest", "sha256_file", "sha256_tree", "write_artifact_manifest", "read_artifact_manifest", "verify_artifact_tree"]
