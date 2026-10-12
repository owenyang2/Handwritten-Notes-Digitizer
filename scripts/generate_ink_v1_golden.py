"""Generate the shared ink_v1 golden fixture used by Python and Swift tests."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.ml.ink_v1 import finalize

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "contracts"


def sample_bundle() -> dict:
    return {
        "schema": "ink_v1",
        "schema_version": 1,
        "page_id": "11111111-1111-4111-8111-111111111111",
        "coordinate_space": {
            "width": 800.0,
            "height": 600.0,
            "unit": "points",
            "origin": "top_left",
        },
        "strokes": [
            {
                "stroke_id": "22222222-2222-4222-8222-222222222222",
                "points": [
                    {"x": 10.0, "y": 20.0, "time_offset": 0.0, "pressure": 0.5},
                    {"x": 40.0, "y": 80.0, "time_offset": 0.25, "pressure": 0.75},
                ],
                "tool": "pen",
                "color": {"red": 0.0, "green": 0.0, "blue": 0.0, "alpha": 1.0},
                "width": 2.0,
                "transform": {"a": 1.0, "b": 0.0, "c": 0.0, "d": 1.0, "tx": 0.0, "ty": 0.0},
            }
        ],
        "capture": {
            "pressure": True,
            "azimuth": False,
            "altitude": False,
            "timing": True,
            "source": "apple_pencil",
        },
    }


def generate(output_dir: Path) -> str:
    """Write the deterministic fixture below ``output_dir``.

    ``output_dir`` is the contract root rather than the ``ink_v1`` directory so
    callers can generate into a temporary tree while checking for drift.  The
    default CLI location remains ``contracts/`` at the repository root.
    """
    out = output_dir / "ink_v1"
    out.mkdir(parents=True, exist_ok=True)
    finalized = finalize(sample_bundle())
    (out / "simple_stroke.json").write_text(
        json.dumps(finalized, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (out / "simple_stroke.sha256").write_text(
        finalized["content_hash"] + "\n", encoding="utf-8"
    )
    return finalized["content_hash"]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUT,
        help="contract root to write (default: repository contracts/)",
    )
    args = parser.parse_args(argv)
    print(generate(args.output_dir))


if __name__ == "__main__":
    main()
