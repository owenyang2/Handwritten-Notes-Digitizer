"""Dump the versioned OpenAPI contract committed under contracts/."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.main import create_app  # noqa: E402
from app.services.error_codes import ERROR_CODES  # noqa: E402


def main() -> None:
    out_dir = ROOT / "contracts"
    out_dir.mkdir(parents=True, exist_ok=True)
    app = create_app()
    schema = app.openapi()
    schema["info"]["x-stable-error-codes"] = sorted(ERROR_CODES)
    (out_dir / "openapi-v1.json").write_text(
        json.dumps(schema, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (out_dir / "error_codes.json").write_text(
        json.dumps(ERROR_CODES, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {out_dir / 'openapi-v1.json'}")


if __name__ == "__main__":
    main()
