"""Unit tests for the object/image store abstraction.

Covers the core store/retrieve roundtrip for the three artifact kinds (image
bytes, region crops, adapter weights), reference safety (no path traversal),
overwrite semantics, idempotent delete, and not-found handling.
"""

import pytest

from app.data.object_store import (
    FilesystemObjectStore,
    InvalidReferenceError,
    ObjectKind,
    ObjectNotFoundError,
)


@pytest.fixture()
def store(tmp_path):
    return FilesystemObjectStore(root=tmp_path / "objectstore")


def test_root_created_on_init(tmp_path):
    root = tmp_path / "nested" / "store"
    s = FilesystemObjectStore(root=root)
    assert s.root.is_dir()


def test_put_image_bytes_roundtrip(store):
    data = b"\x89PNG\r\n\x1a\n fake image bytes"
    ref = store.put_image_bytes("user-1", data, suffix=".png")

    assert ref.startswith(f"{ObjectKind.IMAGE.value}/user-1/")
    assert ref.endswith(".png")
    assert store.exists(ref)
    assert store.get(ref) == data


def test_put_region_crop_roundtrip(store):
    data = b"crop-bytes-123"
    ref = store.put_region_crop("user-2", data, name="region-abc", suffix="png")

    assert ref == f"{ObjectKind.CROP.value}/user-2/region-abc.png"
    assert store.get(ref) == data


def test_put_adapter_weights_roundtrip(store):
    data = b"\x00\x01\x02 adapter weights blob"
    ref = store.put_adapter_weights("user-3", data, name="adapter-v1")

    assert ref == f"{ObjectKind.ADAPTER.value}/user-3/adapter-v1"
    assert store.get(ref) == data


def test_open_streams_same_bytes(store):
    data = b"streamed-content"
    ref = store.put_image_bytes("user-1", data)
    with store.open(ref) as fh:
        assert fh.read() == data


def test_generated_references_are_unique(store):
    data = b"x"
    ref_a = store.put_image_bytes("user-1", data)
    ref_b = store.put_image_bytes("user-1", data)
    assert ref_a != ref_b


def test_references_are_namespaced_by_user(store):
    ref_a = store.put_region_crop("alice", b"a", name="r", suffix="png")
    ref_b = store.put_region_crop("bob", b"b", name="r", suffix="png")
    assert ref_a != ref_b
    assert store.get(ref_a) == b"a"
    assert store.get(ref_b) == b"b"


def test_put_overwrites_existing_reference(store):
    ref = store.put_region_crop("user-1", b"first", name="r")
    store.put(ref, b"second")
    assert store.get(ref) == b"second"


def test_get_missing_reference_raises(store):
    with pytest.raises(ObjectNotFoundError):
        store.get(f"{ObjectKind.IMAGE.value}/user-1/does-not-exist.png")


def test_open_missing_reference_raises(store):
    with pytest.raises(ObjectNotFoundError):
        store.open(f"{ObjectKind.CROP.value}/user-1/missing")


def test_delete_is_idempotent(store):
    ref = store.put_image_bytes("user-1", b"data")
    assert store.exists(ref)
    store.delete(ref)
    assert not store.exists(ref)
    # Deleting again does not raise.
    store.delete(ref)


def test_exists_false_for_unknown_reference(store):
    assert store.exists(f"{ObjectKind.ADAPTER.value}/user-1/none") is False


@pytest.mark.parametrize(
    "bad_reference",
    [
        "../escape",
        "images/../../etc/passwd",
        "/absolute/path",
        "images/user/..",
        "images\\user\\file",
        "",
    ],
)
def test_traversal_and_malformed_references_rejected(store, bad_reference):
    with pytest.raises(InvalidReferenceError):
        store.put(bad_reference, b"data")


@pytest.mark.parametrize("bad_user", ["../etc", "a/b", "", "us er"])
def test_build_reference_rejects_unsafe_user(bad_user):
    with pytest.raises(InvalidReferenceError):
        FilesystemObjectStore.build_reference(ObjectKind.IMAGE, bad_user)


def test_clear_removes_all_objects(store):
    ref = store.put_image_bytes("user-1", b"data")
    assert store.exists(ref)
    store.clear()
    assert not store.exists(ref)
    assert store.root.is_dir()


def test_default_root_uses_settings(monkeypatch, tmp_path):
    from app.config import settings as settings_module

    custom_root = tmp_path / "from-settings"
    monkeypatch.setattr(
        settings_module.get_settings(), "object_store_path", str(custom_root)
    )
    s = FilesystemObjectStore()
    assert s.root == custom_root.resolve()
