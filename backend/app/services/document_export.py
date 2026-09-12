"""Export a reviewed document revision as Markdown, LaTeX, HTML, or PDF."""

from __future__ import annotations

import html
import io
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel

from app.data.models import Document, DocumentRevision, User
from app.data.object_store import ObjectStore
from app.data.repositories import RepositoryFactory
from app.services.auth_api import get_current_user, get_repositories
from app.services.images_api import get_object_store
from app.services.isolation import get_or_404

router = APIRouter(prefix="/documents", tags=["document-export"])

DocumentExportFormat = Literal["markdown", "latex", "latex_bundle", "html", "pdf"]


@dataclass(frozen=True)
class DocumentBlock:
    kind: str
    text: str
    excluded: bool = False
    page_index: int = 0
    order_index: int = 0
    source_id: str | None = None
    asset_ref: str | None = None


def _block_kind(content_type: str) -> str:
    """Map the persisted region type to an export rendering kind."""
    if content_type == "figure":
        return "figure"
    if content_type == "math_inline":
        return "math_inline"
    if content_type == "mixed":
        return "mixed"
    if "math" in content_type:
        return "math"
    return "text"


def assemble_document_blocks(
    user_id: uuid.UUID,
    document: Document,
    repos: RepositoryFactory,
    revision_id: uuid.UUID | None = None,
) -> list[DocumentBlock]:
    """Collect one owner-scoped document revision in page/read order.

    When ``revision_id`` is supplied, canonical review ids in its snapshot are
    used.  Without it, the latest persisted review is used.  Legacy image
    rows remain a compatibility fallback for documents created before the PDF
    workflow.
    """
    blocks: list[DocumentBlock] = []
    snapshot_reviews: dict[uuid.UUID, object] = {}
    if revision_id is not None:
        snapshot = repos.document_revisions.get(user_id, revision_id)
        if snapshot is None or snapshot.document_id != document.document_id:
            raise ValueError("document revision does not belong to this document")
        snapshot_items = (snapshot.block_snapshot or {}).get("blocks", [])
        for item in snapshot_items:
            try:
                region_id = uuid.UUID(str(item["region_id"]))
                review_id = item.get("review_revision_id")
                if review_id:
                    review = repos.review_revisions.get(user_id, uuid.UUID(str(review_id)))
                    if review is not None:
                        snapshot_reviews[region_id] = review
            except (KeyError, TypeError, ValueError):
                continue
        # New revisions carry the complete ordered display state, so an
        # explicit historical export does not depend on mutable current
        # regions.  The fallback below preserves compatibility with early
        # pointer-only revisions.
        if snapshot_items and all(isinstance(item, dict) and "region_id" in item for item in snapshot_items):
            blocks: list[DocumentBlock] = []
            for item in sorted(
                (item for item in snapshot_items if isinstance(item, dict)),
                key=lambda value: (int(value.get("page_index", 0)), int(value.get("order_index", 0))),
            ):
                try:
                    region_id = uuid.UUID(str(item["region_id"]))
                except (KeyError, TypeError, ValueError):
                    continue
                review = snapshot_reviews.get(region_id)
                content_type = str(item.get("content_type") or "text")
                text = (
                    review.reviewed_text
                    if review is not None
                    else str(item.get("reviewed_text") or item.get("prediction_text") or "")
                )
                excluded = review.excluded if review is not None else bool(item.get("excluded", False))
                blocks.append(
                    DocumentBlock(
                        kind=_block_kind(content_type),
                        text=text,
                        excluded=excluded,
                        page_index=int(item.get("page_index", 0)),
                        order_index=int(item.get("order_index", 0)),
                        source_id=str(region_id),
                        asset_ref=item.get("asset_ref") if isinstance(item.get("asset_ref"), str) else None,
                    )
                )
            return blocks
    pages = repos.document_pages.list_for_document(user_id, document.document_id)
    if not pages:
        for image in document.images:
            reviews = {
                review.line_index: review
                for review in repos.line_reviews.list_for_image(user_id, image.image_id)
            }
            words = repos.recognized_words.list_for_image(user_id, image.image_id)
            lines_by_index: dict[int, list[str]] = {}
            for row in words:
                lines_by_index.setdefault(row.line_index, []).append(row.text)
            for idx in sorted(set(lines_by_index) | set(reviews)):
                review = reviews.get(idx)
                if review is not None:
                    blocks.append(
                        DocumentBlock(
                            kind="text",
                            text=review.reviewed_text,
                            excluded=review.excluded,
                            order_index=idx,
                            source_id=str(image.image_id),
                        )
                    )
                else:
                    blocks.append(
                        DocumentBlock(
                            kind="text",
                            text=" ".join(lines_by_index.get(idx, [])),
                            order_index=idx,
                            source_id=str(image.image_id),
                        )
                    )
        return blocks

    for page in pages:
        regions = repos.document_regions.list_for_page(user_id, page.page_id)
        if regions:
            for region in regions:
                review = snapshot_reviews.get(region.region_id)
                if review is None and revision_id is None:
                    review = repos.review_revisions.latest_for_region(user_id, region.region_id)
                if review is None and revision_id is None:
                    review = repos.region_reviews.latest_for_region(user_id, region.region_id)
                if review is not None:
                    blocks.append(
                        DocumentBlock(
                            kind=_block_kind(region.content_type),
                            text=review.reviewed_text,
                            excluded=review.excluded,
                            page_index=page.page_index,
                            order_index=region.order_index,
                            source_id=str(region.region_id),
                            asset_ref=region.source_crop_path,
                        )
                    )
                    continue
                prediction = repos.region_predictions.latest_for_region(
                    user_id, region.region_id
                )
                blocks.append(
                    DocumentBlock(
                        kind=_block_kind(region.content_type),
                        text=prediction.raw_output if prediction is not None else "",
                        excluded=False,
                        page_index=page.page_index,
                        order_index=region.order_index,
                        source_id=str(region.region_id),
                        asset_ref=region.source_crop_path,
                    )
                )
            continue
        if page.image_id is None:
            continue
        reviews = {
            review.line_index: review
            for review in repos.line_reviews.list_for_image(user_id, page.image_id)
        }
        words = repos.recognized_words.list_for_image(user_id, page.image_id)
        lines_by_index: dict[int, list[str]] = {}
        for row in words:
            lines_by_index.setdefault(row.line_index, []).append(row.text)
        for idx in sorted(set(lines_by_index) | set(reviews)):
            review = reviews.get(idx)
            if review is not None:
                blocks.append(
                    DocumentBlock(
                        kind="text",
                        text=review.reviewed_text,
                        excluded=review.excluded,
                        page_index=page.page_index,
                        order_index=idx,
                        source_id=str(page.image_id),
                    )
                )
            else:
                blocks.append(
                    DocumentBlock(
                        kind="text",
                        text=" ".join(lines_by_index.get(idx, [])),
                        page_index=page.page_index,
                        order_index=idx,
                        source_id=str(page.image_id),
                    )
                )
    return blocks


def _visible(blocks: list[DocumentBlock]) -> list[DocumentBlock]:
    return [
        block
        for block in blocks
        if not block.excluded and (block.text.strip() or block.kind == "figure")
    ]


def render_markdown(blocks: list[DocumentBlock]) -> str:
    parts: list[str] = []
    for block in _visible(blocks):
        text = block.text.strip()
        if block.kind == "math":
            parts.append(f"$$\n{text}\n$$")
        elif block.kind == "math_inline":
            parts.append(f"\\({text}\\)")
        elif block.kind == "figure":
            parts.append(f"![Figure](assets/{block.source_id or 'figure'}.png)")
        else:
            parts.append(text)
    return "\n\n".join(parts)


def render_latex(blocks: list[DocumentBlock]) -> str:
    def escape_text(value: str) -> str:
        replacements = {
            "\\": r"\textbackslash{}",
            "&": r"\&",
            "%": r"\%",
            "$": r"\$",
            "#": r"\#",
            "_": r"\_",
            "{": r"\{",
            "}": r"\}",
            "~": r"\textasciitilde{}",
            "^": r"\textasciicircum{}",
        }
        return "".join(replacements.get(char, char) for char in value)

    body: list[str] = []
    for block in _visible(blocks):
        text = block.text.strip()
        if block.kind == "math":
            body.append(f"\\[\n{text}\n\\]")
        elif block.kind == "math_inline":
            body.append(f"\\({text}\\)")
        elif block.kind == "mixed":
            # Mixed blocks are reviewer-authored source that may contain both
            # prose and inline math. Preserve that source just as display-math
            # blocks preserve their reviewed LaTeX.
            body.append(text)
        elif block.kind == "figure":
            body.append(f"\\includegraphics{{assets/{block.source_id or 'figure'}.png}}")
        else:
            body.append(escape_text(text))
    inner = "\n\n".join(body)
    return (
        "\\documentclass{article}\n"
        "\\usepackage{amsmath,amssymb,graphicx}\n"
        "\\begin{document}\n\n"
        f"{inner}\n\n"
        "\\end{document}\n"
    )


def render_html(blocks: list[DocumentBlock]) -> str:
    parts: list[str] = ["<!DOCTYPE html>", "<html><body>"]
    for block in _visible(blocks):
        text = html.escape(block.text.strip(), quote=True)
        if block.kind == "math":
            parts.append(f'<pre class="math">{text}</pre>')
        elif block.kind == "math_inline":
            parts.append(f'<span class="math">\\({text}\\)</span>')
        elif block.kind == "figure":
            source_id = html.escape(block.source_id or "figure", quote=True)
            asset_ref = html.escape(block.asset_ref or "", quote=True)
            parts.append(
                f'<figure data-asset-ref="{asset_ref}">'
                f'<img src="assets/{source_id}.png" alt="Figure">'
                "<figcaption>Figure</figcaption></figure>"
            )
        else:
            parts.append(f"<p>{text}</p>")
    parts.append("</body></html>")
    return "\n".join(parts)


_EXPORT_FONT = Path(__file__).resolve().parents[1] / "assets" / "fonts" / "ExportSans.ttf"


def _unicode_font_path() -> Path | None:
    candidates = [
        _EXPORT_FONT,
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
    ]
    for path in candidates:
        if path.is_file() and path.stat().st_size > 0:
            return path
    return None


def _render_pdf_bytes(blocks: list[DocumentBlock], store: ObjectStore | None = None) -> bytes:
    """Render the same ordered reviewed blocks into a real PDF.

    The application intentionally does not run an unrestricted TeX process in
    the web request.  This controlled renderer preserves block order,
    exclusions, math source, and figure markers in a valid portable PDF.  A
    future isolated TeX renderer can replace this function without changing
    the document contract.
    """
    try:
        from fpdf import FPDF
    except ImportError as exc:  # pragma: no cover - backend requirements pin fpdf2
        raise ValueError("PDF export requires fpdf2.") from exc
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.add_page()
    font_path = _unicode_font_path()
    if font_path is not None:
        pdf.add_font("ExportSans", fname=str(font_path))
        pdf.set_font("ExportSans", size=11)
        unicode_font = True
    else:
        pdf.set_font("helvetica", size=11)
        unicode_font = False
    for block in blocks:
        if block.kind == "math":
            text = f"$$ {block.text.strip()} $$"
        elif block.kind == "math_inline":
            text = f"\\({block.text.strip()}\\)"
        elif block.kind == "figure":
            text = f"[Figure: {block.source_id or 'figure'}]"
        else:
            text = block.text.strip()
        if unicode_font:
            safe = text
        else:
            # Visible hex fallback is honest when no Unicode font is installed.
            safe = "".join(ch if ch.isascii() else f"\\u{ord(ch):04x}" for ch in text)
        if block.kind == "figure" and store is not None and block.asset_ref:
            try:
                pdf.image(io.BytesIO(store.get(block.asset_ref)), w=80)
                pdf.ln(2)
                continue
            except Exception:
                # Keep a visible marker when an optional figure asset has
                # disappeared; the reviewed text remains exportable.
                pass
        pdf.multi_cell(0, 7, text=safe)
        pdf.ln(2)
    output = pdf.output()
    return bytes(output)


def _render_latex_bundle(
    blocks: list[DocumentBlock],
    store: ObjectStore | None = None,
) -> bytes:
    """Package the TeX source and retained figure assets in one zip archive."""
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("document.tex", render_latex(blocks))
        for block in blocks:
            if block.kind != "figure" or not block.asset_ref:
                continue
            if store is None:
                raise ValueError("LaTeX bundle export requires retained figure assets.")
            try:
                payload = store.get(block.asset_ref)
            except Exception as exc:
                raise ValueError(
                    f"Could not read figure asset for {block.source_id or 'figure'}."
                ) from exc
            archive.writestr(f"assets/{block.source_id or 'figure'}.png", payload)
    return buffer.getvalue()


def export_document_revision(
    blocks: list[DocumentBlock],
    format: DocumentExportFormat,  # noqa: A002
    store: ObjectStore | None = None,
) -> tuple[bytes, str]:
    visible = _visible(blocks)
    if not visible:
        raise ValueError("There is no text to export.")
    if format == "markdown":
        return render_markdown(blocks).encode("utf-8"), "text/markdown; charset=utf-8"
    if format == "latex":
        return render_latex(blocks).encode("utf-8"), "application/x-tex; charset=utf-8"
    if format == "latex_bundle":
        return _render_latex_bundle(visible, store), "application/zip"
    if format == "html":
        return render_html(blocks).encode("utf-8"), "text/html; charset=utf-8"
    if format == "pdf":
        return _render_pdf_bytes(visible, store), "application/pdf"
    raise ValueError(f"Unknown export format: {format}")


class DocumentExportRequest(BaseModel):
    format: DocumentExportFormat = "markdown"
    revision_id: uuid.UUID | None = None


class DocumentExportResponse(BaseModel):
    status: str = "success"
    format: str
    text: str


@router.post(
    "/{document_id}/exports",
    response_model=None,
    summary="Export the reviewed document revision",
)
def export_document(
    document_id: uuid.UUID,
    body: DocumentExportRequest,
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
    store: ObjectStore = Depends(get_object_store),
) -> DocumentExportResponse | Response:
    document = get_or_404(repos.documents.get(user.user_id, document_id))
    revision_id = body.revision_id or document.active_revision_id
    try:
        blocks = assemble_document_blocks(user.user_id, document, repos, revision_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        payload, media_type = export_document_revision(blocks, body.format, store)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    if body.format in {"pdf", "latex_bundle"}:
        return Response(content=payload, media_type=media_type)
    return DocumentExportResponse(format=body.format, text=payload.decode("utf-8"))
