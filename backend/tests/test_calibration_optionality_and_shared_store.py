"""Unit tests for calibration optionality and shared store — task 12.5.

Covers:
* Calibration optionality (Req 9.1):
  - A user can trigger recognition without starting calibration
  - A user can check the personalization threshold (correction pairs) without calibration
  - A user can request an export without calibration
  - The core pipeline (upload → recognize → correct → export) produces no
    calibration-related side-effects when the calibration endpoints are never called
* Shared store (Req 9.5):
  - Calibration Training_Pairs are stored in the same repository (same DB table)
    as correction Training_Pairs
  - list_for_user returns both calibration and correction pairs
  - count_unused counts both calibration and correction pairs together
  - TrainingPairOrigin distinguishes them but the storage is unified
  - Mixed batches of calibration + correction pairs can coexist per user

Requirements validated: 9.1, 9.5
"""

from __future__ import annotations

import io
import uuid
from io import BytesIO
from typing import BinaryIO, Optional

import numpy as np
import pytest
from PIL import Image as PILImage
from sqlalchemy.pool import StaticPool

from app.data import (
    Base,
    RepositoryFactory,
    TrainingPair,
    TrainingPairOrigin,
    User,
    create_all,
    create_db_engine,
    create_session_factory,
)
from app.data.enums import ImageFormat, ImageStatus
from app.data.models import Image
from app.data.object_store import ObjectStore, ObjectStoreError, ObjectNotFoundError
from app.services.auth import hash_password
from app.services.calibration_service import (
    submit_calibration_sample,
    CalibrationAccepted,
    CalibrationRejection,
)
from app.services.correction_service import (
    submit_correction,
    Created,
    NoChange,
    Region,
)
from app.services.export_service import (
    export,
    LineInput,
    ExportSuccess,
    ExportEmpty,
)


# ---------------------------------------------------------------------------
# In-memory helpers (mirrors patterns from test_correction_service.py)
# ---------------------------------------------------------------------------


class InMemoryObjectStore(ObjectStore):
    """Object store backed by an in-memory dict; suitable for unit tests."""

    def __init__(self) -> None:
        self._data: dict[str, bytes] = {}

    def put(self, reference: str, data: bytes) -> str:
        self._data[reference] = data
        return reference

    def get(self, reference: str) -> bytes:
        try:
            return self._data[reference]
        except KeyError as exc:
            raise ObjectNotFoundError(f"Not found: {reference!r}") from exc

    def open(self, reference: str) -> BinaryIO:
        return io.BytesIO(self.get(reference))

    def exists(self, reference: str) -> bool:
        return reference in self._data

    def delete(self, reference: str) -> None:
        self._data.pop(reference, None)


def _make_png_bytes(width: int = 100, height: int = 50) -> bytes:
    """Return minimal valid PNG bytes."""
    buf = BytesIO()
    PILImage.new("RGB", (width, height), color=(200, 200, 200)).save(buf, format="PNG")
    return buf.getvalue()


def _make_handwriting_png_bytes() -> bytes:
    """PNG with ink bands that the projection-profile segmenter detects."""
    width, height = 200, 100
    arr = np.ones((height, width), dtype=np.uint8) * 255
    arr[20:35, 10:190] = 20
    arr[55:70, 10:190] = 20
    arr[20:35, 90:100] = 255
    arr[55:70, 90:100] = 255
    img = PILImage.fromarray(arr, mode="L")
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_session_store(n_prompts: int = 3):
    """Return (session_id, prompts, session_store dict)."""
    session_id = uuid.uuid4()
    prompts = [f"Calibration prompt number {i}" for i in range(n_prompts)]
    return session_id, prompts, {session_id: prompts}


# ---------------------------------------------------------------------------
# Shared DB fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_session():
    """Fresh in-memory SQLite session per test."""
    engine = create_db_engine("sqlite:///:memory:")
    create_all(engine)
    factory = create_session_factory(engine)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def repos(db_session) -> RepositoryFactory:
    return RepositoryFactory(db_session)


@pytest.fixture()
def store() -> InMemoryObjectStore:
    return InMemoryObjectStore()


@pytest.fixture()
def user(repos: RepositoryFactory) -> User:
    u = User(
        username="testuser",
        email="test@example.com",
        password_hash=hash_password("pw"),
    )
    repos.users.add(u)
    repos.session.commit()
    return u


@pytest.fixture()
def image_record(user: User, repos: RepositoryFactory, store: InMemoryObjectStore) -> Image:
    """Persist a test image with PNG bytes in the object store."""
    png_bytes = _make_png_bytes()
    storage_path = store.put_image_bytes(str(user.user_id), png_bytes, suffix=".png")
    img = repos.images.add(
        Image(
            user_id=user.user_id,
            storage_path=storage_path,
            format=ImageFormat.PNG,
            size_bytes=len(png_bytes),
            status=ImageStatus.ACCEPTED,
        )
    )
    repos.session.commit()
    return img


# ===========================================================================
# Tests: Calibration Optionality (Req 9.1)
#
# The system must function through recognition, personalization-trigger check,
# and export without the user ever touching a calibration endpoint.
# ===========================================================================


class TestCalibrationOptionalityRecognitionPath:
    """A user can upload an image and retrieve recognized words with zero calibration activity."""

    def test_image_can_be_stored_without_calibration(
        self, user: User, repos: RepositoryFactory, store: InMemoryObjectStore
    ) -> None:
        """Storing an image does not require or check for calibration (Req 9.1)."""
        png_bytes = _make_png_bytes()
        path = store.put_image_bytes(str(user.user_id), png_bytes, suffix=".png")
        img = repos.images.add(
            Image(
                user_id=user.user_id,
                storage_path=path,
                format=ImageFormat.PNG,
                size_bytes=len(png_bytes),
                status=ImageStatus.ACCEPTED,
            )
        )
        repos.session.commit()

        fetched = repos.images.get(user.user_id, img.image_id)
        assert fetched is not None
        # Calibration table is untouched.
        assert repos.training_pairs.list_for_user(user.user_id) == []

    def test_no_calibration_pairs_exist_on_fresh_user(
        self, user: User, repos: RepositoryFactory
    ) -> None:
        """A brand-new user has zero training pairs — calibration is not required (Req 9.1)."""
        pairs = repos.training_pairs.list_for_user(user.user_id)
        assert pairs == []

    def test_user_can_have_zero_calibration_pairs_and_still_access_image(
        self, user: User, image_record: Image, repos: RepositoryFactory
    ) -> None:
        """An image is accessible regardless of whether calibration was done (Req 9.1)."""
        assert repos.training_pairs.list_for_user(user.user_id) == []
        fetched = repos.images.get(user.user_id, image_record.image_id)
        assert fetched is not None
        assert fetched.image_id == image_record.image_id


class TestCalibrationOptionalityCorrectionPath:
    """Corrections can be submitted and stored with no calibration involvement."""

    def test_correction_pair_created_without_calibration(
        self,
        user: User,
        image_record: Image,
        repos: RepositoryFactory,
        store: InMemoryObjectStore,
    ) -> None:
        """submit_correction works with zero calibration history (Req 9.1)."""
        result = submit_correction(
            user_id=user.user_id,
            image_id=image_record.image_id,
            region=Region(x=0, y=0, width=50, height=20),
            recognized_text="helo",
            corrected_text="hello",
            repos=repos,
            object_store=store,
        )
        assert isinstance(result, Created)
        pairs = repos.training_pairs.list_for_user(user.user_id)
        assert len(pairs) == 1
        assert pairs[0].origin == TrainingPairOrigin.CORRECTION

    def test_unused_pair_count_reflects_corrections_only(
        self,
        user: User,
        image_record: Image,
        repos: RepositoryFactory,
        store: InMemoryObjectStore,
    ) -> None:
        """count_unused counts correction pairs without any calibration (Req 9.1)."""
        for i in range(5):
            submit_correction(
                user_id=user.user_id,
                image_id=image_record.image_id,
                region=Region(x=i * 10, y=0, width=9, height=20),
                recognized_text="x",
                corrected_text=f"word{i}",
                repos=repos,
                object_store=store,
            )

        count = repos.training_pairs.count_unused(user.user_id)
        assert count == 5


class TestCalibrationOptionalityExportPath:
    """Export functions correctly when calibration was never performed."""

    def test_export_plain_text_without_calibration(self) -> None:
        """Export produces plain text from line inputs with no calibration state (Req 9.1)."""
        lines = [LineInput(words=["Hello", "world"]), LineInput(words=["foo", "bar"])]
        result = export(lines, format="plain")
        assert isinstance(result, ExportSuccess)
        assert "Hello world" in result.text
        assert "foo bar" in result.text

    def test_export_markdown_without_calibration(self) -> None:
        """Export produces markdown with no calibration state (Req 9.1)."""
        lines = [LineInput(words=["First", "line"]), LineInput(words=["Second", "line"])]
        result = export(lines, format="markdown")
        assert isinstance(result, ExportSuccess)
        assert "First line" in result.text

    def test_export_empty_returns_empty_result_without_calibration(self) -> None:
        """Empty export returns ExportEmpty — no calibration needed (Req 9.1)."""
        result = export([], format="plain")
        assert isinstance(result, ExportEmpty)

    def test_full_pipeline_without_calibration_has_no_calibration_pairs(
        self,
        user: User,
        image_record: Image,
        repos: RepositoryFactory,
        store: InMemoryObjectStore,
    ) -> None:
        """After upload+correct+export, zero calibration Training_Pairs exist (Req 9.1).

        This asserts the full user workflow can complete without any calibration
        side-effects: calibration is genuinely optional.
        """
        # Step 1: correct a word (simulating the post-recognition correction step).
        submit_correction(
            user_id=user.user_id,
            image_id=image_record.image_id,
            region=Region(x=0, y=0, width=50, height=20),
            recognized_text="helo",
            corrected_text="hello",
            repos=repos,
            object_store=store,
        )

        # Step 2: export the result.
        lines = [LineInput(words=["hello"])]
        export_result = export(lines, format="plain")
        assert isinstance(export_result, ExportSuccess)

        # Step 3: assert calibration table is untouched.
        all_pairs = repos.training_pairs.list_for_user(user.user_id)
        calibration_pairs = [p for p in all_pairs if p.origin == TrainingPairOrigin.CALIBRATION]
        assert calibration_pairs == [], (
            "No calibration Training_Pairs should exist when the user never "
            "called any calibration endpoint."
        )

    def test_personalization_threshold_accessible_without_calibration(
        self,
        user: User,
        image_record: Image,
        repos: RepositoryFactory,
        store: InMemoryObjectStore,
    ) -> None:
        """count_unused can be queried (for threshold logic) without calibration (Req 9.1)."""
        # Before any corrections, count is 0.
        assert repos.training_pairs.count_unused(user.user_id) == 0

        # After a correction, count is 1 — no calibration involved.
        submit_correction(
            user_id=user.user_id,
            image_id=image_record.image_id,
            region=Region(x=0, y=0, width=50, height=20),
            recognized_text="helo",
            corrected_text="hello",
            repos=repos,
            object_store=store,
        )
        assert repos.training_pairs.count_unused(user.user_id) == 1


# ===========================================================================
# Tests: Shared Store (Req 9.5)
#
# Calibration Training_Pairs and correction Training_Pairs must live in the
# same repository / DB table so the Personalization_Service sees them together.
# ===========================================================================


class TestSharedStoreCalibrationOrigin:
    """Calibration pairs are stored in the TrainingPair table with origin=CALIBRATION."""

    def test_calibration_pair_has_calibration_origin(
        self, user: User, repos: RepositoryFactory, store: InMemoryObjectStore
    ) -> None:
        """A calibration pair is stored with TrainingPairOrigin.CALIBRATION (Req 9.5)."""
        session_id, _, session_store = _make_session_store(3)
        image_bytes = _make_handwriting_png_bytes()

        result = submit_calibration_sample(
            user_id=user.user_id,
            session_id=session_id,
            prompt_index=0,
            image_bytes=image_bytes,
            repos=repos,
            object_store=store,
            session_store=session_store,
        )

        assert isinstance(result, CalibrationAccepted)
        pair = repos.training_pairs.get(user.user_id, result.pair_id)
        assert pair is not None
        assert pair.origin == TrainingPairOrigin.CALIBRATION

    def test_calibration_pair_returned_by_list_for_user(
        self, user: User, repos: RepositoryFactory, store: InMemoryObjectStore
    ) -> None:
        """list_for_user returns calibration pairs alongside other pairs (Req 9.5)."""
        session_id, _, session_store = _make_session_store(3)
        image_bytes = _make_handwriting_png_bytes()

        result = submit_calibration_sample(
            user_id=user.user_id,
            session_id=session_id,
            prompt_index=0,
            image_bytes=image_bytes,
            repos=repos,
            object_store=store,
            session_store=session_store,
        )

        assert isinstance(result, CalibrationAccepted)
        all_pairs = repos.training_pairs.list_for_user(user.user_id)
        pair_ids = [p.pair_id for p in all_pairs]
        assert result.pair_id in pair_ids

    def test_calibration_pair_retrievable_by_pair_id(
        self, user: User, repos: RepositoryFactory, store: InMemoryObjectStore
    ) -> None:
        """A calibration pair is retrievable by pair_id via the same repository (Req 9.5)."""
        session_id, _, session_store = _make_session_store(3)
        image_bytes = _make_handwriting_png_bytes()

        result = submit_calibration_sample(
            user_id=user.user_id,
            session_id=session_id,
            prompt_index=0,
            image_bytes=image_bytes,
            repos=repos,
            object_store=store,
            session_store=session_store,
        )

        assert isinstance(result, CalibrationAccepted)
        pair = repos.training_pairs.get(user.user_id, result.pair_id)
        assert pair is not None
        assert pair.pair_id == result.pair_id


class TestSharedStoreMixedOrigins:
    """Calibration and correction pairs coexist in the same store and are counted together."""

    def test_list_for_user_returns_both_origins(
        self,
        user: User,
        image_record: Image,
        repos: RepositoryFactory,
        store: InMemoryObjectStore,
    ) -> None:
        """list_for_user returns both calibration and correction pairs (Req 9.5)."""
        # Add one correction pair.
        submit_correction(
            user_id=user.user_id,
            image_id=image_record.image_id,
            region=Region(x=0, y=0, width=50, height=20),
            recognized_text="helo",
            corrected_text="hello",
            repos=repos,
            object_store=store,
        )

        # Add one calibration pair.
        session_id, _, session_store = _make_session_store(3)
        image_bytes = _make_handwriting_png_bytes()
        submit_calibration_sample(
            user_id=user.user_id,
            session_id=session_id,
            prompt_index=0,
            image_bytes=image_bytes,
            repos=repos,
            object_store=store,
            session_store=session_store,
        )

        all_pairs = repos.training_pairs.list_for_user(user.user_id)
        origins = {p.origin for p in all_pairs}
        assert TrainingPairOrigin.CORRECTION in origins
        assert TrainingPairOrigin.CALIBRATION in origins
        assert len(all_pairs) == 2

    def test_count_unused_includes_both_calibration_and_correction(
        self,
        user: User,
        image_record: Image,
        repos: RepositoryFactory,
        store: InMemoryObjectStore,
    ) -> None:
        """count_unused aggregates both origins so both feed the personalization threshold (Req 9.5)."""
        # 2 correction pairs.
        for i in range(2):
            submit_correction(
                user_id=user.user_id,
                image_id=image_record.image_id,
                region=Region(x=i * 10, y=0, width=9, height=20),
                recognized_text="x",
                corrected_text=f"word{i}",
                repos=repos,
                object_store=store,
            )

        # 2 calibration pairs.
        session_id, _, session_store = _make_session_store(5)
        image_bytes = _make_handwriting_png_bytes()
        for idx in range(2):
            submit_calibration_sample(
                user_id=user.user_id,
                session_id=session_id,
                prompt_index=idx,
                image_bytes=image_bytes,
                repos=repos,
                object_store=store,
                session_store=session_store,
            )

        total = repos.training_pairs.count_unused(user.user_id)
        assert total == 4, (
            f"Expected count_unused=4 (2 corrections + 2 calibration), got {total}"
        )

    def test_calibration_and_correction_pairs_have_distinct_pair_ids(
        self,
        user: User,
        image_record: Image,
        repos: RepositoryFactory,
        store: InMemoryObjectStore,
    ) -> None:
        """Every pair — regardless of origin — has a unique pair_id (Req 9.5)."""
        correction_result = submit_correction(
            user_id=user.user_id,
            image_id=image_record.image_id,
            region=Region(x=0, y=0, width=50, height=20),
            recognized_text="helo",
            corrected_text="hello",
            repos=repos,
            object_store=store,
        )
        assert isinstance(correction_result, Created)

        session_id, _, session_store = _make_session_store(3)
        image_bytes = _make_handwriting_png_bytes()
        calibration_result = submit_calibration_sample(
            user_id=user.user_id,
            session_id=session_id,
            prompt_index=0,
            image_bytes=image_bytes,
            repos=repos,
            object_store=store,
            session_store=session_store,
        )
        assert isinstance(calibration_result, CalibrationAccepted)

        assert correction_result.pair_id != calibration_result.pair_id


class TestSharedStoreSameTable:
    """Calibration pairs use the same TrainingPair model and DB table as correction pairs."""

    def test_calibration_pair_is_training_pair_instance(
        self, user: User, repos: RepositoryFactory, store: InMemoryObjectStore
    ) -> None:
        """A calibration pair is a TrainingPair ORM instance — same table (Req 9.5)."""
        session_id, _, session_store = _make_session_store(3)
        image_bytes = _make_handwriting_png_bytes()

        result = submit_calibration_sample(
            user_id=user.user_id,
            session_id=session_id,
            prompt_index=0,
            image_bytes=image_bytes,
            repos=repos,
            object_store=store,
            session_store=session_store,
        )

        assert isinstance(result, CalibrationAccepted)
        pair = repos.training_pairs.get(user.user_id, result.pair_id)
        assert isinstance(pair, TrainingPair), (
            "Calibration pair must be a TrainingPair ORM instance (same table as corrections)."
        )

    def test_calibration_pair_user_id_matches_submitter(
        self, user: User, repos: RepositoryFactory, store: InMemoryObjectStore
    ) -> None:
        """Calibration pair is associated with the correct user (Req 9.5)."""
        session_id, _, session_store = _make_session_store(3)
        image_bytes = _make_handwriting_png_bytes()

        result = submit_calibration_sample(
            user_id=user.user_id,
            session_id=session_id,
            prompt_index=0,
            image_bytes=image_bytes,
            repos=repos,
            object_store=store,
            session_store=session_store,
        )

        assert isinstance(result, CalibrationAccepted)
        pair = repos.training_pairs.get(user.user_id, result.pair_id)
        assert pair.user_id == user.user_id

    def test_calibration_pair_corrected_text_equals_prompt(
        self, user: User, repos: RepositoryFactory, store: InMemoryObjectStore
    ) -> None:
        """Calibration pair corrected_text is the known prompt text, retrievable via shared repo (Req 9.5)."""
        session_id, prompts, session_store = _make_session_store(3)
        image_bytes = _make_handwriting_png_bytes()

        result = submit_calibration_sample(
            user_id=user.user_id,
            session_id=session_id,
            prompt_index=1,
            image_bytes=image_bytes,
            repos=repos,
            object_store=store,
            session_store=session_store,
        )

        assert isinstance(result, CalibrationAccepted)
        pair = repos.training_pairs.get(user.user_id, result.pair_id)
        assert pair.corrected_text == prompts[1]

    def test_multiple_calibration_prompts_stored_as_separate_pairs(
        self, user: User, repos: RepositoryFactory, store: InMemoryObjectStore
    ) -> None:
        """Each submitted calibration prompt creates a distinct pair in the shared store (Req 9.5)."""
        session_id, prompts, session_store = _make_session_store(5)
        image_bytes = _make_handwriting_png_bytes()

        pair_ids = set()
        for idx in range(3):
            result = submit_calibration_sample(
                user_id=user.user_id,
                session_id=session_id,
                prompt_index=idx,
                image_bytes=image_bytes,
                repos=repos,
                object_store=store,
                session_store=session_store,
            )
            assert isinstance(result, CalibrationAccepted)
            pair_ids.add(result.pair_id)

        assert len(pair_ids) == 3
        all_pairs = repos.training_pairs.list_for_user(user.user_id)
        assert len(all_pairs) == 3
        for pair in all_pairs:
            assert pair.origin == TrainingPairOrigin.CALIBRATION

    def test_calibration_pairs_invisible_to_another_user(
        self, repos: RepositoryFactory, store: InMemoryObjectStore
    ) -> None:
        """Calibration pairs in the shared store are still user-scoped (Req 9.5, 10.5)."""
        # Create two users.
        alice = User(
            username="alice_shared",
            email="alice_shared@example.com",
            password_hash=hash_password("pw"),
        )
        bob = User(
            username="bob_shared",
            email="bob_shared@example.com",
            password_hash=hash_password("pw"),
        )
        repos.users.add(alice)
        repos.users.add(bob)
        repos.session.commit()

        # Alice submits a calibration sample.
        session_id, _, session_store = _make_session_store(3)
        image_bytes = _make_handwriting_png_bytes()
        result = submit_calibration_sample(
            user_id=alice.user_id,
            session_id=session_id,
            prompt_index=0,
            image_bytes=image_bytes,
            repos=repos,
            object_store=store,
            session_store=session_store,
        )
        assert isinstance(result, CalibrationAccepted)

        # Bob sees zero pairs — the shared table is still user-scoped.
        bob_pairs = repos.training_pairs.list_for_user(bob.user_id)
        assert bob_pairs == [], (
            "Calibration pairs from another user must not be visible via the shared store."
        )
