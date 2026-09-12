"""FastAPI router for image upload and ownership persistence.

Implements ``POST /images`` — the authenticated image submission endpoint
described in the design's API Surface (Requirements 1.1, 1.6, 10.4):

* Auth is enforced via :func:`~app.services.auth_api.get_current_user`. An
  unauthenticated request is rejected with 401 *before* any handler logic runs,
  so nothing is ever stored for unauthenticated submissions (Req 1.5, 10.4).
* The uploaded file bytes are validated by :func:`~app.services.image_validator.validate_image`.
  - Accepted (PNG/JPEG, > 0 bytes, ≤ 10 MB): bytes are written to the object
    store and an ``Image`` record is created in the DB associated with the
    submitting user (Req 1.6). Returns 201 with the ``image_id``.
  - Rejected (unsupported format / size limit / invalid image): returns 422
    with a ``rejected`` status and the rejection ``reason`` and ``message``.
* The auth gate dependency runs before the handler, fulfilling Req 10.4 without
  any extra branching here.

Object-store dependency injection
----------------------------------
``get_object_store`` is a FastAPI dependency that returns the process-wide
:class:`~app.data.object_store.ObjectStore` (filesystem by default, S3 when
configured). Tests override it via ``app.dependency_overrides`` to inject an
in-memory or tmp-backed store without touching the filesystem.
"""

from __future__ import annotations

from io import BytesIO
import uuid
from functools import lru_cache
from typing import Annotated, Iterator, Optional

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.data.enums import ImageFormat, ImageStatus
from app.data.models import Image
from app.data.object_store import ObjectStore, build_object_store
from app.data.repositories import RepositoryFactory
from app.services.auth_api import get_current_user, get_db_session, get_repositories
from app.services.image_validator import RejectionReason, validate_image
from app.data.models import User
from app.services.isolation import get_or_404

router = APIRouter(tags=["images"])


# --------------------------------------------------------------------------- #
# Object store dependency
# --------------------------------------------------------------------------- #


@lru_cache(maxsize=1)
def _default_object_store() -> ObjectStore:
    """Process-wide default object store (overridable in tests)."""
    return build_object_store()


def get_object_store() -> ObjectStore:
    """Provide the object store. Overridden in tests for isolation."""
    return _default_object_store()


# --------------------------------------------------------------------------- #
# Response schemas
# --------------------------------------------------------------------------- #


class ImageAcceptedResponse(BaseModel):
    """Returned when an uploaded image passes validation and is persisted."""

    status: str = "accepted"
    image_id: uuid.UUID


class ImageRejectedResponse(BaseModel):
    """Returned when an uploaded image fails validation."""

    status: str = "rejected"
    reason: RejectionReason
    message: str


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #


@router.post(
    "/images",
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_201_CREATED: {"model": ImageAcceptedResponse},
        status.HTTP_422_UNPROCESSABLE_ENTITY: {"model": ImageRejectedResponse},
    },
    summary="Upload and validate a handwriting image",
    description=(
        "Accepts a multipart file upload. "
        "On success stores the image and returns its ID. "
        "On validation failure returns a rejection reason. "
        "Requires authentication (401 on missing/invalid credentials)."
    ),
)
def submit_image(
    file: Annotated[UploadFile, File(description="PNG or JPEG image, ≤ 10 MB")],
    document_id: Annotated[Optional[uuid.UUID], Form()] = None,
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
    store: ObjectStore = Depends(get_object_store),
) -> ImageAcceptedResponse | ImageRejectedResponse:
    """Upload an image and persist it if valid (Req 1.1, 1.6, 10.4).

    The auth dependency runs before this handler, so unauthenticated requests
    are rejected with 401 before any I/O occurs (Req 1.5, 10.4).
    """
    file_bytes = file.file.read()

    result = validate_image(file_bytes)

    if not result.accepted:
        # Validation failed: return 422 with the rejection reason and message.
        # Nothing is persisted (Req 1.5 — validation failure, no storage).
        from fastapi import HTTPException

        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "status": "rejected",
                "reason": result.reason.value,
                "message": result.message,
            },
        )

    if (
        document_id is not None
        and repos.documents.get(user.user_id, document_id) is None
    ):
        # Check ownership before writing the image. Besides returning the same
        # non-disclosing 404 used elsewhere, this prevents orphaned objects
        # when a client supplies an unknown or another user's document id.
        from fastapi import HTTPException

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )

    # Map the detected ImageFormat enum from the validator result.
    image_format: ImageFormat = result.image_format  # type: ignore[assignment]

    # Determine the file suffix for the object store reference.
    suffix_map = {
        ImageFormat.PNG: ".png",
        ImageFormat.JPEG: ".jpg",
    }
    suffix = suffix_map.get(image_format, "")

    # Persist bytes in the object store (Req 1.6).
    storage_path: str | None = None
    try:
        storage_path = store.put_image_bytes(
            str(user.user_id), file_bytes, suffix=suffix
        )
        db_image = Image(
            user_id=user.user_id,
            document_id=document_id,
            storage_path=storage_path,
            format=image_format,
            size_bytes=result.size_bytes,
            status=ImageStatus.ACCEPTED,
        )
        image = repos.images.add(db_image)
        if document_id is not None:
            repos.documents.touch(user.user_id, document_id)
        repos.session.commit()
    except Exception:
        repos.session.rollback()
        if storage_path is not None:
            try:
                store.delete(storage_path)
            except Exception:
                pass
        raise

    return ImageAcceptedResponse(image_id=image.image_id)


@router.get(
    "/images/{image_id}/processed",
    response_class=Response,
    status_code=status.HTTP_200_OK,
    summary="Fetch the backend-preprocessed image used for recognition overlays",
    description=(
        "Returns the deskewed grayscale image whose pixel coordinate space owns "
        "recognition regions. Requires authentication and image ownership."
    ),
)
def get_processed_image(
    image_id: uuid.UUID,
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
    store: ObjectStore = Depends(get_object_store),
) -> Response:
    """Return the exact preprocessed image used by segmentation/recognition."""
    image: Image = get_or_404(repos.images.get(user.user_id, image_id))
    image_bytes = store.get(image.storage_path)

    from PIL import Image as PILImage

    from app.ml.preprocessing import preprocess

    processed = preprocess(image_bytes)
    buffer = BytesIO()
    PILImage.fromarray(processed.gray).save(buffer, format="PNG")
    return Response(content=buffer.getvalue(), media_type="image/png")


__all__ = [
    "router",
    "get_object_store",
    "ImageAcceptedResponse",
    "ImageRejectedResponse",
    "get_processed_image",
    "submit_image",
]
