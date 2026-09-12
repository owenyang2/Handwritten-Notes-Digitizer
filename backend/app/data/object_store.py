"""Object/image store abstraction.

The system keeps structured records in a relational database and the large
binary artifacts (image bytes, region crops, and adapter weight files) in a
separate object store. Relational records reference these artifacts *by path*
(see the data model in design.md: ``Image.storage_path``,
``TrainingPair.region_crop_path``, ``Adapter.weights_path``).

This module defines:

* :class:`ObjectKind` - the three artifact categories the store manages.
* :class:`ObjectStore` - an abstract interface for storing and retrieving
  artifacts. Callers store bytes and receive back a *path reference* (a stable
  string) which they persist on the relational record; later they retrieve the
  bytes using that same reference.
* :class:`FilesystemObjectStore` - a concrete filesystem-backed implementation
  rooted at ``Settings.object_store_path``.

Design notes / requirements traceability:
* Accepted images are stored and associated with the owning user; the returned
  reference is what the ``Image`` record persists (Req 1.6).
* Confirmed corrections store a handwriting region crop whose reference the
  ``TrainingPair`` record persists (Req 4.3).
* References are *relative* and opaque to callers: the store owns the layout so
  the physical root can change (e.g. local disk vs. mounted volume) without
  rewriting stored references.
"""

from __future__ import annotations

import os
import re
import shutil
import uuid
from abc import ABC, abstractmethod
from enum import Enum
from pathlib import Path
from typing import BinaryIO

from app.config import get_settings


class ObjectKind(str, Enum):
    """The categories of binary artifact the object store manages.

    The string values double as the top-level folder/prefix for each kind so
    references are human-readable and self-describing.
    """

    IMAGE = "images"
    CROP = "crops"
    ADAPTER = "adapters"
    DOCUMENT = "documents"
    PAGE = "pages"
    UPLOAD = "uploads"


# A reference is a POSIX-style relative path of the form
# ``<kind>/<user_id>/<name>``. We constrain the user/name segments to a safe
# character set and forbid traversal so a reference can never escape the root.
_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")


class ObjectStoreError(Exception):
    """Base error for object store operations."""


class ObjectNotFoundError(ObjectStoreError):
    """Raised when a reference does not resolve to a stored object."""


class InvalidReferenceError(ObjectStoreError):
    """Raised when a reference is malformed or attempts path traversal."""


def _validate_segment(segment: str, *, field: str) -> str:
    """Validate a single path segment (user id or object name)."""
    if not segment or not _SAFE_SEGMENT.match(segment):
        raise InvalidReferenceError(
            f"Invalid {field!r} segment: {segment!r}. Allowed characters are "
            "letters, digits, '.', '_' and '-' (no path separators)."
        )
    if segment in (".", ".."):
        raise InvalidReferenceError(f"Invalid {field!r} segment: {segment!r}.")
    return segment


class ObjectStore(ABC):
    """Abstract interface for storing and retrieving binary artifacts.

    Implementations map an opaque *reference* string to stored bytes. Callers
    use the high-level ``put_*`` helpers to store artifacts of a given kind and
    receive a reference to persist on a relational record; they retrieve bytes
    later via :meth:`get` (or stream via :meth:`open`).
    """

    # --- Reference construction (shared by all implementations) -------------

    @staticmethod
    def build_reference(
        kind: ObjectKind, user_id: str, name: str | None = None, *, suffix: str = ""
    ) -> str:
        """Build a validated, relative reference of the form ``kind/user/name``.

        When ``name`` is omitted a random unique name is generated. ``suffix``
        (e.g. ``".png"``) is appended to the generated/provided name.
        """
        _validate_segment(str(user_id), field="user_id")
        if name is None:
            name = uuid.uuid4().hex
        if suffix:
            if not suffix.startswith("."):
                suffix = "." + suffix
            name = f"{name}{suffix}"
        _validate_segment(name, field="name")
        return f"{kind.value}/{user_id}/{name}"

    # --- High-level, kind-specific helpers ----------------------------------

    def put_image_bytes(
        self, user_id: str, data: bytes, *, name: str | None = None, suffix: str = ""
    ) -> str:
        """Store full image bytes for a user; return the reference (Req 1.6)."""
        reference = self.build_reference(
            ObjectKind.IMAGE, user_id, name, suffix=suffix
        )
        return self.put(reference, data)

    def put_region_crop(
        self, user_id: str, data: bytes, *, name: str | None = None, suffix: str = ""
    ) -> str:
        """Store a handwriting region crop for a user; return the reference (Req 4.3)."""
        reference = self.build_reference(
            ObjectKind.CROP, user_id, name, suffix=suffix
        )
        return self.put(reference, data)

    def put_adapter_weights(
        self, user_id: str, data: bytes, *, name: str | None = None, suffix: str = ""
    ) -> str:
        """Store adapter weight bytes for a user; return the reference."""
        reference = self.build_reference(
            ObjectKind.ADAPTER, user_id, name, suffix=suffix
        )
        return self.put(reference, data)

    def put_document_bytes(
        self, user_id: str, data: bytes, *, name: str | None = None, suffix: str = ".pdf"
    ) -> str:
        """Store an original imported document (typically a PDF)."""
        reference = self.build_reference(
            ObjectKind.DOCUMENT, user_id, name, suffix=suffix
        )
        return self.put(reference, data)

    def put_page_raster(
        self, user_id: str, data: bytes, *, name: str | None = None, suffix: str = ".png"
    ) -> str:
        """Store a rasterized document page."""
        reference = self.build_reference(
            ObjectKind.PAGE, user_id, name, suffix=suffix
        )
        return self.put(reference, data)

    def put_upload_part(
        self, user_id: str, data: bytes, *, name: str | None = None, suffix: str = ".part"
    ) -> str:
        """Store one resumable-upload part for a user; return the reference."""
        reference = self.build_reference(
            ObjectKind.UPLOAD, user_id, name, suffix=suffix
        )
        return self.put(reference, data)

    # --- Core storage operations (implemented by subclasses) ----------------

    @abstractmethod
    def put(self, reference: str, data: bytes) -> str:
        """Store ``data`` at ``reference``, overwriting any existing object.

        Returns the (validated) reference, which callers persist on the
        relational record.
        """

    @abstractmethod
    def get(self, reference: str) -> bytes:
        """Return the bytes stored at ``reference``.

        Raises :class:`ObjectNotFoundError` if nothing is stored there.
        """

    @abstractmethod
    def open(self, reference: str) -> BinaryIO:
        """Open ``reference`` for binary reading (caller closes the stream).

        Raises :class:`ObjectNotFoundError` if nothing is stored there.
        """

    @abstractmethod
    def exists(self, reference: str) -> bool:
        """Return whether an object is stored at ``reference``."""

    @abstractmethod
    def delete(self, reference: str) -> None:
        """Delete the object at ``reference`` if present (idempotent)."""


class FilesystemObjectStore(ObjectStore):
    """Filesystem-backed object store rooted at a configurable directory.

    The physical layout mirrors the reference: ``<root>/<kind>/<user>/<name>``.
    References are always relative to ``root`` so records remain valid if the
    root moves.
    """

    def __init__(self, root: str | os.PathLike[str] | None = None) -> None:
        if root is None:
            root = get_settings().object_store_path
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        """The resolved filesystem root for this store."""
        return self._root

    def _resolve(self, reference: str) -> Path:
        """Validate ``reference`` and resolve it to an absolute path under root.

        Guards against absolute paths and ``..`` traversal so a malformed or
        hostile reference can never escape the store root.
        """
        if not reference or reference.startswith("/") or "\\" in reference:
            raise InvalidReferenceError(f"Invalid reference: {reference!r}.")

        parts = reference.split("/")
        for part in parts:
            if part in ("", ".", ".."):
                raise InvalidReferenceError(f"Invalid reference: {reference!r}.")

        candidate = (self._root / Path(*parts)).resolve()
        # Ensure the resolved path stays within the root.
        if candidate != self._root and self._root not in candidate.parents:
            raise InvalidReferenceError(
                f"Reference {reference!r} escapes the store root."
            )
        return candidate

    def put(self, reference: str, data: bytes) -> str:
        path = self._resolve(reference)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic-ish write: write to a temp file in the same dir then replace.
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with open(tmp, "wb") as fh:
                fh.write(data)
            os.replace(tmp, path)
        finally:
            if tmp.exists():
                tmp.unlink()
        return reference

    def get(self, reference: str) -> bytes:
        path = self._resolve(reference)
        try:
            return path.read_bytes()
        except FileNotFoundError as exc:
            raise ObjectNotFoundError(
                f"No object stored at reference {reference!r}."
            ) from exc

    def open(self, reference: str) -> BinaryIO:
        path = self._resolve(reference)
        try:
            return open(path, "rb")
        except FileNotFoundError as exc:
            raise ObjectNotFoundError(
                f"No object stored at reference {reference!r}."
            ) from exc

    def exists(self, reference: str) -> bool:
        try:
            path = self._resolve(reference)
        except InvalidReferenceError:
            return False
        return path.is_file()

    def delete(self, reference: str) -> None:
        path = self._resolve(reference)
        try:
            path.unlink()
        except FileNotFoundError:
            # Deletion is idempotent.
            pass

    def clear(self) -> None:
        """Remove all stored objects (primarily for tests/teardown)."""
        if self._root.exists():
            shutil.rmtree(self._root)
        self._root.mkdir(parents=True, exist_ok=True)


__all__ = [
    "ObjectKind",
    "ObjectStore",
    "FilesystemObjectStore",
    "ObjectStoreError",
    "ObjectNotFoundError",
    "InvalidReferenceError",
]
