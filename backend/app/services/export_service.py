"""Export_Service: plain-text and markdown export of recognized/corrected text.

This module implements Requirement 8 (Export and Copy of Digital Text).  The
service is deliberately decoupled from FastAPI so the core logic is easy to
unit-test without standing up an HTTP server; the FastAPI router at the bottom
wires it to the ``POST /export`` endpoint.

Behaviour summary
-----------------
* **Plain text** (Req 8.1): words joined by a single space within each line;
  lines joined by a single newline (``\\n``).
* **Markdown** (Req 8.2): same intra-line layout as plain text; blank lines
  (paragraph breaks) are inserted between groups of lines that are separated by
  at least one blank recognized line, or after every line when no grouping is
  present (treated as a single paragraph).
* **Current corrections** (Req 8.3, 8.4): the caller passes the confirmed
  (already-corrected) line/word data; the export function uses whatever text is
  in that data at call time and never waits for or reads adapter training state.
* **Empty text** (Req 8.5): if there are no lines, all lines are empty, or all
  words in every line are empty/whitespace, the function returns an
  :class:`ExportEmpty` result carrying the nothing-to-export message.
* **Failure** (Req 8.6): any unexpected exception is caught and returned as an
  :class:`ExportError`; the caller's text is left unchanged.

Data contract
-------------
The service accepts a list of :class:`LineInput` objects.  Each ``LineInput``
holds a list of word strings.  The caller is responsible for ordering the lines
and words in reading order (top-to-bottom, left-to-right) before calling
:func:`export` — this matches what :func:`~app.services.recognition_service.get_recognition_result`
already returns.  For the ``POST /export`` endpoint the ordering comes from the
stored :class:`~app.data.models.RecognizedWord` rows.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Literal, Optional, Sequence, Union

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.data.models import User
from app.data.repositories import RepositoryFactory
from app.services.auth_api import get_current_user, get_repositories
from app.services.capability_guard import require_capability
from app.services.isolation import get_or_404

# --------------------------------------------------------------------------- #
# Public message constant (Req 8.5)
# --------------------------------------------------------------------------- #

NO_TEXT_TO_EXPORT_MESSAGE = "There is no text to export."

# --------------------------------------------------------------------------- #
# Service-layer data types
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class LineInput:
    """One line of text represented as an ordered list of word strings.

    ``words`` may be empty (an empty line).  The service treats a line whose
    words are all empty strings or whitespace the same as an absent line when
    deciding whether the whole document is empty.
    """

    words: List[str] = field(default_factory=list)

    def as_text(self) -> str:
        """Join the words with a single space."""
        return " ".join(self.words)

    def is_blank(self) -> bool:
        """Return True when every word is empty or whitespace."""
        return all(w.strip() == "" for w in self.words)


# ---- Result types --------------------------------------------------------- #


@dataclass(frozen=True)
class ExportSuccess:
    """The export produced output text."""

    text: str
    format: str  # "plain" | "markdown"


@dataclass(frozen=True)
class ExportEmpty:
    """The document was empty; no output is produced (Req 8.5)."""

    message: str = NO_TEXT_TO_EXPORT_MESSAGE


@dataclass(frozen=True)
class ExportError:
    """An unexpected failure; the caller's text is left unchanged (Req 8.6)."""

    message: str


ExportResult = Union[ExportSuccess, ExportEmpty, ExportError]

# --------------------------------------------------------------------------- #
# Core export logic
# --------------------------------------------------------------------------- #


def _is_document_empty(lines: Sequence[LineInput]) -> bool:
    """Return True when lines is empty or every line is blank."""
    if not lines:
        return True
    return all(line.is_blank() for line in lines)


def _to_plain_text(lines: Sequence[LineInput]) -> str:
    """Produce plain text: words space-joined per line, lines newline-joined.

    Implements Req 8.1: preserves line breaks.
    """
    return "\n".join(line.as_text() for line in lines)


def _to_markdown(lines: Sequence[LineInput]) -> str:
    """Produce markdown text: preserves line and paragraph structure (Req 8.2).

    Strategy: consecutive non-blank lines form a paragraph; a blank recognized
    line acts as a paragraph separator (rendered as a blank markdown line).
    When there are no blank lines in the input, all lines form a single
    paragraph joined with newlines (no extra blank lines added).
    """
    paragraphs: list[list[str]] = []
    current: list[str] = []

    for line in lines:
        if line.is_blank():
            # A blank line in the input triggers a paragraph break.
            if current:
                paragraphs.append(current)
                current = []
            # Skip the blank line itself (it becomes the separator).
        else:
            current.append(line.as_text())

    if current:
        paragraphs.append(current)

    # Each paragraph is a block of text lines; paragraphs are separated by a
    # blank line in the markdown output.
    return "\n\n".join("\n".join(para) for para in paragraphs)


def _to_pdf(lines: Sequence[LineInput]) -> str:
    """Produce PDF as base64 encoded string or raw bytes (returned as string for now).
    Actually, to keep ExportResult text string, we'll return base64 encoded PDF.
    """
    import base64
    from fpdf import FPDF
    
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("helvetica", size=12)
    
    text = _to_plain_text(lines)
    for line in text.split("\n"):
        pdf.multi_cell(0, 10, text=line)
        
    pdf_bytes = pdf.output()
    return base64.b64encode(pdf_bytes).decode('utf-8')


def export(
    lines: Sequence[LineInput],
    format: Literal["plain", "markdown", "pdf"],  # noqa: A002  (shadows builtin 'format')
) -> ExportResult:
    """Export *lines* as plain text or markdown.

    This is the single public entry point for the export logic.  It is
    deliberately synchronous and has no I/O: callers supply the ready-to-export
    lines, making the function trivially testable.

    Parameters
    ----------
    lines:
        Ordered sequence of :class:`LineInput` objects representing the current
        (corrected) content.  Must already be in reading order.
    format:
        ``"plain"`` for plain text (Req 8.1) or ``"markdown"`` for markdown
        output (Req 8.2).

    Returns
    -------
    ExportResult
        * :class:`ExportSuccess` — output text produced.
        * :class:`ExportEmpty`   — document is empty; no output (Req 8.5).
        * :class:`ExportError`   — unexpected failure; text unchanged (Req 8.6).
    """
    try:
        # Req 8.5: empty document → nothing-to-export message, no output.
        if _is_document_empty(lines):
            return ExportEmpty()

        if format == "plain":
            text = _to_plain_text(lines)
        elif format == "markdown":
            text = _to_markdown(lines)
        elif format == "pdf":
            text = _to_pdf(lines)
        else:
            raise ValueError(f"Unknown export format: {format}")

        return ExportSuccess(text=text, format=format)

    except Exception as exc:  # Req 8.6: unexpected failure
        return ExportError(message=f"Export failed: {exc}")


# --------------------------------------------------------------------------- #
# FastAPI endpoint
# --------------------------------------------------------------------------- #

router = APIRouter(tags=["export"])


# ---- Request / response schemas ------------------------------------------ #


class ExportFormat(str, Enum):
    PLAIN = "plain"
    MARKDOWN = "markdown"
    PDF = "pdf"


class ExportRequest(BaseModel):
    """Request payload for exporting lines as text (Req 8)."""

    image_id: uuid.UUID
    format: Literal["plain", "markdown", "pdf"] = "plain"


class ExportSuccessResponse(BaseModel):
    """Returned when export produces output."""

    status: str = "success"
    format: str
    text: str


class ExportEmptyResponse(BaseModel):
    """Returned when there is no text to export (Req 8.5)."""

    status: str = "empty"
    message: str


class ExportErrorResponse(BaseModel):
    """Returned when the export attempt fails (Req 8.6)."""

    status: str = "error"
    message: str


# ---- POST /export --------------------------------------------------------- #


@router.post(
    "/export",
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_200_OK: {
            "description": "Export succeeded, is empty, or failed with an error message"
        }
    },
    summary="Export recognized/corrected text as plain text or markdown",
    description=(
        "Reads the current recognized words for the requested image "
        "(including all confirmed corrections) and returns them as plain text "
        "or markdown.  The export uses text as it stands at request time and "
        "never waits for adapter training (Req 8.3, 8.4).  "
        "Requires authentication. Returns 503 when the export capability is "
        "unavailable (Req 11.6)."
    ),
)
def export_text(
    body: ExportRequest,
    _guard: None = Depends(require_capability("export")),
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
) -> ExportSuccessResponse | ExportEmptyResponse | ExportErrorResponse:
    """POST /export — export text for an image (Req 8.1–8.6).

    Scoped to the authenticated user: returns 404 for images owned by others
    (Req 10.3, 10.5).
    """
    # Ownership check: raises 404 if the image doesn't exist or belongs to
    # another user (Req 10.5).
    image = get_or_404(repos.images.get(user.user_id, body.image_id))

    # Fetch words in reading order (line_index asc, word_index asc) — the
    # repository guarantees this order.  These words already reflect all
    # confirmed corrections stored at the time of the request (Req 8.3, 8.4).
    word_rows = repos.recognized_words.list_for_image(user.user_id, image.image_id)

    # Group into lines.
    lines_by_index: dict[int, list[str]] = {}
    for row in word_rows:
        lines_by_index.setdefault(row.line_index, []).append(row.text)

    line_inputs: list[LineInput] = [
        LineInput(words=lines_by_index[idx])
        for idx in sorted(lines_by_index)
    ]

    result = export(line_inputs, format=body.format)

    if isinstance(result, ExportSuccess):
        return ExportSuccessResponse(format=result.format, text=result.text)
    if isinstance(result, ExportEmpty):
        return ExportEmptyResponse(message=result.message)
    # ExportError
    return ExportErrorResponse(message=result.message)


__all__ = [
    "router",
    "export",
    "LineInput",
    "ExportSuccess",
    "ExportEmpty",
    "ExportError",
    "ExportResult",
    "ExportRequest",
    "ExportSuccessResponse",
    "ExportEmptyResponse",
    "ExportErrorResponse",
    "ExportFormat",
    "NO_TEXT_TO_EXPORT_MESSAGE",
]
