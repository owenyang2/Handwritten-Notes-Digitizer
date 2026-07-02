"""Image upload validator for the Recognition_Service.

This module implements the validation rules the Recognition_Service applies to
an uploaded file *before* it accepts the image and initiates recognition
(design.md, "Recognition_Service"; Requirements 1.1-1.4).

Validation is performed on the **decoded content** of the bytes, never on a
filename or a client-supplied content-type. A file is accepted if and only if
all of the following hold:

* its size is greater than ``0`` bytes and no greater than
  ``max_bytes`` (default ``10,485,760`` = 10 MB, Req 1.1, 1.3);
* its content can be decoded as an image (Req 1.4); and
* the decoded image format is PNG or JPEG (Req 1.1, 1.2).

Every rejection carries a structured :class:`RejectionReason` identifying the
single violated rule plus a human-readable message:

* :attr:`RejectionReason.UNSUPPORTED_FORMAT` - decoded fine but is not PNG/JPEG
  (Req 1.2); the message names PNG and JPEG as the supported formats.
* :attr:`RejectionReason.SIZE_LIMIT` - larger than the maximum allowed size
  (Req 1.3); the message states the 10 MB maximum.
* :attr:`RejectionReason.INVALID_IMAGE` - empty, or content that cannot be
  decoded as an image (Req 1.4).

The public entry point is :func:`validate_image`, which returns an immutable
:class:`ImageValidationResult` distinguishing accept from reject. On success the
result also reports the detected :class:`~app.data.enums.ImageFormat`.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from io import BytesIO
from typing import Optional

from app.config.settings import get_settings
from app.data.enums import ImageFormat

# Pillow format strings (``Image.format``) mapped to our ImageFormat enum.
# Detection is by decoded content, so these come from the image's magic bytes,
# not its extension.
_PILLOW_FORMAT_TO_ENUM: dict[str, ImageFormat] = {
    "PNG": ImageFormat.PNG,
    "JPEG": ImageFormat.JPEG,
}


class RejectionReason(str, enum.Enum):
    """The single validation rule a rejected upload violated."""

    UNSUPPORTED_FORMAT = "unsupported_format"
    SIZE_LIMIT = "size_limit"
    INVALID_IMAGE = "invalid_image"


# User-facing messages. These intentionally name the relevant rule so a caller
# can surface them directly (Req 1.2 names PNG/JPEG; Req 1.3 states 10 MB).
_SUPPORTED_FORMATS_MESSAGE = "Unsupported image format. The supported formats are PNG and JPEG."
_INVALID_IMAGE_MESSAGE = "The file is not a valid image."


def _size_limit_message(max_bytes: int) -> str:
    megabytes = max_bytes / (1024 * 1024)
    # Render whole numbers cleanly (e.g. "10 megabytes") while still supporting
    # non-default limits.
    if megabytes == int(megabytes):
        megabytes_str = str(int(megabytes))
    else:
        megabytes_str = f"{megabytes:g}"
    return f"The image exceeds the maximum allowed size of {megabytes_str} megabytes."


@dataclass(frozen=True)
class ImageValidationResult:
    """Outcome of validating an uploaded file's bytes.

    Exactly one of two shapes is produced:

    * **accepted** - ``accepted is True``, ``image_format`` is the detected
      :class:`~app.data.enums.ImageFormat`, and ``reason`` is ``None``.
    * **rejected** - ``accepted is False``, ``reason`` identifies the violated
      rule, ``message`` explains it, and ``image_format`` is ``None``.

    ``size_bytes`` always reports the length of the validated payload.
    """

    accepted: bool
    size_bytes: int
    image_format: Optional[ImageFormat] = None
    reason: Optional[RejectionReason] = None
    message: Optional[str] = None

    def __bool__(self) -> bool:  # pragma: no cover - convenience only
        return self.accepted


def _accept(image_format: ImageFormat, size_bytes: int) -> ImageValidationResult:
    return ImageValidationResult(
        accepted=True,
        size_bytes=size_bytes,
        image_format=image_format,
    )


def _reject(
    reason: RejectionReason, message: str, size_bytes: int
) -> ImageValidationResult:
    return ImageValidationResult(
        accepted=False,
        size_bytes=size_bytes,
        reason=reason,
        message=message,
    )


def _detect_format(file_bytes: bytes) -> Optional[ImageFormat]:
    """Return the decoded image format, or ``None`` if it is not PNG/JPEG.

    Raises an exception (any Pillow/IO error) when the bytes cannot be decoded
    as an image at all, which the caller maps to ``INVALID_IMAGE``.
    """
    # Imported lazily so the module stays importable without the ML/image stack.
    from PIL import Image

    with Image.open(BytesIO(file_bytes)) as img:
        pillow_format = img.format
        # Force a full decode of the pixel data so truncated/corrupt payloads
        # that merely have a valid header are still rejected as undecodable.
        img.load()

    if pillow_format is None:
        return None
    return _PILLOW_FORMAT_TO_ENUM.get(pillow_format)


def validate_image(
    file_bytes: bytes, *, max_bytes: Optional[int] = None
) -> ImageValidationResult:
    """Validate uploaded ``file_bytes`` by decoded content, size, and decodability.

    Args:
        file_bytes: The raw uploaded bytes.
        max_bytes: Maximum allowed size in bytes. Defaults to the configured
            ``max_upload_bytes`` (10 MB, Req 1.3).

    Returns:
        An :class:`ImageValidationResult`. ``accepted`` is ``True`` exactly when
        the content decodes as a PNG or JPEG image whose size is in
        ``(0, max_bytes]``; otherwise it is ``False`` and ``reason`` identifies
        the violated rule.
    """
    if max_bytes is None:
        max_bytes = get_settings().max_upload_bytes

    size_bytes = len(file_bytes)

    # Rule: empty file is not a valid image (Req 1.4).
    if size_bytes == 0:
        return _reject(RejectionReason.INVALID_IMAGE, _INVALID_IMAGE_MESSAGE, size_bytes)

    # Rule: size limit (Req 1.3). Checked before decoding so an oversized
    # payload is rejected without the cost (and risk) of decoding it.
    if size_bytes > max_bytes:
        return _reject(
            RejectionReason.SIZE_LIMIT, _size_limit_message(max_bytes), size_bytes
        )

    # Rule: decodability (Req 1.4) and format (Req 1.1, 1.2).
    try:
        image_format = _detect_format(file_bytes)
    except Exception:
        # UnidentifiedImageError, OSError (truncated), DecompressionBombError,
        # etc. all mean the content is not a usable image.
        return _reject(RejectionReason.INVALID_IMAGE, _INVALID_IMAGE_MESSAGE, size_bytes)

    if image_format is None:
        # Decoded successfully but the format is something other than PNG/JPEG
        # (e.g. GIF, BMP, WEBP) (Req 1.2).
        return _reject(
            RejectionReason.UNSUPPORTED_FORMAT, _SUPPORTED_FORMATS_MESSAGE, size_bytes
        )

    return _accept(image_format, size_bytes)


__all__ = [
    "ImageFormat",
    "ImageValidationResult",
    "RejectionReason",
    "validate_image",
]
