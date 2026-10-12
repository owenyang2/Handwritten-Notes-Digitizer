from __future__ import annotations

import asyncio

import pytest

from app.services.upload_limits import (
    UploadTooLarge,
    read_request_stream_bounded,
    read_upload_file_bounded,
)


class FakeUpload:
    def __init__(self, data: bytes, *, declared: int | None = None) -> None:
        self.data = data
        self.headers = {} if declared is None else {"content-length": str(declared)}
        self.requested_sizes: list[int] = []

    async def read(self, size: int = -1) -> bytes:
        self.requested_sizes.append(size)
        if size < 0:
            raise AssertionError("bounded reader requested an unbounded read")
        chunk, self.data = self.data[:size], self.data[size:]
        return chunk


def test_upload_file_reader_never_requests_an_unbounded_body() -> None:
    upload = FakeUpload(b"abcdef")
    assert asyncio.run(read_upload_file_bounded(upload, 6)) == b"abcdef"
    assert upload.requested_sizes
    assert all(size > 0 for size in upload.requested_sizes)


def test_upload_file_reader_rejects_declared_oversize_before_reading() -> None:
    upload = FakeUpload(b"should not be read", declared=7)
    with pytest.raises(UploadTooLarge):
        asyncio.run(read_upload_file_bounded(upload, 6))
    assert upload.requested_sizes == []


def test_upload_file_reader_rejects_streamed_oversize() -> None:
    upload = FakeUpload(b"abcdefg")
    with pytest.raises(UploadTooLarge):
        asyncio.run(read_upload_file_bounded(upload, 6))


async def _chunks(*chunks: bytes):
    for chunk in chunks:
        yield chunk


def test_request_stream_reader_bounds_total_bytes() -> None:
    assert asyncio.run(read_request_stream_bounded(_chunks(b"ab", b"cd"), 4)) == b"abcd"
    with pytest.raises(UploadTooLarge):
        asyncio.run(read_request_stream_bounded(_chunks(b"ab", b"cde"), 4))
