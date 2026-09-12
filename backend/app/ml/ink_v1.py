"""App-neutral ``ink_v1`` codec shared with the Swift client.

Canonical bytes are UTF-8 JSON with sorted keys, compact separators, snake_case
fields, lowercase UUIDs, and no ``content_hash`` inside the hashed payload.
Both languages SHA-256 those bytes.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

SCHEMA = "ink_v1"
SCHEMA_VERSION = 1


class InkIntegrityError(ValueError):
    pass


def _uuid(value: str | uuid.UUID) -> str:
    return str(value).lower()


def canonical_payload(bundle: dict[str, Any]) -> dict[str, Any]:
    strokes = []
    for stroke in bundle.get("strokes") or []:
        points = []
        for point in stroke.get("points") or []:
            item = {
                "x": float(point["x"]),
                "y": float(point["y"]),
                "time_offset": float(point["time_offset"]),
            }
            if point.get("pressure") is not None:
                item["pressure"] = float(point["pressure"])
            if point.get("azimuth") is not None:
                item["azimuth"] = float(point["azimuth"])
            if point.get("altitude") is not None:
                item["altitude"] = float(point["altitude"])
            points.append(item)
        color = stroke.get("color") or {}
        transform = stroke.get("transform") or {}
        strokes.append(
            {
                "stroke_id": _uuid(stroke["stroke_id"]),
                "points": points,
                "tool": stroke.get("tool", "pen"),
                "color": {
                    "red": float(color.get("red", 0)),
                    "green": float(color.get("green", 0)),
                    "blue": float(color.get("blue", 0)),
                    "alpha": float(color.get("alpha", 1)),
                },
                "width": float(stroke.get("width", 1)),
                "transform": {
                    "a": float(transform.get("a", 1)),
                    "b": float(transform.get("b", 0)),
                    "c": float(transform.get("c", 0)),
                    "d": float(transform.get("d", 1)),
                    "tx": float(transform.get("tx", 0)),
                    "ty": float(transform.get("ty", 0)),
                },
            }
        )
    space = bundle["coordinate_space"]
    capture = bundle.get("capture") or {}
    payload: dict[str, Any] = {
        "schema": bundle.get("schema", SCHEMA),
        "schema_version": int(bundle.get("schema_version", SCHEMA_VERSION)),
        "page_id": _uuid(bundle["page_id"]),
        "coordinate_space": {
            "width": float(space["width"]),
            "height": float(space["height"]),
            "unit": space.get("unit", "points"),
            "origin": space.get("origin", "top_left"),
        },
        "strokes": strokes,
        "capture": {
            "pressure": bool(capture.get("pressure", False)),
            "azimuth": bool(capture.get("azimuth", False)),
            "altitude": bool(capture.get("altitude", False)),
            "timing": bool(capture.get("timing", True)),
            "source": capture.get("source", "apple_pencil"),
        },
    }
    native = bundle.get("native_drawing")
    if native:
        payload["native_drawing"] = {
            "platform": native.get("platform", "apple.pkdrawing"),
            "media_type": native.get("media_type", "application/x-apple-pkdrawing"),
            "platform_version": native.get("platform_version"),
            "data": native.get("data"),
        }
    return payload


def canonical_bytes(bundle: dict[str, Any]) -> bytes:
    return json.dumps(
        canonical_payload(bundle),
        separators=(",", ":"),
        sort_keys=True,
        ensure_ascii=False,
    ).encode("utf-8")


def content_hash(bundle: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_bytes(bundle)).hexdigest()


def finalize(bundle: dict[str, Any]) -> dict[str, Any]:
    payload = canonical_payload(bundle)
    payload["content_hash"] = hashlib.sha256(
        json.dumps(payload, separators=(",", ":"), sort_keys=True, ensure_ascii=False).encode(
            "utf-8"
        )
    ).hexdigest()
    return payload


def validate(bundle: dict[str, Any]) -> dict[str, Any]:
    payload = canonical_payload(bundle)
    expected = hashlib.sha256(
        json.dumps(payload, separators=(",", ":"), sort_keys=True, ensure_ascii=False).encode(
            "utf-8"
        )
    ).hexdigest()
    actual = str(bundle.get("content_hash") or "")
    if actual != expected:
        raise InkIntegrityError("ink bundle content_hash does not match canonical bytes")
    if payload["schema"] != SCHEMA or payload["schema_version"] != SCHEMA_VERSION:
        raise InkIntegrityError("unsupported ink schema")
    if not payload["strokes"]:
        raise InkIntegrityError("ink bundle must contain at least one stroke")
    return {**payload, "content_hash": expected}
