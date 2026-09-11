"""Regressions for math-notes reliability and the compact formula model."""

from __future__ import annotations

import math
import json
import uuid
from unittest.mock import MagicMock

import pytest
import torch
from sqlalchemy.pool import StaticPool

from app.data import (
    Image,
    ImageFormat,
    ImageStatus,
    DocumentPage,
    DocumentRegion,
    RegionPrediction,
    RecognizedWord,
    RepositoryFactory,
    TrainingPair,
    TrainingPairOrigin,
    User,
    create_all,
    create_db_engine,
    create_session_factory,
)
from app.ml.general_eval_set import load_verified_eval_pairs
from app.ml.math.model import CompactMathConfig, CompactMathModel
from app.ml.math.tokenizer import LatexTokenizer, UnsupportedLatexError
from app.ml.recognition import ModelUnavailableError, get_base_model, get_loaded_model_status
from app.ml.train_base import UnsupportedLabelError, encode_text
from app.services.personalization_service import complete_training_run


def test_encode_text_rejects_unsupported_characters():
    with pytest.raises(UnsupportedLabelError):
        encode_text("a=b")
    with pytest.raises(UnsupportedLabelError):
        encode_text("a\tb")
    assert encode_text("abc") == [encode_text("a")[0], encode_text("b")[0], encode_text("c")[0]]
    assert encode_text("") == []


def test_get_base_model_does_not_fall_back_to_random_weights(tmp_path, monkeypatch):
    monkeypatch.setenv("HND_OBJECT_STORE_PATH", str(tmp_path / "objectstore"))
    from app.config.settings import get_settings

    get_settings.cache_clear()
    with pytest.raises(ModelUnavailableError):
        get_base_model(force_reload=True)
    status = get_loaded_model_status()
    assert status.loaded is False
    assert status.health_label == "unavailable"
    from app.ml.recognition import reset_base_model_runtime

    reset_base_model_runtime()
    get_settings.cache_clear()


def test_verified_eval_pairs_are_empty_without_fixtures_or_synthetic_flag(tmp_path):
    pairs = load_verified_eval_pairs(allow_synthetic=False, fixtures_dir=tmp_path)
    assert pairs == []


def test_synthetic_eval_pairs_only_when_explicitly_enabled():
    pairs = load_verified_eval_pairs(allow_synthetic=True)
    assert len(pairs) > 0
    assert pairs[0][1]


def test_zero_cer_is_a_valid_guardrail_metric(monkeypatch):
    class Report:
        before_cer = 0.0
        before_wer = 0.0
        after_cer = 0.0
        after_wer = 0.0
        delta_cer = 0.0
        delta_wer = 0.0
        availability = "available"

    class AdapterResult:
        classifier_weight = None
        classifier_bias = None
        epochs_run = 1
        cer_history: list[float] = []

    monkeypatch.setattr(
        "app.services.personalization_service.adapter_result_to_bytes",
        lambda _result: b"weights",
    )
    repos = MagicMock()
    repos.training_runs.get.return_value = None
    repos.adapters.get_for_user.return_value = None

    def _add(adapter):
        adapter.adapter_id = uuid.uuid4()
        return adapter

    repos.adapters.add.side_effect = _add
    store = MagicMock()
    store.put_adapter_weights.return_value = "adapters/user/a.pt"
    outcome = complete_training_run(
        uuid.uuid4(),
        uuid.uuid4(),
        AdapterResult(),
        Report(),
        repos,
        store,
    )
    assert outcome.accepted is True
    assert outcome.run_final_state.value == "accepted"


def test_nonfinite_guardrail_metric_cannot_promote():
    class Report:
        before_cer = math.nan
        before_wer = 0.0
        after_cer = 0.0
        after_wer = 0.0
        availability = "available"

    class AdapterResult:
        classifier_weight = None
        classifier_bias = None

    repos = MagicMock()
    repos.training_runs.get.return_value = None
    repos.adapters.get_for_user.return_value = None
    outcome = complete_training_run(
        uuid.uuid4(),
        uuid.uuid4(),
        AdapterResult(),
        Report(),
        repos,
        MagicMock(),
    )
    assert outcome.accepted is False
    assert outcome.run_final_state.value == "failed"


def test_latex_tokenizer_round_trips_accepted_formulas():
    tokenizer = LatexTokenizer.default()
    sample = r"\frac{d}{dx}x^2=2x"
    assert tokenizer.round_trip(sample) == sample
    with pytest.raises(UnsupportedLatexError):
        tokenizer.encode(r"\notacommand{x}")


def test_compact_model_forward_and_greedy_shapes():
    config = CompactMathConfig(d_model=32, nhead=4, num_layers=1, image_height=32, max_tokens=8)
    tokenizer = LatexTokenizer.default()
    model = CompactMathModel(tokenizer=tokenizer, config=config)
    images = torch.rand(1, 1, 32, 64)
    tokens = torch.tensor([tokenizer.encode(r"x^2=4")], dtype=torch.long)
    logits = model(images, tokens[:, :-1])
    assert logits.shape[0] == 1
    assert logits.shape[-1] == tokenizer.vocab_size
    texts, truncated = model.greedy_decode(images, max_tokens=4)
    assert len(texts) == 1
    assert isinstance(truncated[0], bool)


def test_coverage_variant_forward_shapes():
    config = CompactMathConfig(
        d_model=32, nhead=4, num_layers=1, image_height=32, max_tokens=8, coverage=True
    )
    tokenizer = LatexTokenizer.default()
    model = CompactMathModel(tokenizer=tokenizer, config=config)
    images = torch.rand(1, 1, 32, 64)
    tokens = torch.tensor([tokenizer.encode(r"x^2=4")], dtype=torch.long)
    logits = model(images, tokens[:, :-1])
    assert logits.shape[-1] == tokenizer.vocab_size


def test_compact_engine_unavailable_without_checkpoint(tmp_path):
    from app.ml.backends.compact import CompactMathEngine

    engine = CompactMathEngine(checkpoint=tmp_path / "missing.pt")
    assert engine.is_available() is False
    result = engine.recognize_crop(b"\x89PNG\r\n\x1a\n")
    assert result.available is False


def test_formula_engine_falls_back_when_optional_backend_is_present_but_unavailable(monkeypatch):
    from app.ml.backends.base import EngineCandidate, EngineResult
    from app.services import math_regions_api

    class UnavailableSnapshot:
        def is_available(self):
            return True

        def recognize_crop(self, _crop):
            return EngineResult(
                engine_id="unimernet-small",
                engine_version="snapshot",
                available=False,
                reason="adapter not wired",
            )

    class LoadedCompact:
        def is_available(self):
            return True

        def recognize_crop(self, _crop):
            return EngineResult(
                engine_id="compact-math",
                engine_version="test",
                candidates=[EngineCandidate(text=r"x^2", kind="latex")],
            )

    monkeypatch.setattr(math_regions_api, "UniMERNetEngine", UnavailableSnapshot)
    monkeypatch.setattr(math_regions_api, "CompactMathEngine", LoadedCompact)
    result = math_regions_api.recognize_formula_crop(b"crop")
    assert result.available is True
    assert result.engine_id == "compact-math"
    assert result.text == r"x^2"


def test_document_export_uses_region_reviews_and_skips_excluded():
    from app.services.document_export import DocumentBlock, render_markdown, render_html, render_latex

    blocks = [
        DocumentBlock(kind="text", text="prose", excluded=False, page_index=0, order_index=0),
        DocumentBlock(kind="math", text=r"x^2=4", excluded=False, page_index=0, order_index=1),
        DocumentBlock(kind="math", text="skip", excluded=True, page_index=0, order_index=2),
    ]
    markdown = render_markdown(blocks)
    assert "prose" in markdown
    assert "x^2=4" in markdown
    assert "skip" not in markdown
    html = render_html(blocks)
    assert "<p>prose</p>" in html
    assert "<img" not in html
    latex = render_latex(blocks)
    assert r"\begin{document}" in latex
    assert r"x^2=4" in latex


def test_pdf_rasterize_round_trip():
    pytest.importorskip("pypdfium2")
    from fpdf import FPDF

    from app.services.pdf_import import rasterize_pdf

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("helvetica", size=12)
    pdf.cell(40, 10, "Hello")
    pages = rasterize_pdf(bytes(pdf.output()), dpi=72, max_pages=2)
    assert len(pages) == 1
    assert pages[0].png_bytes[:8] == b"\x89PNG\r\n\x1a\n"
    assert pages[0].width > 0 and pages[0].height > 0


def test_pdf_rasterize_rejects_oversized_page_before_render():
    pytest.importorskip("pypdfium2")
    from fpdf import FPDF

    from app.services.pdf_import import PdfImportError, rasterize_pdf

    pdf = FPDF(unit="pt", format=(1_000, 1_000))
    pdf.add_page()
    with pytest.raises(PdfImportError, match="pixel limit"):
        rasterize_pdf(bytes(pdf.output()), dpi=72, max_pages=2, max_pixels=10_000)


def test_pdf_rasterize_rejects_non_positive_limits():
    pytest.importorskip("pypdfium2")
    from fpdf import FPDF

    from app.services.pdf_import import PdfImportError, rasterize_pdf

    pdf = FPDF()
    pdf.add_page()
    with pytest.raises(PdfImportError, match="must be positive"):
        rasterize_pdf(bytes(pdf.output()), dpi=0)


def test_recognition_replace_does_not_duplicate_rows():
    engine = create_db_engine("sqlite://", poolclass=StaticPool)
    create_all(engine)
    factory = create_session_factory(engine)
    with factory() as session:
        user = User(username="r", email="r@example.com", password_hash="x")
        session.add(user)
        session.flush()
        image = Image(
            user_id=user.user_id,
            storage_path="images/r/a.png",
            format=ImageFormat.PNG,
            size_bytes=10,
            status=ImageStatus.RECOGNIZED,
        )
        session.add(image)
        session.flush()
        repos = RepositoryFactory(session)
        first = [
            RecognizedWord(
                image_id=image.image_id,
                line_index=0,
                word_index=0,
                text="one",
                region_x=0,
                region_y=0,
                region_width=1,
                region_height=1,
            )
        ]
        repos.recognized_words.replace_all_for_image(user.user_id, image.image_id, first)
        second = [
            RecognizedWord(
                image_id=image.image_id,
                line_index=0,
                word_index=0,
                text="two",
                region_x=0,
                region_y=0,
                region_width=1,
                region_height=1,
            )
        ]
        repos.recognized_words.replace_all_for_image(user.user_id, image.image_id, second)
        rows = repos.recognized_words.list_for_image(user.user_id, image.image_id)
        assert len(rows) == 1
        assert rows[0].text == "two"


def test_recognition_retry_reapplies_durable_word_corrections():
    from app.services.recognition_service import _saved_word_corrections

    engine = create_db_engine("sqlite://", poolclass=StaticPool)
    create_all(engine)
    factory = create_session_factory(engine)
    with factory() as session:
        user = User(username="corr", email="corr@example.com", password_hash="x")
        session.add(user)
        session.flush()
        image = Image(
            user_id=user.user_id,
            storage_path="images/corr/a.png",
            format=ImageFormat.PNG,
            size_bytes=10,
            status=ImageStatus.RECOGNIZED,
        )
        session.add(image)
        session.flush()
        repos = RepositoryFactory(session)
        region_key = f"{image.image_id}:4:5:40:20"
        repos.training_pairs.add(
            TrainingPair(
                user_id=user.user_id,
                image_id=image.image_id,
                source_region_key=region_key,
                region_crop_path="regions/corr.png",
                corrected_text="corrected",
                origin=TrainingPairOrigin.CORRECTION,
            )
        )
        session.flush()
        assert _saved_word_corrections(repos, user.user_id, image.image_id) == {
            (4, 5, 40, 20): "corrected"
        }


def test_recognition_timeout_reads_settings(monkeypatch):
    from app.config.settings import get_settings
    from app.services.recognition_service import recognition_timeout_seconds

    monkeypatch.setenv("HND_RECOGNITION_TIMEOUT_SECONDS", "3.5")
    get_settings.cache_clear()
    assert recognition_timeout_seconds() == 3.5
    get_settings.cache_clear()


def test_line_reviews_drive_export_and_survive_reload():
    engine = create_db_engine("sqlite://", poolclass=StaticPool)
    create_all(engine)
    factory = create_session_factory(engine)
    with factory() as session:
        user = User(username="rev", email="rev@example.com", password_hash="x")
        session.add(user)
        session.flush()
        image = Image(
            user_id=user.user_id,
            storage_path="images/rev/a.png",
            format=ImageFormat.PNG,
            size_bytes=10,
            status=ImageStatus.RECOGNIZED,
        )
        session.add(image)
        session.flush()
        repos = RepositoryFactory(session)
        repos.recognized_words.add(
            RecognizedWord(
                image_id=image.image_id,
                line_index=0,
                word_index=0,
                text="stale",
                region_x=0,
                region_y=0,
                region_width=1,
                region_height=1,
            )
        )
        repos.recognized_words.add(
            RecognizedWord(
                image_id=image.image_id,
                line_index=1,
                word_index=0,
                text="skip-me",
                region_x=0,
                region_y=10,
                region_width=1,
                region_height=1,
            )
        )
        repos.line_reviews.upsert(user.user_id, image.image_id, 0, "reviewed line", False)
        repos.line_reviews.upsert(user.user_id, image.image_id, 1, "ignored", True)
        session.commit()

        from app.services.export_service import LineInput, export

        reviews = {r.line_index: r for r in repos.line_reviews.list_for_image(user.user_id, image.image_id)}
        words = repos.recognized_words.list_for_image(user.user_id, image.image_id)
        lines_by_index: dict[int, list[str]] = {}
        for row in words:
            lines_by_index.setdefault(row.line_index, []).append(row.text)
        inputs: list[LineInput] = []
        for idx in sorted(set(lines_by_index) | set(reviews)):
            review = reviews.get(idx)
            if review is not None and review.excluded:
                continue
            if review is not None:
                inputs.append(LineInput(words=[review.reviewed_text]))
            else:
                inputs.append(LineInput(words=lines_by_index[idx]))
        result = export(inputs, format="plain")
        assert result.text == "reviewed line"  # type: ignore[attr-defined]


def test_pdf_rasterize_rejects_non_pdf():
    from app.services.pdf_import import PdfImportError, rasterize_pdf

    with pytest.raises(PdfImportError):
        rasterize_pdf(b"not a pdf")


def test_formula_manifest_checks_document_splits_before_filtering(tmp_path):
    from training.datasets import load_formula_records

    manifest = tmp_path / "formulas.jsonl"
    manifest.write_text(
        "\n".join(
            json.dumps(item)
            for item in (
                {"sample_id": "p1-a", "source_document_id": "page-group", "split": "train", "normalizedLabel": "x"},
                {"sample_id": "p1-b", "source_document_id": "page-group", "split": "test", "normalizedLabel": "y"},
            )
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="appears in multiple splits"):
        load_formula_records(manifest, split="train", require_files=False)


def test_document_revision_snapshot_covers_all_pages_and_survives_later_changes():
    from app.services.document_export import assemble_document_blocks
    from app.services.document_review import RegionReviewRequest, post_region_review

    engine = create_db_engine("sqlite://", poolclass=StaticPool)
    create_all(engine)
    factory = create_session_factory(engine)
    with factory() as session:
        user = User(username="snapshot", email="snapshot@example.com", password_hash="x")
        session.add(user)
        session.flush()
        repos = RepositoryFactory(session)
        document = repos.documents.create(user.user_id, "Snapshot")
        page_one = repos.document_pages.add(
            DocumentPage(
                document_id=document.document_id,
                page_index=0,
                raster_path="pages/one.png",
                width=100,
                height=100,
            )
        )
        page_two = repos.document_pages.add(
            DocumentPage(
                document_id=document.document_id,
                page_index=1,
                raster_path="pages/two.png",
                width=100,
                height=100,
            )
        )
        first = repos.document_regions.add(
            DocumentRegion(
                page_id=page_one.page_id,
                order_index=0,
                bbox_x=0,
                bbox_y=0,
                bbox_width=20,
                bbox_height=20,
                source_crop_path="crops/one.png",
                crop_sha256="a" * 64,
            )
        )
        second = repos.document_regions.add(
            DocumentRegion(
                page_id=page_two.page_id,
                order_index=0,
                bbox_x=0,
                bbox_y=0,
                bbox_width=20,
                bbox_height=20,
                source_crop_path="crops/two.png",
                crop_sha256="b" * 64,
            )
        )
        prediction = repos.region_predictions.add(
            RegionPrediction(
                region_id=second.region_id,
                engine_id="fixture",
                raw_output="page-two",
            )
        )
        session.commit()

        first_review = post_region_review(
            first.region_id,
            RegionReviewRequest(reviewed_text="page-one"),
            user=user,
            repos=repos,
        )
        first_revision_id = first_review.document_revision_id
        assert first_revision_id is not None
        revision = repos.document_revisions.get(user.user_id, first_revision_id)
        assert revision is not None
        snapshot = revision.block_snapshot["blocks"]
        assert {item["page_index"] for item in snapshot} == {0, 1}
        assert first_review.review_revision_id is not None
        canonical = repos.review_revisions.get(user.user_id, first_review.review_revision_id)
        assert canonical is not None
        assert canonical.document_revision_id == first_revision_id

        post_region_review(
            first.region_id,
            RegionReviewRequest(reviewed_text="page-one-new", expected_version=1),
            user=user,
            repos=repos,
        )
        repos.document_regions.delete(user.user_id, second.region_id)
        session.commit()
        document = repos.documents.get(user.user_id, document.document_id)
        assert document is not None
        blocks = assemble_document_blocks(user.user_id, document, repos, first_revision_id)
        assert [block.text for block in blocks] == ["page-one", "page-two"]
        assert prediction.prediction_id
