import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from app.data.models import Document
from app.data.repositories import RepositoryFactory
from app.services.auth_api import get_current_user, User, get_repositories

router = APIRouter(prefix="/documents", tags=["documents"])


class PageSummary(BaseModel):
    page_id: uuid.UUID
    page_index: int
    image_id: uuid.UUID | None
    width: int
    height: int
    dpi: int
    coordinate_space: str


class DocumentResponse(BaseModel):
    document_id: uuid.UUID
    title: str
    created_at: datetime
    image_ids: list[uuid.UUID]
    source_kind: str = "images"
    pages: list[PageSummary] = Field(default_factory=list)
    active_revision_id: uuid.UUID | None = None
    updated_at: datetime
    archived_at: datetime | None = None
    trashed_at: datetime | None = None
    sync_version: int = 0
    default_language: str = "en"
    course_context: str | None = None
    source_metadata: dict | None = None


class RevisionSummary(BaseModel):
    revision_id: uuid.UUID
    revision_number: int
    created_at: datetime


class CreateDocumentRequest(BaseModel):
    title: str = Field(default="Untitled Note", min_length=1, max_length=255)
    default_language: str = Field(default="en", min_length=2, max_length=32)
    course_context: str | None = Field(default=None, max_length=1_000)
    source_metadata: dict | None = None


class UpdateDocumentRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    archived: bool | None = None
    default_language: str | None = Field(default=None, min_length=2, max_length=32)
    course_context: str | None = Field(default=None, max_length=1_000)
    expected_sync_version: int | None = Field(default=None, ge=0)


def _document_response(document: Document, repos: RepositoryFactory | None = None) -> DocumentResponse:
    """Map an ORM document without exposing its ownership fields."""
    pages: list[PageSummary] = []
    if repos is not None:
        pages = [
            PageSummary(
                page_id=page.page_id,
                page_index=page.page_index,
                image_id=page.image_id,
                width=page.width,
                height=page.height,
                dpi=page.dpi,
                coordinate_space=page.coordinate_space,
            )
            for page in repos.document_pages.list_for_document(
                document.user_id, document.document_id
            )
        ]
    return DocumentResponse(
        document_id=document.document_id,
        title=document.title,
        created_at=document.created_at,
        image_ids=[image.image_id for image in document.images],
        source_kind=document.source_kind,
        pages=pages,
        active_revision_id=document.active_revision_id,
        updated_at=document.updated_at,
        archived_at=document.archived_at,
        trashed_at=document.trashed_at,
        sync_version=document.sync_version,
        default_language=document.default_language,
        course_context=document.course_context,
        source_metadata=document.source_metadata,
    )


@router.post("", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
def create_document(
    req: CreateDocumentRequest,
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
) -> DocumentResponse:
    title = req.title.strip()
    if not title:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Title is required",
        )

    default_language = req.default_language.strip()
    if not default_language:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Default language is required",
        )

    doc = repos.documents.create(
        user_id=user.user_id,
        title=title,
        default_language=default_language,
        course_context=req.course_context.strip() if req.course_context else None,
        source_metadata=req.source_metadata,
    )
    repos.documents.session.commit()
    return _document_response(doc, repos)


@router.get("", response_model=list[DocumentResponse])
def list_documents(
    response: Response,
    query: str | None = Query(default=None, max_length=120),
    include_archived: bool = Query(default=False),
    include_trashed: bool = Query(default=False),
    view: Literal["active", "archived", "trash"] | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
) -> list[DocumentResponse]:
    normalized_query = query.strip() if query and query.strip() else None
    documents = repos.documents.list_for_user(
        user.user_id,
        query=normalized_query,
        include_archived=include_archived,
        include_trashed=include_trashed,
        view=view,
        limit=limit,
        offset=offset,
    )
    total = repos.documents.count_for_user(
        user.user_id,
        query=normalized_query,
        include_archived=include_archived,
        include_trashed=include_trashed,
        view=view,
    )
    response.headers["X-Document-Offset"] = str(offset)
    response.headers["X-Document-Limit"] = str(limit)
    response.headers["X-Document-Total"] = str(total)
    response.headers["X-Document-Has-More"] = str(offset + len(documents) < total).lower()
    return [_document_response(document, repos) for document in documents]


@router.get("/{document_id}", response_model=DocumentResponse)
def get_document(
    document_id: uuid.UUID,
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
) -> DocumentResponse:
    doc = repos.documents.get(user.user_id, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return _document_response(doc, repos)


@router.patch("/{document_id}", response_model=DocumentResponse)
def update_document(
    document_id: uuid.UUID,
    req: UpdateDocumentRequest,
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
) -> DocumentResponse:
    """Update owner-scoped library metadata with an optimistic sync version."""
    updates = req.model_dump(exclude_unset=True)
    if not updates or not any(key != "expected_sync_version" for key in updates):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="At least one document field must be provided",
        )
    title = updates.get("title")
    if isinstance(title, str):
        title = title.strip()
        if not title:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Title is required",
            )
    language = updates.get("default_language")
    if isinstance(language, str):
        language = language.strip()
        if not language:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Default language is required",
            )
    context = updates.get("course_context")
    if isinstance(context, str):
        context = context.strip() or None
    try:
        document = repos.documents.update_metadata(
            user.user_id,
            document_id,
            title=title,
            archived=updates.get("archived"),
            default_language=language,
            course_context=context,
            set_course_context="course_context" in updates,
            expected_sync_version=updates.get("expected_sync_version"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    repos.documents.session.commit()
    return _document_response(document, repos)


@router.post("/{document_id}/restore", response_model=DocumentResponse)
def restore_document(
    document_id: uuid.UUID,
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
) -> DocumentResponse:
    """Restore a document from the reversible trash state."""
    document = repos.documents.restore(user.user_id, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    repos.documents.session.commit()
    return _document_response(document, repos)


@router.delete("/{document_id}", response_model=DocumentResponse)
def trash_document(
    document_id: uuid.UUID,
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
) -> DocumentResponse:
    """Move a document to trash; permanent deletion remains a separate workflow."""
    document = repos.documents.trash(user.user_id, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    repos.documents.session.commit()
    return _document_response(document, repos)


@router.get("/{document_id}/revisions", response_model=list[RevisionSummary])
def list_document_revisions(
    document_id: uuid.UUID,
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
) -> list[RevisionSummary]:
    """List immutable review snapshots available for an owner-scoped export."""
    document = repos.documents.get(user.user_id, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return [
        RevisionSummary(
            revision_id=revision.revision_id,
            revision_number=revision.revision_number,
            created_at=revision.created_at,
        )
        for revision in repos.document_revisions.list_for_document(user.user_id, document_id)
    ]
