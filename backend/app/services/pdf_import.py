"""PDF import: store the original file and rasterize ordered page records."""

from __future__ import annotations

import io
import uuid
from dataclasses import dataclass

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel

from app.config.settings import get_settings
from app.data.enums import ImageFormat, ImageStatus
from app.data.models import Document, DocumentPage, Image, User
from app.data.object_store import ObjectStore
from app.data.repositories import RepositoryFactory
from app.services.auth_api import get_current_user, get_repositories
from app.services.images_api import get_object_store
from app.services.image_validator import normalize_image_for_model, validate_image

router = APIRouter(prefix="/documents", tags=["documents"])


class PdfImportError(ValueError):
    """Raised when a PDF cannot be accepted or rasterized."""


@dataclass(frozen=True)
class RasterPage:
    index: int
    png_bytes: bytes
    width: int
    height: int
    dpi: int


def rasterize_pdf(
    pdf_bytes: bytes,
    *,
    dpi: int | None = None,
    max_pages: int | None = None,
    max_pixels: int | None = None,
) -> list[RasterPage]:
    """Rasterize PDF pages to PNG. Password-protected files are rejected."""
    settings = get_settings()
    dpi = settings.pdf_raster_dpi if dpi is None else dpi
    max_pages = settings.pdf_max_pages if max_pages is None else max_pages
    max_pixels = settings.pdf_max_pixels if max_pixels is None else max_pixels

    if dpi <= 0 or max_pages <= 0 or max_pixels <= 0:
        raise PdfImportError("PDF rasterization limits must be positive.")

    if not pdf_bytes:
        raise PdfImportError("The PDF is empty.")
    if pdf_bytes[:4] != b"%PDF" and b"%PDF" not in pdf_bytes[:1024]:
        raise PdfImportError("The file is not a PDF.")

    try:
        import pypdfium2 as pdfium
    except ImportError as exc:  # pragma: no cover - optional until installed
        raise PdfImportError(
            "PDF import requires pypdfium2. Install backend requirements and retry."
        ) from exc

    try:
        document = pdfium.PdfDocument(pdf_bytes)
    except Exception as exc:
        message = str(exc).lower()
        if "password" in message or "encrypt" in message:
            raise PdfImportError("Password-protected PDFs are not supported.") from exc
        raise PdfImportError("The PDF could not be opened.") from exc

    try:
        page_count = len(document)
        if page_count == 0:
            raise PdfImportError("The PDF has no pages.")
        if page_count > max_pages:
            raise PdfImportError(
                f"The PDF has {page_count} pages; the limit is {max_pages}."
            )

        scale = dpi / 72.0
        pages: list[RasterPage] = []
        for index in range(page_count):
            page = document[index]
            try:
                # Check declared dimensions before pdfium allocates a bitmap.
                # Keep the post-render check below for malformed files whose
                # decoded dimensions differ from the PDF metadata.
                point_width, point_height = page.get_size()
                expected_width = max(1, int(round(point_width * scale)))
                expected_height = max(1, int(round(point_height * scale)))
                if expected_width * expected_height > max_pixels:
                    raise PdfImportError(
                        f"Page {index + 1} exceeds the decoded pixel limit ({max_pixels})."
                    )
                bitmap = page.render(scale=scale)
                pil = bitmap.to_pil().convert("RGB")
            except PdfImportError:
                raise
            except Exception as exc:
                raise PdfImportError(
                    f"Page {index + 1} could not be rasterized."
                ) from exc
            finally:
                page.close()
            width, height = pil.size
            if width * height > max_pixels:
                raise PdfImportError(
                    f"Page {index + 1} exceeds the decoded pixel limit ({max_pixels})."
                )
            buf = io.BytesIO()
            pil.save(buf, format="PNG")
            pages.append(
                RasterPage(
                    index=index,
                    png_bytes=buf.getvalue(),
                    width=width,
                    height=height,
                    dpi=dpi,
                )
            )
        return pages
    finally:
        document.close()


def import_pdf(
    *,
    user_id: uuid.UUID,
    title: str,
    pdf_bytes: bytes,
    repos: RepositoryFactory,
    store: ObjectStore,
) -> Document:
    pages = rasterize_pdf(pdf_bytes)
    stored_paths: list[str] = []
    try:
        original_path = store.put_document_bytes(str(user_id), pdf_bytes, suffix=".pdf")
        stored_paths.append(original_path)
        document = repos.documents.create(user_id=user_id, title=title or "Imported notes")
        document.source_kind = "pdf"
        document.original_path = original_path
        repos.session.flush()

        for page in pages:
            image_path = store.put_image_bytes(str(user_id), page.png_bytes, suffix=".png")
            stored_paths.append(image_path)
            image = Image(
                user_id=user_id,
                document_id=document.document_id,
                storage_path=image_path,
                format=ImageFormat.PNG,
                size_bytes=len(page.png_bytes),
                status=ImageStatus.ACCEPTED,
            )
            repos.images.add(image)
            raster_path = store.put_page_raster(str(user_id), page.png_bytes, suffix=".png")
            stored_paths.append(raster_path)
            repos.document_pages.add(
                DocumentPage(
                    document_id=document.document_id,
                    page_index=page.index,
                    original_path=original_path,
                    raster_path=raster_path,
                    image_id=image.image_id,
                    width=page.width,
                    height=page.height,
                    dpi=page.dpi,
                    coordinate_space="page-raster-v1",
                    transforms={"dpi": page.dpi, "rotation": 0},
                )
            )
        repos.session.commit()
        return document
    except Exception:
        repos.session.rollback()
        for path in reversed(stored_paths):
            try:
                store.delete(path)
            except Exception:
                pass
        raise


def import_image_document(
    *,
    user_id: uuid.UUID,
    title: str,
    image_bytes: bytes,
    repos: RepositoryFactory,
    store: ObjectStore,
) -> Document:
    """Create the same page/document aggregate for a single photo/image.

    The original upload is retained for audit while the page raster is a
    normalized RGB PNG whose coordinate space is stable for crops and review.
    """
    validation = validate_image(image_bytes)
    if not validation.accepted or validation.image_format is None:
        raise PdfImportError(validation.message or "The image could not be accepted.")
    try:
        normalized, transforms = normalize_image_for_model(
            image_bytes, max_pixels=get_settings().pdf_max_pixels
        )
        from PIL import Image as PILImage

        with PILImage.open(io.BytesIO(normalized)) as image:
            width, height = image.size
    except Exception as exc:
        raise PdfImportError(f"The image could not be normalized: {exc}") from exc

    suffix = ".png" if validation.image_format == ImageFormat.PNG else ".jpg"
    stored_paths: list[str] = []
    try:
        original_path = store.put_document_bytes(str(user_id), image_bytes, suffix=suffix)
        stored_paths.append(original_path)
        document = repos.documents.create(user_id=user_id, title=title or "Imported notes")
        document.source_kind = "image"
        document.original_path = original_path
        repos.session.flush()

        image_path = store.put_image_bytes(str(user_id), image_bytes, suffix=suffix)
        stored_paths.append(image_path)
        image = Image(
            user_id=user_id,
            document_id=document.document_id,
            storage_path=image_path,
            format=validation.image_format,
            size_bytes=len(image_bytes),
            status=ImageStatus.ACCEPTED,
        )
        repos.images.add(image)
        page_path = store.put_page_raster(str(user_id), normalized, suffix=".png")
        stored_paths.append(page_path)
        repos.document_pages.add(
            DocumentPage(
                document_id=document.document_id,
                page_index=0,
                original_path=original_path,
                raster_path=page_path,
                image_id=image.image_id,
                width=width,
                height=height,
                dpi=200,
                coordinate_space="normalized-raster-v1",
                transforms=transforms,
            )
        )
        repos.session.commit()
        return document
    except Exception:
        repos.session.rollback()
        for path in reversed(stored_paths):
            try:
                store.delete(path)
            except Exception:
                pass
        raise


class ImportedPageResponse(BaseModel):
    page_id: uuid.UUID
    page_index: int
    image_id: uuid.UUID | None
    width: int
    height: int
    dpi: int
    coordinate_space: str


class ImportDocumentResponse(BaseModel):
    document_id: uuid.UUID
    title: str
    page_count: int
    pages: list[ImportedPageResponse]


@router.post(
    "/import",
    response_model=ImportDocumentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Import a PDF or image into ordered page rasters",
)
def import_document(
    file: UploadFile = File(...),
    title: str = Form(default="Imported notes"),
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
    store: ObjectStore = Depends(get_object_store),
) -> ImportDocumentResponse:
    pdf_bytes = file.file.read()
    max_bytes = get_settings().max_upload_bytes
    if len(pdf_bytes) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"File exceeds the {max_bytes} byte upload limit.",
        )
    try:
        if b"%PDF" in pdf_bytes[:1024]:
            document = import_pdf(
                user_id=user.user_id,
                title=title.strip() or "Imported notes",
                pdf_bytes=pdf_bytes,
                repos=repos,
                store=store,
            )
        else:
            document = import_image_document(
                user_id=user.user_id,
                title=title.strip() or "Imported notes",
                image_bytes=pdf_bytes,
                repos=repos,
                store=store,
            )
    except PdfImportError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    pages = repos.document_pages.list_for_document(user.user_id, document.document_id)
    return ImportDocumentResponse(
        document_id=document.document_id,
        title=document.title,
        page_count=len(pages),
        pages=[
            ImportedPageResponse(
                page_id=page.page_id,
                page_index=page.page_index,
                image_id=page.image_id,
                width=page.width,
                height=page.height,
                dpi=page.dpi,
                coordinate_space=page.coordinate_space,
            )
            for page in pages
        ],
    )
