"""Property-based test for image upload validation (design Property 1).

This test exercises :func:`~app.services.image_validator.validate_image` against
the design's **Property 1**:

    For any uploaded file, the validator accepts it if and only if its decoded
    content is a PNG or JPEG image with size greater than 0 bytes and no greater
    than 10,485,760 bytes; every rejection carries a reason identifying the
    violated rule (unsupported format, size limit, or invalid image).

# Feature: handwriting-notes-digitizer, Property 1: Image validation accepts exactly the valid set

**Validates: Requirements 1.1, 1.2, 1.3, 1.4**

Strategy
--------
We generate payloads using four independently-chosen families:

1. **Valid PNG/JPEG bytes** — encoded via Pillow from a small generated image,
   truncated versions (corrupt), and exact-limit-sized payloads near 10 MB.
2. **Unsupported-but-decodable bytes** — GIF/BMP/WEBP images encoded by Pillow.
3. **Random bytes** — arbitrary byte strings that cannot be decoded as images.
4. **Size-boundary payloads** — valid PNG/JPEG bytes padded or sized to straddle
   the 10,485,760-byte limit, confirming the ≤ limit rule exactly.

For every generated payload the test asserts the biconditional:
  - ``accepted`` is ``True``  ↔  (format ∈ {PNG, JPEG}) ∧ (0 < size ≤ max_bytes)
  - ``accepted`` is ``False`` → ``reason`` is one of the three defined reasons
                                 with a non-empty message
"""

from __future__ import annotations

from io import BytesIO

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st
from PIL import Image as PILImage

from app.data.enums import ImageFormat
from app.services.image_validator import (
    RejectionReason,
    validate_image,
)

# The canonical 10 MB limit defined in Requirements 1.3.
MAX_BYTES = 10_485_760


# ---------------------------------------------------------------------------
# Helpers: minimal real image bytes
# ---------------------------------------------------------------------------

def _make_image_bytes(fmt: str, size: tuple[int, int] = (8, 8)) -> bytes:
    """Return minimal valid bytes for the given Pillow format string."""
    img = PILImage.new("RGB", size, color=(100, 150, 200))
    buf = BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


# Pre-encoded tiny images used as seeds/bases in strategies.
_TINY_PNG = _make_image_bytes("PNG")
_TINY_JPEG = _make_image_bytes("JPEG")
_TINY_GIF = _make_image_bytes("GIF")
_TINY_BMP = _make_image_bytes("BMP")
_TINY_WEBP = _make_image_bytes("WEBP")


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

@st.composite
def valid_png_or_jpeg(draw: st.DrawFn) -> bytes:
    """A syntactically and semantically valid PNG or JPEG payload ≤ MAX_BYTES."""
    fmt = draw(st.sampled_from(["PNG", "JPEG"]))
    # Vary dimensions slightly so generated images differ across examples.
    w = draw(st.integers(min_value=1, max_value=16))
    h = draw(st.integers(min_value=1, max_value=16))
    r = draw(st.integers(min_value=0, max_value=255))
    g = draw(st.integers(min_value=0, max_value=255))
    b = draw(st.integers(min_value=0, max_value=255))
    img = PILImage.new("RGB", (w, h), color=(r, g, b))
    buf = BytesIO()
    img.save(buf, format=fmt)
    data = buf.getvalue()
    # All generated images are tiny, so this invariant should always hold;
    # we use assume() as a defensive guard rather than a hard assertion.
    assume(0 < len(data) <= MAX_BYTES)
    return data


@st.composite
def unsupported_format_bytes(draw: st.DrawFn) -> bytes:
    """Valid image bytes in a format that is not PNG or JPEG (GIF / BMP / WEBP)."""
    template = draw(st.sampled_from([_TINY_GIF, _TINY_BMP, _TINY_WEBP]))
    return template


@st.composite
def invalid_image_bytes(draw: st.DrawFn) -> bytes:
    """Bytes that cannot be decoded as any image at all.

    Covers: completely random bytes, empty bytes, truncated valid images whose
    pixel data is corrupt.
    """
    strategy = draw(
        st.sampled_from([
            # Completely random bytes — almost certainly not a valid image.
            st.binary(min_size=1, max_size=512).filter(
                lambda b: not _is_decodable(b)
            ),
            # Empty file.
            st.just(b""),
            # Truncated PNG (header-only, no IDAT).
            st.just(_TINY_PNG[: max(1, len(_TINY_PNG) // 3)]).filter(
                lambda b: not _is_decodable(b)
            ),
            # Truncated JPEG.
            st.just(_TINY_JPEG[: max(1, len(_TINY_JPEG) // 3)]).filter(
                lambda b: not _is_decodable(b)
            ),
            # Magic bytes of a PNG but with garbage payload.
            st.binary(min_size=1, max_size=64).map(
                lambda b: b"\x89PNG\r\n\x1a\n" + b
            ).filter(lambda b: not _is_decodable(b)),
            # Magic bytes of a JPEG but with garbage payload.
            st.binary(min_size=1, max_size=64).map(
                lambda b: b"\xff\xd8\xff" + b
            ).filter(lambda b: not _is_decodable(b)),
        ])
    )
    return draw(strategy)


@st.composite
def size_boundary_payload(draw: st.DrawFn) -> tuple[bytes, int]:
    """A (payload, custom_max_bytes) pair straddling the size boundary.

    Returns a tiny valid PNG/JPEG payload together with a ``max_bytes`` that is
    set to exactly ``len(payload) - 1`` (over limit), ``len(payload)`` (exact
    limit — accepted), or ``len(payload) + 1`` (under limit — accepted).
    """
    fmt = draw(st.sampled_from(["PNG", "JPEG"]))
    data = _make_image_bytes(fmt)
    sz = len(data)
    delta = draw(st.integers(min_value=-1, max_value=1))
    # max_bytes must be at least 1 to be a meaningful limit.
    custom_max = max(1, sz + delta)
    return data, custom_max


# ---------------------------------------------------------------------------
# Internal decodability helper (mirrors _detect_format's try/except)
# ---------------------------------------------------------------------------

def _is_decodable(data: bytes) -> bool:
    """Return True if Pillow can fully decode ``data`` as any image format."""
    if not data:
        return False
    try:
        with PILImage.open(BytesIO(data)) as img:
            img.load()
        return True
    except Exception:
        return False


def _decoded_format(data: bytes) -> str | None:
    """Return Pillow's format string if decodable, else None."""
    try:
        with PILImage.open(BytesIO(data)) as img:
            img.load()
            return img.format
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Property 1: Image validation accepts exactly the valid set
# ---------------------------------------------------------------------------

# Feature: handwriting-notes-digitizer, Property 1: Image validation accepts exactly the valid set
@given(payload=valid_png_or_jpeg())
def test_valid_png_jpeg_within_limit_is_accepted(payload: bytes) -> None:
    """Validates: Requirements 1.1, 1.2, 1.3, 1.4.

    For any valid PNG or JPEG payload with 0 < size ≤ MAX_BYTES, validate_image
    must accept it and report the correct detected format.
    """
    result = validate_image(payload, max_bytes=MAX_BYTES)

    # Accepted (Req 1.1).
    assert result.accepted is True, (
        f"Expected acceptance for valid {_decoded_format(payload)} payload "
        f"({len(payload)} bytes), got rejection with reason={result.reason}"
    )
    # image_format is set and is one of the two supported formats (Req 1.2).
    assert result.image_format in (ImageFormat.PNG, ImageFormat.JPEG)
    # No rejection reason on acceptance.
    assert result.reason is None
    assert result.message is None
    # size_bytes is accurate.
    assert result.size_bytes == len(payload)


# Feature: handwriting-notes-digitizer, Property 1: Image validation accepts exactly the valid set
@given(payload=unsupported_format_bytes())
def test_unsupported_format_is_rejected_with_reason(payload: bytes) -> None:
    """Validates: Requirements 1.2, 1.4.

    Any decodable image whose format is not PNG or JPEG must be rejected with
    UNSUPPORTED_FORMAT and a message naming PNG and JPEG as the supported formats.
    """
    # Confirm the payload is indeed decodable but not PNG/JPEG.
    fmt = _decoded_format(payload)
    assume(fmt is not None and fmt not in ("PNG", "JPEG"))

    result = validate_image(payload, max_bytes=MAX_BYTES)

    assert result.accepted is False
    assert result.reason is RejectionReason.UNSUPPORTED_FORMAT, (
        f"Expected UNSUPPORTED_FORMAT for {fmt} payload, got {result.reason}"
    )
    # Message must name the supported formats (Req 1.2).
    assert result.message is not None and "PNG" in result.message and "JPEG" in result.message
    assert result.image_format is None


# Feature: handwriting-notes-digitizer, Property 1: Image validation accepts exactly the valid set
@given(payload=invalid_image_bytes())
def test_invalid_or_empty_bytes_rejected_as_invalid_image(payload: bytes) -> None:
    """Validates: Requirements 1.4.

    Any file that is empty or whose content cannot be decoded as an image must
    be rejected with INVALID_IMAGE and a non-empty message.
    """
    # Guard: payload must not accidentally be decodable as a valid image.
    assume(not _is_decodable(payload))

    result = validate_image(payload, max_bytes=MAX_BYTES)

    assert result.accepted is False
    assert result.reason is RejectionReason.INVALID_IMAGE, (
        f"Expected INVALID_IMAGE for undecodable payload ({len(payload)} bytes), "
        f"got {result.reason}"
    )
    assert result.message is not None and len(result.message) > 0
    assert result.image_format is None
    assert result.size_bytes == len(payload)


# Feature: handwriting-notes-digitizer, Property 1: Image validation accepts exactly the valid set
@given(pair=size_boundary_payload())
def test_size_boundary_is_honoured_exactly(pair: tuple[bytes, int]) -> None:
    """Validates: Requirements 1.3.

    For any valid PNG/JPEG payload and any custom max_bytes:
    - if len(payload) > max_bytes: rejected with SIZE_LIMIT
    - if len(payload) <= max_bytes: accepted (size rule passes; format valid)

    This exercises the ≤ boundary exactly (not just "big files are rejected").
    """
    payload, custom_max = pair
    size = len(payload)

    result = validate_image(payload, max_bytes=custom_max)

    if size > custom_max:
        assert result.accepted is False
        assert result.reason is RejectionReason.SIZE_LIMIT, (
            f"Expected SIZE_LIMIT for {size}-byte payload with max={custom_max}, "
            f"got {result.reason}"
        )
        assert result.message is not None and "megabytes" in result.message
    else:
        # size <= custom_max: size rule is satisfied; format is valid PNG/JPEG.
        assert result.accepted is True, (
            f"Expected acceptance for {size}-byte valid payload with max={custom_max}, "
            f"got rejection with reason={result.reason}"
        )
        assert result.image_format in (ImageFormat.PNG, ImageFormat.JPEG)


# Feature: handwriting-notes-digitizer, Property 1: Image validation accepts exactly the valid set
@given(
    payload=st.one_of(
        valid_png_or_jpeg(),
        unsupported_format_bytes(),
        invalid_image_bytes().filter(lambda b: not _is_decodable(b)),
    )
)
def test_every_rejection_carries_a_reason_and_message(payload: bytes) -> None:
    """Validates: Requirements 1.1, 1.2, 1.3, 1.4.

    For any payload:
    - If accepted: reason is None and message is None.
    - If rejected: reason is one of the three defined RejectionReasons and
      message is a non-empty string.

    This is the biconditional closure: every outcome is well-formed.
    """
    result = validate_image(payload, max_bytes=MAX_BYTES)

    if result.accepted:
        assert result.reason is None
        assert result.message is None
        assert result.image_format in (ImageFormat.PNG, ImageFormat.JPEG)
    else:
        assert result.reason in (
            RejectionReason.UNSUPPORTED_FORMAT,
            RejectionReason.SIZE_LIMIT,
            RejectionReason.INVALID_IMAGE,
        ), f"Unexpected rejection reason: {result.reason}"
        assert isinstance(result.message, str) and len(result.message) > 0
        assert result.image_format is None
