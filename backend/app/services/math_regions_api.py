"""Manual formula-region recognition on imported pages."""

from __future__ import annotations

import hashlib
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field

from app.config.settings import get_settings
from app.data.enums import ModelTask
from app.data.models import DocumentRegion, RegionPrediction, User
from app.data.object_store import ObjectStore
from app.data.repositories import RepositoryFactory
from app.ml.backends.compact import CompactMathEngine
from app.ml.backends.unimernet import UniMERNetEngine
from app.ml.backends.base import EngineResult, EngineSettings
from app.services.auth_api import get_current_user, get_repositories
from app.services.document_review import create_document_revision
from app.services.images_api import get_object_store
from app.services.isolation import get_or_404
from app.services.model_resolution import resolve_model_assignment

router = APIRouter(tags=["math-regions"])


def get_formula_engine_status() -> dict[str, object]:
    """Return truthful health data for the formula backend chain."""
    reasons: list[str] = []
    for engine in _formula_engines():
        if engine.is_available():
            return {
                "backend": engine.engine_id,
                "loaded": True,
                "reason": None,
                "provenance": getattr(engine, "_provenance", {}),
            }
        reason = getattr(engine, "_reason", None)
        if reason:
            reasons.append(str(reason))
    return {"backend": None, "loaded": False, "reason": "; ".join(reasons)}


class CreateRegionRequest(BaseModel):
    bbox: list[int] = Field(min_length=4, max_length=4)
    content_type: str = "math_display"
    order_index: int = 0
    coordinate_space: str = "page-raster-v1"

    @property
    def box(self) -> tuple[int, int, int, int]:
        return tuple(int(value) for value in self.bbox)  # type: ignore[return-value]


SUPPORTED_CONTENT_TYPES = {"text", "math_display", "math_inline", "mixed", "figure"}


def _validate_content_type(value: str) -> str:
    if value not in SUPPORTED_CONTENT_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported region content_type; expected one of {sorted(SUPPORTED_CONTENT_TYPES)}.",
        )
    return value


class RegionResponse(BaseModel):
    region_id: uuid.UUID
    page_id: uuid.UUID
    content_type: str
    bbox: list[int]
    order_index: int
    coordinate_space: str = "page-raster-v1"
    page_revision: int = 1
    crop_sha256: str | None = None


class RecognizeRegionResponse(BaseModel):
    region_id: uuid.UUID
    prediction_id: uuid.UUID | None = None
    engine_id: str
    text: str
    truncated: bool = False
    available: bool = True
    reason: str | None = None
    score: float | None = None
    candidates: list[dict] = []
    latency_ms: float | None = None
    model_assignment_id: uuid.UUID | None = None
    model_task: str | None = None
    base_model_version: str | None = None
    adapter_version: str | None = None
    personalized: bool = False


class RegionDetailResponse(BaseModel):
    region_id: uuid.UUID
    page_id: uuid.UUID
    content_type: str
    bbox: list[int]
    order_index: int
    prediction_text: str | None = None
    reviewed_text: str | None = None
    excluded: bool = False
    unresolved: bool = True
    prediction_id: uuid.UUID | None = None
    review_revision_id: uuid.UUID | None = None
    review_version: int | None = None
    crop_revision: int = 1
    engine_id: str | None = None
    score: float | None = None
    truncated: bool = False
    crop_sha256: str | None = None
    model_assignment_id: uuid.UUID | None = None
    model_task: str | None = None
    base_model_version: str | None = None
    adapter_version: str | None = None
    personalized: bool = False


class PatchRegionRequest(BaseModel):
    bbox: list[int] | None = Field(default=None, min_length=4, max_length=4)
    content_type: str | None = None
    order_index: int | None = Field(default=None, ge=0)
    expected_page_revision: int | None = Field(default=None, ge=1)


class ReorderRegionsRequest(BaseModel):
    region_ids: list[uuid.UUID] = Field(min_length=1)


class SplitRegionRequest(BaseModel):
    boxes: list[list[int]] = Field(min_length=2, max_length=16)


class MergeRegionsRequest(BaseModel):
    region_ids: list[uuid.UUID] = Field(min_length=2, max_length=16)
    content_type: str | None = None


def _formula_engines() -> tuple[object, ...]:
    """Construct only the configured formula backends in preference order."""
    configured = get_settings().formula_engine
    if configured == "unimernet":
        return (UniMERNetEngine(),)
    if configured == "compact":
        return (CompactMathEngine(),)
    return (UniMERNetEngine(), CompactMathEngine())


def recognize_formula_crop(crop: bytes, settings: EngineSettings | None = None) -> EngineResult:
    """Use the first loaded formula engine. Never invent output from random weights."""
    unavailable_reasons: list[str] = []
    for engine in _formula_engines():
        if engine.is_available():
            result = engine.recognize_crop(crop, settings) if settings is not None else engine.recognize_crop(crop)
            # A directory/config file is not sufficient to prove that an
            # adapter can actually run inference.  Keep trying other loaded
            # backends when an optional backend is present but unavailable.
            if result.available:
                return result
            if result.reason:
                unavailable_reasons.append(result.reason)
    reason = (
        "; ".join(unavailable_reasons)
        if unavailable_reasons
        else "No formula recognition engine is loaded."
    )
    return EngineResult(
        engine_id="formula",
        engine_version="",
        available=False,
        reason=f"{reason} You can still type LaTeX.",
    )


def _crop_png(png_bytes: bytes, bbox: list[int]) -> bytes:
    import io
    from PIL import Image

    x, y, w, h = (int(value) for value in bbox)
    if x < 0 or y < 0 or w <= 0 or h <= 0:
        raise ValueError("Bounding box must have a positive, non-negative size.")
    with Image.open(io.BytesIO(png_bytes)) as image:
        if x + w > image.width or y + h > image.height:
            raise ValueError("Bounding box is outside the page raster.")
        crop = image.crop((x, y, x + w, y + h))
        buf = io.BytesIO()
        crop.save(buf, format="PNG")
        return buf.getvalue()


@router.post("/pages/{page_id}/regions", response_model=RegionResponse, status_code=status.HTTP_201_CREATED)
def create_region(
    page_id: uuid.UUID,
    body: CreateRegionRequest,
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
    store: ObjectStore = Depends(get_object_store),
) -> RegionResponse:
    page = get_or_404(repos.document_pages.get(user.user_id, page_id))
    x, y, w, h = body.bbox
    _validate_content_type(body.content_type)
    if w <= 0 or h <= 0:
        raise HTTPException(status_code=422, detail="Bounding box must have positive size.")
    if x < 0 or y < 0 or x + w > page.width or y + h > page.height:
        raise HTTPException(
            status_code=422,
            detail="Bounding box must be contained within the page raster.",
        )
    if body.coordinate_space != page.coordinate_space:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported coordinate space; expected {page.coordinate_space}.",
        )
    try:
        page_bytes = store.get(page.raster_path)
        crop_bytes = _crop_png(page_bytes, [x, y, w, h])
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Could not create region crop: {exc}") from exc
    crop_hash = hashlib.sha256(crop_bytes).hexdigest()
    crop_path = store.put_region_crop(str(user.user_id), crop_bytes, suffix=".png")
    region = DocumentRegion(
        page_id=page.page_id,
        order_index=body.order_index,
        content_type=body.content_type,
        bbox_x=x,
        bbox_y=y,
        bbox_width=w,
        bbox_height=h,
        coordinate_space=body.coordinate_space,
        page_revision=1,
        source_crop_path=crop_path,
        crop_sha256=crop_hash,
    )
    repos.document_regions.add(region)
    repos.session.commit()
    return RegionResponse(
        region_id=region.region_id,
        page_id=page.page_id,
        content_type=region.content_type,
        bbox=[region.bbox_x, region.bbox_y, region.bbox_width, region.bbox_height],
        order_index=region.order_index,
        coordinate_space=region.coordinate_space,
        page_revision=region.page_revision,
        crop_sha256=region.crop_sha256,
    )


@router.post("/regions/{region_id}/recognize", response_model=RecognizeRegionResponse)
def recognize_region(
    region_id: uuid.UUID,
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
    store: ObjectStore = Depends(get_object_store),
) -> RecognizeRegionResponse:
    region = get_or_404(repos.document_regions.get(user.user_id, region_id))
    page = get_or_404(repos.document_pages.get(user.user_id, region.page_id))
    png_bytes = store.get(page.raster_path)
    crop = _crop_png(
        png_bytes,
        [region.bbox_x, region.bbox_y, region.bbox_width, region.bbox_height],
    )
    result = recognize_formula_crop(crop)
    resolved = resolve_model_assignment(
        repos,
        user.user_id,
        task=ModelTask.MATH_RASTER,
        engine_id=result.engine_id,
        engine_version=result.engine_version,
        provenance=getattr(result, "provenance", None),
    )
    candidate_dicts = [
        {
            "text": candidate.text,
            "score": candidate.score,
            "truncated": candidate.truncated,
            "kind": candidate.kind,
        }
        for candidate in result.candidates
    ]
    prediction = RegionPrediction(
        region_id=region.region_id,
        engine_id=result.engine_id,
        engine_version=result.engine_version,
        raw_output=result.text,
        score=result.candidates[0].score if result.candidates else None,
        truncated=result.truncated,
        status="ok" if result.available else "unavailable",
        latency_ms=result.latency_ms,
        alternatives=candidate_dicts[1:] or None,
        model_assignment_id=resolved.assignment.assignment_id,
        provenance={
            **dict(getattr(result, "provenance", None) or {}),
            "model_assignment_id": str(resolved.assignment.assignment_id),
            "model_task": resolved.task,
            "base_model_version": resolved.base_model_version,
            "adapter_version": resolved.adapter_version,
            "personalized": resolved.personalized,
        },
        settings=getattr(result, "settings", None),
    )
    repos.region_predictions.add(prediction)
    document = repos.documents.get(user.user_id, page.document_id)
    if document is not None:
        create_document_revision(user.user_id, document, repos)
    repos.session.commit()
    return RecognizeRegionResponse(
        region_id=region.region_id,
        prediction_id=prediction.prediction_id,
        engine_id=result.engine_id,
        text=result.text,
        truncated=result.truncated,
        available=result.available,
        reason=result.reason,
        score=prediction.score,
        candidates=candidate_dicts,
        latency_ms=result.latency_ms,
        model_assignment_id=resolved.assignment.assignment_id,
        model_task=resolved.task,
        base_model_version=resolved.base_model_version,
        adapter_version=resolved.adapter_version,
        personalized=resolved.personalized,
    )


@router.patch("/regions/{region_id}", response_model=RegionResponse)
def patch_region(
    region_id: uuid.UUID,
    body: PatchRegionRequest,
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
    store: ObjectStore = Depends(get_object_store),
) -> RegionResponse:
    """Update a crop/type/order while preserving its revision identity."""
    region = get_or_404(repos.document_regions.get(user.user_id, region_id))
    page = get_or_404(repos.document_pages.get(user.user_id, region.page_id))
    if body.expected_page_revision is not None and body.expected_page_revision != region.page_revision:
        raise HTTPException(status_code=409, detail="Region is stale; reload before editing it.")
    if body.content_type is not None:
        region.content_type = _validate_content_type(body.content_type)
    if body.order_index is not None:
        region.order_index = body.order_index
    if body.bbox is not None:
        x, y, w, h = (int(value) for value in body.bbox)
        if w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > page.width or y + h > page.height:
            raise HTTPException(status_code=422, detail="Bounding box must be contained within the page raster.")
        try:
            crop = _crop_png(store.get(page.raster_path), [x, y, w, h])
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Could not update region crop: {exc}") from exc
        region.bbox_x, region.bbox_y = x, y
        region.bbox_width, region.bbox_height = w, h
        region.page_revision += 1
        region.crop_sha256 = hashlib.sha256(crop).hexdigest()
        region.source_crop_path = store.put_region_crop(str(user.user_id), crop, suffix=".png")
    document = repos.documents.get(user.user_id, page.document_id)
    if document is not None:
        create_document_revision(user.user_id, document, repos)
    repos.session.commit()
    return RegionResponse(
        region_id=region.region_id,
        page_id=region.page_id,
        content_type=region.content_type,
        bbox=[region.bbox_x, region.bbox_y, region.bbox_width, region.bbox_height],
        order_index=region.order_index,
        coordinate_space=region.coordinate_space,
        page_revision=region.page_revision,
        crop_sha256=region.crop_sha256,
    )


@router.delete("/regions/{region_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_region(
    region_id: uuid.UUID,
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
) -> Response:
    region = get_or_404(repos.document_regions.get(user.user_id, region_id))
    page = get_or_404(repos.document_pages.get(user.user_id, region.page_id))
    repos.document_regions.delete(user.user_id, region.region_id)
    document = repos.documents.get(user.user_id, page.document_id)
    if document is not None:
        create_document_revision(user.user_id, document, repos)
    repos.session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/pages/{page_id}/regions/reorder", response_model=list[RegionResponse])
def reorder_regions(
    page_id: uuid.UUID,
    body: ReorderRegionsRequest,
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
) -> list[RegionResponse]:
    get_or_404(repos.document_pages.get(user.user_id, page_id))
    existing = {region.region_id for region in repos.document_regions.list_for_page(user.user_id, page_id)}
    if set(body.region_ids) != existing:
        raise HTTPException(status_code=422, detail="region_ids must contain every region on the page exactly once.")
    try:
        regions = repos.document_regions.update_order(user.user_id, body.region_ids)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    page = get_or_404(repos.document_pages.get(user.user_id, page_id))
    document = repos.documents.get(user.user_id, page.document_id)
    if document is not None:
        create_document_revision(user.user_id, document, repos)
    repos.session.commit()
    return [
        RegionResponse(
            region_id=region.region_id,
            page_id=region.page_id,
            content_type=region.content_type,
            bbox=[region.bbox_x, region.bbox_y, region.bbox_width, region.bbox_height],
            order_index=region.order_index,
            coordinate_space=region.coordinate_space,
            page_revision=region.page_revision,
            crop_sha256=region.crop_sha256,
        )
        for region in regions
    ]


@router.post("/regions/{region_id}/split", response_model=list[RegionResponse], status_code=status.HTTP_201_CREATED)
def split_region(
    region_id: uuid.UUID,
    body: SplitRegionRequest,
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
    store: ObjectStore = Depends(get_object_store),
) -> list[RegionResponse]:
    """Create reviewer-controlled child crops and remove the parent crop."""
    parent = get_or_404(repos.document_regions.get(user.user_id, region_id))
    page = get_or_404(repos.document_pages.get(user.user_id, parent.page_id))
    created: list[DocumentRegion] = []
    page_bytes = store.get(page.raster_path)
    for offset, values in enumerate(body.boxes):
        if len(values) != 4:
            raise HTTPException(status_code=422, detail="Each split box must contain four integers.")
        x, y, w, h = (int(value) for value in values)
        if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > page.width or y + h > page.height:
            raise HTTPException(status_code=422, detail="Split box is outside the page raster.")
        crop = _crop_png(page_bytes, [x, y, w, h])
        created.append(DocumentRegion(
            page_id=page.page_id,
            order_index=parent.order_index + offset,
            content_type=parent.content_type,
            bbox_x=x, bbox_y=y, bbox_width=w, bbox_height=h,
            coordinate_space=page.coordinate_space,
            source_crop_path=store.put_region_crop(str(user.user_id), crop, suffix=".png"),
            crop_sha256=hashlib.sha256(crop).hexdigest(),
        ))
    for child in created:
        repos.document_regions.add(child)
    repos.document_regions.delete(user.user_id, parent.region_id)
    document = repos.documents.get(user.user_id, page.document_id)
    if document is not None:
        create_document_revision(user.user_id, document, repos)
    repos.session.commit()
    return [
        RegionResponse(
            region_id=child.region_id, page_id=child.page_id, content_type=child.content_type,
            bbox=[child.bbox_x, child.bbox_y, child.bbox_width, child.bbox_height],
            order_index=child.order_index, coordinate_space=child.coordinate_space,
            page_revision=child.page_revision, crop_sha256=child.crop_sha256,
        ) for child in created
    ]


@router.post("/pages/{page_id}/regions/merge", response_model=RegionResponse, status_code=status.HTTP_201_CREATED)
def merge_regions(
    page_id: uuid.UUID,
    body: MergeRegionsRequest,
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
    store: ObjectStore = Depends(get_object_store),
) -> RegionResponse:
    """Merge reviewer-selected sibling regions into one retained crop."""
    page = get_or_404(repos.document_pages.get(user.user_id, page_id))
    unique_ids = list(dict.fromkeys(body.region_ids))
    if len(unique_ids) < 2:
        raise HTTPException(status_code=422, detail="At least two distinct regions are required to merge.")
    regions = [repos.document_regions.get(user.user_id, region_id) for region_id in unique_ids]
    if any(region is None or region.page_id != page.page_id for region in regions):
        raise HTTPException(status_code=404, detail="One or more regions were not found on this page.")
    resolved = [region for region in regions if region is not None]
    x = min(region.bbox_x for region in resolved)
    y = min(region.bbox_y for region in resolved)
    right = max(region.bbox_x + region.bbox_width for region in resolved)
    bottom = max(region.bbox_y + region.bbox_height for region in resolved)
    content_type = body.content_type or resolved[0].content_type
    _validate_content_type(content_type)
    try:
        crop = _crop_png(store.get(page.raster_path), [x, y, right - x, bottom - y])
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Could not create merged crop: {exc}") from exc
    merged = DocumentRegion(
        page_id=page.page_id,
        order_index=min(region.order_index for region in resolved),
        content_type=content_type,
        bbox_x=x,
        bbox_y=y,
        bbox_width=right - x,
        bbox_height=bottom - y,
        coordinate_space=page.coordinate_space,
        source_crop_path=store.put_region_crop(str(user.user_id), crop, suffix=".png"),
        crop_sha256=hashlib.sha256(crop).hexdigest(),
    )
    repos.document_regions.add(merged)
    for region in resolved:
        repos.document_regions.delete(user.user_id, region.region_id)
    document = repos.documents.get(user.user_id, page.document_id)
    if document is not None:
        create_document_revision(user.user_id, document, repos)
    repos.session.commit()
    return RegionResponse(
        region_id=merged.region_id,
        page_id=merged.page_id,
        content_type=merged.content_type,
        bbox=[merged.bbox_x, merged.bbox_y, merged.bbox_width, merged.bbox_height],
        order_index=merged.order_index,
        coordinate_space=merged.coordinate_space,
        page_revision=merged.page_revision,
        crop_sha256=merged.crop_sha256,
    )


@router.get("/pages/{page_id}/regions", response_model=list[RegionDetailResponse])
def list_page_regions(
    page_id: uuid.UUID,
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
) -> list[RegionDetailResponse]:
    get_or_404(repos.document_pages.get(user.user_id, page_id))
    details: list[RegionDetailResponse] = []
    for region in repos.document_regions.list_for_page(user.user_id, page_id):
        canonical_review = repos.review_revisions.latest_for_region(user.user_id, region.region_id)
        review = canonical_review or repos.region_reviews.latest_for_region(user.user_id, region.region_id)
        prediction = repos.region_predictions.latest_for_region(user.user_id, region.region_id)
        reviewed_text = review.reviewed_text if review is not None else None
        prediction_text = prediction.raw_output if prediction is not None else None
        excluded = bool(review.excluded) if review is not None else False
        details.append(
            RegionDetailResponse(
                region_id=region.region_id,
                page_id=region.page_id,
                content_type=region.content_type,
                bbox=[region.bbox_x, region.bbox_y, region.bbox_width, region.bbox_height],
                order_index=region.order_index,
                prediction_text=prediction_text,
                reviewed_text=reviewed_text,
                excluded=excluded,
                unresolved=reviewed_text is None or excluded,
                prediction_id=prediction.prediction_id if prediction is not None else None,
                review_revision_id=canonical_review.review_revision_id if canonical_review is not None else None,
                review_version=review.version if review is not None else None,
                crop_revision=region.page_revision,
                engine_id=prediction.engine_id if prediction is not None else None,
                score=prediction.score if prediction is not None else None,
                truncated=bool(prediction.truncated) if prediction is not None else False,
                crop_sha256=region.crop_sha256,
                model_assignment_id=(
                    prediction.model_assignment_id if prediction is not None else None
                ),
                model_task=(
                    prediction.model_assignment.task
                    if prediction is not None and prediction.model_assignment is not None
                    else None
                ),
                base_model_version=(
                    prediction.model_assignment.base_model_version
                    if prediction is not None and prediction.model_assignment is not None
                    else None
                ),
                adapter_version=(
                    prediction.model_assignment.adapter_version
                    if prediction is not None and prediction.model_assignment is not None
                    else None
                ),
                personalized=bool(
                    prediction is not None
                    and prediction.model_assignment is not None
                    and prediction.model_assignment.adapter_id is not None
                ),
            )
        )
    return details


@router.get("/pages/{page_id}/raster")
def get_page_raster(
    page_id: uuid.UUID,
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
    store: ObjectStore = Depends(get_object_store),
) -> Response:
    page = get_or_404(repos.document_pages.get(user.user_id, page_id))
    return Response(content=store.get(page.raster_path), media_type="image/png")
