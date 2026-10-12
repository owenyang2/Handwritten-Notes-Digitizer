"""Render owner-scoped canonical ink in the page's existing coordinate space."""

from __future__ import annotations

import io
import json
import math

from PIL import Image, ImageDraw

from app.config.settings import get_settings
from app.ml.ink_v1 import validate


def rasterize_ink(payload: dict, *, width: int, height: int, background: bytes | None = None) -> bytes:
    if width <= 0 or height <= 0 or width * height > get_settings().pdf_max_pixels:
        raise ValueError("Page exceeds the rendering pixel limit")
    space = payload["coordinate_space"]
    sw, sh = float(space["width"]), float(space["height"])
    if not all(math.isfinite(v) and v > 0 for v in (sw, sh)):
        raise ValueError("Invalid ink coordinate dimensions")
    scale = 2 if width * height * 4 <= get_settings().pdf_max_pixels else 1
    size = (width * scale, height * scale)
    image = Image.new("RGBA", size, "white")
    if background:
        with Image.open(io.BytesIO(background)) as source:
            image = source.convert("RGBA").resize(size, Image.Resampling.LANCZOS)
    sx, sy = width / sw * scale, height / sh * scale
    for stroke in payload["strokes"]:
        transform = stroke["transform"]
        values = [float(transform[k]) for k in ("a", "b", "c", "d", "tx", "ty")]
        values += [float(stroke["width"])]
        if not all(math.isfinite(v) and abs(v) <= 100_000 for v in values):
            raise ValueError("Invalid ink transform or width")
        a, b, c, d, tx, ty, pen_width = values
        channels = [float(stroke["color"][k]) for k in ("red", "green", "blue", "alpha")]
        if not all(math.isfinite(v) and 0 <= v <= 1 for v in channels):
            raise ValueError("Invalid ink color")
        layer = Image.new("RGBA", size)
        draw = ImageDraw.Draw(layer)
        color = tuple(round(v * 255) for v in channels)
        radius = max(0.5, min(512, pen_width * (sx + sy) / 4))
        points = []
        for point in stroke["points"]:
            x, y = float(point["x"]), float(point["y"])
            if not math.isfinite(x) or not math.isfinite(y) or max(abs(x), abs(y)) > 100_000:
                raise ValueError("Invalid ink point")
            points.append(((a*x + c*y + tx)*sx, (b*x + d*y + ty)*sy))
        if len(points) > 1:
            draw.line(points, fill=color, width=max(1, round(radius*2)), joint="curve")
        for x, y in points:
            draw.ellipse((x-radius, y-radius, x+radius, y+radius), fill=color)
        image = Image.alpha_composite(image, layer)
    image = image.convert("RGB").resize((width, height), Image.Resampling.LANCZOS)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def page_raster(page, user_id, repos, store, *, bundle_id=None) -> tuple[bytes, dict]:
    """Return a composed raster and the immutable source identity used to render it."""
    selected = bundle_id if bundle_id is not None else page.active_ink_bundle_id
    ink = repos.ink_bundles.get(user_id, selected) if selected else None
    if selected and (ink is None or ink.page_id != page.page_id):
        raise ValueError("Ink revision does not belong to this page")
    background = None if page.raster_path.startswith("native-ink://") else store.get(page.raster_path)
    identity = {"page_revision": page.page_revision, "ink_bundle_id": None, "ink_hash": None}
    if ink is None:
        if background is None:
            raise ValueError("This page has no uploaded ink source")
        return background, identity
    payload = validate(json.loads(store.get(ink.asset.object_ref)))
    if payload["page_id"] != str(page.page_id) or payload["content_hash"] != ink.content_hash:
        raise ValueError("Ink revision failed its manifest check")
    identity.update(ink_bundle_id=str(ink.bundle_id), ink_hash=ink.content_hash)
    return rasterize_ink(payload, width=page.width, height=page.height, background=background), identity
