"""Persist reviewed line/region text so export and reload share one contract."""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.data.enums import TrainingExampleSplit
from app.data.models import DocumentRevision, RegionReview, ReviewRevision, TrainingExample, User
from app.data.repositories import RepositoryFactory
from app.services.auth_api import get_current_user, get_repositories
from app.services.isolation import get_or_404

router = APIRouter(tags=["review"])


class RegionInput(BaseModel):
    x: int = Field(default=0, ge=0)
    y: int = Field(default=0, ge=0)
    width: int = Field(default=0, ge=0)
    height: int = Field(default=0, ge=0)


class LineReviewInput(BaseModel):
    line_index: int = Field(ge=0)
    reviewed_text: str = Field(default="", max_length=10_000)
    excluded: bool = False
    region: Optional[RegionInput] = None


class SaveImageReviewsRequest(BaseModel):
    lines: list[LineReviewInput] = Field(default_factory=list)


class LineReviewResponse(BaseModel):
    line_index: int
    reviewed_text: str
    excluded: bool


class SaveImageReviewsResponse(BaseModel):
    image_id: uuid.UUID
    lines: list[LineReviewResponse]


def save_image_line_reviews(
    user_id: uuid.UUID,
    image_id: uuid.UUID,
    lines: list[LineReviewInput],
    repos: RepositoryFactory,
) -> list[LineReviewResponse]:
    get_or_404(repos.images.get(user_id, image_id))
    saved: list[LineReviewResponse] = []
    for line in lines:
        region = line.region or RegionInput()
        review = repos.line_reviews.upsert(
            user_id,
            image_id,
            line.line_index,
            line.reviewed_text,
            line.excluded,
            region_x=region.x,
            region_y=region.y,
            region_width=region.width,
            region_height=region.height,
        )
        saved.append(
            LineReviewResponse(
                line_index=review.line_index,
                reviewed_text=review.reviewed_text,
                excluded=review.excluded,
            )
        )
    repos.session.commit()
    return saved


@router.put(
    "/images/{image_id}/reviews",
    response_model=SaveImageReviewsResponse,
    summary="Persist line-level review and exclusion state",
)
def put_image_reviews(
    image_id: uuid.UUID,
    body: SaveImageReviewsRequest,
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
) -> SaveImageReviewsResponse:
    lines = save_image_line_reviews(user.user_id, image_id, body.lines, repos)
    return SaveImageReviewsResponse(image_id=image_id, lines=lines)


class RegionReviewRequest(BaseModel):
    reviewed_text: str = Field(max_length=10_000)
    excluded: bool = False
    prediction_id: Optional[uuid.UUID] = None
    expected_version: Optional[int] = Field(default=None, ge=1)
    include_in_training: bool = False
    split: TrainingExampleSplit = TrainingExampleSplit.UNASSIGNED


class RegionReviewResponse(BaseModel):
    review_id: uuid.UUID
    region_id: uuid.UUID
    reviewed_text: str
    excluded: bool
    version: int = 1
    review_revision_id: uuid.UUID | None = None
    document_revision_id: uuid.UUID | None = None
    training_example_id: uuid.UUID | None = None


def _document_snapshot(
    user_id: uuid.UUID,
    document_id: uuid.UUID,
    repos: RepositoryFactory,
    *,
    current_region_id: uuid.UUID | None = None,
    current_review: ReviewRevision | None = None,
) -> list[dict[str, object]]:
    """Capture every page/region in deterministic reading order.

    A revision is a document snapshot, not merely the page that happened to
    be open when a review was saved.  Keeping review ids as immutable pointers
    means later edits cannot change an already exported revision.
    """
    snapshot: list[dict[str, object]] = []
    pages = repos.document_pages.list_for_document(user_id, document_id)
    for page in pages:
        for region in repos.document_regions.list_for_page(user_id, page.page_id):
            canonical = (
                current_review
                if current_region_id is not None
                and region.region_id == current_region_id
                else repos.review_revisions.latest_for_region(user_id, region.region_id)
            )
            legacy = None if canonical is not None else repos.region_reviews.latest_for_region(
                user_id, region.region_id
            )
            review = canonical or legacy
            prediction = repos.region_predictions.latest_for_region(user_id, region.region_id)
            snapshot.append(
                {
                    "region_id": str(region.region_id),
                    "page_index": page.page_index,
                    "order_index": region.order_index,
                    "content_type": region.content_type,
                    "review_revision_id": (
                        str(canonical.review_revision_id) if canonical is not None else None
                    ),
                    # Copy the display state as well as the normalized row ids.
                    # This keeps an old revision exportable after a region is
                    # deleted, split, or receives a later prediction.
                    "reviewed_text": getattr(review, "reviewed_text", None),
                    "excluded": bool(getattr(review, "excluded", False)),
                    "prediction_text": prediction.raw_output if prediction is not None else None,
                    "asset_ref": region.source_crop_path,
                }
            )
    return snapshot


def create_document_revision(
    user_id: uuid.UUID,
    document,
    repos: RepositoryFactory,
    *,
    force: bool = False,
) -> DocumentRevision | None:
    """Snapshot the current document after a structural/prediction mutation.

    Before the first reviewed revision, exports intentionally read the live
    region/prediction rows so a newly created crop is immediately usable. Once
    a document has an active revision, later changes must advance that pointer
    or an export could silently omit a newly added/reordered region. The
    review endpoint keeps a specialized path so the just-created immutable
    review can be included in the same initial snapshot.
    """
    if document.active_revision_id is None and not force:
        return None
    revision = DocumentRevision(
        document_id=document.document_id,
        revision_number=repos.document_revisions.next_number(user_id, document.document_id),
        author_user_id=user_id,
        parent_revision_id=document.active_revision_id,
        block_snapshot={
            "blocks": _document_snapshot(
                user_id,
                document.document_id,
                repos,
            )
        },
    )
    repos.document_revisions.add(revision)
    document.active_revision_id = revision.revision_id
    return revision


@router.post(
    "/regions/{region_id}/review",
    response_model=RegionReviewResponse,
    summary="Persist an immutable reviewed revision for a document region",
)
def post_region_review(
    region_id: uuid.UUID,
    body: RegionReviewRequest,
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
) -> RegionReviewResponse:
    region = get_or_404(repos.document_regions.get(user.user_id, region_id))
    if body.prediction_id is not None:
        prediction = repos.region_predictions.get(user.user_id, body.prediction_id)
        if prediction is None or prediction.region_id != region.region_id:
            # Do not allow a review to link to another user's prediction or to
            # a prediction belonging to a different region.
            raise HTTPException(
                status_code=422,
                detail="prediction_id does not belong to this region.",
            )

    previous = repos.review_revisions.latest_for_region(user.user_id, region.region_id)
    legacy_previous = repos.region_reviews.latest_for_region(user.user_id, region.region_id)
    current_version = max(
        int(previous.version) if previous is not None else 0,
        int(legacy_previous.version) if legacy_previous is not None else 0,
    )
    if body.expected_version is not None and body.expected_version != current_version:
        raise HTTPException(
            status_code=409,
            detail=f"Review is stale; expected version {current_version}.",
        )
    next_version = current_version + 1
    if body.include_in_training and (body.excluded or not body.reviewed_text.strip()):
        raise HTTPException(
            status_code=422,
            detail="Only non-empty, non-excluded reviews may be added to training.",
        )
    if body.include_in_training and not region.source_crop_path:
        raise HTTPException(
            status_code=422,
            detail="This region has no retained crop and cannot be used for training.",
        )
    crop_hash = region.crop_sha256
    if body.include_in_training and not crop_hash and region.source_crop_path:
        # Older rows may not have the digest column populated.  The route does
        # not have to read object storage just to preserve a review; callers
        # can still review it, but consented training requires a real digest.
        raise HTTPException(
            status_code=422,
            detail="This region is missing its crop hash; recreate the crop before training.",
        )

    # Keep the legacy table populated for compatibility with the original
    # image workflow while the new immutable table is the canonical history.
    review = RegionReview(
        region_id=region.region_id,
        prediction_id=body.prediction_id,
        reviewed_text=body.reviewed_text,
        excluded=body.excluded,
        author_user_id=user.user_id,
        crop_revision=region.page_revision,
        crop_sha256=crop_hash,
        version=next_version,
    )
    repos.region_reviews.add(review)
    canonical = ReviewRevision(
        region_id=region.region_id,
        prediction_id=body.prediction_id,
        reviewed_text=body.reviewed_text,
        excluded=body.excluded,
        crop_revision=region.page_revision,
        crop_sha256=crop_hash,
        version=next_version,
        author_user_id=user.user_id,
    )
    repos.review_revisions.add(canonical)

    training_example = None
    if body.include_in_training:
        training_example = TrainingExample(
            user_id=user.user_id,
            region_id=region.region_id,
            review_revision_id=canonical.review_revision_id,
            crop_sha256=crop_hash or "",
            split=body.split,
            consent=True,
            eligible=True,
        )
        repos.training_examples.add(training_example)
    elif body.excluded:
        # Keep the audit history, but make an explicit exclusion authoritative
        # for future personalization runs.
        repos.training_examples.mark_region_ineligible(user.user_id, region.region_id)

    # A document snapshot makes exports/review reloads reproducible even when
    # later predictions or reviews are added.  It is intentionally tiny: the
    # normalized region/prediction/review rows remain the source of detail.
    page = repos.document_pages.get(user.user_id, region.page_id)
    document_revision = None
    if page is not None:
        document = repos.documents.get(user.user_id, page.document_id)
        if document is not None:
            document_revision = DocumentRevision(
                document_id=document.document_id,
                revision_number=repos.document_revisions.next_number(user.user_id, document.document_id),
                author_user_id=user.user_id,
                parent_revision_id=document.active_revision_id,
                block_snapshot={
                    "blocks": _document_snapshot(
                        user.user_id,
                        document.document_id,
                        repos,
                        current_region_id=region.region_id,
                        current_review=canonical,
                    )
                },
            )
            repos.document_revisions.add(document_revision)
            canonical.document_revision_id = document_revision.revision_id
            document.active_revision_id = document_revision.revision_id
    repos.session.commit()
    return RegionReviewResponse(
        review_id=review.review_id,
        region_id=region.region_id,
        reviewed_text=review.reviewed_text,
        excluded=review.excluded,
        version=review.version,
        review_revision_id=canonical.review_revision_id,
        document_revision_id=document_revision.revision_id if document_revision else None,
        training_example_id=training_example.example_id if training_example else None,
    )


# PATCH is the versioned API spelling from the document workflow.  POST stays
# available for clients from the first vertical-slice release.
router.add_api_route(
    "/regions/{region_id}/review",
    post_region_review,
    methods=["PATCH"],
    response_model=RegionReviewResponse,
    summary="Persist a version-checked reviewed region revision",
)
