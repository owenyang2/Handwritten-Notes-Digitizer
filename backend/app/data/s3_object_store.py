"""S3-compatible object store used by the optional production profile.

Local/demo deployments keep :class:`~app.data.object_store.FilesystemObjectStore`.
This backend is constructed only when ``HND_OBJECT_STORE_BACKEND=s3``. boto3 is
a production extra (``backend/requirements-prod.txt``); constructing this class
without boto3 and without an injected client raises a clear error.
"""

from __future__ import annotations

import io
from typing import Any, BinaryIO

from app.data.object_store import (
    InvalidReferenceError,
    ObjectNotFoundError,
    ObjectStore,
    ObjectStoreError,
    validate_reference,
)


def _import_boto3():
    """Import boto3 lazily so the lean CI image does not need it."""
    import boto3

    return boto3


def _error_code(exc: BaseException) -> str | None:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        error = response.get("Error") or {}
        code = error.get("Code")
        if isinstance(code, str):
            return code
    return None


def _is_missing(exc: BaseException) -> bool:
    code = _error_code(exc)
    if code in {"NoSuchKey", "NotFound", "404", "NoSuchBucket"}:
        return True
    status = getattr(exc, "response", None)
    if isinstance(status, dict):
        http = status.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if http == 404:
            return True
    return False


class S3CompatibleObjectStore(ObjectStore):
    """Object store backed by an S3-compatible API (AWS, MinIO, etc.).

    ``client`` may be injected for tests. When omitted, a boto3 client is built
    from the constructor arguments.
    """

    def __init__(
        self,
        bucket: str | None,
        *,
        endpoint_url: str | None = None,
        region: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        prefix: str | None = None,
        client: Any | None = None,
    ) -> None:
        if not bucket:
            raise ObjectStoreError(
                "s3_bucket is required when object_store_backend=s3."
            )
        self._bucket = bucket
        self._prefix = (prefix or "").strip("/")
        if client is not None:
            self._client = client
            return
        try:
            boto3 = _import_boto3()
        except ImportError as exc:
            raise ObjectStoreError(
                "S3CompatibleObjectStore requires boto3. Install production "
                "extras with: pip install -r backend/requirements-prod.txt"
            ) from exc
        kwargs: dict[str, Any] = {
            "service_name": "s3",
            "region_name": region or "us-east-1",
        }
        if endpoint_url:
            kwargs["endpoint_url"] = endpoint_url
        if access_key:
            kwargs["aws_access_key_id"] = access_key
        if secret_key:
            kwargs["aws_secret_access_key"] = secret_key
        try:
            from botocore.config import Config

            kwargs["config"] = Config(s3={"addressing_style": "path"})
        except ImportError:
            pass
        self._client = boto3.client(**kwargs)

    def _key(self, reference: str) -> str:
        reference = validate_reference(reference)
        if self._prefix:
            return f"{self._prefix}/{reference}"
        return reference

    def put(self, reference: str, data: bytes) -> str:
        key = self._key(reference)
        self._client.put_object(Bucket=self._bucket, Key=key, Body=data)
        return reference

    def get(self, reference: str) -> bytes:
        key = self._key(reference)
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
        except Exception as exc:
            if isinstance(exc, InvalidReferenceError):
                raise
            if _is_missing(exc):
                raise ObjectNotFoundError(
                    f"No object stored at reference {reference!r}."
                ) from exc
            raise
        body = response.get("Body")
        if body is None:
            raise ObjectNotFoundError(
                f"No object stored at reference {reference!r}."
            )
        if hasattr(body, "read"):
            return body.read()
        if isinstance(body, bytes):
            return body
        raise ObjectStoreError(f"Unexpected S3 body type for {reference!r}.")

    def open(self, reference: str) -> BinaryIO:
        return io.BytesIO(self.get(reference))

    def exists(self, reference: str) -> bool:
        try:
            key = self._key(reference)
        except InvalidReferenceError:
            return False
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
        except Exception as exc:
            if _is_missing(exc):
                return False
            raise
        return True

    def delete(self, reference: str) -> None:
        key = self._key(reference)
        try:
            self._client.delete_object(Bucket=self._bucket, Key=key)
        except Exception as exc:
            if _is_missing(exc):
                return
            raise

    def delete_owner(self, user_id: str) -> None:
        """Delete every key under this user's kind prefixes."""
        from app.data.object_store import ObjectKind, _validate_segment

        owner = _validate_segment(user_id, field="user_id")
        for kind in ObjectKind:
            prefix = self._key(f"{kind.value}/{owner}").rstrip("/") + "/"
            token: str | None = None
            while True:
                kwargs: dict[str, Any] = {"Bucket": self._bucket, "Prefix": prefix}
                if token:
                    kwargs["ContinuationToken"] = token
                page = self._client.list_objects_v2(**kwargs)
                keys = [
                    item["Key"]
                    for item in page.get("Contents") or []
                    if item.get("Key")
                ]
                if keys:
                    result = self._client.delete_objects(
                        Bucket=self._bucket,
                        Delete={"Objects": [{"Key": key} for key in keys], "Quiet": True},
                    )
                    if result.get("Errors"):
                        raise ObjectStoreError("S3 could not delete every account object.")
                if not page.get("IsTruncated"):
                    break
                token = page.get("NextContinuationToken")
                if not token:
                    raise ObjectStoreError("S3 returned a truncated listing without a cursor.")


# Alias used by other production-profile call sites.
S3ObjectStore = S3CompatibleObjectStore

__all__ = ["S3CompatibleObjectStore", "S3ObjectStore"]
