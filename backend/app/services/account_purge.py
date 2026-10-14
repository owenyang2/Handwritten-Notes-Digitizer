"""Hard-delete every row and object owned by one account.

Account deletion disables the user and revokes sessions before this runs, so a
failure still leaves the account unable to log in. The purge itself removes
the user row, every row that points at that user, and the object-store prefixes
that hold their bytes. ``deletion_jobs`` is kept with ``user_id`` cleared so the
receipt survives without a live account.
"""

from __future__ import annotations

import uuid

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.data.base import Base
from app.data.models import (
    Adapter,
    Asset,
    ConsentEvent,
    Document,
    DocumentConflict,
    DocumentOperation,
    DocumentPage,
    DocumentRegion,
    DocumentRevision,
    DocumentSyncSnapshot,
    EvaluationResult,
    Image,
    ImageLineReview,
    InkBundle,
    ModelAssignment,
    PasswordResetToken,
    RecognitionJob,
    RecognitionRun,
    RecognizedWord,
    RegionPrediction,
    RegionReview,
    ReviewRevision,
    SyncClient,
    SyncCursorState,
    TrainingExample,
    TrainingPair,
    TrainingRun,
    TrainingRunPair,
    UploadPart,
    UploadSession,
    User,
    UserSession,
    UserVocabularyEntry,
    DeletionJob,
)
from app.data.object_store import ObjectStore

def collect_object_refs(session: Session, user_id: uuid.UUID) -> set[str]:
    """Return object-store references recorded on this user's rows."""
    refs: set[str] = set()

    def add(value: object) -> None:
        if isinstance(value, str) and value:
            refs.add(value)

    doc_ids = select(Document.document_id).where(Document.user_id == user_id)
    page_ids = select(DocumentPage.page_id).where(DocumentPage.document_id.in_(doc_ids))
    image_ids = select(Image.image_id).where(Image.user_id == user_id)
    upload_ids = select(UploadSession.upload_id).where(UploadSession.user_id == user_id)

    for (path,) in session.execute(select(Image.storage_path).where(Image.image_id.in_(image_ids))):
        add(path)
    for (path,) in session.execute(select(Document.original_path).where(Document.document_id.in_(doc_ids))):
        add(path)
    for row in session.execute(
        select(
            DocumentPage.original_path,
            DocumentPage.raster_path,
            DocumentPage.ink_asset_ref,
            DocumentPage.preview_asset_ref,
        ).where(DocumentPage.page_id.in_(page_ids))
    ):
        for value in row:
            add(value)
    for (path,) in session.execute(
        select(DocumentRegion.source_crop_path).where(DocumentRegion.page_id.in_(page_ids))
    ):
        add(path)
    for (path,) in session.execute(
        select(TrainingPair.region_crop_path).where(TrainingPair.user_id == user_id)
    ):
        add(path)
    for (path,) in session.execute(
        select(Adapter.weights_path).where(Adapter.user_id == user_id)
    ):
        add(path)
    for (path,) in session.execute(select(Asset.object_ref).where(Asset.user_id == user_id)):
        add(path)
    for (path,) in session.execute(
        select(UploadSession.assembled_ref).where(UploadSession.user_id == user_id)
    ):
        add(path)
    for (path,) in session.execute(
        select(UploadPart.storage_path).where(UploadPart.upload_id.in_(upload_ids))
    ):
        add(path)
    return refs


def _delete_objects(store: ObjectStore, user_id: uuid.UUID, refs: set[str]) -> None:
    for ref in refs:
        store.delete(ref)
    delete_owner = getattr(store, "delete_owner", None)
    if not callable(delete_owner):
        return
    try:
        delete_owner(str(user_id))
    except NotImplementedError:
        return


def delete_user_rows(session: Session, user_id: uuid.UUID) -> int:
    """Delete every row owned by ``user_id`` and return the deleted-row count.

    Child tables are removed before their parents so a database with foreign
    keys enabled accepts the purge. The user row is deleted last.
    """
    deleted = 0

    def run(stmt, *, count: bool = True) -> None:
        nonlocal deleted
        result = session.execute(stmt)
        if count and result.rowcount and result.rowcount > 0:
            deleted += int(result.rowcount)

    doc_ids = select(Document.document_id).where(Document.user_id == user_id)
    page_ids = select(DocumentPage.page_id).where(DocumentPage.document_id.in_(doc_ids))
    region_ids = select(DocumentRegion.region_id).where(DocumentRegion.page_id.in_(page_ids))
    image_ids = select(Image.image_id).where(Image.user_id == user_id)
    upload_ids = select(UploadSession.upload_id).where(UploadSession.user_id == user_id)
    run_ids = select(TrainingRun.run_id).where(TrainingRun.user_id == user_id)
    pair_ids = select(TrainingPair.pair_id).where(TrainingPair.user_id == user_id)

    run(
        update(DocumentPage)
        .where(DocumentPage.document_id.in_(doc_ids))
        .values(active_ink_bundle_id=None, image_id=None),
        count=False,
    )
    run(
        update(InkBundle)
        .where(InkBundle.user_id == user_id)
        .values(parent_bundle_id=None, native_asset_id=None),
        count=False,
    )
    run(
        update(TrainingPair)
        .where(TrainingPair.user_id == user_id)
        .values(consumed_by_run_id=None, image_id=None),
        count=False,
    )
    run(
        update(ModelAssignment)
        .where(ModelAssignment.user_id == user_id)
        .values(adapter_id=None),
        count=False,
    )
    run(
        update(UploadSession)
        .where(UploadSession.user_id == user_id)
        .values(document_id=None, page_id=None, ink_bundle_id=None),
        count=False,
    )
    run(update(Image).where(Image.user_id == user_id).values(document_id=None), count=False)
    run(
        update(ConsentEvent).where(ConsentEvent.user_id == user_id).values(document_id=None),
        count=False,
    )
    run(
        update(RecognitionJob)
        .where(RecognitionJob.user_id == user_id)
        .values(document_id=None, page_id=None, image_id=None),
        count=False,
    )
    run(
        update(RecognitionRun).where(RecognitionRun.user_id == user_id).values(job_id=None),
        count=False,
    )
    run(
        update(Asset).where(Asset.user_id == user_id).values(source_asset_id=None),
        count=False,
    )
    run(
        update(RegionReview)
        .where(RegionReview.author_user_id == user_id)
        .values(prediction_id=None),
        count=False,
    )
    run(
        update(ReviewRevision)
        .where(ReviewRevision.author_user_id == user_id)
        .values(prediction_id=None, document_revision_id=None),
        count=False,
    )
    run(
        update(RegionPrediction)
        .where(RegionPrediction.region_id.in_(region_ids))
        .values(model_assignment_id=None),
        count=False,
    )

    run(
        delete(TrainingRunPair).where(
            TrainingRunPair.run_id.in_(run_ids) | TrainingRunPair.pair_id.in_(pair_ids)
        )
    )
    run(delete(TrainingExample).where(TrainingExample.user_id == user_id))
    run(delete(RegionReview).where(RegionReview.author_user_id == user_id))
    run(delete(ReviewRevision).where(ReviewRevision.author_user_id == user_id))
    run(delete(RegionPrediction).where(RegionPrediction.region_id.in_(region_ids)))
    run(delete(RecognizedWord).where(RecognizedWord.image_id.in_(image_ids)))
    run(delete(ImageLineReview).where(ImageLineReview.image_id.in_(image_ids)))
    run(delete(UploadPart).where(UploadPart.upload_id.in_(upload_ids)))
    run(delete(DocumentRegion).where(DocumentRegion.page_id.in_(page_ids)))
    run(delete(InkBundle).where(InkBundle.user_id == user_id))
    run(delete(RecognitionRun).where(RecognitionRun.user_id == user_id))
    run(delete(RecognitionJob).where(RecognitionJob.user_id == user_id))
    run(delete(DocumentPage).where(DocumentPage.document_id.in_(doc_ids)))
    run(delete(DocumentRevision).where(DocumentRevision.author_user_id == user_id))
    run(delete(DocumentOperation).where(DocumentOperation.user_id == user_id))
    run(delete(DocumentConflict).where(DocumentConflict.user_id == user_id))
    run(delete(DocumentSyncSnapshot).where(DocumentSyncSnapshot.user_id == user_id))
    run(delete(EvaluationResult).where(EvaluationResult.user_id == user_id))
    run(delete(ModelAssignment).where(ModelAssignment.user_id == user_id))
    run(delete(TrainingPair).where(TrainingPair.user_id == user_id))
    run(delete(TrainingRun).where(TrainingRun.user_id == user_id))
    run(delete(Adapter).where(Adapter.user_id == user_id))
    run(delete(Image).where(Image.user_id == user_id))
    run(delete(UploadSession).where(UploadSession.user_id == user_id))
    run(delete(Asset).where(Asset.user_id == user_id))
    run(delete(Document).where(Document.user_id == user_id))
    run(delete(SyncClient).where(SyncClient.user_id == user_id))
    run(delete(SyncCursorState).where(SyncCursorState.user_id == user_id))
    run(delete(ConsentEvent).where(ConsentEvent.user_id == user_id))
    run(delete(UserVocabularyEntry).where(UserVocabularyEntry.user_id == user_id))
    run(delete(PasswordResetToken).where(PasswordResetToken.user_id == user_id))
    run(delete(UserSession).where(UserSession.user_id == user_id))

    # Catch a user-scoped table added later. Children above are already gone.
    for table in Base.metadata.tables.values():
        if table.name in {"users", "deletion_jobs"}:
            continue
        for column in table.columns:
            if column.name in {"user_id", "author_user_id"}:
                run(delete(table).where(column == user_id))

    run(
        update(DeletionJob).where(DeletionJob.user_id == user_id).values(user_id=None),
        count=False,
    )
    run(delete(User).where(User.user_id == user_id))
    return deleted


def purge_account(session: Session, store: ObjectStore, user_id: uuid.UUID) -> int:
    """Delete stored bytes and rows for ``user_id``. Does not commit."""
    refs = collect_object_refs(session, user_id)
    _delete_objects(store, user_id, refs)
    return delete_user_rows(session, user_id)


__all__ = ["collect_object_refs", "delete_user_rows", "purge_account"]
