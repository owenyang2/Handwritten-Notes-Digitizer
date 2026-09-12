"""Generate the shared ink_v1 golden fixture used by Python and Swift tests."""

from __future__ import annotations

import json
from pathlib import Path

from app.ml.ink_v1 import finalize

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "contracts" / "ink_v1"


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


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    finalized = finalize(sample_bundle())
    (OUT / "simple_stroke.json").write_text(
        json.dumps(finalized, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (OUT / "simple_stroke.sha256").write_text(
        finalized["content_hash"] + "\n", encoding="utf-8"
    )
    print(finalized["content_hash"])


if __name__ == "__main__":
    main()
