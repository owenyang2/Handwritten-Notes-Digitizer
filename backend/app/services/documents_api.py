import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
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


class RevisionSummary(BaseModel):
    revision_id: uuid.UUID
    revision_number: int
    created_at: datetime


class CreateDocumentRequest(BaseModel):
    title: str = Field(default="Untitled Note", min_length=1, max_length=255)


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

    doc = repos.documents.create(user_id=user.user_id, title=title)
    repos.documents.session.commit()
    return _document_response(doc, repos)


@router.get("", response_model=list[DocumentResponse])
def list_documents(
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
) -> list[DocumentResponse]:
    documents = repos.documents.list_for_user(user.user_id)
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
