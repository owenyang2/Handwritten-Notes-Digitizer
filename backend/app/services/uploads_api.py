"""Authenticated resumable upload sessions.

Large notebook exports should not force the API to buffer an entire request
before validating size or digest. Clients create an owner-scoped session, put
idempotent parts, then ask the server to reassemble and verify the final
SHA-256 before the object is accepted for later ingestion.
"""

from __future__ import annotations

import hashlib
import re
import uuid
import datetime as _dt
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator

from app.config.settings import get_settings
from app.data.base import utcnow
from app.data.models import UploadPart, UploadSession, User
from app.data.object_store import ObjectStore
from app.data.repositories import RepositoryFactory
from app.services.auth_api import get_current_user, get_repositories
from app.services.images_api import get_object_store

router = APIRouter(prefix="/uploads", tags=["uploads"])

DEFAULT_PART_SIZE = 5 * 1024 * 1024
MIN_PART_SIZE = 256 * 1024
MAX_PART_SIZE = 8 * 1024 * 1024
SESSION_TTL = timedelta(hours=24)
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
    )


class CreateUploadRequest(BaseModel):
    filename: str = Field(default="upload", max_length=255)
    content_type: str | None = Field(default=None, max_length=128)
    expected_size: int = Field(gt=0)
    expected_sha256: str = Field(min_length=64, max_length=64)
    part_size: int | None = Field(default=None, gt=0)
    idempotency_key: uuid.UUID | None = None
    document_id: uuid.UUID | None = None

    @field_validator("expected_sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        if not _SHA256_RE.match(value):
            raise ValueError("expected_sha256 must be a 64-character hex digest")
        return value.lower()

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value: str) -> str:
        cleaned = value.strip() or "upload"
        return cleaned[:255]


class UploadPartSummary(BaseModel):
    part_number: int
    size_bytes: int
    sha256: str


class UploadSessionResponse(BaseModel):
    upload_id: uuid.UUID
    filename: str
    content_type: str | None
    expected_size: int
    expected_sha256: str
    received_size: int
    part_size: int
    status: str
    document_id: uuid.UUID | None
    assembled_ref: str | None
    parts: list[UploadPartSummary]
    expires_at: datetime
    error_code: str | None = None
    error_message: str | None = None
    completed_at: datetime | None = None
    idempotent: bool = False


def _session_response(
    session: UploadSession,
    parts: list[UploadPart] | None = None,
    *,
    idempotent: bool = False,
) -> UploadSessionResponse:
    ordered = parts if parts is not None else list(session.parts or [])
    ordered = sorted(ordered, key=lambda part: part.part_number)
    return UploadSessionResponse(
        upload_id=session.upload_id,
        filename=session.filename,
        content_type=session.content_type,
        expected_size=session.expected_size,
        expected_sha256=session.expected_sha256,
        received_size=session.received_size,
        part_size=session.part_size,
        status=session.status,
        document_id=session.document_id,
        assembled_ref=session.assembled_ref,
        parts=[
            UploadPartSummary(
                part_number=part.part_number,
                size_bytes=part.size_bytes,
                sha256=part.sha256,
            )
            for part in ordered
        ],
        expires_at=session.expires_at,
        error_code=session.error_code,
        error_message=session.error_message,
        completed_at=session.completed_at,
        idempotent=idempotent,
    )


def _clamp_part_size(value: int | None) -> int:
    size = DEFAULT_PART_SIZE if value is None else int(value)
    return max(MIN_PART_SIZE, min(MAX_PART_SIZE, size))


def _as_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=_dt.timezone.utc)
    return value


def _require_open_session(
    repos: RepositoryFactory, user: User, upload_id: uuid.UUID
) -> UploadSession:
    session = repos.uploads.get(user.user_id, upload_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if _as_aware(session.expires_at) <= utcnow() and session.status == "open":
        session.status = "expired"
        session.error_code = "upload_expired"
        session.error_message = "Upload session expired before completion"
        repos.session.commit()
    if session.status == "expired":
        raise _error(
            status.HTTP_410_GONE,
            "upload_expired",
            "Upload session expired before completion",
        )
    return session


@router.post("", response_model=UploadSessionResponse, status_code=status.HTTP_201_CREATED)
def create_upload(
    body: CreateUploadRequest,
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
) -> UploadSessionResponse:
    settings = get_settings()
    if body.expected_size > settings.max_upload_bytes:
        raise _error(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            "upload_too_large",
            f"Upload exceeds the {settings.max_upload_bytes} byte limit",
        )
    if body.idempotency_key is not None:
        existing = repos.uploads.get_by_idempotency(user.user_id, body.idempotency_key)
        if existing is not None:
            return _session_response(
                existing,
                list(repos.uploads.list_parts(existing.upload_id)),
                idempotent=True,
            )

    if body.document_id is not None:
        document = repos.documents.get(user.user_id, body.document_id)
        if document is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    session = UploadSession(
        user_id=user.user_id,
        idempotency_key=body.idempotency_key,
        filename=body.filename,
        content_type=body.content_type,
        expected_size=body.expected_size,
        expected_sha256=body.expected_sha256,
        part_size=_clamp_part_size(body.part_size),
        document_id=body.document_id,
        expires_at=utcnow() + SESSION_TTL,
    )
    repos.uploads.add(session)
    repos.session.commit()
    return _session_response(session, [])


@router.get("/{upload_id}", response_model=UploadSessionResponse)
def get_upload(
    upload_id: uuid.UUID,
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
) -> UploadSessionResponse:
    session = repos.uploads.get(user.user_id, upload_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return _session_response(session, list(repos.uploads.list_parts(upload_id)))


@router.put("/{upload_id}/parts/{part_number}", response_model=UploadSessionResponse)
async def put_upload_part(
    upload_id: uuid.UUID,
    part_number: int,
    request: Request,
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
    store: ObjectStore = Depends(get_object_store),
) -> UploadSessionResponse:
    if part_number < 1:
        raise _error(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "upload_incomplete",
            "part_number must be at least 1",
        )
    session = _require_open_session(repos, user, upload_id)
    if session.status != "open":
        raise _error(
            status.HTTP_409_CONFLICT,
            "upload_conflict",
            f"Upload session is {session.status}",
        )

    declared = request.headers.get("X-Part-SHA256", "").strip().lower()
    if not _SHA256_RE.match(declared):
        raise _error(
            status.HTTP_400_BAD_REQUEST,
            "upload_hash_mismatch",
            "X-Part-SHA256 header must be a 64-character hex digest",
        )

    data = await request.body()
    if not data:
        raise _error(
            status.HTTP_400_BAD_REQUEST,
            "upload_incomplete",
            "Upload part body must not be empty",
        )
    if len(data) > session.part_size:
        raise _error(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            "upload_too_large",
            "Part exceeds the session part_size",
        )
    digest = hashlib.sha256(data).hexdigest()
    if digest != declared:
        raise _error(
            status.HTTP_400_BAD_REQUEST,
            "upload_hash_mismatch",
            "Part body digest does not match X-Part-SHA256",
        )

    existing = repos.uploads.get_part(upload_id, part_number)
    if existing is not None:
        if existing.sha256 != digest or existing.size_bytes != len(data):
            raise _error(
                status.HTTP_409_CONFLICT,
                "upload_conflict",
                "Part was already uploaded with a different digest",
            )
        return _session_response(
            session,
            list(repos.uploads.list_parts(upload_id)),
            idempotent=True,
        )

    if session.received_size + len(data) > session.expected_size:
        raise _error(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            "upload_too_large",
            "Parts exceed the declared expected_size",
        )

    reference = store.put_upload_part(
        str(user.user_id),
        data,
        name=f"{session.upload_id.hex}-{part_number:05d}",
    )
    repos.uploads.add_part(
        UploadPart(
            upload_id=session.upload_id,
            part_number=part_number,
            size_bytes=len(data),
            sha256=digest,
            storage_path=reference,
        )
    )
    session.received_size += len(data)
    session.updated_at = utcnow()
    repos.session.commit()
    return _session_response(session, list(repos.uploads.list_parts(upload_id)))


@router.post("/{upload_id}/complete", response_model=UploadSessionResponse)
def complete_upload(
    upload_id: uuid.UUID,
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
    store: ObjectStore = Depends(get_object_store),
) -> UploadSessionResponse:
    session = _require_open_session(repos, user, upload_id)
    if session.status == "completed":
        return _session_response(session, list(repos.uploads.list_parts(upload_id)))
    if session.status != "open":
        raise _error(
            status.HTTP_409_CONFLICT,
            "upload_conflict",
            f"Upload session is {session.status}",
        )

    parts = list(repos.uploads.list_parts(upload_id))
    if not parts:
        raise _error(
            status.HTTP_400_BAD_REQUEST,
            "upload_incomplete",
            "No upload parts have been received",
        )
    expected_numbers = list(range(1, len(parts) + 1))
    actual_numbers = [part.part_number for part in parts]
    if actual_numbers != expected_numbers:
        raise _error(
            status.HTTP_400_BAD_REQUEST,
            "upload_incomplete",
            "Upload parts must form a contiguous sequence starting at 1",
        )
    if sum(part.size_bytes for part in parts) != session.expected_size:
        raise _error(
            status.HTTP_400_BAD_REQUEST,
            "upload_incomplete",
            "Received part sizes do not match expected_size",
        )

    hasher = hashlib.sha256()
    chunks: list[bytes] = []
    for part in parts:
        chunk = store.get(part.storage_path)
        hasher.update(chunk)
        chunks.append(chunk)
    digest = hasher.hexdigest()
    if digest != session.expected_sha256:
        session.status = "failed"
        session.error_code = "upload_hash_mismatch"
        session.error_message = "Assembled upload digest does not match expected_sha256"
        repos.session.commit()
        raise _error(
            status.HTTP_400_BAD_REQUEST,
            "upload_hash_mismatch",
            session.error_message,
        )

    payload = b"".join(chunks)
    filename = session.filename.lower()
    content_type = (session.content_type or "").lower()
    if content_type.endswith("pdf") or filename.endswith(".pdf"):
        assembled_ref = store.put_document_bytes(
            str(user.user_id), payload, suffix=".pdf"
        )
    elif content_type in {"image/jpeg", "image/jpg"} or filename.endswith((".jpg", ".jpeg")):
        assembled_ref = store.put_image_bytes(
            str(user.user_id), payload, suffix=".jpg"
        )
    else:
        assembled_ref = store.put_image_bytes(
            str(user.user_id), payload, suffix=".png"
        )

    session.assembled_ref = assembled_ref
    session.parts_manifest = [
        {
            "part_number": part.part_number,
            "size_bytes": part.size_bytes,
            "sha256": part.sha256,
            "storage_path": part.storage_path,
        }
        for part in parts
    ]
    session.status = "completed"
    session.completed_at = utcnow()
    session.updated_at = session.completed_at
    session.error_code = None
    session.error_message = None
    repos.session.commit()
    return _session_response(session, parts)
