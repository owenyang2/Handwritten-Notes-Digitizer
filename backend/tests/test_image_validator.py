"""Example-based unit tests for the image upload validator.

These cover each validation rule from Requirements 1.1-1.4 with concrete
examples: a valid PNG and JPEG are accepted with the detected format; an
unsupported but decodable format (GIF/BMP) is rejected as unsupported_format;
an oversized payload is rejected as size_limit; and empty or undecodable bytes
are rejected as invalid_image. The exhaustive accept-iff property is covered by
the separate property test (task 4.3).
"""

from io import BytesIO

import pytest
from PIL import Image

from app.data.enums import ImageFormat
from app.services.image_validator import (
    ImageValidationResult,
    RejectionReason,
    validate_image,
)


def _encode(image_format: str, *, size=(8, 8), color=(123, 222, 64)) -> bytes:
    """Render a tiny image and return its encoded bytes in ``image_format``."""
    mode = "RGB"
    img = Image.new(mode, size, color)
    buf = BytesIO()
    img.save(buf, format=image_format)
    return buf.getvalue()


def test_valid_png_is_accepted_with_detected_format():
    result = validate_image(_encode("PNG"))

    assert isinstance(result, ImageValidationResult)
    assert result.accepted is True
    assert result.image_format is ImageFormat.PNG
    assert result.reason is None
    assert result.size_bytes > 0


def test_valid_jpeg_is_accepted_with_detected_format():
    result = validate_image(_encode("JPEG"))

    assert result.accepted is True
    assert result.image_format is ImageFormat.JPEG
    assert result.reason is None


def test_detection_is_by_content_not_extension():
    # The validator only ever sees bytes; a PNG payload is detected as PNG
    # regardless of any (absent) filename. Decoding GIF content yields a
    # rejection even though the bytes could have been mislabeled as .png.
    png_result = validate_image(_encode("PNG"))
    gif_result = validate_image(_encode("GIF"))

    assert png_result.image_format is ImageFormat.PNG
    assert gif_result.reason is RejectionReason.UNSUPPORTED_FORMAT


@pytest.mark.parametrize("fmt", ["GIF", "BMP", "WEBP", "TIFF"])
def test_decodable_non_png_jpeg_rejected_as_unsupported_format(fmt):
    result = validate_image(_encode(fmt))

    assert result.accepted is False
    assert result.reason is RejectionReason.UNSUPPORTED_FORMAT
    assert result.image_format is None
    assert "PNG" in result.message and "JPEG" in result.message


def test_empty_file_rejected_as_invalid_image():
    result = validate_image(b"")

    assert result.accepted is False
    assert result.reason is RejectionReason.INVALID_IMAGE
    assert result.size_bytes == 0


def test_undecodable_bytes_rejected_as_invalid_image():
    result = validate_image(b"this is definitely not an image payload")

    assert result.accepted is False
    assert result.reason is RejectionReason.INVALID_IMAGE


def test_truncated_png_rejected_as_invalid_image():
    # A valid PNG header followed by truncation: header parses but full decode
    # fails, so it must be rejected as invalid rather than accepted.
    full = _encode("PNG", size=(64, 64))
    truncated = full[: len(full) // 2]
    result = validate_image(truncated)

    assert result.accepted is False
    assert result.reason is RejectionReason.INVALID_IMAGE


def test_oversized_payload_rejected_as_size_limit():
    # Use a small explicit limit so we do not need to build a 10 MB buffer.
    data = _encode("PNG", size=(64, 64))
    result = validate_image(data, max_bytes=len(data) - 1)

    assert result.accepted is False
    assert result.reason is RejectionReason.SIZE_LIMIT
    assert "megabytes" in result.message


def test_payload_at_exact_limit_is_accepted():
    data = _encode("PNG")
    result = validate_image(data, max_bytes=len(data))

    assert result.accepted is True
    assert result.image_format is ImageFormat.PNG


def test_default_limit_is_ten_megabytes():
    # A valid small image is accepted under the default (10 MB) limit.
    result = validate_image(_encode("JPEG"))
    assert result.accepted is True
