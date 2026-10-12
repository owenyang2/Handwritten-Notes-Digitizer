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

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from .base import utcnow
from .enums import AdapterStatus, RecognitionJobState, TrainingRunState
from .models import (
    Adapter,
    Asset,
    ConsentEvent,
    DeletionJob,
    Document,
    DocumentConflict,
    DocumentOperation,
    DocumentSyncSnapshot,
    DocumentPage,
    DocumentRegion,
    DocumentRevision,
    EvaluationResult,
    Image,
    ImageLineReview,
    InkBundle,
    ModelArtifact,
    ModelAssignment,
    PasswordResetToken,
    RecognizedWord,
    RegionPrediction,
    RegionReview,
    RecognitionJob,
    RecognitionRun,
    ReviewRevision,
    SyncClient,
    SyncCursorState,
    TrainingExample,
    TrainingPair,
    TrainingRun,
    User,
    UserVocabularyEntry,
    UploadPart,
    UploadSession,
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

    def create(
        self,
        user_id: UUIDLike | str,
        title: str,
        *,
        document_id: UUIDLike | str | None = None,
        source_kind: str = "images",
        is_favorite: bool = False,
        default_language: str = "en",
        course_context: str | None = None,
        source_metadata: dict | None = None,
    ) -> Document:
        doc = Document(
            document_id=None if document_id is None else _as_uuid(document_id),
            user_id=_as_uuid(user_id),
            title=title,
            source_kind=source_kind,
            is_favorite=is_favorite,
            default_language=default_language,
            course_context=course_context,
            source_metadata=source_metadata,
        )
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
            Document.deleted_at.is_(None),
        )
        return self._session.scalars(stmt).one_or_none()

    def get_for_update(
        self, user_id: UUIDLike | str, document_id: UUIDLike | str
    ) -> Optional[Document]:
        """Return an owner-scoped live document while taking a row lock.

        PostgreSQL uses the lock to serialize sync-version increments. SQLite
        ignores ``FOR UPDATE`` but still benefits from the same single
        transaction semantics in the local/demo profile.
        """
        stmt = (
            select(Document)
            .where(
                Document.document_id == _as_uuid(document_id),
                Document.user_id == _as_uuid(user_id),
                Document.deleted_at.is_(None),
            )
            .with_for_update()
        )
        return self._session.scalars(stmt).one_or_none()

    def _library_statement(
        self,
        user_id: UUIDLike | str,
        *,
        query: str | None = None,
        include_archived: bool = False,
        include_trashed: bool = False,
        view: str | None = None,
    ):
        stmt = select(Document).where(
            Document.user_id == _as_uuid(user_id),
            Document.deleted_at.is_(None),
        )
        if view == "active":
            stmt = stmt.where(
                Document.archived_at.is_(None), Document.trashed_at.is_(None)
            )
        elif view == "archived":
            stmt = stmt.where(
                Document.archived_at.is_not(None), Document.trashed_at.is_(None)
            )
        elif view == "trash":
            stmt = stmt.where(Document.trashed_at.is_not(None))
        elif view == "favorites":
            stmt = stmt.where(
                Document.is_favorite.is_(True),
                Document.archived_at.is_(None),
                Document.trashed_at.is_(None),
            )
        else:
            if not include_archived:
                stmt = stmt.where(Document.archived_at.is_(None))
            if not include_trashed:
                stmt = stmt.where(Document.trashed_at.is_(None))
        if query:
            stmt = stmt.where(
                func.lower(Document.title).contains(
                    query.strip().lower(), autoescape=True
                )
            )
        return stmt

    def list_for_user(
        self,
        user_id: UUIDLike | str,
        *,
        query: str | None = None,
        include_archived: bool = False,
        include_trashed: bool = False,
        view: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Document]:
        """List a user's live documents with safe library filters.

        ``deleted_at`` is always excluded.  Trash is opt-in so the normal
        library cannot accidentally reopen a discarded note; archived notes
        are also opt-in for the active library view.
        """
        stmt = self._library_statement(
            user_id,
            query=query,
            include_archived=include_archived,
            include_trashed=include_trashed,
            view=view,
        )
        stmt = stmt.order_by(Document.updated_at.desc(), Document.document_id.desc())
        stmt = stmt.limit(max(1, min(limit, 100))).offset(max(0, offset))
        return list(self._session.scalars(stmt).all())

    def count_for_user(
        self,
        user_id: UUIDLike | str,
        *,
        query: str | None = None,
        include_archived: bool = False,
        include_trashed: bool = False,
        view: str | None = None,
    ) -> int:
        """Count the same filtered library view used by ``list_for_user``."""
        stmt = self._library_statement(
            user_id,
            query=query,
            include_archived=include_archived,
            include_trashed=include_trashed,
            view=view,
        )
        count_stmt = select(func.count()).select_from(stmt.subquery())
        return int(self._session.scalar(count_stmt) or 0)

    def update_metadata(
        self,
        user_id: UUIDLike | str,
        document_id: UUIDLike | str,
        *,
        title: str | None = None,
        archived: bool | None = None,
        favorite: bool | None = None,
        default_language: str | None = None,
        course_context: str | None = None,
        set_course_context: bool = False,
        expected_sync_version: int | None = None,
    ) -> Optional[Document]:
        """Update owner-scoped library metadata and advance the sync version."""
        document = self.get(user_id, document_id)
        if document is None:
            return None
        if (
            expected_sync_version is not None
            and expected_sync_version != document.sync_version
        ):
            raise ValueError(
                f"Document is stale; expected sync version {document.sync_version}."
            )
        if title is not None:
            document.title = title
        if archived is not None:
            document.archived_at = utcnow() if archived else None
        if favorite is not None:
            document.is_favorite = favorite
        if default_language is not None:
            document.default_language = default_language
        if set_course_context:
            document.course_context = course_context
        document.sync_version += 1
        document.updated_at = utcnow()
        self._record_sync_operation(document, "document.metadata", {
            "title": document.title,
            "archived": document.archived_at is not None,
            "is_favorite": document.is_favorite,
            "default_language": document.default_language,
            "course_context": document.course_context,
        })
        self._session.flush()
        return document

    def _record_sync_operation(self, document: Document, operation_type: str, payload: dict) -> None:
        """Publish REST mutations in the same transaction as the canonical state."""
        self._session.add(DocumentOperation(
            user_id=document.user_id,
            document_id=document.document_id,
            client_id="server:documents",
            client_operation_id=uuid.uuid4(),
            base_sync_version=document.sync_version - 1,
            document_sync_version=document.sync_version,
            entity_type="document",
            entity_id=str(document.document_id),
            operation_type=operation_type,
            payload=payload,
            created_at=document.updated_at,
        ))

    def touch(
        self,
        user_id: UUIDLike | str,
        document_id: UUIDLike | str,
        *,
        bump_sync_version: bool = True,
    ) -> Optional[Document]:
        """Mark related content changes in the document library metadata."""
        document = self.get(user_id, document_id)
        if document is None:
            return None
        if bump_sync_version:
            document.sync_version += 1
        document.updated_at = utcnow()
        self._session.flush()
        return document

    def trash(
        self, user_id: UUIDLike | str, document_id: UUIDLike | str
    ) -> Optional[Document]:
        document = self.get(user_id, document_id)
        if document is None:
            return None
        if document.trashed_at is None:
            document.trashed_at = utcnow()
            document.sync_version += 1
            document.updated_at = utcnow()
            self._record_sync_operation(document, "document.trash", {})
            self._session.flush()
        return document

    def restore(
        self, user_id: UUIDLike | str, document_id: UUIDLike | str
    ) -> Optional[Document]:
        document = self.get(user_id, document_id)
        if document is None:
            return None
        if document.trashed_at is not None:
            document.trashed_at = None
            document.sync_version += 1
            document.updated_at = utcnow()
            self._record_sync_operation(document, "document.restore", {})
            self._session.flush()
        return document


class DocumentSyncRepository(_ScopedRepository):
    """Owner-scoped access to accepted sync operations and sync watermarks."""

    def get_operation(
        self,
        user_id: UUIDLike | str,
        client_id: str,
        client_operation_id: UUIDLike | str,
    ) -> Optional[DocumentOperation]:
        stmt = select(DocumentOperation).where(
            DocumentOperation.user_id == _as_uuid(user_id),
            DocumentOperation.client_id == client_id,
            DocumentOperation.client_operation_id == _as_uuid(client_operation_id),
        )
        return self._session.scalars(stmt).one_or_none()

    def get_conflict(
        self,
        user_id: UUIDLike | str,
        conflict_id: UUIDLike | str,
    ) -> Optional[DocumentConflict]:
        stmt = select(DocumentConflict).where(
            DocumentConflict.conflict_id == _as_uuid(conflict_id),
            DocumentConflict.user_id == _as_uuid(user_id),
        )
        return self._session.scalars(stmt).one_or_none()

    def get_conflict_for_operation(
        self,
        user_id: UUIDLike | str,
        client_id: str,
        client_operation_id: UUIDLike | str,
    ) -> Optional[DocumentConflict]:
        stmt = select(DocumentConflict).where(
            DocumentConflict.user_id == _as_uuid(user_id),
            DocumentConflict.client_id == client_id,
            DocumentConflict.client_operation_id == _as_uuid(client_operation_id),
        )
        return self._session.scalars(stmt).one_or_none()

    def add_operation(self, operation: DocumentOperation) -> DocumentOperation:
        self._session.add(operation)
        self._session.flush()
        return operation

    def add_conflict(self, conflict: DocumentConflict) -> DocumentConflict:
        self._session.add(conflict)
        self._session.flush()
        return conflict

    def list_operations(
        self,
        user_id: UUIDLike | str,
        *,
        cursor: int = 0,
        limit: int = 50,
    ) -> Sequence[DocumentOperation]:
        """Return retained changes after a global server cursor.

        Compacted operations remain in the database for audit/idempotency but
        are intentionally omitted from replay. A client whose cursor predates
        the repository's watermark must resync from canonical document state.
        """
        stmt = (
            select(DocumentOperation)
            .where(
                DocumentOperation.user_id == _as_uuid(user_id),
                DocumentOperation.server_sequence > max(0, cursor),
                DocumentOperation.compacted_at.is_(None),
            )
            .order_by(DocumentOperation.server_sequence)
            .limit(max(1, min(limit, 101)))
        )
        return list(self._session.scalars(stmt).all())

    def current_cursor(self) -> int:
        """Return the greatest server sequence in the shared operation log."""
        return int(
            self._session.scalar(select(func.max(DocumentOperation.server_sequence)))
            or 0
        )

    def cursor_floor(self, user_id: UUIDLike | str) -> int:
        """Return the oldest cursor that can be replayed incrementally."""
        state = self._session.get(SyncCursorState, _as_uuid(user_id))
        return int(state.cursor_floor) if state is not None else 0

    def get_cursor_state(
        self, user_id: UUIDLike | str
    ) -> Optional[SyncCursorState]:
        return self._session.get(SyncCursorState, _as_uuid(user_id))

    def ensure_cursor_state(self, user_id: UUIDLike | str) -> SyncCursorState:
        state = self.get_cursor_state(user_id)
        if state is None:
            state = SyncCursorState(user_id=_as_uuid(user_id), cursor_floor=0)
            self._session.add(state)
            self._session.flush()
        return state

    def list_snapshots(
        self,
        user_id: UUIDLike | str,
        *,
        at_or_before_cursor: int | None = None,
    ) -> Sequence[DocumentSyncSnapshot]:
        stmt = select(DocumentSyncSnapshot).where(
            DocumentSyncSnapshot.user_id == _as_uuid(user_id)
        )
        if at_or_before_cursor is not None:
            stmt = stmt.where(
                DocumentSyncSnapshot.server_sequence <= max(0, at_or_before_cursor)
            )
        return list(
            self._session.scalars(
                stmt.order_by(
                    DocumentSyncSnapshot.server_sequence,
                    DocumentSyncSnapshot.document_id,
                )
            ).all()
        )

    def compact_operations(
        self,
        user_id: UUIDLike | str,
        *,
        through_cursor: int,
        tombstone_retention_before: _dt.datetime | None = None,
    ) -> tuple[int, int, int]:
        """Create canonical snapshots and mark replay rows compacted.

        Returns ``(compacted_count, snapshot_count, cursor_floor)``. Compaction
        is refused beyond the slowest active client acknowledgement. Tombstones
        are retained by default; when a retention cutoff is supplied, only
        tombstones older than that cutoff may be compacted.
        """
        uid = _as_uuid(user_id)
        through_cursor = int(through_cursor)
        if through_cursor < 0:
            raise ValueError("through_cursor must be non-negative")
        current = self.current_cursor()
        if through_cursor > current:
            raise ValueError("through_cursor is ahead of the server cursor")

        active_min = self._session.scalar(
            select(func.min(SyncClient.cursor)).where(
                SyncClient.user_id == uid,
                SyncClient.revoked_at.is_(None),
            )
        )
        if active_min is not None and through_cursor > int(active_min):
            raise ValueError(
                "Cannot compact past the slowest active client cursor"
            )

        state = self.ensure_cursor_state(uid)
        if through_cursor <= state.cursor_floor:
            return 0, 0, int(state.cursor_floor)

        document_ids = list(
            self._session.scalars(
                select(DocumentOperation.document_id)
                .where(
                    DocumentOperation.user_id == uid,
                    DocumentOperation.server_sequence <= through_cursor,
                )
                .distinct()
            ).all()
        )
        snapshot_count = 0
        now = utcnow()
        for document_id in document_ids:
            document = self._session.scalar(
                select(Document).where(
                    Document.document_id == document_id,
                    Document.user_id == uid,
                )
            )
            if document is None:
                continue
            existing_snapshot = self._session.scalar(
                select(DocumentSyncSnapshot).where(
                    DocumentSyncSnapshot.document_id == document.document_id,
                    DocumentSyncSnapshot.server_sequence == through_cursor,
                )
            )
            if existing_snapshot is None:
                self._session.add(
                    DocumentSyncSnapshot(
                        user_id=uid,
                        document_id=document.document_id,
                        server_sequence=through_cursor,
                        document_sync_version=document.sync_version,
                        state={
                            "document_id": str(document.document_id),
                            "title": document.title,
                            "source_kind": document.source_kind,
                            "is_favorite": document.is_favorite,
                            "active_revision_id": (
                                str(document.active_revision_id)
                                if document.active_revision_id is not None
                                else None
                            ),
                            "archived_at": (
                                document.archived_at.isoformat()
                                if document.archived_at is not None
                                else None
                            ),
                            "trashed_at": (
                                document.trashed_at.isoformat()
                                if document.trashed_at is not None
                                else None
                            ),
                            "deleted_at": (
                                document.deleted_at.isoformat()
                                if document.deleted_at is not None
                                else None
                            ),
                            "sync_version": document.sync_version,
                        },
                        created_at=now,
                    )
                )
                snapshot_count += 1

        compactable = [
            DocumentOperation.compacted_at.is_(None),
            DocumentOperation.user_id == uid,
            DocumentOperation.server_sequence <= through_cursor,
        ]
        if tombstone_retention_before is None:
            compactable.append(DocumentOperation.is_tombstone.is_(False))
        else:
            compactable.append(
                or_(
                    DocumentOperation.is_tombstone.is_(False),
                    DocumentOperation.created_at <= tombstone_retention_before,
                )
            )
        result = self._session.execute(
            update(DocumentOperation)
            .where(and_(*compactable))
            .values(compacted_at=now)
        )
        state.cursor_floor = through_cursor
        state.updated_at = now
        self._session.flush()
        return int(result.rowcount or 0), snapshot_count, through_cursor

    def list_conflicts(
        self,
        user_id: UUIDLike | str,
        *,
        include_resolved: bool = False,
    ) -> Sequence[DocumentConflict]:
        stmt = select(DocumentConflict).where(
            DocumentConflict.user_id == _as_uuid(user_id)
        )
        if not include_resolved:
            stmt = stmt.where(DocumentConflict.status == "pending")
        stmt = stmt.order_by(DocumentConflict.created_at.desc())
        return list(self._session.scalars(stmt).all())

    def resolve_conflict(
        self,
        user_id: UUIDLike | str,
        conflict_id: UUIDLike | str,
        *,
        resolution: dict,
    ) -> Optional[DocumentConflict]:
        conflict = self.get_conflict(user_id, conflict_id)
        if conflict is None:
            return None
        if conflict.status != "resolved":
            conflict.status = "resolved"
            conflict.resolution = resolution
            conflict.resolved_at = utcnow()
            self._session.flush()
        return conflict


class SyncClientRepository(_ScopedRepository):
    """Owner-scoped durable acknowledgement cursors for sync clients."""

    def get(self, user_id: UUIDLike | str, client_id: str) -> Optional[SyncClient]:
        stmt = select(SyncClient).where(
            SyncClient.user_id == _as_uuid(user_id),
            SyncClient.client_id == client_id,
        )
        return self._session.scalars(stmt).one_or_none()

    def register(
        self,
        user_id: UUIDLike | str,
        client_id: str,
        *,
        platform: str | None = None,
        device_name: str | None = None,
    ) -> SyncClient:
        client = self.get(user_id, client_id)
        if client is None:
            client = SyncClient(
                user_id=_as_uuid(user_id),
                client_id=client_id,
                platform=platform,
                device_name=device_name,
            )
            self._session.add(client)
        else:
            if platform is not None:
                client.platform = platform
            if device_name is not None:
                client.device_name = device_name
            client.revoked_at = None
            client.last_seen_at = utcnow()
        self._session.flush()
        return client

    def acknowledge(
        self, user_id: UUIDLike | str, client_id: str, cursor: int
    ) -> Optional[SyncClient]:
        client = self.get(user_id, client_id)
        if client is None or client.revoked_at is not None:
            return None
        if cursor < client.cursor:
            raise ValueError("client cursor cannot move backwards")
        client.cursor = cursor
        client.last_seen_at = utcnow()
        self._session.flush()
        return client

    def list_for_user(self, user_id: UUIDLike | str) -> Sequence[SyncClient]:
        stmt = (
            select(SyncClient)
            .where(SyncClient.user_id == _as_uuid(user_id))
            .order_by(SyncClient.created_at, SyncClient.client_id)
        )
        return list(self._session.scalars(stmt).all())

    def revoke(self, user_id: UUIDLike | str, client_id: str) -> bool:
        client = self.get(user_id, client_id)
        if client is None:
            return False
        client.revoked_at = client.revoked_at or utcnow()
        self._session.flush()
        return True


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


class ModelAssignmentRepository(_ScopedRepository):
    """Per-user model resolution records used for prediction provenance."""

    def add(self, assignment: ModelAssignment) -> ModelAssignment:
        self._session.add(assignment)
        self._session.flush()
        return assignment

    def get_by_key(
        self,
        user_id: UUIDLike | str,
        task: str,
        assignment_key: str,
    ) -> Optional[ModelAssignment]:
        stmt = select(ModelAssignment).where(
            ModelAssignment.user_id == _as_uuid(user_id),
            ModelAssignment.task == task,
            ModelAssignment.assignment_key == assignment_key,
        )
        return self._session.scalars(stmt).one_or_none()

    def get_active(
        self, user_id: UUIDLike | str, task: str
    ) -> Optional[ModelAssignment]:
        stmt = select(ModelAssignment).where(
            ModelAssignment.user_id == _as_uuid(user_id),
            ModelAssignment.task == task,
            ModelAssignment.active.is_(True),
        )
        return self._session.scalars(stmt).first()

    def list_for_user(
        self, user_id: UUIDLike | str, *, include_inactive: bool = True
    ) -> Sequence[ModelAssignment]:
        stmt = select(ModelAssignment).where(
            ModelAssignment.user_id == _as_uuid(user_id)
        )
        if not include_inactive:
            stmt = stmt.where(ModelAssignment.active.is_(True))
        stmt = stmt.order_by(
            ModelAssignment.task,
            ModelAssignment.created_at.desc(),
            ModelAssignment.assignment_id.desc(),
        )
        return list(self._session.scalars(stmt).all())

    def retire_active(
        self, user_id: UUIDLike | str, task: str, *, except_id: uuid.UUID | None = None
    ) -> None:
        stmt = select(ModelAssignment).where(
            ModelAssignment.user_id == _as_uuid(user_id),
            ModelAssignment.task == task,
            ModelAssignment.active.is_(True),
        )
        for assignment in self._session.scalars(stmt).all():
            if except_id is not None and assignment.assignment_id == except_id:
                continue
            assignment.active = False
            assignment.retired_at = utcnow()

    def activate(self, assignment: ModelAssignment) -> ModelAssignment:
        self.retire_active(
            assignment.user_id, assignment.task, except_id=assignment.assignment_id
        )
        assignment.active = True
        assignment.retired_at = None
        self._session.flush()
        return assignment


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


class RecognitionRunRepository(_ScopedRepository):
    """Owner-scoped immutable recognition-run envelopes."""

    def get(
        self, user_id: UUIDLike | str, run_id: UUIDLike | str
    ) -> Optional[RecognitionRun]:
        stmt = select(RecognitionRun).where(
            RecognitionRun.run_id == _as_uuid(run_id),
            RecognitionRun.user_id == _as_uuid(user_id),
        )
        return self._session.scalars(stmt).one_or_none()

    def get_by_idempotency(
        self, user_id: UUIDLike | str, idempotency_key: UUIDLike | str
    ) -> Optional[RecognitionRun]:
        stmt = select(RecognitionRun).where(
            RecognitionRun.user_id == _as_uuid(user_id),
            RecognitionRun.idempotency_key == _as_uuid(idempotency_key),
        )
        return self._session.scalars(stmt).one_or_none()

    def add(self, run: RecognitionRun) -> RecognitionRun:
        self._session.add(run)
        self._session.flush()
        return run


class UploadRepository(_ScopedRepository):
    """Owner-scoped resumable upload sessions and parts."""

    def get(
        self, user_id: UUIDLike | str, upload_id: UUIDLike | str
    ) -> Optional[UploadSession]:
        stmt = select(UploadSession).where(
            UploadSession.upload_id == _as_uuid(upload_id),
            UploadSession.user_id == _as_uuid(user_id),
        )
        return self._session.scalars(stmt).one_or_none()

    def get_by_idempotency(
        self, user_id: UUIDLike | str, idempotency_key: UUIDLike | str
    ) -> Optional[UploadSession]:
        stmt = select(UploadSession).where(
            UploadSession.user_id == _as_uuid(user_id),
            UploadSession.idempotency_key == _as_uuid(idempotency_key),
        )
        return self._session.scalars(stmt).one_or_none()

    def add(self, session: UploadSession) -> UploadSession:
        self._session.add(session)
        self._session.flush()
        return session

    def get_part(
        self, upload_id: UUIDLike | str, part_number: int
    ) -> Optional[UploadPart]:
        stmt = select(UploadPart).where(
            UploadPart.upload_id == _as_uuid(upload_id),
            UploadPart.part_number == part_number,
        )
        return self._session.scalars(stmt).one_or_none()

    def add_part(self, part: UploadPart) -> UploadPart:
        self._session.add(part)
        self._session.flush()
        return part

    def list_parts(self, upload_id: UUIDLike | str) -> Sequence[UploadPart]:
        stmt = (
            select(UploadPart)
            .where(UploadPart.upload_id == _as_uuid(upload_id))
            .order_by(UploadPart.part_number)
        )
        return list(self._session.scalars(stmt).all())


class DeletionJobRepository(_ScopedRepository):
    """Owner-scoped account deletion evidence rows."""

    def get(
        self, user_id: UUIDLike | str, job_id: UUIDLike | str
    ) -> Optional[DeletionJob]:
        stmt = select(DeletionJob).where(
            DeletionJob.job_id == _as_uuid(job_id),
            DeletionJob.user_id == _as_uuid(user_id),
        )
        return self._session.scalars(stmt).one_or_none()

    def add(self, job: DeletionJob) -> DeletionJob:
        self._session.add(job)
        self._session.flush()
        return job

    def latest_for_user(self, user_id: UUIDLike | str) -> Optional[DeletionJob]:
        stmt = (
            select(DeletionJob)
            .where(DeletionJob.user_id == _as_uuid(user_id))
            .order_by(DeletionJob.created_at.desc())
        )
        return self._session.scalars(stmt).first()


class AssetRepository(_ScopedRepository):
    def add(self, asset: Asset) -> Asset:
        self._session.add(asset)
        self._session.flush()
        return asset

    def get(self, user_id: UUIDLike | str, asset_id: UUIDLike | str) -> Optional[Asset]:
        stmt = select(Asset).where(
            Asset.asset_id == _as_uuid(asset_id),
            Asset.user_id == _as_uuid(user_id),
        )
        return self._session.scalars(stmt).one_or_none()

    def get_by_hash(
        self, user_id: UUIDLike | str, content_hash: str, media_type: str
    ) -> Optional[Asset]:
        stmt = select(Asset).where(
            Asset.user_id == _as_uuid(user_id),
            Asset.content_hash == content_hash,
            Asset.media_type == media_type,
        )
        return self._session.scalars(stmt).one_or_none()

    def total_bytes(self, user_id: UUIDLike | str) -> int:
        stmt = select(func.coalesce(func.sum(Asset.byte_size), 0)).where(
            Asset.user_id == _as_uuid(user_id)
        )
        return int(self._session.scalar(stmt) or 0)


class InkBundleRepository(_ScopedRepository):
    def add(self, bundle: InkBundle) -> InkBundle:
        self._session.add(bundle)
        self._session.flush()
        return bundle

    def get(
        self, user_id: UUIDLike | str, bundle_id: UUIDLike | str
    ) -> Optional[InkBundle]:
        stmt = select(InkBundle).where(
            InkBundle.bundle_id == _as_uuid(bundle_id),
            InkBundle.user_id == _as_uuid(user_id),
        )
        return self._session.scalars(stmt).one_or_none()

    def get_by_hash(
        self, user_id: UUIDLike | str, page_id: UUIDLike | str, content_hash: str
    ) -> Optional[InkBundle]:
        stmt = select(InkBundle).where(
            InkBundle.user_id == _as_uuid(user_id),
            InkBundle.page_id == _as_uuid(page_id),
            InkBundle.content_hash == content_hash,
        )
        return self._session.scalars(stmt).one_or_none()

    def next_revision_number(
        self, user_id: UUIDLike | str, page_id: UUIDLike | str
    ) -> int:
        stmt = select(func.max(InkBundle.revision_number)).where(
            InkBundle.user_id == _as_uuid(user_id),
            InkBundle.page_id == _as_uuid(page_id),
        )
        return int(self._session.scalar(stmt) or 0) + 1

    def list_for_page(
        self, user_id: UUIDLike | str, page_id: UUIDLike | str
    ) -> Sequence[InkBundle]:
        stmt = (
            select(InkBundle)
            .where(
                InkBundle.user_id == _as_uuid(user_id),
                InkBundle.page_id == _as_uuid(page_id),
            )
            .order_by(InkBundle.revision_number.desc())
        )
        return list(self._session.scalars(stmt).all())

    def latest_for_page(
        self, user_id: UUIDLike | str, page_id: UUIDLike | str
    ) -> Optional[InkBundle]:
        stmt = (
            select(InkBundle)
            .where(
                InkBundle.user_id == _as_uuid(user_id),
                InkBundle.page_id == _as_uuid(page_id),
            )
            .order_by(InkBundle.revision_number.desc(), InkBundle.created_at.desc())
            .limit(1)
        )
        return self._session.scalars(stmt).first()


class ConsentEventRepository(_ScopedRepository):
    def add(self, event: ConsentEvent) -> ConsentEvent:
        self._session.add(event)
        self._session.flush()
        return event

    def latest(
        self, user_id: UUIDLike | str, *, scope: str | None = None
    ) -> Optional[ConsentEvent]:
        stmt = select(ConsentEvent).where(ConsentEvent.user_id == _as_uuid(user_id))
        if scope:
            stmt = stmt.where(ConsentEvent.scope == scope)
        stmt = stmt.order_by(ConsentEvent.created_at.desc()).limit(1)
        return self._session.scalars(stmt).first()

    def list_for_user(
        self, user_id: UUIDLike | str, *, limit: int = 50
    ) -> Sequence[ConsentEvent]:
        stmt = (
            select(ConsentEvent)
            .where(ConsentEvent.user_id == _as_uuid(user_id))
            .order_by(ConsentEvent.created_at.desc())
            .limit(limit)
        )
        return list(self._session.scalars(stmt).all())


class VocabularyRepository(_ScopedRepository):
    def add(self, entry: UserVocabularyEntry) -> UserVocabularyEntry:
        self._session.add(entry)
        self._session.flush()
        return entry

    def list_for_user(self, user_id: UUIDLike | str) -> Sequence[UserVocabularyEntry]:
        stmt = (
            select(UserVocabularyEntry)
            .where(UserVocabularyEntry.user_id == _as_uuid(user_id))
            .order_by(UserVocabularyEntry.token)
        )
        return list(self._session.scalars(stmt).all())

    def upsert(
        self, user_id: UUIDLike | str, token: str, preferred_form: str
    ) -> UserVocabularyEntry:
        stmt = select(UserVocabularyEntry).where(
            UserVocabularyEntry.user_id == _as_uuid(user_id),
            UserVocabularyEntry.token == token,
        )
        existing = self._session.scalars(stmt).one_or_none()
        if existing is None:
            return self.add(
                UserVocabularyEntry(
                    user_id=_as_uuid(user_id),
                    token=token,
                    preferred_form=preferred_form,
                )
            )
        existing.preferred_form = preferred_form
        self._session.flush()
        return existing


class PasswordResetRepository(_ScopedRepository):
    def add(self, token: PasswordResetToken) -> PasswordResetToken:
        self._session.add(token)
        self._session.flush()
        return token

    def get(self, token_hash: str) -> Optional[PasswordResetToken]:
        return self._session.get(PasswordResetToken, token_hash)


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
        self.sync = DocumentSyncRepository(session)
        self.sync_clients = SyncClientRepository(session)
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
        self.recognition_runs = RecognitionRunRepository(session)
        self.uploads = UploadRepository(session)
        self.deletion_jobs = DeletionJobRepository(session)
        self.assets = AssetRepository(session)
        self.ink_bundles = InkBundleRepository(session)
        self.consent_events = ConsentEventRepository(session)
        self.vocabulary = VocabularyRepository(session)
        self.password_resets = PasswordResetRepository(session)
        self.model_artifacts = ModelArtifactRepository(session)
        self.model_assignments = ModelAssignmentRepository(session)
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
    "DocumentSyncRepository",
    "SyncClientRepository",
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
    "RecognitionRunRepository",
    "UploadRepository",
    "DeletionJobRepository",
    "AssetRepository",
    "InkBundleRepository",
    "ConsentEventRepository",
    "VocabularyRepository",
    "PasswordResetRepository",
    "ModelArtifactRepository",
    "ModelAssignmentRepository",
    "TrainingPairRepository",
    "AdapterRepository",
    "TrainingRunRepository",
    "EvaluationResultRepository",
    "DocumentRepository",
    "RepositoryFactory",
]
