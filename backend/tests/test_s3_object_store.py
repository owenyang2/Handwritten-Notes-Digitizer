"""S3CompatibleObjectStore tests against an in-process fake client.

No AWS, MinIO, moto, or boto3 required — the fake records put/get/delete.
"""

from __future__ import annotations

import io

import pytest

from app.data.object_store import (
    InvalidReferenceError,
    ObjectKind,
    ObjectNotFoundError,
    ObjectStoreError,
)
from app.data.s3_object_store import S3CompatibleObjectStore


class FakeS3Error(Exception):
    def __init__(self, code: str, http_status: int = 404) -> None:
        super().__init__(code)
        self.response = {
            "Error": {"Code": code},
            "ResponseMetadata": {"HTTPStatusCode": http_status},
        }


class FakeS3:
    """Minimal S3 client that records put/get/delete calls."""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.puts: list[tuple[str, str, bytes]] = []
        self.gets: list[tuple[str, str]] = []
        self.heads: list[tuple[str, str]] = []
        self.deletes: list[tuple[str, str]] = []

    def put_object(self, *, Bucket: str, Key: str, Body: bytes) -> dict:
        payload = Body if isinstance(Body, bytes) else bytes(Body)
        self.objects[(Bucket, Key)] = payload
        self.puts.append((Bucket, Key, payload))
        return {}

    def get_object(self, *, Bucket: str, Key: str) -> dict:
        self.gets.append((Bucket, Key))
        try:
            data = self.objects[(Bucket, Key)]
        except KeyError as exc:
            raise FakeS3Error("NoSuchKey") from exc
        return {"Body": io.BytesIO(data)}

    def head_object(self, *, Bucket: str, Key: str) -> dict:
        self.heads.append((Bucket, Key))
        try:
            data = self.objects[(Bucket, Key)]
        except KeyError as exc:
            raise FakeS3Error("404", http_status=404) from exc
        return {"ContentLength": len(data)}

    def delete_object(self, *, Bucket: str, Key: str) -> dict:
        self.deletes.append((Bucket, Key))
        self.objects.pop((Bucket, Key), None)
        return {}


@pytest.fixture()
def fake_client() -> FakeS3:
    return FakeS3()


@pytest.fixture()
def store(fake_client: FakeS3) -> S3CompatibleObjectStore:
    return S3CompatibleObjectStore(bucket="hnd-objects", prefix="prod", client=fake_client)


def test_missing_boto3_raises_clear_error(monkeypatch) -> None:
    def _fail():
        raise ImportError("No module named boto3")

    monkeypatch.setattr("app.data.s3_object_store._import_boto3", _fail)
    with pytest.raises(ObjectStoreError, match="boto3"):
        S3CompatibleObjectStore(bucket="hnd-objects")


def test_missing_bucket_raises() -> None:
    with pytest.raises(ObjectStoreError, match="s3_bucket"):
        S3CompatibleObjectStore(bucket=None, client=FakeS3())


def test_put_get_roundtrip(store: S3CompatibleObjectStore, fake_client: FakeS3) -> None:
    data = b"\x89PNG\r\n\x1a\n fake"
    ref = store.put_image_bytes("user-1", data, name="page", suffix=".png")
    assert ref == f"{ObjectKind.IMAGE.value}/user-1/page.png"
    assert store.get(ref) == data
    assert fake_client.puts[0][0] == "hnd-objects"
    assert fake_client.puts[0][1] == "prod/images/user-1/page.png"


def test_open_streams_bytes(store: S3CompatibleObjectStore) -> None:
    ref = store.put_region_crop("user-2", b"crop", name="r", suffix="png")
    with store.open(ref) as fh:
        assert fh.read() == b"crop"


def test_exists_and_idempotent_delete(
    store: S3CompatibleObjectStore, fake_client: FakeS3
) -> None:
    ref = store.put_adapter_weights("user-3", b"weights", name="a1")
    assert store.exists(ref) is True
    store.delete(ref)
    assert store.exists(ref) is False
    store.delete(ref)
    assert fake_client.deletes[-1][1] == "prod/adapters/user-3/a1"


def test_get_missing_raises(store: S3CompatibleObjectStore) -> None:
    with pytest.raises(ObjectNotFoundError):
        store.get("images/user-1/missing.png")


def test_put_overwrites(store: S3CompatibleObjectStore) -> None:
    ref = store.put_document_bytes("user-1", b"v1", name="notes")
    store.put(ref, b"v2")
    assert store.get(ref) == b"v2"


@pytest.mark.parametrize(
    "bad_reference",
    ["../escape", "images/../../etc/passwd", "/absolute/path", "images\\user\\file", ""],
)
def test_invalid_references_rejected(
    store: S3CompatibleObjectStore, bad_reference: str
) -> None:
    with pytest.raises(InvalidReferenceError):
        store.put(bad_reference, b"data")
