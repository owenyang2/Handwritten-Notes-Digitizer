"""Image preprocessing for the HTR_Engine inference pipeline (task 7.1).

This module owns the first stage of the engine pipeline described in the design
(HTR_Engine → Preprocess): it turns raw uploaded image *bytes* into a clean,
normalised grayscale representation suitable for layout analysis and word
segmentation (Requirements 2.1, 2.2).

Stages
------
1. **Decode** — interpret the raw bytes as a PNG/JPEG (or any Pillow-decodable
   raster) image. Invalid / undecodable bytes raise :class:`ImageDecodeError`.
2. **Grayscale** — collapse to a single luminance channel.
3. **Deskew** — estimate the dominant text skew angle via a projection-profile
   search and rotate the page upright so lines are horizontal. This makes the
   subsequent horizontal-projection line finder reliable.
4. **Normalize** — produce a ``float32`` array scaled to ``[0, 1]`` plus a
   boolean *ink mask* (text pixels = ``True``) using an Otsu threshold, so the
   segmenter can reason about ink density independent of absolute brightness.

The module deliberately keeps coordinates in the pixel space of the
*deskewed* image: everything downstream (line/word regions, crops) is expressed
relative to :attr:`PreprocessedImage.width` / :attr:`PreprocessedImage.height`,
which guarantees in-bounds regions (design Property 4).

Pillow is imported lazily inside :func:`decode_image` so importing this module
never requires the optional real-image dependency until a decode is attempted.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import numpy as np


class ImageDecodeError(ValueError):
    """Raised when raw bytes cannot be decoded as an image (Req 1.4 / 2.x)."""


@dataclass(frozen=True)
class PreprocessConfig:
    """Pinned, tunable parameters for the preprocessing stage.

    Defaults are chosen to work on clean scanned/handwritten pages and on the
    small synthetic fixtures used by the tests. All values are conservative so
    deskew is effectively a no-op on already-upright input.
    """

    # Deskew search bounds (degrees) and step. The optimum projection-profile
    # angle is searched over ``[-limit, +limit]`` in ``step`` increments.
    deskew_angle_limit: float = 10.0
    deskew_angle_step: float = 0.5
    # An estimated skew below this magnitude (degrees) is treated as zero so we
    # never rotate (and therefore never resample) an already-upright image.
    deskew_min_angle: float = 0.25
    # Background fill value used when rotating during deskew (white page).
    background_value: int = 255


@dataclass(frozen=True)
class PreprocessedImage:
    """The output of :func:`preprocess`.

    Attributes
    ----------
    gray:
        ``uint8`` array of shape ``(H, W)`` — the deskewed grayscale image.
    normalized:
        ``float32`` array of shape ``(H, W)`` scaled to ``[0, 1]``.
    ink_mask:
        boolean array of shape ``(H, W)``; ``True`` where a pixel is ink
        (text) rather than background.
    width / height:
        pixel dimensions of the deskewed image; the coordinate space for every
        downstream region and crop.
    """

    gray: np.ndarray
    normalized: np.ndarray
    ink_mask: np.ndarray

    @property
    def height(self) -> int:
        return int(self.gray.shape[0])

    @property
    def width(self) -> int:
        return int(self.gray.shape[1])


def decode_image(image_bytes: bytes) -> np.ndarray:
    """Decode raw image bytes into a grayscale ``uint8`` array ``(H, W)``.

    Raises
    ------
    ImageDecodeError
        If ``image_bytes`` is empty or cannot be decoded as an image.
    """
    if not image_bytes:
        raise ImageDecodeError("image bytes are empty")

    # Local import so the module stays importable without Pillow until needed.
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            img.load()
            gray = img.convert("L")
            array = np.asarray(gray, dtype=np.uint8)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ImageDecodeError("file is not a valid image") from exc

    if array.ndim != 2 or array.size == 0:
        raise ImageDecodeError("decoded image has no pixel data")
    return array


def to_grayscale(array: np.ndarray) -> np.ndarray:
    """Return a 2-D grayscale ``uint8`` view of ``array``.

    Accepts an already-grayscale ``(H, W)`` array or an ``(H, W, C)`` colour
    array (averaged across channels). :func:`decode_image` already yields
    grayscale; this helper exists for callers holding raw arrays.
    """
    if array.ndim == 2:
        return array.astype(np.uint8, copy=False)
    if array.ndim == 3:
        return np.mean(array[..., :3], axis=2).astype(np.uint8)
    raise ValueError(f"expected a 2-D or 3-D array, got shape {array.shape}")


def otsu_threshold(gray: np.ndarray) -> int:
    """Compute an Otsu global threshold for a grayscale ``uint8`` image.

    Returns the threshold ``t`` in ``[0, 255]`` that maximises between-class
    variance. Pixels with value ``<= t`` are considered ink (dark text on a
    light background).
    """
    hist = np.bincount(gray.reshape(-1), minlength=256).astype(np.float64)
    total = gray.size
    if total == 0:
        return 127

    sum_total = np.dot(np.arange(256), hist)
    weight_bg = 0.0
    sum_bg = 0.0
    best_threshold = 0
    best_variance = -1.0

    for t in range(256):
        weight_bg += hist[t]
        if weight_bg == 0:
            continue
        weight_fg = total - weight_bg
        if weight_fg == 0:
            break
        sum_bg += t * hist[t]
        mean_bg = sum_bg / weight_bg
        mean_fg = (sum_total - sum_bg) / weight_fg
        between = weight_bg * weight_fg * (mean_bg - mean_fg) ** 2
        if between > best_variance:
            best_variance = between
            best_threshold = t

    return best_threshold


def ink_mask_from_gray(gray: np.ndarray, threshold: int | None = None) -> np.ndarray:
    """Return a boolean ink mask (``True`` = text) for a grayscale image.

    Assumes dark text on a light background: pixels at or below the Otsu
    threshold are ink. If the image is blank (single intensity), the mask is
    all ``False``.
    """
    if threshold is None:
        threshold = otsu_threshold(gray)
    # A truly uniform image has no foreground; Otsu would otherwise pick an
    # arbitrary split. Guard against marking the whole page as ink.
    if int(gray.min()) == int(gray.max()):
        return np.zeros(gray.shape, dtype=bool)
    return gray <= threshold


def _projection_profile_score(ink_mask: np.ndarray) -> float:
    """Sharpness score of the horizontal projection profile.

    A well-deskewed page has text concentrated in a few rows, producing a
    spiky row-wise ink histogram. We score that spikiness as the sum of squared
    differences between consecutive row sums; the skew angle that maximises it
    aligns text lines with image rows.
    """
    row_sums = ink_mask.sum(axis=1).astype(np.float64)
    if row_sums.size < 2:
        return 0.0
    diffs = np.diff(row_sums)
    return float(np.dot(diffs, diffs))


def estimate_skew_angle(gray: np.ndarray, config: PreprocessConfig) -> float:
    """Estimate the page skew angle in degrees via projection-profile search.

    Rotating by the negative of the returned angle makes text lines horizontal.
    Returns ``0.0`` when no rotation improves the profile or when the estimated
    magnitude is below ``config.deskew_min_angle``.
    """
    from PIL import Image

    base_mask = ink_mask_from_gray(gray)
    if not base_mask.any():
        return 0.0

    pil = Image.fromarray(gray)
    limit = config.deskew_angle_limit
    step = config.deskew_angle_step
    # Build a symmetric set of candidate angles including 0.
    n = int(round(limit / step))
    candidates = [0.0]
    for i in range(1, n + 1):
        candidates.append(i * step)
        candidates.append(-i * step)

    best_angle = 0.0
    best_score = _projection_profile_score(base_mask)
    for angle in candidates:
        if angle == 0.0:
            continue
        rotated = pil.rotate(
            angle,
            resample=Image.BILINEAR,
            expand=False,
            fillcolor=config.background_value,
        )
        mask = ink_mask_from_gray(np.asarray(rotated, dtype=np.uint8))
        score = _projection_profile_score(mask)
        if score > best_score:
            best_score = score
            best_angle = angle

    if abs(best_angle) < config.deskew_min_angle:
        return 0.0
    return best_angle


def deskew(gray: np.ndarray, config: PreprocessConfig | None = None) -> np.ndarray:
    """Deskew a grayscale image so its text lines are horizontal.

    Returns a new ``uint8`` array of the same shape. If no meaningful skew is
    detected the input is returned unchanged (no resampling).
    """
    config = config or PreprocessConfig()
    angle = estimate_skew_angle(gray, config)
    if angle == 0.0:
        return gray

    from PIL import Image

    rotated = Image.fromarray(gray).rotate(
        angle,
        resample=Image.BILINEAR,
        expand=False,
        fillcolor=config.background_value,
    )
    return np.asarray(rotated, dtype=np.uint8)


def normalize(gray: np.ndarray) -> np.ndarray:
    """Scale a grayscale ``uint8`` image to a ``float32`` array in ``[0, 1]``."""
    return gray.astype(np.float32) / 255.0


def preprocess(
    image_bytes: bytes, config: PreprocessConfig | None = None
) -> PreprocessedImage:
    """Decode → grayscale → deskew → normalize raw image bytes.

    This is the single entry point used by the segmentation stage. It returns a
    :class:`PreprocessedImage` carrying the deskewed grayscale image, its
    normalised float form, and the ink mask used for layout analysis.

    Raises
    ------
    ImageDecodeError
        If the bytes cannot be decoded as an image.
    """
    config = config or PreprocessConfig()
    gray = decode_image(image_bytes)  # already grayscale uint8 (H, W)
    deskewed = deskew(gray, config)
    normalized = normalize(deskewed)
    ink = ink_mask_from_gray(deskewed)
    return PreprocessedImage(gray=deskewed, normalized=normalized, ink_mask=ink)


__all__ = [
    "ImageDecodeError",
    "PreprocessConfig",
    "PreprocessedImage",
    "decode_image",
    "to_grayscale",
    "otsu_threshold",
    "ink_mask_from_gray",
    "estimate_skew_angle",
    "deskew",
    "normalize",
    "preprocess",
]
