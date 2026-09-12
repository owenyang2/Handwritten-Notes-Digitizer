"""Authenticated account session, export, and deletion endpoints.

These routes keep owner isolation absolute: session listing never returns raw
tokens, revoke/export/delete only observe the caller's own rows, and account
deletion records durable evidence in ``deletion_jobs`` before soft-deleting
library state.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.data.base import utcnow
from app.data.models import DeletionJob, Document, UploadSession, User, UserSession
from app.data.object_store import ObjectStore
from app.data.repositories import RepositoryFactory
from app.services.auth_api import (
    SESSION_COOKIE_NAME,
    get_current_user,
    get_repositories,
)
from app.services.images_api import get_object_store

router = APIRouter(prefix="/account", tags=["account"])


class AccountSessionResponse(BaseModel):
    session_id: uuid.UUID
    created_at: datetime
    expires_at: datetime
    last_seen_at: datetime | None = None
    revoked_at: datetime | None = None
    device_name: str | None = None
    client_id: str | None = None
    user_agent: str | None = None
    is_current: bool


class AccountExportResponse(BaseModel):
    export_kind: str = "account_export_receipt"
    exported_at: datetime
    user: dict[str, Any]
    documents: list[dict[str, Any]]
    image_count: int
    session_count: int
    upload_count: int


class DeleteAccountRequest(BaseModel):
    confirmation: str = Field(min_length=1, max_length=32)


class DeletionJobResponse(BaseModel):
    job_id: uuid.UUID
    status: str
    target_count: int
    deleted_count: int
    target_refs: list[Any]
    error_message: str | None = None
    created_at: datetime
    completed_at: datetime | None = None


def _current_token(
    authorization: Optional[str],
    session_cookie: Optional[str],
) -> Optional[str]:
    if authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() == "bearer" and value.strip():
            return value.strip()
    return session_cookie


def _session_public_id(row: UserSession) -> uuid.UUID:
    return row.session_id


@router.get(
    "/sessions",
    response_model=list[AccountSessionResponse],
    summary="List authentication sessions for the current account",
)
def list_account_sessions(
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
    authorization: Optional[str] = Header(default=None),
    session: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> list[AccountSessionResponse]:
    token = _current_token(authorization, session)
    rows = list(
        repos.session.scalars(
            select(UserSession)
            .where(UserSession.user_id == user.user_id)
            .order_by(UserSession.created_at.desc())
        ).all()
    )
    return [
        AccountSessionResponse(
            session_id=_session_public_id(row),
            created_at=row.created_at,
            expires_at=row.expires_at,
            last_seen_at=row.last_seen_at,
            revoked_at=row.revoked_at,
            device_name=row.device_name,
            client_id=row.client_id,
            user_agent=row.user_agent,
            is_current=bool(token)
            and row.token_hash == hashlib.sha256(token.encode("utf-8")).hexdigest(),
        )
        for row in rows
    ]


@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Revoke one of the current user's sessions",
)
def revoke_account_session(
    session_id: uuid.UUID,
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
) -> Response:
    rows = list(
        repos.session.scalars(
            select(UserSession).where(UserSession.user_id == user.user_id)
        ).all()
    )
    target: UserSession | None = None
    for row in rows:
        if _session_public_id(row) == session_id:
            target = row
            break
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if hasattr(target, "revoked_at"):
        target.revoked_at = target.revoked_at or utcnow()
    else:
        repos.session.delete(target)
    repos.session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/export",
    response_model=AccountExportResponse,
    summary="Export account metadata and library titles",
)
def export_account(
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
) -> AccountExportResponse:
    documents = list(
        repos.session.scalars(
            select(Document)
            .where(Document.user_id == user.user_id)
            .order_by(Document.created_at, Document.document_id)
        ).all()
    )
    images = repos.images.list_for_user(user.user_id)
    sessions = list(
        repos.session.scalars(
            select(UserSession).where(UserSession.user_id == user.user_id)
        ).all()
    )
    uploads = list(
        repos.session.scalars(
            select(UploadSession).where(UploadSession.user_id == user.user_id)
        ).all()
    )
    return AccountExportResponse(
        exported_at=utcnow(),
        user={
            "user_id": str(user.user_id),
            "username": user.username,
            "email": user.email,
            "created_at": user.created_at.isoformat(),
        },
        documents=[
            {
                "document_id": str(document.document_id),
                "title": document.title,
                "source_kind": document.source_kind,
                "created_at": document.created_at.isoformat(),
                "updated_at": document.updated_at.isoformat(),
                "archived_at": (
                    document.archived_at.isoformat() if document.archived_at else None
                ),
                "trashed_at": (
                    document.trashed_at.isoformat() if document.trashed_at else None
                ),
                "deleted_at": (
                    document.deleted_at.isoformat() if document.deleted_at else None
                ),
                "sync_version": document.sync_version,
            }
            for document in documents
        ],
        image_count=len(images),
        session_count=len(sessions),
        upload_count=len(uploads),
    )


@router.delete(
    "",
    response_model=DeletionJobResponse,
    summary="Request account deletion with durable evidence",
)
def delete_account(
    body: DeleteAccountRequest,
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
    store: ObjectStore = Depends(get_object_store),
) -> DeletionJobResponse:
    if body.confirmation != "DELETE":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail='confirmation must be exactly "DELETE"',
        )

    documents = list(
        repos.session.scalars(
            select(Document).where(Document.user_id == user.user_id)
        ).all()
    )
    images = list(repos.images.list_for_user(user.user_id))
    sessions = list(
        repos.session.scalars(
            select(UserSession).where(UserSession.user_id == user.user_id)
        ).all()
    )
    uploads = list(
        repos.session.scalars(
            select(UploadSession).where(UploadSession.user_id == user.user_id)
        ).all()
    )

    target_refs: list[dict[str, Any]] = []
    for document in documents:
        target_refs.append(
            {
                "kind": "document",
                "id": str(document.document_id),
                "title": document.title,
            }
        )
    for image in images:
        target_refs.append(
            {
                "kind": "image",
                "id": str(image.image_id),
                "storage_path": image.storage_path,
            }
        )
    for row in sessions:
        target_refs.append({"kind": "session", "id": str(_session_public_id(row))})
    for upload in uploads:
        target_refs.append(
            {
                "kind": "upload",
                "id": str(upload.upload_id),
                "assembled_ref": upload.assembled_ref,
            }
        )

    job = DeletionJob(
        user_id=user.user_id,
        status="pending",
        target_count=len(target_refs),
        deleted_count=0,
        target_refs=target_refs,
    )
    repos.deletion_jobs.add(job)
    repos.session.flush()

    deleted = 0
    try:
        now = utcnow()
        for document in documents:
            if document.deleted_at is None:
                document.deleted_at = now
            deleted += 1
        for image in images:
            try:
                store.delete(image.storage_path)
            except Exception:
                pass
            repos.session.delete(image)
            deleted += 1
        for upload in uploads:
            if upload.assembled_ref:
                try:
                    store.delete(upload.assembled_ref)
                except Exception:
                    pass
            for part in repos.uploads.list_parts(upload.upload_id):
                try:
                    store.delete(part.storage_path)
                except Exception:
                    pass
            repos.session.delete(upload)
            deleted += 1
        for row in sessions:
            if hasattr(row, "revoked_at"):
                row.revoked_at = row.revoked_at or now
            else:
                repos.session.delete(row)
            deleted += 1
        job.deleted_count = deleted
        job.status = "completed"
        job.completed_at = now
        repos.session.commit()
    except Exception as exc:  # pragma: no cover - defensive path
        job.status = "failed"
        job.error_message = str(exc)
        job.deleted_count = deleted
        job.completed_at = utcnow()
        repos.session.commit()

    return DeletionJobResponse(
        job_id=job.job_id,
        status=job.status,
        target_count=job.target_count,
        deleted_count=job.deleted_count,
        target_refs=list(job.target_refs or []),
        error_message=job.error_message,
        created_at=job.created_at,
        completed_at=job.completed_at,
    )


__all__ = ["router"]
