import uuid
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from datetime import datetime

from app.data.repositories import RepositoryFactory
from app.services.auth_api import get_current_user, User, get_repositories

router = APIRouter(prefix="/documents", tags=["documents"])

class DocumentResponse(BaseModel):
    document_id: uuid.UUID
    title: str
    created_at: datetime
    image_ids: list[uuid.UUID]

class CreateDocumentRequest(BaseModel):
    title: str = "Untitled Note"

@router.post("", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
def create_document(
    req: CreateDocumentRequest,
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
):
    doc = repos.documents.create(user_id=user.user_id, title=req.title)
    repos.documents.session.commit()
    return DocumentResponse(
        document_id=doc.document_id,
        title=doc.title,
        created_at=doc.created_at,
        image_ids=[]
    )

@router.get("", response_model=list[DocumentResponse])
def list_documents(
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
):
    docs = repos.documents.list_by_user(user.user_id)
    out = []
    for d in docs:
        out.append(
            DocumentResponse(
                document_id=d.document_id,
                title=d.title,
                created_at=d.created_at,
                image_ids=[img.image_id for img in d.images]
            )
        )
    return out

@router.get("/{document_id}", response_model=DocumentResponse)
def get_document(
    document_id: uuid.UUID,
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
):
    doc = repos.documents.get_by_id(document_id)
    if not doc or doc.user_id != user.user_id:
        raise HTTPException(status_code=404, detail="Document not found")
    return DocumentResponse(
        document_id=doc.document_id,
        title=doc.title,
        created_at=doc.created_at,
        image_ids=[img.image_id for img in doc.images]
    )
