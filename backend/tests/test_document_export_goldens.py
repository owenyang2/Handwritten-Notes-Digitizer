"""Golden coverage for the reviewed-document export contract."""

from __future__ import annotations

import io
import zipfile

from PIL import Image

from app.data.object_store import FilesystemObjectStore
from app.services.document_export import (
    DocumentBlock,
    _render_latex_bundle,
    _render_pdf_bytes,
    render_html,
    render_latex,
    render_markdown,
)


def _figure_png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (20, 40, 80)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_text_math_unicode_matrix_and_exclusion_golden() -> None:
    blocks = [
        DocumentBlock(kind="text", text="Σ café naïve & 50%", page_index=0),
        DocumentBlock(
            kind="math",
            text=r"\begin{bmatrix} 1 & 2 \\ 3 & 4 \end{bmatrix}",
            page_index=0,
        ),
        DocumentBlock(kind="math_inline", text=r"x \times y", page_index=0),
        DocumentBlock(kind="text", text="This must not be exported", excluded=True),
    ]

    assert render_markdown(blocks) == (
        "Σ café naïve & 50%\n\n"
        "$$\n\\begin{bmatrix} 1 & 2 \\\\ 3 & 4 \\end{bmatrix}\n$$\n\n"
        "\\(x \\times y\\)"
    )
    latex = render_latex(blocks)
    assert r"Σ café naïve \& 50\%" in latex
    assert r"\[" in latex and r"\begin{bmatrix}" in latex
    assert "This must not be exported" not in latex
    html = render_html(blocks)
    assert "Σ café naïve &amp; 50%" in html
    assert r"\\begin{bmatrix}" not in html  # math is escaped for HTML text
    assert "This must not be exported" not in html


def test_malicious_latex_is_never_executed_by_controlled_renderers() -> None:
    malicious = r"\input{/etc/passwd} \write18{touch /tmp/hnd-export-pwned} <script>alert(1)</script>"
    block = DocumentBlock(kind="math", text=malicious)

    # Source LaTeX is preserved for an explicit .tex export, but the
    # application never invokes a TeX process during HTML/PDF rendering.
    assert r"\input{/etc/passwd}" in render_latex([block])
    html = render_html([block])
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    pdf = _render_pdf_bytes([block])
    assert pdf.startswith(b"%PDF")


def test_figure_bundle_golden_retains_the_original_asset(tmp_path) -> None:
    store = FilesystemObjectStore(root=tmp_path / "objects")
    reference = store.put_region_crop("golden-user", _figure_png(), name="figure-1", suffix=".png")
    block = DocumentBlock(
        kind="figure",
        text="",
        source_id="figure-1",
        asset_ref=reference,
    )

    payload = _render_latex_bundle([block], store)
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        assert archive.namelist() == ["document.tex", "assets/figure-1.png"]
        assert archive.read("assets/figure-1.png") == _figure_png()
        assert r"\includegraphics{assets/figure-1.png}" in archive.read(
            "document.tex"
        ).decode("utf-8")


def test_pdf_golden_paginates_long_content_and_preserves_order() -> None:
    blocks = [
        DocumentBlock(
            kind="text",
            text=f"Page-order paragraph {index}: " + ("long content " * 32),
            page_index=index,
            order_index=0,
        )
        for index in range(80)
    ]
    payload = _render_pdf_bytes(blocks)
    assert payload.startswith(b"%PDF")

    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(payload)
    assert len(document) > 1
    text = "\n".join(
        document[index].get_textpage().get_text_bounded() for index in range(len(document))
    )
    assert "Page-order paragraph 0" in text
    assert "Page-order paragraph 79" in text
