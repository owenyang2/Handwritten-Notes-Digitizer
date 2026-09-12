"""Bounded readers for multipart and raw upload request bodies.

Starlette's convenience ``request.body()`` and ``UploadFile.file.read()``
methods are unbounded from the application code's point of view.  The routes
in this project use these helpers before parsing images, PDFs, or upload parts
so a client cannot make the API buffer an arbitrary request before the size
limit is applied.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol


class UploadTooLarge(ValueError):
    """Raised after a bounded reader observes more than its configured limit."""


class _UploadReader(Protocol):
    headers: object

    async def read(self, size: int = -1) -> bytes: ...


def _declared_length(headers: object) -> int | None:
    """Return a valid declared length when a headers object exposes one."""
    getter = getattr(headers, "get", None)
    if not callable(getter):
        return None
    value = getter("content-length")
    if value is None:
        return None
    try:
        length = int(str(value))
    except (TypeError, ValueError):
        return None
    return length if length >= 0 else None


async def read_upload_file_bounded(upload: _UploadReader, max_bytes: int) -> bytes:
    """Read an ``UploadFile`` without ever asking it for an unbounded body."""
    if max_bytes < 0:
        raise ValueError("max_bytes must be non-negative")
    declared = _declared_length(upload.headers)
    if declared is not None and declared > max_bytes:
        raise UploadTooLarge(f"upload exceeds the {max_bytes}-byte limit")

    chunks: list[bytes] = []
    total = 0
    chunk_size = min(1024 * 1024, max_bytes + 1)
    while total <= max_bytes:
        chunk = await upload.read(chunk_size)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise UploadTooLarge(f"upload exceeds the {max_bytes}-byte limit")
        chunks.append(chunk)
    return b"".join(chunks)


async def read_request_stream_bounded(
    stream: AsyncIterator[bytes],
    max_bytes: int,
) -> bytes:
    """Collect an async request stream while enforcing a hard byte bound."""
    if max_bytes < 0:
        raise ValueError("max_bytes must be non-negative")
    chunks: list[bytes] = []
    total = 0
    async for chunk in stream:
        if not chunk:
            continue
        total += len(chunk)
        if total > max_bytes:
            raise UploadTooLarge(f"request body exceeds the {max_bytes}-byte limit")
        chunks.append(chunk)
    return b"".join(chunks)
