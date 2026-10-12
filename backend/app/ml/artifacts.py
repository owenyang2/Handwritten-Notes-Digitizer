"""Model-artifact provenance and integrity helpers."""

from __future__ import annotations

import hashlib
from pathlib import Path


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


__all__ = ["sha256_file", "sha256_tree"]
