import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.data.models import Document
from app.data.repositories import RepositoryFactory
from app.services.auth_api import get_current_user, User, get_repositories

router = APIRouter(prefix="/documents", tags=["documents"])


class DocumentResponse(BaseModel):
    document_id: uuid.UUID
    title: str
    created_at: datetime
    image_ids: list[uuid.UUID]


class CreateDocumentRequest(BaseModel):
    title: str = Field(default="Untitled Note", min_length=1, max_length=255)


def _document_response(document: Document) -> DocumentResponse:
    """Map an ORM document without exposing its ownership fields."""
    return DocumentResponse(
        document_id=document.document_id,
        title=document.title,
        created_at=document.created_at,
        image_ids=[image.image_id for image in document.images],
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
    return _document_response(doc)


@router.get("", response_model=list[DocumentResponse])
def list_documents(
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
) -> list[DocumentResponse]:
    documents = repos.documents.list_for_user(user.user_id)
    return [_document_response(document) for document in documents]


@router.get("/{document_id}", response_model=DocumentResponse)
def get_document(
    document_id: uuid.UUID,
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
) -> DocumentResponse:
    doc = repos.documents.get(user.user_id, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return _document_response(doc)
