"""Owner-scoped assets, ink bundles, and conversion-run envelopes."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from app.config.settings import get_settings
from app.data.models import Asset, DocumentPage, InkBundle, RecognitionRun, User
from app.data.object_store import ObjectStore
from app.data.repositories import RepositoryFactory
from app.ml.ink_v1 import InkIntegrityError, finalize, validate
from app.ml.personal_reranker import rerank_candidates
from app.ml.recognition import get_loaded_model_status
from app.services.auth_api import get_current_user, get_repositories
from app.services.error_codes import http_error
from app.services.images_api import get_object_store
from app.services.math_regions_api import get_formula_engine_status

router = APIRouter(tags=["assets"])


class InkBundleRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    bundle: dict[str, Any] | None = None
    idempotency_key: uuid.UUID | None = None
    schema_name: str | None = Field(default=None, alias="schema")
    schema_version: int | None = None
    page_id: str | None = None
    coordinate_space: dict[str, Any] | None = None
    strokes: list[dict[str, Any]] | None = None
    capture: dict[str, Any] | None = None
    native_drawing: dict[str, Any] | None = None
    content_hash: str | None = None

    def resolved_bundle(self) -> dict[str, Any]:
        if self.bundle:
            return dict(self.bundle)
        payload = self.model_dump(
            exclude={"bundle", "idempotency_key"},
            exclude_none=True,
            by_alias=True,
        )
        return payload


class InkBundleResponse(BaseModel):
    bundle_id: uuid.UUID
    page_id: uuid.UUID
    content_hash: str
    schema_name: str
    schema_version: int
    stroke_count: int
    point_count: int
    asset_id: uuid.UUID
    created_at: datetime
    idempotent: bool = False


class CreatePageRequest(BaseModel):
    width: int = Field(default=612, ge=1, le=20_000)
    height: int = Field(default=792, ge=1, le=20_000)
    paper_template: str | None = Field(default="blank", max_length=64)


class PageCreatedResponse(BaseModel):
    page_id: uuid.UUID
    document_id: uuid.UUID
    page_index: int
    width: int
    height: int
    coordinate_space: str


class RecognitionRunRequest(BaseModel):
    scope: str = Field(default="page", max_length=16)
    mode: str = Field(default="raster", max_length=32)
    idempotency_key: uuid.UUID | None = None
    region_ids: list[uuid.UUID] = Field(default_factory=list)


class RecognitionRunResponse(BaseModel):
    run_id: uuid.UUID
    document_id: uuid.UUID
    page_id: uuid.UUID
    state: str
    scope: str
    mode: str
    error_message: str | None = None
    result_ids: list = Field(default_factory=list)
    created_at: datetime
    idempotent: bool = False


def _blank_png() -> bytes:
    from PIL import Image as PILImage
    import io

    buffer = io.BytesIO()
    PILImage.new("RGB", (8, 8), color=(255, 255, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


def _ensure_quota(repos: RepositoryFactory, user: User, extra_bytes: int) -> None:
    settings = get_settings()
    used = repos.assets.total_bytes(user.user_id)
    if used + extra_bytes > settings.max_account_bytes:
        raise http_error(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "quota_exceeded",
            used_bytes=used,
            extra_bytes=extra_bytes,
            limit_bytes=settings.max_account_bytes,
        )


def _store_asset(
    repos: RepositoryFactory,
    store: ObjectStore,
    user: User,
    data: bytes,
    *,
    media_type: str,
    source_kind: str,
    suffix: str,
) -> Asset:
    digest = hashlib.sha256(data).hexdigest()
    existing = repos.assets.get_by_hash(user.user_id, digest, media_type)
    if existing is not None:
        return existing
    _ensure_quota(repos, user, len(data))
    reference = store.put_asset_bytes(str(user.user_id), data, suffix=suffix)
    asset = Asset(
        user_id=user.user_id,
        content_hash=digest,
        media_type=media_type,
        byte_size=len(data),
        object_ref=reference,
        scan_status="accepted",
        source_kind=source_kind,
    )
    return repos.assets.add(asset)


@router.post(
    "/documents/{document_id}/pages",
    response_model=PageCreatedResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_notebook_page(
    document_id: uuid.UUID,
    req: CreatePageRequest,
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
    store: ObjectStore = Depends(get_object_store),
) -> PageCreatedResponse:
    document = repos.documents.get(user.user_id, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    pages = list(repos.document_pages.list_for_document(user.user_id, document_id))
    png = _blank_png()
    raster_ref = store.put_page_raster(str(user.user_id), png)
    page = DocumentPage(
        document_id=document.document_id,
        page_index=len(pages),
        raster_path=raster_ref,
        width=req.width,
        height=req.height,
        dpi=72,
        coordinate_space="ink-points-v1",
        paper_template=req.paper_template,
        order_key=f"{len(pages):020d}",
    )
    repos.document_pages.add(page)
    document.source_kind = document.source_kind or "notebook"
    if document.source_kind == "images" and not pages:
        document.source_kind = "notebook"
    repos.session.commit()
    return PageCreatedResponse(
        page_id=page.page_id,
        document_id=document.document_id,
        page_index=page.page_index,
        width=page.width,
        height=page.height,
        coordinate_space=page.coordinate_space,
    )


@router.post(
    "/pages/{page_id}/ink-bundles",
    response_model=InkBundleResponse,
    status_code=status.HTTP_201_CREATED,
)
def upload_ink_bundle(
    page_id: uuid.UUID,
    req: InkBundleRequest,
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
    store: ObjectStore = Depends(get_object_store),
) -> InkBundleResponse:
    page = repos.document_pages.get(user.user_id, page_id)
    if page is None:
        raise HTTPException(status_code=404, detail="Not found")
    payload = req.resolved_bundle()
    payload.setdefault("page_id", str(page_id))
    try:
        validated = finalize(payload)
        if payload.get("content_hash"):
            validated = validate(payload)
        else:
            validate(validated)
    except InkIntegrityError as exc:
        raise http_error(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "ink_integrity_failure",
            str(exc),
        ) from exc
    if str(validated["page_id"]) != str(page_id):
        raise http_error(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "ink_integrity_failure",
            "ink bundle page_id does not match the path",
        )
    existing = repos.ink_bundles.get_by_hash(
        user.user_id, page_id, validated["content_hash"]
    )
    if existing is not None:
        return InkBundleResponse(
            bundle_id=existing.bundle_id,
            page_id=existing.page_id,
            content_hash=existing.content_hash,
            schema_name=existing.schema_name,
            schema_version=existing.schema_version,
            stroke_count=existing.stroke_count,
            point_count=existing.point_count,
            asset_id=existing.asset_id,
            created_at=existing.created_at,
            idempotent=True,
        )
    encoded = json.dumps(validated, separators=(",", ":"), sort_keys=True).encode("utf-8")
    asset = _store_asset(
        repos,
        store,
        user,
        encoded,
        media_type="application/vnd.hnd.ink-v1+json",
        source_kind="ink",
        suffix=".json",
    )
    native_asset = None
    native = validated.get("native_drawing")
    if isinstance(native, dict) and native.get("data"):
        raw = native["data"]
        native_bytes = raw.encode("utf-8") if isinstance(raw, str) else bytes(raw)
        native_asset = _store_asset(
            repos,
            store,
            user,
            native_bytes,
            media_type=str(native.get("media_type") or "application/x-apple-pkdrawing"),
            source_kind="native_drawing",
            suffix=".pkdrawing",
        )
    point_count = sum(len(stroke.get("points") or []) for stroke in validated["strokes"])
    bundle = repos.ink_bundles.add(
        InkBundle(
            user_id=user.user_id,
            page_id=page.page_id,
            asset_id=asset.asset_id,
            schema_name=validated["schema"],
            schema_version=int(validated["schema_version"]),
            content_hash=validated["content_hash"],
            stroke_count=len(validated["strokes"]),
            point_count=point_count,
            coordinate_space=validated["coordinate_space"],
            capture=validated["capture"],
            native_asset_id=None if native_asset is None else native_asset.asset_id,
        )
    )
    page.ink_asset_ref = asset.object_ref
    page.page_revision += 1
    repos.session.commit()
    return InkBundleResponse(
        bundle_id=bundle.bundle_id,
        page_id=bundle.page_id,
        content_hash=bundle.content_hash,
        schema_name=bundle.schema_name,
        schema_version=bundle.schema_version,
        stroke_count=bundle.stroke_count,
        point_count=bundle.point_count,
        asset_id=bundle.asset_id,
        created_at=bundle.created_at,
    )


@router.get("/pages/{page_id}/ink-bundles/latest", response_model=InkBundleResponse)
def latest_ink_bundle(
    page_id: uuid.UUID,
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
) -> InkBundleResponse:
    bundle = repos.ink_bundles.latest_for_page(user.user_id, page_id)
    if bundle is None:
        raise HTTPException(status_code=404, detail="Not found")
    return InkBundleResponse(
        bundle_id=bundle.bundle_id,
        page_id=bundle.page_id,
        content_hash=bundle.content_hash,
        schema_name=bundle.schema_name,
        schema_version=bundle.schema_version,
        stroke_count=bundle.stroke_count,
        point_count=bundle.point_count,
        asset_id=bundle.asset_id,
        created_at=bundle.created_at,
    )


@router.post(
    "/pages/{page_id}/recognition-runs",
    response_model=RecognitionRunResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_recognition_run(
    page_id: uuid.UUID,
    req: RecognitionRunRequest,
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
) -> RecognitionRunResponse:
    page = repos.document_pages.get(user.user_id, page_id)
    if page is None:
        raise HTTPException(status_code=404, detail="Not found")
    if req.idempotency_key is not None:
        existing = repos.recognition_runs.get_by_idempotency(
            user.user_id, req.idempotency_key
        )
        if existing is not None:
            return RecognitionRunResponse(
                run_id=existing.run_id,
                document_id=existing.document_id,
                page_id=existing.page_id,
                state=existing.state,
                scope=existing.scope,
                mode=existing.mode,
                error_message=existing.error_message,
                result_ids=list(existing.result_ids or []),
                created_at=existing.created_at,
                idempotent=True,
            )
    ink = repos.ink_bundles.latest_for_page(user.user_id, page_id)
    snapshot = [
        {
            "page_id": str(page.page_id),
            "raster_ref": page.raster_path,
            "ink_hash": None if ink is None else ink.content_hash,
            "region_ids": [str(item) for item in req.region_ids],
        }
    ]
    run = RecognitionRun(
        user_id=user.user_id,
        document_id=page.document_id,
        page_id=page.page_id,
        scope=req.scope,
        mode=req.mode,
        idempotency_key=req.idempotency_key,
        source_snapshot=snapshot,
        state="queued",
    )
    prose = get_loaded_model_status()
    formula = get_formula_engine_status()
    available = bool(prose.loaded or formula.get("loaded"))
    if not available:
        run.state = "failed"
        run.error_message = "model_unavailable"
    else:
        # Personal Level-0 reranking is recorded even when conversion is queued
        # so clients can show that vocabulary would apply to n-best lists.
        vocab = {
            entry.token: entry.preferred_form
            for entry in repos.vocabulary.list_for_user(user.user_id)
        }
        if vocab:
            rerank_candidates([("", 0.0)], vocabulary=vocab)
        run.state = "queued"
    repos.recognition_runs.add(run)
    repos.session.commit()
    return RecognitionRunResponse(
        run_id=run.run_id,
        document_id=run.document_id,
        page_id=run.page_id,
        state=run.state,
        scope=run.scope,
        mode=run.mode,
        error_message=run.error_message,
        result_ids=list(run.result_ids or []),
        created_at=run.created_at,
    )


@router.get("/recognition-runs/{run_id}", response_model=RecognitionRunResponse)
def get_recognition_run(
    run_id: uuid.UUID,
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
) -> RecognitionRunResponse:
    run = repos.recognition_runs.get(user.user_id, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Not found")
    return RecognitionRunResponse(
        run_id=run.run_id,
        document_id=run.document_id,
        page_id=run.page_id,
        state=run.state,
        scope=run.scope,
        mode=run.mode,
        error_message=run.error_message,
        result_ids=list(run.result_ids or []),
        created_at=run.created_at,
    )
