"""Authenticated document synchronization endpoints.

The sync protocol is intentionally small and explicit:

* clients submit bounded batches of UUID-keyed operations with the last
  document version they observed;
* accepted operations advance the document's integer ``sync_version`` and get
  a database-assigned server cursor;
* retries of the same ``client_id`` + ``client_operation_id`` are idempotent;
* stale writes are retained as owner-scoped conflicts instead of being
  silently discarded; and
* pulls are ordered by the server cursor, not by wall-clock timestamps.

This is an operation-log foundation for a future local-first iPad client. It
does not attempt to merge arbitrary note content on the server. A client must
inspect a conflict and retry a reconciled operation with a new idempotency key.
"""

from __future__ import annotations

import uuid
import json
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field, field_validator

from app.data.base import utcnow
from app.data.models import DocumentConflict, DocumentOperation, User
from app.data.repositories import RepositoryFactory
from app.services.auth_api import get_current_user, get_repositories

router = APIRouter(prefix="/sync", tags=["sync"])

MAX_SYNC_BATCH = 50
MAX_SYNC_PAYLOAD_BYTES = 64 * 1024


def _trimmed_text(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("value must not be blank")
    return value


class SyncOperationRequest(BaseModel):
    """One client mutation submitted to ``POST /sync/push``."""

    client_id: str = Field(min_length=1, max_length=128)
    client_operation_id: uuid.UUID
    document_id: uuid.UUID
    base_sync_version: int = Field(ge=0)
    entity_type: str = Field(min_length=1, max_length=64)
    entity_id: str | None = Field(default=None, max_length=255)
    operation_type: str = Field(min_length=1, max_length=64)
    payload: dict[str, Any] = Field(default_factory=dict)

    _trim_client_id = field_validator("client_id")(_trimmed_text)
    _trim_entity_type = field_validator("entity_type")(_trimmed_text)
    _trim_operation_type = field_validator("operation_type")(_trimmed_text)

    @field_validator("payload")
    @classmethod
    def validate_payload_size(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(json.dumps(value, separators=(",", ":"))) > MAX_SYNC_PAYLOAD_BYTES:
            raise ValueError(
                f"payload must be at most {MAX_SYNC_PAYLOAD_BYTES} bytes"
            )
        return value


class SyncPushRequest(BaseModel):
    """Bounded batch accepted by the push endpoint."""

    operations: list[SyncOperationRequest] = Field(
        min_length=1, max_length=MAX_SYNC_BATCH
    )


class SyncAcceptedOperation(BaseModel):
    client_id: str
    client_operation_id: uuid.UUID
    operation_id: uuid.UUID
    server_sequence: int
    document_id: uuid.UUID
    sync_version: int
    idempotent: bool


class SyncConflictResponse(BaseModel):
    conflict_id: uuid.UUID
    document_id: uuid.UUID
    client_id: str
    client_operation_id: uuid.UUID
    base_sync_version: int
    current_sync_version: int
    entity_type: str
    entity_id: str | None
    operation_type: str
    payload: dict[str, Any]
    status: str
    resolution: dict[str, Any] | None = None
    created_at: datetime
    resolved_at: datetime | None = None


class SyncPushResponse(BaseModel):
    accepted: list[SyncAcceptedOperation]
    conflicts: list[SyncConflictResponse]
    current_versions: dict[str, int]


class SyncChangeResponse(BaseModel):
    server_sequence: int
    operation_id: uuid.UUID
    document_id: uuid.UUID
    client_id: str
    client_operation_id: uuid.UUID
    base_sync_version: int
    sync_version: int
    entity_type: str
    entity_id: str | None
    operation_type: str
    payload: dict[str, Any]
    created_at: datetime
    is_tombstone: bool = False
    contract_version: str = "v1"


class SyncPullResponse(BaseModel):
    items: list[SyncChangeResponse]
    next_cursor: int
    has_more: bool


class ResolveConflictRequest(BaseModel):
    """Record a client decision without silently applying note content."""

    decision: Literal["keep_server", "retry_as_new"]
    details: dict[str, Any] = Field(default_factory=dict)


class SyncClientRegisterRequest(BaseModel):
    client_id: str = Field(min_length=1, max_length=128)
    platform: str | None = Field(default=None, max_length=32)
    device_name: str | None = Field(default=None, max_length=128)

    _trim_client_id = field_validator("client_id")(_trimmed_text)

    @field_validator("platform", "device_name")
    @classmethod
    def trim_optional(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class SyncClientAckRequest(BaseModel):
    cursor: int = Field(ge=0)


class SyncClientResponse(BaseModel):
    client_id: str
    platform: str | None = None
    device_name: str | None = None
    cursor: int
    last_seen_at: datetime
    revoked_at: datetime | None = None
    created_at: datetime


class SyncCompactRequest(BaseModel):
    through_cursor: int = Field(ge=0)


class SyncCompactResponse(BaseModel):
    compacted_count: int
    snapshot_count: int
    cursor_floor: int


class SyncSnapshotResponse(BaseModel):
    snapshot_id: uuid.UUID
    document_id: uuid.UUID
    server_sequence: int
    document_sync_version: int
    state: dict[str, Any]
    created_at: datetime


def _accepted(
    operation: DocumentOperation,
    *,
    idempotent: bool,
) -> SyncAcceptedOperation:
    return SyncAcceptedOperation(
        client_id=operation.client_id,
        client_operation_id=operation.client_operation_id,
        operation_id=operation.operation_id,
        server_sequence=operation.server_sequence,
        document_id=operation.document_id,
        sync_version=operation.document_sync_version,
        idempotent=idempotent,
    )


def _conflict(conflict: DocumentConflict) -> SyncConflictResponse:
    return SyncConflictResponse(
        conflict_id=conflict.conflict_id,
        document_id=conflict.document_id,
        client_id=conflict.client_id,
        client_operation_id=conflict.client_operation_id,
        base_sync_version=conflict.base_sync_version,
        current_sync_version=conflict.current_sync_version,
        entity_type=conflict.entity_type,
        entity_id=conflict.entity_id,
        operation_type=conflict.operation_type,
        payload=conflict.payload,
        status=conflict.status,
        resolution=conflict.resolution,
        created_at=conflict.created_at,
        resolved_at=conflict.resolved_at,
    )


def _change(operation: DocumentOperation) -> SyncChangeResponse:
    payload = {
        "server_sequence": operation.server_sequence,
        "operation_id": operation.operation_id,
        "document_id": operation.document_id,
        "client_id": operation.client_id,
        "client_operation_id": operation.client_operation_id,
        "base_sync_version": operation.base_sync_version,
        "sync_version": operation.document_sync_version,
        "entity_type": operation.entity_type,
        "entity_id": operation.entity_id,
        "operation_type": operation.operation_type,
        "payload": operation.payload,
        "created_at": operation.created_at,
    }
    if hasattr(operation, "is_tombstone"):
        payload["is_tombstone"] = bool(operation.is_tombstone)
    if hasattr(operation, "contract_version"):
        payload["contract_version"] = operation.contract_version or "v1"
    return SyncChangeResponse(**payload)


def _client(row) -> SyncClientResponse:
    return SyncClientResponse(
        client_id=row.client_id,
        platform=row.platform,
        device_name=row.device_name,
        cursor=row.cursor,
        last_seen_at=row.last_seen_at,
        revoked_at=row.revoked_at,
        created_at=row.created_at,
    )


def _snapshot(row) -> SyncSnapshotResponse:
    return SyncSnapshotResponse(
        snapshot_id=row.snapshot_id,
        document_id=row.document_id,
        server_sequence=row.server_sequence,
        document_sync_version=row.document_sync_version,
        state=row.state,
        created_at=row.created_at,
    )


def _live_document_version(
    repos: RepositoryFactory,
    user_id: uuid.UUID,
    document_id: uuid.UUID,
    fallback: int,
) -> int:
    """Return the current version for a retry response when still visible."""
    document = repos.documents.get(user_id, document_id)
    return document.sync_version if document is not None else fallback


def _same_operation(
    existing: DocumentOperation | DocumentConflict,
    incoming: SyncOperationRequest,
) -> bool:
    """Check that an idempotency-key retry is byte-equivalent in meaning."""
    return (
        existing.document_id == incoming.document_id
        and existing.base_sync_version == incoming.base_sync_version
        and existing.entity_type == incoming.entity_type
        and existing.entity_id == incoming.entity_id
        and existing.operation_type == incoming.operation_type
        and existing.payload == incoming.payload
    )


def _apply_known_document_operation(
    document,
    incoming: SyncOperationRequest,
) -> None:
    """Project supported library mutations onto canonical document state.

    Stroke/page operations remain append-only until their normalized entities
    are added. They are still durable and ordered in the operation log, while
    the library operations below keep existing document reads consistent.
    """
    if incoming.entity_type != "document":
        return

    payload = incoming.payload
    if incoming.operation_type == "document.rename":
        title = payload.get("title")
        if not isinstance(title, str) or not title.strip() or len(title.strip()) > 255:
            raise HTTPException(
                status_code=422,
                detail="document.rename requires a non-blank title of at most 255 characters",
            )
        document.title = title.strip()
    elif incoming.operation_type == "document.archive":
        archived = payload.get("archived")
        if not isinstance(archived, bool):
            raise HTTPException(
                status_code=422,
                detail="document.archive requires a boolean archived value",
            )
        document.archived_at = utcnow() if archived else None
    elif incoming.operation_type == "document.trash":
        document.trashed_at = utcnow()
    elif incoming.operation_type == "document.restore":
        document.trashed_at = None


@router.post(
    "/push",
    response_model=SyncPushResponse,
    status_code=status.HTTP_200_OK,
    summary="Apply an idempotent batch of document operations",
)
def push_sync_operations(
    body: SyncPushRequest,
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
) -> SyncPushResponse:
    """Accept current-version operations and retain stale ones as conflicts."""
    accepted: list[SyncAcceptedOperation] = []
    conflicts: list[SyncConflictResponse] = []
    current_versions: dict[str, int] = {}

    for incoming in body.operations:
        existing = repos.sync.get_operation(
            user.user_id, incoming.client_id, incoming.client_operation_id
        )
        if existing is not None:
            if not _same_operation(existing, incoming):
                raise HTTPException(
                    status_code=409,
                    detail="Idempotency key was already used for a different operation",
                )
            accepted.append(_accepted(existing, idempotent=True))
            current_versions[str(existing.document_id)] = _live_document_version(
                repos,
                user.user_id,
                existing.document_id,
                existing.document_sync_version,
            )
            continue

        existing_conflict = repos.sync.get_conflict_for_operation(
            user.user_id, incoming.client_id, incoming.client_operation_id
        )
        if existing_conflict is not None:
            if not _same_operation(existing_conflict, incoming):
                raise HTTPException(
                    status_code=409,
                    detail="Idempotency key was already used for a different operation",
                )
            conflicts.append(_conflict(existing_conflict))
            current_versions[str(existing_conflict.document_id)] = _live_document_version(
                repos,
                user.user_id,
                existing_conflict.document_id,
                existing_conflict.current_sync_version,
            )
            continue

        document = repos.documents.get(user.user_id, incoming.document_id)
        if document is None:
            # Do not disclose another user's document, and do not partially
            # commit a batch that contains an inaccessible target.
            raise HTTPException(status_code=404, detail="Document not found")

        current_version = document.sync_version
        if incoming.base_sync_version != current_version:
            conflict = repos.sync.add_conflict(
                DocumentConflict(
                    user_id=user.user_id,
                    document_id=document.document_id,
                    client_id=incoming.client_id,
                    client_operation_id=incoming.client_operation_id,
                    base_sync_version=incoming.base_sync_version,
                    current_sync_version=current_version,
                    entity_type=incoming.entity_type,
                    entity_id=incoming.entity_id,
                    operation_type=incoming.operation_type,
                    payload=incoming.payload,
                )
            )
            conflicts.append(_conflict(conflict))
            current_versions[str(document.document_id)] = current_version
            continue

        _apply_known_document_operation(document, incoming)
        document.sync_version += 1
        document.updated_at = utcnow()
        operation = repos.sync.add_operation(
            DocumentOperation(
                user_id=user.user_id,
                document_id=document.document_id,
                client_id=incoming.client_id,
                client_operation_id=incoming.client_operation_id,
                base_sync_version=incoming.base_sync_version,
                document_sync_version=document.sync_version,
                entity_type=incoming.entity_type,
                entity_id=incoming.entity_id,
                operation_type=incoming.operation_type,
                payload=incoming.payload,
            )
        )
        accepted.append(_accepted(operation, idempotent=False))
        current_versions[str(document.document_id)] = document.sync_version

    repos.session.commit()
    return SyncPushResponse(
        accepted=accepted,
        conflicts=conflicts,
        current_versions=current_versions,
    )


@router.get(
    "/pull",
    response_model=SyncPullResponse,
    summary="Pull ordered document operations after a server cursor",
)
def pull_sync_operations(
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
) -> SyncPullResponse:
    """Return owner-scoped changes and a resumable global cursor."""
    cursor_floor = repos.sync.cursor_floor(user.user_id)
    if cursor < cursor_floor:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "sync_cursor_stale",
                "message": "Cursor is older than the compacted floor; resync from snapshots",
                "cursor_floor": cursor_floor,
                "suggest": "snapshots",
            },
        )

    rows = list(
        repos.sync.list_operations(
            user.user_id,
            cursor=cursor,
            # Fetch one extra row so ``has_more`` is exact, including when a
            # full page happens to be the final page.
            limit=limit + 1,
        )
    )
    has_more = len(rows) > limit
    visible = rows[:limit]
    next_cursor = visible[-1].server_sequence if visible else cursor
    return SyncPullResponse(
        items=[_change(row) for row in visible],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@router.get(
    "/conflicts",
    response_model=list[SyncConflictResponse],
    summary="List document sync conflicts for this account",
)
def list_sync_conflicts(
    include_resolved: bool = Query(default=False),
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
) -> list[SyncConflictResponse]:
    return [_conflict(row) for row in repos.sync.list_conflicts(
        user.user_id, include_resolved=include_resolved
    )]


@router.post(
    "/conflicts/{conflict_id}/resolve",
    response_model=SyncConflictResponse,
    summary="Record a client-side sync conflict decision",
)
def resolve_sync_conflict(
    conflict_id: uuid.UUID,
    body: ResolveConflictRequest,
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
) -> SyncConflictResponse:
    """Resolve bookkeeping while requiring a new operation for new content."""
    conflict = repos.sync.resolve_conflict(
        user.user_id,
        conflict_id,
        resolution={"decision": body.decision, "details": body.details},
    )
    if conflict is None:
        raise HTTPException(status_code=404, detail="Sync conflict not found")
    repos.session.commit()
    return _conflict(conflict)


@router.post(
    "/clients",
    response_model=SyncClientResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register or refresh a sync client acknowledgement cursor",
)
def register_sync_client(
    body: SyncClientRegisterRequest,
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
) -> SyncClientResponse:
    client = repos.sync_clients.register(
        user.user_id,
        body.client_id,
        platform=body.platform,
        device_name=body.device_name,
    )
    repos.session.commit()
    return _client(client)


@router.post(
    "/clients/{client_id}/ack",
    response_model=SyncClientResponse,
    summary="Acknowledge that a client has durably applied through a cursor",
)
def acknowledge_sync_client(
    client_id: str,
    body: SyncClientAckRequest,
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
) -> SyncClientResponse:
    try:
        client = repos.sync_clients.acknowledge(
            user.user_id, client_id, body.cursor
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if client is None:
        raise HTTPException(status_code=404, detail="Sync client not found")
    repos.session.commit()
    return _client(client)


@router.get(
    "/clients",
    response_model=list[SyncClientResponse],
    summary="List sync clients for this account",
)
def list_sync_clients(
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
) -> list[SyncClientResponse]:
    return [_client(row) for row in repos.sync_clients.list_for_user(user.user_id)]


@router.delete(
    "/clients/{client_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Revoke a sync client so it no longer gates compaction",
)
def revoke_sync_client(
    client_id: str,
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
) -> Response:
    if not repos.sync_clients.revoke(user.user_id, client_id):
        raise HTTPException(status_code=404, detail="Sync client not found")
    repos.session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/compact",
    response_model=SyncCompactResponse,
    summary="Compact acknowledged sync operations into snapshots",
)
def compact_sync_operations(
    body: SyncCompactRequest,
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
) -> SyncCompactResponse:
    try:
        compacted_count, snapshot_count, cursor_floor = repos.sync.compact_operations(
            user.user_id, through_cursor=body.through_cursor
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    repos.session.commit()
    return SyncCompactResponse(
        compacted_count=compacted_count,
        snapshot_count=snapshot_count,
        cursor_floor=cursor_floor,
    )


@router.get(
    "/snapshots",
    response_model=list[SyncSnapshotResponse],
    summary="List compacted document snapshots for resync",
)
def list_sync_snapshots(
    cursor: int | None = Query(default=None, ge=0),
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
) -> list[SyncSnapshotResponse]:
    rows = repos.sync.list_snapshots(
        user.user_id, at_or_before_cursor=cursor
    )
    return [_snapshot(row) for row in rows]


__all__ = [
    "MAX_SYNC_BATCH",
    "ResolveConflictRequest",
    "SyncAcceptedOperation",
    "SyncChangeResponse",
    "SyncClientAckRequest",
    "SyncClientRegisterRequest",
    "SyncClientResponse",
    "SyncCompactRequest",
    "SyncCompactResponse",
    "SyncConflictResponse",
    "SyncOperationRequest",
    "SyncPullResponse",
    "SyncPushRequest",
    "SyncPushResponse",
    "SyncSnapshotResponse",
    "router",
]
