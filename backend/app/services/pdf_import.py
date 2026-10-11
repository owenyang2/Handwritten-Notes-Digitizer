"""PDF import: store the original file and rasterize ordered page records."""

from __future__ import annotations

import io
import hashlib
import uuid
from dataclasses import dataclass

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.config.settings import get_settings
from app.data.enums import ImageFormat, ImageStatus
from app.data.models import Document, DocumentPage, Image, User
from app.data.object_store import ObjectStore
from app.data.object_store import ObjectNotFoundError
from app.data.repositories import RepositoryFactory
from app.services.auth_api import get_current_user, get_repositories
from app.services.images_api import get_object_store
from app.services.image_validator import normalize_image_for_model, validate_image
from app.services.upload_limits import UploadTooLarge, read_upload_file_bounded

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


@dataclass(frozen=True)
class PreparedSource:
    source_kind: str
    original_suffix: str
    image_format: ImageFormat
    image_bytes: bytes
    pages: list[RasterPage]
    transforms: list[dict | None]


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
        document.source_kind = "pdf_import"
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
        document.source_kind = "image_import"
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


def _prepare_source(file_bytes: bytes) -> PreparedSource:
    """Validate and normalize an uploaded source without touching persistence."""
    if b"%PDF" in file_bytes[:1024]:
        pages = rasterize_pdf(file_bytes)
        return PreparedSource(
            source_kind="pdf_import",
            original_suffix=".pdf",
            image_format=ImageFormat.PNG,
            image_bytes=b"",
            pages=pages,
            transforms=[{"dpi": page.dpi, "rotation": 0} for page in pages],
        )

    validation = validate_image(file_bytes)
    if not validation.accepted or validation.image_format is None:
        raise PdfImportError(validation.message or "The image could not be accepted.")
    try:
        normalized, transforms = normalize_image_for_model(
            file_bytes, max_pixels=get_settings().pdf_max_pixels
        )
        from PIL import Image as PILImage

        with PILImage.open(io.BytesIO(normalized)) as image:
            width, height = image.size
    except Exception as exc:
        raise PdfImportError(f"The image could not be normalized: {exc}") from exc
    return PreparedSource(
        source_kind="image_import",
        original_suffix=(".png" if validation.image_format == ImageFormat.PNG else ".jpg"),
        image_format=validation.image_format,
        image_bytes=file_bytes,
        pages=[
            RasterPage(
                index=0,
                png_bytes=normalized,
                width=width,
                height=height,
                dpi=200,
            )
        ],
        transforms=[transforms],
    )


def _source_response(
    document: Document,
    pages: list[DocumentPage],
    source_sha256: str,
) -> "SourceUploadResponse":
    return SourceUploadResponse(
        document_id=document.document_id,
        title=document.title,
        source_kind=document.source_kind,
        source_sha256=source_sha256,
        page_count=len(pages),
        pages=[
            ImportedPageResponse(
                page_id=page.page_id,
                page_index=page.page_index,
                title=page.title,
                image_id=page.image_id,
                width=page.width,
                height=page.height,
                dpi=page.dpi,
                coordinate_space=page.coordinate_space,
                paper_template=page.paper_template,
            )
            for page in pages
        ],
    )


class ImportedPageResponse(BaseModel):
    page_id: uuid.UUID
    page_index: int
    title: str | None = None
    image_id: uuid.UUID | None
    width: int
    height: int
    dpi: int
    coordinate_space: str
    paper_template: str | None = None


class ImportDocumentResponse(BaseModel):
    document_id: uuid.UUID
    title: str
    page_count: int
    pages: list[ImportedPageResponse]


class SourceUploadResponse(ImportDocumentResponse):
    source_kind: str
    source_sha256: str


@router.post(
    "/import",
    response_model=ImportDocumentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Import a PDF or image into ordered page rasters",
)
async def import_document(
    file: UploadFile = File(...),
    title: str = Form(default="Imported notes"),
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
    store: ObjectStore = Depends(get_object_store),
) -> ImportDocumentResponse:
    max_bytes = get_settings().max_upload_bytes
    try:
        pdf_bytes = await read_upload_file_bounded(file, max_bytes)
    except UploadTooLarge as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"File exceeds the {max_bytes} byte upload limit.",
        ) from exc
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
                title=page.title,
                image_id=page.image_id,
                width=page.width,
                height=page.height,
                dpi=page.dpi,
                coordinate_space=page.coordinate_space,
                paper_template=page.paper_template,
            )
            for page in pages
        ],
    )


@router.post(
    "/{document_id}/source",
    response_model=SourceUploadResponse,
    status_code=status.HTTP_200_OK,
    summary="Attach a durable PDF or image source to an existing document",
)
async def attach_document_source(
    document_id: uuid.UUID,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
    store: ObjectStore = Depends(get_object_store),
) -> SourceUploadResponse:
    """Attach source bytes while preserving client-assigned page identities.

    The iPad creates and syncs the document/page aggregate first. This endpoint
    only replaces the source layer and raster metadata, so retrying a timed-out
    upload cannot create a second document or a second page set.
    """
    document = repos.documents.get(user.user_id, document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    max_bytes = get_settings().max_upload_bytes
    try:
        file_bytes = await read_upload_file_bounded(file, max_bytes)
    except UploadTooLarge as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"File exceeds the {max_bytes} byte upload limit.",
        ) from exc

    try:
        prepared = _prepare_source(file_bytes)
    except PdfImportError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    digest = hashlib.sha256(file_bytes).hexdigest()
    metadata = dict(document.source_metadata or {})
    if (
        metadata.get("source_sha256") == digest
        and document.source_kind == prepared.source_kind
        and document.original_path
        and store.exists(document.original_path)
    ):
        pages = list(repos.document_pages.list_for_document(user.user_id, document_id))
        return _source_response(document, pages, digest)

    pages = list(repos.document_pages.list_for_document(user.user_id, document_id))
    if pages and len(pages) != len(prepared.pages):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"The source contains {len(prepared.pages)} pages, but this notebook "
                f"already has {len(pages)} pages."
            ),
        )

    stored_paths: list[str] = []
    previous_paths: list[str] = []
    try:
        original_path = store.put_document_bytes(
            str(user.user_id), file_bytes, suffix=prepared.original_suffix
        )
        stored_paths.append(original_path)
        document.source_kind = prepared.source_kind
        document.original_path = original_path
        metadata.update(
            {
                "source_attached": True,
                "source_sha256": digest,
                "source_bytes": len(file_bytes),
                "original_filename": file.filename or "imported-source",
            }
        )
        document.source_metadata = metadata
        repos.session.flush()

        if not pages:
            for prepared_page in prepared.pages:
                page = DocumentPage(
                    document_id=document.document_id,
                    page_index=prepared_page.index,
                    original_path=original_path,
                    raster_path="",
                    width=prepared_page.width,
                    height=prepared_page.height,
                    dpi=prepared_page.dpi,
                    coordinate_space=(
                        "page-raster-v1"
                        if prepared.source_kind == "pdf_import"
                        else "normalized-raster-v1"
                    ),
                    transforms=prepared.transforms[prepared_page.index],
                )
                repos.document_pages.add(page)
            pages = list(repos.document_pages.list_for_document(user.user_id, document_id))

        for page, prepared_page in zip(pages, prepared.pages, strict=True):
            raster_path = store.put_page_raster(
                str(user.user_id),
                prepared_page.png_bytes,
                name=page.page_id.hex,
                suffix=".png",
            )
            stored_paths.append(raster_path)
            image_payload = (
                prepared_page.png_bytes
                if prepared.source_kind == "pdf_import"
                else prepared.image_bytes
            )
            image_suffix = ".png" if prepared.source_kind == "pdf_import" else prepared.original_suffix
            image_path = store.put_image_bytes(
                str(user.user_id),
                image_payload,
                name=page.page_id.hex,
                suffix=image_suffix,
            )
            stored_paths.append(image_path)
            image = repos.session.get(Image, page.image_id) if page.image_id else None
            if image is None:
                image = Image(
                    user_id=user.user_id,
                    document_id=document.document_id,
                    storage_path=image_path,
                    format=prepared.image_format,
                    size_bytes=len(image_payload),
                    status=ImageStatus.ACCEPTED,
                )
                repos.images.add(image)
                page.image_id = image.image_id
            else:
                if image.user_id != user.user_id or image.document_id != document.document_id:
                    raise PdfImportError("The existing page image is not owned by this document.")
                if image.storage_path != image_path:
                    previous_paths.append(image.storage_path)
                image.storage_path = image_path
                image.format = prepared.image_format
                image.size_bytes = len(image_payload)
                image.status = ImageStatus.ACCEPTED
            if page.raster_path and not page.raster_path.startswith("native-ink://"):
                previous_paths.append(page.raster_path)
            page.original_path = original_path
            page.raster_path = raster_path
            page.width = prepared_page.width
            page.height = prepared_page.height
            page.dpi = prepared_page.dpi
            page.coordinate_space = (
                "page-raster-v1"
                if prepared.source_kind == "pdf_import"
                else "normalized-raster-v1"
            )
            page.transforms = prepared.transforms[prepared_page.index]

        repos.session.commit()
    except PdfImportError as exc:
        repos.session.rollback()
        for path in reversed(stored_paths):
            store.delete(path)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception:
        repos.session.rollback()
        for path in reversed(stored_paths):
            try:
                store.delete(path)
            except Exception:
                pass
        raise

    for path in previous_paths:
        if path != document.original_path:
            try:
                store.delete(path)
            except Exception:
                pass
    return _source_response(document, pages, digest)


@router.get(
    "/{document_id}/source",
    response_class=StreamingResponse,
    summary="Download an attached PDF or image source",
)
def download_document_source(
    document_id: uuid.UUID,
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
    store: ObjectStore = Depends(get_object_store),
) -> StreamingResponse:
    document = repos.documents.get(user.user_id, document_id)
    if document is None or not document.original_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    try:
        stream = store.open(document.original_path)
    except ObjectNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found") from exc
    from pathlib import PurePosixPath
    suffix = ".pdf" if document.source_kind == "pdf_import" else PurePosixPath(document.original_path).suffix.lower()
    media_type = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}.get(suffix, "application/octet-stream")
    suffix = suffix or ".bin"
    metadata = document.source_metadata or {}
    filename = metadata.get("original_filename")
    if not isinstance(filename, str) or not filename.strip():
        filename = f"{document.title}{suffix}"
    safe_filename = filename.replace('"', "_").replace("\r", "_").replace("\n", "_")
    return StreamingResponse(
        stream,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{safe_filename}"'},
    )
