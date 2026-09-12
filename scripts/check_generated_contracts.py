"""Check that committed generated contracts match a fresh regeneration.

The checker deliberately generates into a temporary directory.  It therefore
does not modify the checkout being checked and works both from the repository
root and from the ``backend/`` working directory used by CI.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERATORS = (
    ROOT / "scripts" / "generate_ink_v1_golden.py",
    ROOT / "scripts" / "export_openapi.py",
)
CONTRACT_FILES = (
    Path("ink_v1/simple_stroke.json"),
    Path("ink_v1/simple_stroke.sha256"),
    Path("openapi-v1.json"),
    Path("error_codes.json"),
)


def _generation_environment() -> dict[str, str]:
    environment = os.environ.copy()
    backend = str(ROOT / "backend")
    existing = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        backend if not existing else backend + os.pathsep + existing
    )
    return environment


def _generate_expected(output_dir: Path) -> list[str]:
    errors: list[str] = []
    for generator in GENERATORS:
        result = subprocess.run(
            [sys.executable, str(generator), "--output-dir", str(output_dir)],
            cwd=ROOT,
            env=_generation_environment(),
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode:
            errors.append(
                f"{generator.name} failed with exit {result.returncode}:\n"
                f"{result.stdout}{result.stderr}"
            )
    return errors


def check_generated_contracts(repository_root: Path) -> list[str]:
    """Return drift diagnostics for ``repository_root``.

    ``repository_root`` may be a temporary checkout containing only a copied
    ``contracts/`` directory; generators are always taken from this source
    checkout so the regression test exercises the same command as CI.
    """
    with tempfile.TemporaryDirectory(prefix="hnd-contracts-") as temporary:
        expected_root = Path(temporary) / "contracts"
        errors = _generate_expected(expected_root)
        if errors:
            return errors

        actual_root = repository_root / "contracts"
        diagnostics: list[str] = []
        for relative_path in CONTRACT_FILES:
            actual = actual_root / relative_path
            expected = expected_root / relative_path
            if not actual.exists():
                diagnostics.append(f"missing generated artifact: {actual}")
                continue
            if actual.read_bytes() != expected.read_bytes():
                diagnostics.append(
                    f"generated artifact is stale: {actual} (regenerate it)"
                )
        return diagnostics


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="repository root whose contracts/ directory is checked",
    )
    args = parser.parse_args(argv)
    diagnostics = check_generated_contracts(args.root.resolve())
    if diagnostics:
        print("Generated contract check failed:", file=sys.stderr)
        for diagnostic in diagnostics:
            print(f"- {diagnostic}", file=sys.stderr)
        return 1
    print("Generated contract check passed for all four artifacts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
