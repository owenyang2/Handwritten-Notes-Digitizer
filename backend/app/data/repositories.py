"""Per-user-scoped data-access layer (repositories).

This module implements the design's *strict per-user isolation* goal at the
data-access layer as **defense in depth** (Req 10.2, 10.5). Every read/query in
these repositories is scoped by an authenticated ``user_id``: a repository can
only ever return, update, or delete rows that belong to the user it was asked
about. Even if a route handler forgets to check ownership, a repository call
cannot leak another user's images, recognized text, training pairs, adapters,
training runs, or evaluation results.

Design rules enforced here
--------------------------
* Construct every query with a mandatory ``user_id`` filter. There is no
  "fetch by id" that ignores ownership.
* Lookups for a resource owned by another user return ``None`` (callers map
  this to a 404 so existence is not disclosed - Req 10.5), never the row.
* ``RecognizedWord`` has no ``user_id`` column of its own; it is scoped through
  a join to its owning :class:`~app.data.models.Image` so recognized text stays
  per-user isolated too.
* The Personalization_Service consumes only a user's own training pairs; the
  training-pair queries here never cross the ``user_id`` boundary (Req 10.2).

These repositories are deliberately thin and side-effect-light: they add/flush
objects and run scoped queries, leaving transaction boundaries (commit) to the
calling service so multi-repository writes can share a transaction.
"""

from __future__ import annotations

import datetime as _dt
import uuid
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from .enums import AdapterStatus, RecognitionJobState, TrainingRunState
from .models import (
    Adapter,
    Document,
    DocumentPage,
    DocumentRegion,
    DocumentRevision,
    EvaluationResult,
    Image,
    ImageLineReview,
    ModelArtifact,
    RecognizedWord,
    RegionPrediction,
    RegionReview,
    RecognitionJob,
    ReviewRevision,
    TrainingExample,
    TrainingPair,
    TrainingRun,
    User,
)

UUIDLike = uuid.UUID


def _as_uuid(value: UUIDLike | str) -> uuid.UUID:
    """Coerce a ``user_id``/identifier to :class:`uuid.UUID`.

    Accepting both ``uuid.UUID`` and its string form keeps the boundary tolerant
    of auth layers that hand us the id as a string, while still normalizing to a
    single comparable type for every scoped query.
    """
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


class _ScopedRepository:
    """Base class holding the session shared by all scoped repositories."""

    def __init__(self, session: Session) -> None:
        self._session = session

    @property
    def session(self) -> Session:
        return self._session


class UserRepository(_ScopedRepository):
    """Access to :class:`User` rows.

    The user is the identity itself, so "scoping" here means identity lookups
    (by id / username / email) used by the auth layer. There is intentionally
    no method that lists *all* users for a non-admin caller.
    """

    def add(self, user: User) -> User:
        self._session.add(user)
        self._session.flush()
        return user

    def get_by_id(self, user_id: UUIDLike | str) -> Optional[User]:
        return self._session.get(User, _as_uuid(user_id))

    def get_by_username(self, username: str) -> Optional[User]:
        stmt = select(User).where(User.username == username)
        return self._session.scalars(stmt).one_or_none()

    def get_by_email(self, email: str) -> Optional[User]:
        stmt = select(User).where(User.email == email)
        return self._session.scalars(stmt).one_or_none()


class DocumentRepository(_ScopedRepository):
    """Per-user access to document aggregates and their image groups."""

    def create(self, user_id: UUIDLike | str, title: str) -> Document:
        doc = Document(user_id=_as_uuid(user_id), title=title)
        self._session.add(doc)
        self._session.flush()
        return doc

    def get(
        self, user_id: UUIDLike | str, document_id: UUIDLike | str
    ) -> Optional[Document]:
        """Return a document only when it belongs to ``user_id``."""
        stmt = select(Document).where(
            Document.document_id == _as_uuid(document_id),
            Document.user_id == _as_uuid(user_id),
        )
        return self._session.scalars(stmt).one_or_none()

    def list_for_user(self, user_id: UUIDLike | str) -> list[Document]:
        stmt = (
            select(Document)
            .where(Document.user_id == _as_uuid(user_id))
            .order_by(Document.created_at.desc())
        )
        return list(self._session.scalars(stmt).all())


class ImageRepository(_ScopedRepository):
    """Per-user access to :class:`Image` rows (Req 1.6, 10.5)."""

    def add(self, image: Image) -> Image:
        self._session.add(image)
        self._session.flush()
        return image

    def get(self, user_id: UUIDLike | str, image_id: UUIDLike | str) -> Optional[Image]:
        """Return the image *only* if it belongs to ``user_id`` (else ``None``)."""
        stmt = select(Image).where(
            Image.image_id == _as_uuid(image_id),
            Image.user_id == _as_uuid(user_id),
        )
        return self._session.scalars(stmt).one_or_none()

    def list_for_user(self, user_id: UUIDLike | str) -> Sequence[Image]:
        stmt = (
            select(Image)
            .where(Image.user_id == _as_uuid(user_id))
            .order_by(Image.created_at)
        )
        return list(self._session.scalars(stmt).all())

    def delete(self, user_id: UUIDLike | str, image_id: UUIDLike | str) -> bool:
        """Delete the image iff owned by ``user_id``; return whether it existed."""
        image = self.get(user_id, image_id)
        if image is None:
            return False
        self._session.delete(image)
        return True


class RecognizedWordRepository(_ScopedRepository):
    """Per-user access to :class:`RecognizedWord` rows.

    Recognized words have no ``user_id`` column; ownership is derived from the
    parent :class:`Image`. Every query joins ``Image`` and filters on
    ``Image.user_id`` so recognized text is isolated per user (Req 10.5).
    """

    def add(self, word: RecognizedWord) -> RecognizedWord:
        self._session.add(word)
        self._session.flush()
        return word

    def add_all(self, words: Sequence[RecognizedWord]) -> Sequence[RecognizedWord]:
        self._session.add_all(list(words))
        self._session.flush()
        return words

    def list_for_image(
        self, user_id: UUIDLike | str, image_id: UUIDLike | str
    ) -> Sequence[RecognizedWord]:
        """Return words for an image only if that image belongs to ``user_id``.

        Ordered in reading order (line top-to-bottom, word left-to-right) to
        match how the Editor displays them.
        """
        stmt = (
            select(RecognizedWord)
            .join(Image, RecognizedWord.image_id == Image.image_id)
            .where(
                Image.user_id == _as_uuid(user_id),
                RecognizedWord.image_id == _as_uuid(image_id),
            )
            .order_by(RecognizedWord.line_index, RecognizedWord.word_index)
        )
        return list(self._session.scalars(stmt).all())

    def get(
        self, user_id: UUIDLike | str, word_id: UUIDLike | str
    ) -> Optional[RecognizedWord]:
        stmt = (
            select(RecognizedWord)
            .join(Image, RecognizedWord.image_id == Image.image_id)
            .where(
                Image.user_id == _as_uuid(user_id),
                RecognizedWord.word_id == _as_uuid(word_id),
            )
        )
        return self._session.scalars(stmt).one_or_none()

    def replace_all_for_image(
        self,
        user_id: UUIDLike | str,
        image_id: UUIDLike | str,
        words: Sequence[RecognizedWord],
    ) -> Sequence[RecognizedWord]:
        """Replace the active recognition rows for an owned image.

        User line reviews are stored separately and are not deleted here.
        """
        existing = self.list_for_image(user_id, image_id)
        for row in existing:
            self._session.delete(row)
        self._session.flush()
        if not words:
            return []
        return self.add_all(words)


class ImageLineReviewRepository(_ScopedRepository):
    """Per-user access to persisted line reviews and exclusions."""

    def list_for_image(
        self, user_id: UUIDLike | str, image_id: UUIDLike | str
    ) -> Sequence[ImageLineReview]:
        stmt = (
            select(ImageLineReview)
            .join(Image, ImageLineReview.image_id == Image.image_id)
            .where(
                Image.user_id == _as_uuid(user_id),
                ImageLineReview.image_id == _as_uuid(image_id),
            )
            .order_by(ImageLineReview.line_index)
        )
        return list(self._session.scalars(stmt).all())

    def upsert(
        self,
        user_id: UUIDLike | str,
        image_id: UUIDLike | str,
        line_index: int,
        reviewed_text: str,
        excluded: bool,
        region_x: int = 0,
        region_y: int = 0,
        region_width: int = 0,
        region_height: int = 0,
    ) -> ImageLineReview:
        image = (
            select(Image)
            .where(
                Image.user_id == _as_uuid(user_id),
                Image.image_id == _as_uuid(image_id),
            )
        )
        if self._session.scalars(image).one_or_none() is None:
            raise LookupError("image not found")
        existing = self._session.scalars(
            select(ImageLineReview).where(
                ImageLineReview.image_id == _as_uuid(image_id),
                ImageLineReview.line_index == line_index,
            )
        ).one_or_none()
        if existing is None:
            existing = ImageLineReview(
                image_id=_as_uuid(image_id),
                line_index=line_index,
            )
            self._session.add(existing)
        existing.reviewed_text = reviewed_text
        existing.excluded = excluded
        existing.region_x = region_x
        existing.region_y = region_y
        existing.region_width = region_width
        existing.region_height = region_height
        self._session.flush()
        return existing


class DocumentPageRepository(_ScopedRepository):
    def add(self, page: DocumentPage) -> DocumentPage:
        self._session.add(page)
        self._session.flush()
        return page

    def list_for_document(
        self, user_id: UUIDLike | str, document_id: UUIDLike | str
    ) -> Sequence[DocumentPage]:
        stmt = (
            select(DocumentPage)
            .join(Document, DocumentPage.document_id == Document.document_id)
            .where(
                Document.user_id == _as_uuid(user_id),
                DocumentPage.document_id == _as_uuid(document_id),
            )
            .order_by(DocumentPage.page_index)
        )
        return list(self._session.scalars(stmt).all())

    def get(
        self, user_id: UUIDLike | str, page_id: UUIDLike | str
    ) -> Optional[DocumentPage]:
        stmt = (
            select(DocumentPage)
            .join(Document, DocumentPage.document_id == Document.document_id)
            .where(
                Document.user_id == _as_uuid(user_id),
                DocumentPage.page_id == _as_uuid(page_id),
            )
        )
        return self._session.scalars(stmt).one_or_none()


class DocumentRegionRepository(_ScopedRepository):
    def add(self, region: DocumentRegion) -> DocumentRegion:
        self._session.add(region)
        self._session.flush()
        return region

    def get(
        self, user_id: UUIDLike | str, region_id: UUIDLike | str
    ) -> Optional[DocumentRegion]:
        stmt = (
            select(DocumentRegion)
            .join(DocumentPage, DocumentRegion.page_id == DocumentPage.page_id)
            .join(Document, DocumentPage.document_id == Document.document_id)
            .where(
                Document.user_id == _as_uuid(user_id),
                DocumentRegion.region_id == _as_uuid(region_id),
            )
        )
        return self._session.scalars(stmt).one_or_none()

    def list_for_page(
        self, user_id: UUIDLike | str, page_id: UUIDLike | str
    ) -> Sequence[DocumentRegion]:
        stmt = (
            select(DocumentRegion)
            .join(DocumentPage, DocumentRegion.page_id == DocumentPage.page_id)
            .join(Document, DocumentPage.document_id == Document.document_id)
            .where(
                Document.user_id == _as_uuid(user_id),
                DocumentRegion.page_id == _as_uuid(page_id),
            )
            .order_by(DocumentRegion.order_index)
        )
        return list(self._session.scalars(stmt).all())

    def update_order(self, user_id: UUIDLike | str, ordered_ids: Sequence[UUIDLike | str]) -> list[DocumentRegion]:
        """Persist a complete, owner-scoped reading order for a page."""
        regions = [self.get(user_id, region_id) for region_id in ordered_ids]
        if any(region is None for region in regions):
            raise LookupError("one or more regions were not found")
        for index, region in enumerate(regions):
            assert region is not None
            region.order_index = index
        self._session.flush()
        return [region for region in regions if region is not None]

    def delete(self, user_id: UUIDLike | str, region_id: UUIDLike | str) -> bool:
        region = self.get(user_id, region_id)
        if region is None:
            return False
        self._session.delete(region)
        return True


class RegionPredictionRepository(_ScopedRepository):
    def add(self, prediction: RegionPrediction) -> RegionPrediction:
        self._session.add(prediction)
        self._session.flush()
        return prediction

    def get(
        self, user_id: UUIDLike | str, prediction_id: UUIDLike | str
    ) -> Optional[RegionPrediction]:
        """Return a prediction only when its region belongs to the user."""
        stmt = (
            select(RegionPrediction)
            .join(DocumentRegion, RegionPrediction.region_id == DocumentRegion.region_id)
            .join(DocumentPage, DocumentRegion.page_id == DocumentPage.page_id)
            .join(Document, DocumentPage.document_id == Document.document_id)
            .where(
                Document.user_id == _as_uuid(user_id),
                RegionPrediction.prediction_id == _as_uuid(prediction_id),
            )
        )
        return self._session.scalars(stmt).one_or_none()

    def latest_for_region(
        self, user_id: UUIDLike | str, region_id: UUIDLike | str
    ) -> Optional[RegionPrediction]:
        stmt = (
            select(RegionPrediction)
            .join(DocumentRegion, RegionPrediction.region_id == DocumentRegion.region_id)
            .join(DocumentPage, DocumentRegion.page_id == DocumentPage.page_id)
            .join(Document, DocumentPage.document_id == Document.document_id)
            .where(
                Document.user_id == _as_uuid(user_id),
                RegionPrediction.region_id == _as_uuid(region_id),
            )
            .order_by(RegionPrediction.created_at.desc(), RegionPrediction.prediction_id.desc())
        )
        return self._session.scalars(stmt).first()


class RegionReviewRepository(_ScopedRepository):
    def add(self, review: RegionReview) -> RegionReview:
        self._session.add(review)
        self._session.flush()
        return review

    def latest_for_region(
        self, user_id: UUIDLike | str, region_id: UUIDLike | str
    ) -> Optional[RegionReview]:
        stmt = (
            select(RegionReview)
            .join(DocumentRegion, RegionReview.region_id == DocumentRegion.region_id)
            .join(DocumentPage, DocumentRegion.page_id == DocumentPage.page_id)
            .join(Document, DocumentPage.document_id == Document.document_id)
            .where(
                Document.user_id == _as_uuid(user_id),
                RegionReview.region_id == _as_uuid(region_id),
            )
            .order_by(RegionReview.created_at.desc(), RegionReview.review_id.desc())
        )
        return self._session.scalars(stmt).first()


class DocumentRevisionRepository(_ScopedRepository):
    """Owner-scoped access to immutable document snapshots."""

    def next_number(self, user_id: UUIDLike | str, document_id: UUIDLike | str) -> int:
        stmt = select(DocumentRevision.revision_number).where(
            DocumentRevision.document_id == _as_uuid(document_id),
            DocumentRevision.author_user_id == _as_uuid(user_id),
        ).order_by(DocumentRevision.revision_number.desc())
        current = self._session.scalars(stmt).first()
        return int(current or 0) + 1

    def add(self, revision: DocumentRevision) -> DocumentRevision:
        self._session.add(revision)
        self._session.flush()
        return revision

    def get(self, user_id: UUIDLike | str, revision_id: UUIDLike | str) -> Optional[DocumentRevision]:
        stmt = (
            select(DocumentRevision)
            .join(Document, DocumentRevision.document_id == Document.document_id)
            .where(
                Document.user_id == _as_uuid(user_id),
                DocumentRevision.revision_id == _as_uuid(revision_id),
            )
        )
        return self._session.scalars(stmt).one_or_none()

    def list_for_document(self, user_id: UUIDLike | str, document_id: UUIDLike | str) -> Sequence[DocumentRevision]:
        stmt = (
            select(DocumentRevision)
            .join(Document, DocumentRevision.document_id == Document.document_id)
            .where(
                Document.user_id == _as_uuid(user_id),
                DocumentRevision.document_id == _as_uuid(document_id),
            )
            .order_by(DocumentRevision.revision_number)
        )
        return list(self._session.scalars(stmt).all())


class ReviewRevisionRepository(_ScopedRepository):
    """Owner-scoped canonical review history."""

    def add(self, revision: ReviewRevision) -> ReviewRevision:
        self._session.add(revision)
        self._session.flush()
        return revision

    def latest_for_region(self, user_id: UUIDLike | str, region_id: UUIDLike | str) -> Optional[ReviewRevision]:
        stmt = (
            select(ReviewRevision)
            .join(DocumentRegion, ReviewRevision.region_id == DocumentRegion.region_id)
            .join(DocumentPage, DocumentRegion.page_id == DocumentPage.page_id)
            .join(Document, DocumentPage.document_id == Document.document_id)
            .where(
                Document.user_id == _as_uuid(user_id),
                ReviewRevision.region_id == _as_uuid(region_id),
            )
            .order_by(ReviewRevision.created_at.desc(), ReviewRevision.review_revision_id.desc())
        )
        return self._session.scalars(stmt).first()

    def get(self, user_id: UUIDLike | str, revision_id: UUIDLike | str) -> Optional[ReviewRevision]:
        stmt = (
            select(ReviewRevision)
            .join(DocumentRegion, ReviewRevision.region_id == DocumentRegion.region_id)
            .join(DocumentPage, DocumentRegion.page_id == DocumentPage.page_id)
            .join(Document, DocumentPage.document_id == Document.document_id)
            .where(
                Document.user_id == _as_uuid(user_id),
                ReviewRevision.review_revision_id == _as_uuid(revision_id),
            )
        )
        return self._session.scalars(stmt).one_or_none()


class TrainingExampleRepository(_ScopedRepository):
    """Explicit-consent training examples derived from reviewed revisions."""

    def add(self, example: TrainingExample) -> TrainingExample:
        self._session.add(example)
        self._session.flush()
        return example

    def get(self, user_id: UUIDLike | str, example_id: UUIDLike | str) -> Optional[TrainingExample]:
        stmt = select(TrainingExample).where(
            TrainingExample.user_id == _as_uuid(user_id),
            TrainingExample.example_id == _as_uuid(example_id),
        )
        return self._session.scalars(stmt).one_or_none()

    def get_for_review(self, user_id: UUIDLike | str, review_revision_id: UUIDLike | str) -> Optional[TrainingExample]:
        stmt = select(TrainingExample).where(
            TrainingExample.user_id == _as_uuid(user_id),
            TrainingExample.review_revision_id == _as_uuid(review_revision_id),
        )
        return self._session.scalars(stmt).one_or_none()

    def list_eligible(self, user_id: UUIDLike | str) -> Sequence[TrainingExample]:
        stmt = select(TrainingExample).where(
            TrainingExample.user_id == _as_uuid(user_id),
            TrainingExample.consent.is_(True),
            TrainingExample.eligible.is_(True),
        ).order_by(TrainingExample.created_at)
        return list(self._session.scalars(stmt).all())

    def list_for_user(self, user_id: UUIDLike | str) -> Sequence[TrainingExample]:
        stmt = (
            select(TrainingExample)
            .where(TrainingExample.user_id == _as_uuid(user_id))
            .order_by(TrainingExample.created_at)
        )
        return list(self._session.scalars(stmt).all())

    def mark_region_ineligible(
        self, user_id: UUIDLike | str, region_id: UUIDLike | str
    ) -> int:
        """Withdraw training eligibility for an explicitly excluded region.

        The immutable example rows remain for audit, but a later ``Not text`` /
        exclusion decision must not leave an older consented crop eligible for
        future personalization.
        """
        stmt = select(TrainingExample).where(
            TrainingExample.user_id == _as_uuid(user_id),
            TrainingExample.region_id == _as_uuid(region_id),
            TrainingExample.eligible.is_(True),
        )
        examples = list(self._session.scalars(stmt).all())
        for example in examples:
            example.eligible = False
        if examples:
            self._session.flush()
        return len(examples)


class RecognitionJobRepository(_ScopedRepository):
    """Owner-scoped persistence and lifecycle transitions for recognition jobs."""

    def add(self, job: RecognitionJob) -> RecognitionJob:
        self._session.add(job)
        self._session.flush()
        return job

    def get(self, user_id: UUIDLike | str, job_id: UUIDLike | str) -> Optional[RecognitionJob]:
        stmt = select(RecognitionJob).where(
            RecognitionJob.user_id == _as_uuid(user_id),
            RecognitionJob.job_id == _as_uuid(job_id),
        )
        return self._session.scalars(stmt).one_or_none()

    def get_active_for_scope(
        self,
        user_id: UUIDLike | str,
        *,
        page_id: UUIDLike | str | None = None,
        image_id: UUIDLike | str | None = None,
        region_ids: Sequence[str] = (),
        mode: str = "formula",
    ) -> Optional[RecognitionJob]:
        states = (RecognitionJobState.QUEUED, RecognitionJobState.RUNNING)
        stmt = select(RecognitionJob).where(
            RecognitionJob.user_id == _as_uuid(user_id),
            RecognitionJob.mode == mode,
            RecognitionJob.state.in_(states),
        )
        if page_id is not None:
            stmt = stmt.where(RecognitionJob.page_id == _as_uuid(page_id))
        if image_id is not None:
            stmt = stmt.where(RecognitionJob.image_id == _as_uuid(image_id))
        for job in self._session.scalars(stmt.order_by(RecognitionJob.created_at.desc())).all():
            if tuple(job.region_ids or []) == tuple(region_ids):
                return job
        return None

    def list_stale(self, now: _dt.datetime) -> Sequence[RecognitionJob]:
        stmt = select(RecognitionJob).where(
            RecognitionJob.state == RecognitionJobState.RUNNING,
            RecognitionJob.deadline_at.is_not(None),
            RecognitionJob.deadline_at < now,
        )
        return list(self._session.scalars(stmt).all())

    def list_for_states(self, states: Sequence[RecognitionJobState]) -> Sequence[RecognitionJob]:
        """Return jobs in the requested lifecycle states for restart recovery."""
        if not states:
            return []
        stmt = (
            select(RecognitionJob)
            .where(RecognitionJob.state.in_(tuple(states)))
            .order_by(RecognitionJob.created_at)
        )
        return list(self._session.scalars(stmt).all())

    def list_for_user(self, user_id: UUIDLike | str) -> Sequence[RecognitionJob]:
        stmt = select(RecognitionJob).where(
            RecognitionJob.user_id == _as_uuid(user_id)
        ).order_by(RecognitionJob.created_at.desc())
        return list(self._session.scalars(stmt).all())


class ModelArtifactRepository(_ScopedRepository):
    """Global, immutable-ish registry of loadable model artifact identities."""

    def add(self, artifact: ModelArtifact) -> ModelArtifact:
        self._session.add(artifact)
        self._session.flush()
        return artifact

    def get_by_backend(self, backend_id: str) -> Optional[ModelArtifact]:
        stmt = select(ModelArtifact).where(ModelArtifact.backend_id == backend_id)
        return self._session.scalars(stmt).one_or_none()

    def list_all(self) -> Sequence[ModelArtifact]:
        stmt = select(ModelArtifact).order_by(ModelArtifact.backend_id)
        return list(self._session.scalars(stmt).all())


class TrainingPairRepository(_ScopedRepository):
    """Per-user access to :class:`TrainingPair` rows (Req 10.2, 10.5).

    The Personalization_Service trains only on a user's own pairs, so every
    accessor here is scoped by ``user_id`` and never crosses that boundary.
    """

    def add(self, pair: TrainingPair) -> TrainingPair:
        self._session.add(pair)
        self._session.flush()
        return pair

    def get(
        self, user_id: UUIDLike | str, pair_id: UUIDLike | str
    ) -> Optional[TrainingPair]:
        stmt = select(TrainingPair).where(
            TrainingPair.pair_id == _as_uuid(pair_id),
            TrainingPair.user_id == _as_uuid(user_id),
        )
        return self._session.scalars(stmt).one_or_none()

    def get_by_region_key(
        self, user_id: UUIDLike | str, source_region_key: str
    ) -> Optional[TrainingPair]:
        """Look up a user's pair by its stable region key (drives upsert, Req 4.7)."""
        stmt = select(TrainingPair).where(
            TrainingPair.user_id == _as_uuid(user_id),
            TrainingPair.source_region_key == source_region_key,
        )
        return self._session.scalars(stmt).one_or_none()

    def list_for_user(self, user_id: UUIDLike | str) -> Sequence[TrainingPair]:
        stmt = (
            select(TrainingPair)
            .where(TrainingPair.user_id == _as_uuid(user_id))
            .order_by(TrainingPair.created_at)
        )
        return list(self._session.scalars(stmt).all())

    def list_for_image(
        self, user_id: UUIDLike | str, image_id: UUIDLike | str
    ) -> Sequence[TrainingPair]:
        """Return correction pairs attached to one owned image.

        Recognition retries replace transient word rows, so the service uses
        these durable pairs to reapply confirmed corrections to matching crops.
        """
        stmt = select(TrainingPair).where(
            TrainingPair.user_id == _as_uuid(user_id),
            TrainingPair.image_id == _as_uuid(image_id),
        ).order_by(TrainingPair.created_at)
        return list(self._session.scalars(stmt).all())

    def list_unused(self, user_id: UUIDLike | str) -> Sequence[TrainingPair]:
        """Pairs not yet consumed by a completed training (drives the trigger, Req 5.1)."""
        stmt = (
            select(TrainingPair)
            .where(
                TrainingPair.user_id == _as_uuid(user_id),
                TrainingPair.consumed_by_run_id.is_(None),
            )
            .order_by(TrainingPair.created_at)
        )
        return list(self._session.scalars(stmt).all())

    def count_unused(self, user_id: UUIDLike | str) -> int:
        return len(self.list_unused(user_id))

    def delete(self, user_id: UUIDLike | str, pair_id: UUIDLike | str) -> bool:
        pair = self.get(user_id, pair_id)
        if pair is None:
            return False
        self._session.delete(pair)
        return True


class AdapterRepository(_ScopedRepository):
    """Per-user access to :class:`Adapter` rows; at most one per user (Req 5.3, 10.5)."""

    def add(self, adapter: Adapter) -> Adapter:
        self._session.add(adapter)
        self._session.flush()
        return adapter

    def get_for_user(self, user_id: UUIDLike | str) -> Optional[Adapter]:
        stmt = select(Adapter).where(Adapter.user_id == _as_uuid(user_id))
        return self._session.scalars(stmt).one_or_none()

    def get_active_for_user(self, user_id: UUIDLike | str) -> Optional[Adapter]:
        """The user's active adapter, if any (selected at recognition time)."""
        stmt = select(Adapter).where(
            Adapter.user_id == _as_uuid(user_id),
            Adapter.status == AdapterStatus.ACTIVE,
        )
        return self._session.scalars(stmt).one_or_none()

    def delete_for_user(self, user_id: UUIDLike | str) -> bool:
        adapter = self.get_for_user(user_id)
        if adapter is None:
            return False
        self._session.delete(adapter)
        return True


class TrainingRunRepository(_ScopedRepository):
    """Per-user access to :class:`TrainingRun` rows (Req 5.6, 10.5)."""

    def add(self, run: TrainingRun) -> TrainingRun:
        self._session.add(run)
        self._session.flush()
        return run

    def get(
        self, user_id: UUIDLike | str, run_id: UUIDLike | str
    ) -> Optional[TrainingRun]:
        stmt = select(TrainingRun).where(
            TrainingRun.run_id == _as_uuid(run_id),
            TrainingRun.user_id == _as_uuid(user_id),
        )
        return self._session.scalars(stmt).one_or_none()

    def list_for_user(self, user_id: UUIDLike | str) -> Sequence[TrainingRun]:
        stmt = (
            select(TrainingRun)
            .where(TrainingRun.user_id == _as_uuid(user_id))
            .order_by(TrainingRun.started_at)
        )
        return list(self._session.scalars(stmt).all())

    def get_running_for_user(self, user_id: UUIDLike | str) -> Optional[TrainingRun]:
        """A user's in-progress run, if any (used to prevent concurrency, Req 5.6)."""
        stmt = select(TrainingRun).where(
            TrainingRun.user_id == _as_uuid(user_id),
            TrainingRun.state == TrainingRunState.RUNNING,
        )
        return self._session.scalars(stmt).first()

    def get_queued_for_user(self, user_id: UUIDLike | str) -> Optional[TrainingRun]:
        stmt = (
            select(TrainingRun)
            .where(
                TrainingRun.user_id == _as_uuid(user_id),
                TrainingRun.state == TrainingRunState.QUEUED,
            )
            .order_by(TrainingRun.started_at, TrainingRun.run_id)
        )
        return self._session.scalars(stmt).first()


class EvaluationResultRepository(_ScopedRepository):
    """Per-user access to :class:`EvaluationResult` rows (Req 7.3, 10.5)."""

    def add(self, result: EvaluationResult) -> EvaluationResult:
        self._session.add(result)
        self._session.flush()
        return result

    def get(
        self, user_id: UUIDLike | str, eval_id: UUIDLike | str
    ) -> Optional[EvaluationResult]:
        stmt = select(EvaluationResult).where(
            EvaluationResult.eval_id == _as_uuid(eval_id),
            EvaluationResult.user_id == _as_uuid(user_id),
        )
        return self._session.scalars(stmt).one_or_none()

    def get_for_run(
        self, user_id: UUIDLike | str, run_id: UUIDLike | str
    ) -> Optional[EvaluationResult]:
        stmt = select(EvaluationResult).where(
            EvaluationResult.user_id == _as_uuid(user_id),
            EvaluationResult.run_id == _as_uuid(run_id),
        )
        return self._session.scalars(stmt).one_or_none()

    def list_for_user(self, user_id: UUIDLike | str) -> Sequence[EvaluationResult]:
        stmt = select(EvaluationResult).where(
            EvaluationResult.user_id == _as_uuid(user_id)
        )
        return list(self._session.scalars(stmt).all())

    def latest_for_user(self, user_id: UUIDLike | str) -> Optional[EvaluationResult]:
        """Most recent evaluation for the user via its run's start time."""
        stmt = (
            select(EvaluationResult)
            .join(TrainingRun, EvaluationResult.run_id == TrainingRun.run_id)
            .where(EvaluationResult.user_id == _as_uuid(user_id))
            .order_by(TrainingRun.started_at.desc())
        )
        return self._session.scalars(stmt).first()


class RepositoryFactory:
    """Convenience aggregator exposing every scoped repository on one session.

    Services receive a single factory bound to a request-scoped session so all
    their writes participate in one transaction while every read stays scoped to
    the authenticated user.
    """

    def __init__(self, session: Session) -> None:
        self._session = session
        self.users = UserRepository(session)
        self.documents = DocumentRepository(session)
        self.images = ImageRepository(session)
        self.recognized_words = RecognizedWordRepository(session)
        self.line_reviews = ImageLineReviewRepository(session)
        self.document_pages = DocumentPageRepository(session)
        self.document_regions = DocumentRegionRepository(session)
        self.region_predictions = RegionPredictionRepository(session)
        self.region_reviews = RegionReviewRepository(session)
        self.document_revisions = DocumentRevisionRepository(session)
        self.review_revisions = ReviewRevisionRepository(session)
        self.training_examples = TrainingExampleRepository(session)
        self.recognition_jobs = RecognitionJobRepository(session)
        self.model_artifacts = ModelArtifactRepository(session)
        self.training_pairs = TrainingPairRepository(session)
        self.adapters = AdapterRepository(session)
        self.training_runs = TrainingRunRepository(session)
        self.evaluation_results = EvaluationResultRepository(session)

    @property
    def session(self) -> Session:
        return self._session


__all__ = [
    "UserRepository",
    "ImageRepository",
    "RecognizedWordRepository",
    "ImageLineReviewRepository",
    "DocumentPageRepository",
    "DocumentRegionRepository",
    "RegionPredictionRepository",
    "RegionReviewRepository",
    "DocumentRevisionRepository",
    "ReviewRevisionRepository",
    "TrainingExampleRepository",
    "RecognitionJobRepository",
    "ModelArtifactRepository",
    "TrainingPairRepository",
    "AdapterRepository",
    "TrainingRunRepository",
    "EvaluationResultRepository",
    "RepositoryFactory",
]
