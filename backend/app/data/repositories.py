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

import uuid
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from .enums import AdapterStatus, TrainingRunState
from .models import (
    Adapter,
    EvaluationResult,
    Image,
    RecognizedWord,
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
        self.images = ImageRepository(session)
        self.recognized_words = RecognizedWordRepository(session)
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
    "TrainingPairRepository",
    "AdapterRepository",
    "TrainingRunRepository",
    "EvaluationResultRepository",
    "RepositoryFactory",
]
