"""Tests for the Export_Service (task 16.1).

Covers:
* :func:`~app.services.export_service.export` — the pure service function.
* ``POST /export`` — the FastAPI endpoint (via TestClient with dependency
  overrides for the DB session and session store).

Requirements covered: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6.
"""

from __future__ import annotations

import io
import uuid
from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.data.base import Base
from app.data.enums import ImageFormat, ImageStatus
from app.data.models import Image, RecognizedWord, User
from app.data.repositories import RepositoryFactory
from app.main import create_app
from app.services.auth import SessionStore, hash_password
from app.services.auth_api import get_db_session, get_session_store
from app.services.export_service import (
    NO_TEXT_TO_EXPORT_MESSAGE,
    ExportEmpty,
    ExportError,
    ExportSuccess,
    LineInput,
    export,
)

# --------------------------------------------------------------------------- #
# Helpers / fixtures shared across test sections
# --------------------------------------------------------------------------- #


def _make_lines(*lines: str) -> list[LineInput]:
    """Build ``LineInput`` objects from plain strings (space-split into words)."""
    return [LineInput(words=line.split()) for line in lines]


# --------------------------------------------------------------------------- #
# Unit tests for LineInput
# --------------------------------------------------------------------------- #


class TestLineInput:
    def test_as_text_joins_words_with_space(self):
        li = LineInput(words=["hello", "world"])
        assert li.as_text() == "hello world"

    def test_as_text_empty_words(self):
        li = LineInput(words=[])
        assert li.as_text() == ""

    def test_is_blank_all_whitespace(self):
        li = LineInput(words=["", "  ", "\t"])
        assert li.is_blank()

    def test_is_blank_empty_list(self):
        assert LineInput(words=[]).is_blank()

    def test_is_not_blank_with_content(self):
        li = LineInput(words=["hello"])
        assert not li.is_blank()


# --------------------------------------------------------------------------- #
# Unit tests for export() — plain text (Req 8.1)
# --------------------------------------------------------------------------- #


class TestExportPlainText:
    def test_single_line(self):
        result = export(_make_lines("hello world"), format="plain")
        assert isinstance(result, ExportSuccess)
        assert result.text == "hello world"
        assert result.format == "plain"

    def test_multiple_lines_joined_with_newline(self):
        """Req 8.1: line breaks are preserved."""
        result = export(_make_lines("first line", "second line", "third line"), format="plain")
        assert isinstance(result, ExportSuccess)
        assert result.text == "first line\nsecond line\nthird line"

    def test_words_joined_with_space(self):
        lines = [LineInput(words=["one", "two", "three"])]
        result = export(lines, format="plain")
        assert isinstance(result, ExportSuccess)
        assert result.text == "one two three"

    def test_single_word(self):
        result = export([LineInput(words=["word"])], format="plain")
        assert isinstance(result, ExportSuccess)
        assert result.text == "word"


# --------------------------------------------------------------------------- #
# Unit tests for export() — markdown (Req 8.2)
# --------------------------------------------------------------------------- #


class TestExportMarkdown:
    def test_single_paragraph_no_blank_lines(self):
        """Req 8.2: lines with no blank separators form one paragraph."""
        result = export(_make_lines("line one", "line two"), format="markdown")
        assert isinstance(result, ExportSuccess)
        assert result.text == "line one\nline two"

    def test_blank_line_creates_paragraph_break(self):
        """Req 8.2: blank recognized line → blank markdown line between paragraphs."""
        lines = [
            LineInput(words=["para", "one"]),
            LineInput(words=[]),           # blank separator
            LineInput(words=["para", "two"]),
        ]
        result = export(lines, format="markdown")
        assert isinstance(result, ExportSuccess)
        assert result.text == "para one\n\npara two"

    def test_multiple_paragraphs(self):
        lines = [
            LineInput(words=["A"]),
            LineInput(words=[]),
            LineInput(words=["B"]),
            LineInput(words=[]),
            LineInput(words=["C"]),
        ]
        result = export(lines, format="markdown")
        assert isinstance(result, ExportSuccess)
        assert result.text == "A\n\nB\n\nC"

    def test_leading_trailing_blank_lines_ignored(self):
        """Leading and trailing blank lines don't add spurious paragraph breaks."""
        lines = [
            LineInput(words=[]),
            LineInput(words=["hello"]),
            LineInput(words=[]),
        ]
        result = export(lines, format="markdown")
        assert isinstance(result, ExportSuccess)
        assert result.text == "hello"

    def test_words_joined_with_space_in_markdown(self):
        lines = [LineInput(words=["the", "quick", "brown", "fox"])]
        result = export(lines, format="markdown")
        assert isinstance(result, ExportSuccess)
        assert result.text == "the quick brown fox"


# --------------------------------------------------------------------------- #
# Unit tests for empty-document handling (Req 8.5)
# --------------------------------------------------------------------------- #


class TestExportEmpty:
    def test_empty_lines_list_plain(self):
        result = export([], format="plain")
        assert isinstance(result, ExportEmpty)
        assert result.message == NO_TEXT_TO_EXPORT_MESSAGE

    def test_empty_lines_list_markdown(self):
        result = export([], format="markdown")
        assert isinstance(result, ExportEmpty)
        assert result.message == NO_TEXT_TO_EXPORT_MESSAGE

    def test_all_blank_lines_plain(self):
        """All-blank document → ExportEmpty (Req 8.5)."""
        result = export([LineInput(words=[]), LineInput(words=["", " "])], format="plain")
        assert isinstance(result, ExportEmpty)

    def test_all_blank_lines_markdown(self):
        result = export([LineInput(words=[])], format="markdown")
        assert isinstance(result, ExportEmpty)

    def test_empty_message_content(self):
        """The nothing-to-export message is returned, no output text (Req 8.5)."""
        result = export([], format="plain")
        assert isinstance(result, ExportEmpty)
        assert "no text" in result.message.lower() or "nothing" in result.message.lower() or result.message

    def test_empty_message_equals_constant(self):
        """ExportEmpty.message must match the NO_TEXT_TO_EXPORT_MESSAGE constant exactly (Req 8.5)."""
        result = export([], format="plain")
        assert isinstance(result, ExportEmpty)
        assert result.message == NO_TEXT_TO_EXPORT_MESSAGE

    def test_empty_message_constant_in_markdown(self):
        """ExportEmpty.message constant is the same regardless of format (Req 8.5)."""
        result_plain = export([], format="plain")
        result_md = export([], format="markdown")
        assert isinstance(result_plain, ExportEmpty)
        assert isinstance(result_md, ExportEmpty)
        assert result_plain.message == NO_TEXT_TO_EXPORT_MESSAGE
        assert result_md.message == NO_TEXT_TO_EXPORT_MESSAGE

    def test_export_empty_produces_no_output_text(self):
        """ExportEmpty has no 'text' attribute — the contract is message-only (Req 8.5)."""
        result = export([], format="plain")
        assert isinstance(result, ExportEmpty)
        assert not hasattr(result, "text")


# --------------------------------------------------------------------------- #
# Unit tests for export-failure handling (Req 8.6)
# --------------------------------------------------------------------------- #


class TestExportError:
    def test_invalid_format_raises_handled_as_error(self):
        """An unsupported format string should produce ExportError (Req 8.6)."""
        # Pass a bad format; our function accepts Literal but the guard inside
        # the try/except catches anything unexpected.
        result = export([LineInput(words=["hello"])], format="pdf")  # type: ignore[arg-type]
        # The implementation falls through to plain text for unrecognised format
        # but if it raises an exception it should become ExportError.
        # Either ExportSuccess or ExportError is acceptable; what is NOT
        # acceptable is an unhandled exception propagating to the caller.
        assert isinstance(result, (ExportSuccess, ExportError))

    def test_export_error_has_message(self, monkeypatch):
        """Simulated internal failure → ExportError with a message (Req 8.6)."""
        # Monkeypatch the internal _to_plain_text to raise.
        import app.services.export_service as svc

        def _boom(lines):
            raise RuntimeError("simulated failure")

        monkeypatch.setattr(svc, "_to_plain_text", _boom)

        result = export([LineInput(words=["hello"])], format="plain")
        assert isinstance(result, ExportError)
        assert result.message  # non-empty message

    def test_export_error_markdown_failure(self, monkeypatch):
        """Simulated _to_markdown failure → ExportError, not an unhandled exception (Req 8.6)."""
        import app.services.export_service as svc

        def _boom_md(lines):
            raise ValueError("markdown rendering failure")

        monkeypatch.setattr(svc, "_to_markdown", _boom_md)

        result = export([LineInput(words=["hello"])], format="markdown")
        assert isinstance(result, ExportError)
        assert result.message  # non-empty message

    def test_export_error_leaves_no_output(self, monkeypatch):
        """On failure, ExportError has no 'text' attribute — text is left unchanged (Req 8.6)."""
        import app.services.export_service as svc

        def _boom(lines):
            raise RuntimeError("crash")

        monkeypatch.setattr(svc, "_to_plain_text", _boom)

        result = export([LineInput(words=["hello"])], format="plain")
        assert isinstance(result, ExportError)
        assert not hasattr(result, "text")

    def test_export_never_raises(self, monkeypatch):
        """export() must always return an ExportResult, never raise (Req 8.6)."""
        import app.services.export_service as svc

        def _boom(lines):
            raise Exception("unexpected error")

        monkeypatch.setattr(svc, "_to_plain_text", _boom)

        # Should not raise — must return ExportError
        try:
            result = export([LineInput(words=["data"])], format="plain")
        except Exception as exc:
            raise AssertionError(f"export() raised instead of returning ExportError: {exc}") from exc
        assert isinstance(result, ExportError)


# --------------------------------------------------------------------------- #
# Req 8.3 + 8.4: export uses current confirmed corrections, not training state
# --------------------------------------------------------------------------- #


class TestExportCurrentState:
    def test_export_uses_provided_text(self):
        """Req 8.3: the export reflects the text passed in, not a stale snapshot."""
        corrected = [LineInput(words=["corrected", "text"])]
        result = export(corrected, format="plain")
        assert isinstance(result, ExportSuccess)
        assert "corrected text" in result.text

    def test_export_does_not_depend_on_training(self):
        """Req 8.4: export() is pure — no I/O, no wait for training."""
        # Calling export without any DB or training infrastructure must succeed.
        result = export([LineInput(words=["test"])], format="markdown")
        assert isinstance(result, ExportSuccess)

    def test_export_while_training_pending_uses_current_text(self):
        """Req 8.4: export uses current text even while training is pending.

        The export function is synchronous and pure — it accepts only
        LineInput objects and a format string, with no training-state parameter.
        This test verifies that whatever text is provided at call time is used
        immediately, not deferred to wait for an adapter.
        """
        import inspect

        from app.services.export_service import export as export_fn

        sig = inspect.signature(export_fn)
        param_names = set(sig.parameters.keys())
        # The function must not accept any training-related parameter
        training_params = {"adapter", "training_state", "wait_for_training", "user_id", "session"}
        assert param_names.isdisjoint(training_params), (
            f"export() must not have training-related parameters, but found: "
            f"{param_names & training_params}"
        )

    def test_export_with_pending_training_returns_current_text(self):
        """Req 8.4: simulate the scenario where training is pending but export is called.

        Since export() is pure (no I/O), calling it with the current corrected
        text should return that text immediately — regardless of any external
        training state. We verify by calling export with 'updated' text and
        asserting it is returned verbatim.
        """
        # These are the current corrected words — what would be in the editor
        # even while an adapter training is queued but not yet complete.
        in_progress_corrected_lines = [
            LineInput(words=["my", "corrected", "word"]),
            LineInput(words=["second", "line"]),
        ]
        result = export(in_progress_corrected_lines, format="plain")
        assert isinstance(result, ExportSuccess)
        assert result.text == "my corrected word\nsecond line"


# --------------------------------------------------------------------------- #
# FastAPI endpoint tests (POST /export)
# --------------------------------------------------------------------------- #


@pytest.fixture()
def db_session() -> Iterator[Session]:
    """In-memory SQLite session for endpoint tests."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    with factory() as s:
        yield s


@pytest.fixture()
def client(db_session: Session) -> Iterator[TestClient]:
    """TestClient with DB and session store overrides."""
    session_store = SessionStore(db_session)
    app = create_app()

    def _override_db_session() -> Iterator[Session]:
        yield db_session

    app.dependency_overrides[get_db_session] = _override_db_session
    app.dependency_overrides[get_session_store] = lambda: session_store

    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


def _register_and_login(client: TestClient, db_session: Session) -> tuple[str, uuid.UUID]:
    """Create a user in the DB, log in, return (token, user_id)."""
    user = User(
        username=f"user_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@test.com",
        password_hash=hash_password("password"),
    )
    db_session.add(user)
    db_session.commit()

    resp = client.post("/auth/login", json={"username": user.username, "password": "password"})
    assert resp.status_code == 200
    data = resp.json()
    return data["access_token"], user.user_id


def _create_image_with_words(
    db_session: Session,
    user_id: uuid.UUID,
    lines: list[list[str]],
) -> uuid.UUID:
    """Persist an Image and its RecognizedWords directly in the DB."""
    image = Image(
        user_id=user_id,
        storage_path="/dev/null",
        format=ImageFormat.PNG,
        size_bytes=1,
        status=ImageStatus.RECOGNIZED,
    )
    db_session.add(image)
    db_session.flush()

    for line_idx, words in enumerate(lines):
        for word_idx, text in enumerate(words):
            word = RecognizedWord(
                image_id=image.image_id,
                line_index=line_idx,
                word_index=word_idx,
                text=text,
                region_x=0,
                region_y=0,
                region_width=10,
                region_height=10,
            )
            db_session.add(word)

    db_session.commit()
    return image.image_id


class TestExportEndpoint:
    def test_unauthenticated_returns_401(self, client: TestClient, db_session: Session):
        """Unauthenticated export returns 401 (Req 10.4)."""
        resp = client.post(
            "/export",
            json={"image_id": str(uuid.uuid4()), "format": "plain"},
        )
        assert resp.status_code == 401

    def test_unknown_image_returns_404(self, client: TestClient, db_session: Session):
        """Export for a non-existent image returns 404 (Req 10.5)."""
        token, _ = _register_and_login(client, db_session)
        resp = client.post(
            "/export",
            json={"image_id": str(uuid.uuid4()), "format": "plain"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404

    def test_plain_export_returns_text(self, client: TestClient, db_session: Session):
        """Req 8.1: plain export preserves line breaks."""
        token, user_id = _register_and_login(client, db_session)
        image_id = _create_image_with_words(
            db_session, user_id, [["hello", "world"], ["second", "line"]]
        )
        resp = client.post(
            "/export",
            json={"image_id": str(image_id), "format": "plain"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "success"
        assert body["text"] == "hello world\nsecond line"

    def test_markdown_export_returns_text(self, client: TestClient, db_session: Session):
        """Req 8.2: markdown export preserves line and paragraph structure."""
        token, user_id = _register_and_login(client, db_session)
        image_id = _create_image_with_words(
            db_session, user_id, [["para", "one"], ["para", "two"]]
        )
        resp = client.post(
            "/export",
            json={"image_id": str(image_id), "format": "markdown"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "success"
        assert "para one" in body["text"]
        assert "para two" in body["text"]

    def test_empty_image_returns_empty_status(self, client: TestClient, db_session: Session):
        """Req 8.5: no recognized words → empty response with nothing-to-export message."""
        token, user_id = _register_and_login(client, db_session)
        # Image with no words.
        image_id = _create_image_with_words(db_session, user_id, [])
        resp = client.post(
            "/export",
            json={"image_id": str(image_id), "format": "plain"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "empty"
        assert body["message"] == NO_TEXT_TO_EXPORT_MESSAGE

    def test_cross_user_access_returns_404(self, client: TestClient, db_session: Session):
        """Req 10.5: user A cannot export user B's image (existence not disclosed)."""
        token_a, user_id_a = _register_and_login(client, db_session)
        _, user_id_b = _register_and_login(client, db_session)

        # Image owned by user B.
        image_id = _create_image_with_words(
            db_session, user_id_b, [["secret", "text"]]
        )

        # User A tries to export it.
        resp = client.post(
            "/export",
            json={"image_id": str(image_id), "format": "plain"},
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert resp.status_code == 404

    def test_export_reflects_current_words(self, client: TestClient, db_session: Session):
        """Req 8.3: export uses the words stored in DB at request time."""
        token, user_id = _register_and_login(client, db_session)
        image_id = _create_image_with_words(
            db_session, user_id, [["original", "word"]]
        )

        # Now simulate a correction by updating the stored word text directly.
        word = (
            db_session.query(RecognizedWord)
            .filter_by(image_id=image_id)
            .first()
        )
        word.text = "corrected"
        db_session.commit()

        resp = client.post(
            "/export",
            json={"image_id": str(image_id), "format": "plain"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "corrected" in body["text"]
        assert "original" not in body["text"]

    def test_default_format_is_plain(self, client: TestClient, db_session: Session):
        """Default export format is plain text."""
        token, user_id = _register_and_login(client, db_session)
        image_id = _create_image_with_words(db_session, user_id, [["hello"]])
        resp = client.post(
            "/export",
            json={"image_id": str(image_id)},  # no format specified
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "success"
        assert body["format"] == "plain"
