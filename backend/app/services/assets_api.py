"""Owner-scoped assets, ink bundles, and conversion-run envelopes."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from app.config.settings import get_settings
from app.data.models import Asset, DocumentPage, DocumentRegion, InkBundle, RecognitionJob, RecognitionRun, User
from app.data.enums import RecognitionJobState
from app.data.object_store import ObjectNotFoundError, ObjectStore
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
    revision_number: int = 1
    parent_bundle_id: uuid.UUID | None = None
    active: bool = False
    upload_state: str = "complete"


class InkBundleManifestResponse(BaseModel):
    page_id: uuid.UUID
    active_bundle_id: uuid.UUID | None = None
    revisions: list[InkBundleResponse]


class InkBundleContentResponse(BaseModel):
    """The immutable canonical payload used to restore ink on another client."""

    model_config = ConfigDict(populate_by_name=True)

    schema_name: str = Field(alias="schema")
    schema_version: int
    page_id: uuid.UUID
    coordinate_space: dict[str, Any]
    strokes: list[dict[str, Any]]
    capture: dict[str, Any]
    native_drawing: dict[str, Any] | None = None
    content_hash: str


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
    scope: Literal["page", "region"] = "page"
    mode: Literal["raster", "ink", "formula", "prose", "mixed"] = "raster"
    idempotency_key: uuid.UUID | None = None
    region_ids: list[uuid.UUID] = Field(default_factory=list, max_length=256)
    ink_bundle_id: uuid.UUID | None = None


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
    progress_current: int = 0
    progress_total: int = 0


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


def _ink_bundle_response(
    bundle: InkBundle,
    *,
    active: bool = False,
    idempotent: bool = False,
) -> InkBundleResponse:
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
        idempotent=idempotent,
        revision_number=bundle.revision_number,
        parent_bundle_id=bundle.parent_bundle_id,
        active=active,
        upload_state="complete",
    )


def validate_ink_payload(payload: dict[str, Any], page_id: uuid.UUID) -> dict[str, Any]:
    """Finalize and validate a canonical ink payload for one owned page."""
    payload = dict(payload)
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
    return validated


def persist_ink_bundle(
    *,
    page: DocumentPage,
    user: User,
    repos: RepositoryFactory,
    store: ObjectStore,
    validated: dict[str, Any],
) -> tuple[InkBundleResponse, Asset]:
    """Persist one validated immutable revision and activate it on the page.

    The helper deliberately receives an already validated payload so the
    direct JSON route and resumable completion route share exactly the same
    revision, deduplication, quota, and active-pointer semantics.
    """
    existing = repos.ink_bundles.get_by_hash(
        user.user_id, page.page_id, validated["content_hash"]
    )
    if existing is not None:
        # A retry of an older revision is still allowed to restore that exact
        # immutable revision as the page's active source. It never rewrites
        # the bundle or its asset.
        if page.active_ink_bundle_id != existing.bundle_id:
            page.page_revision += 1
        page.active_ink_bundle_id = existing.bundle_id
        page.ink_asset_ref = existing.asset.object_ref
        return (
            _ink_bundle_response(existing, active=True, idempotent=True),
            existing.asset,
        )

    encoded = json.dumps(validated, separators=(",", ":"), sort_keys=True).encode("utf-8")
    settings = get_settings()
    if len(encoded) > settings.max_upload_bytes:
        raise http_error(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            "upload_too_large",
            size_bytes=len(encoded),
            limit_bytes=settings.max_upload_bytes,
        )
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
        try:
            # JSONEncoder represents Swift/Python Data as base64 text. Do not
            # persist the textual envelope as if it were the native drawing.
            native_bytes = (
                base64.b64decode(raw, validate=True)
                if isinstance(raw, str)
                else bytes(raw)
            )
        except (ValueError, TypeError, binascii.Error) as exc:
            raise http_error(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "ink_integrity_failure",
                "native drawing data is not valid base64",
            ) from exc
        if not native_bytes:
            raise http_error(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "ink_integrity_failure",
                "native drawing data is empty",
            )
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
    revision_number = repos.ink_bundles.next_revision_number(user.user_id, page.page_id)
    parent_bundle_id = page.active_ink_bundle_id
    bundle = repos.ink_bundles.add(
        InkBundle(
            user_id=user.user_id,
            page_id=page.page_id,
            asset_id=asset.asset_id,
            schema_name=validated["schema"],
            schema_version=int(validated["schema_version"]),
            content_hash=validated["content_hash"],
            revision_number=revision_number,
            parent_bundle_id=parent_bundle_id,
            stroke_count=len(validated["strokes"]),
            point_count=point_count,
            coordinate_space=validated["coordinate_space"],
            capture=validated["capture"],
            native_asset_id=None if native_asset is None else native_asset.asset_id,
        )
    )
    page.ink_asset_ref = asset.object_ref
    page.active_ink_bundle_id = bundle.bundle_id
    page.page_revision += 1
    return _ink_bundle_response(bundle, active=True), asset


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
    validated = validate_ink_payload(req.resolved_bundle(), page_id)
    response, _asset = persist_ink_bundle(
        page=page,
        user=user,
        repos=repos,
        store=store,
        validated=validated,
    )
    repos.session.commit()
    return response


@router.get("/pages/{page_id}/ink-bundles/latest", response_model=InkBundleResponse)
def latest_ink_bundle(
    page_id: uuid.UUID,
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
) -> InkBundleResponse:
    page = repos.document_pages.get(user.user_id, page_id)
    if page is None:
        raise HTTPException(status_code=404, detail="Not found")
    bundle = (
        repos.ink_bundles.get(user.user_id, page.active_ink_bundle_id)
        if page.active_ink_bundle_id is not None
        else None
    ) or repos.ink_bundles.latest_for_page(user.user_id, page_id)
    if bundle is None:
        raise HTTPException(status_code=404, detail="Not found")
    return _ink_bundle_response(
        bundle,
        active=page.active_ink_bundle_id == bundle.bundle_id,
    )


@router.get(
    "/pages/{page_id}/ink-bundles",
    response_model=InkBundleManifestResponse,
)
def ink_bundle_manifest(
    page_id: uuid.UUID,
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
) -> InkBundleManifestResponse:
    page = repos.document_pages.get(user.user_id, page_id)
    if page is None:
        raise HTTPException(status_code=404, detail="Not found")
    revisions = repos.ink_bundles.list_for_page(user.user_id, page_id)
    active_id = page.active_ink_bundle_id
    if active_id is None and revisions:
        active_id = revisions[0].bundle_id
    return InkBundleManifestResponse(
        page_id=page_id,
        active_bundle_id=active_id,
        revisions=[
            _ink_bundle_response(bundle, active=bundle.bundle_id == active_id)
            for bundle in revisions
        ],
    )


@router.get(
    "/pages/{page_id}/ink-bundles/{bundle_id}/content",
    response_model=InkBundleContentResponse,
)
def download_ink_bundle_content(
    page_id: uuid.UUID,
    bundle_id: uuid.UUID,
    user: User = Depends(get_current_user),
    repos: RepositoryFactory = Depends(get_repositories),
    store: ObjectStore = Depends(get_object_store),
) -> dict[str, Any]:
    """Return one owner-scoped immutable ``ink_v1`` revision.

    The manifest intentionally contains metadata only. This endpoint retrieves
    the exact canonical bytes for second-client bootstrap and validates them
    again before they cross the API boundary. A bundle ID from another page or
    owner is indistinguishable from a missing revision.
    """

    page = repos.document_pages.get(user.user_id, page_id)
    bundle = repos.ink_bundles.get(user.user_id, bundle_id)
    if page is None or bundle is None or bundle.page_id != page.page_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    try:
        encoded = store.get(bundle.asset.object_ref)
    except ObjectNotFoundError as exc:
        raise http_error(
            status.HTTP_409_CONFLICT,
            "ink_integrity_failure",
            "The stored ink revision content is unavailable.",
        ) from exc
    try:
        payload = json.loads(encoded)
        if not isinstance(payload, dict):
            raise ValueError("ink payload must be an object")
        validated = validate(payload)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError, InkIntegrityError) as exc:
        raise http_error(
            status.HTTP_409_CONFLICT,
            "ink_integrity_failure",
            "The stored ink revision content failed validation.",
        ) from exc
    if (
        str(validated.get("page_id")) != str(page_id)
        or validated.get("content_hash") != bundle.content_hash
    ):
        raise http_error(
            status.HTTP_409_CONFLICT,
            "ink_integrity_failure",
            "The stored ink revision does not match its manifest.",
        )
    return validated


def _run_response(run, repos, *, idempotent=False):
    job = repos.recognition_jobs.get(run.user_id, run.job_id) if run.job_id else None
    if job is not None:
        run.state = {"succeeded": "complete", "no_text": "complete", "timeout": "failed"}.get(job.state.value, job.state.value)
        run.result_ids = list(job.result_ids or [])
        run.error_message = job.error_message
        run.finished_at = job.finished_at
    return RecognitionRunResponse(
        run_id=run.run_id, document_id=run.document_id, page_id=run.page_id,
        state=run.state, scope=run.scope, mode=run.mode, error_message=run.error_message,
        result_ids=list(run.result_ids or []), created_at=run.created_at, idempotent=idempotent,
        progress_current=0 if job is None else job.progress_current,
        progress_total=0 if job is None else job.progress_total,
    )


@router.post("/pages/{page_id}/recognition-runs", response_model=RecognitionRunResponse, status_code=201)
def create_recognition_run(
    page_id: uuid.UUID, req: RecognitionRunRequest,
    user: User = Depends(get_current_user), repos: RepositoryFactory = Depends(get_repositories),
    store: ObjectStore = Depends(get_object_store),
) -> RecognitionRunResponse:
    from app.services.jobs import _enqueue, _session_factory_for
    from app.services.layout import detect_page_regions, crop_png
    from app.services.page_raster import page_raster
    from app.services.prose_engine import get_prose_engine_status

    page = repos.document_pages.get(user.user_id, page_id)
    if page is None:
        raise HTTPException(404, "Not found")
    if req.idempotency_key:
        existing = repos.recognition_runs.get_by_idempotency(user.user_id, req.idempotency_key)
        if existing:
            source = existing.source_snapshot[0] if existing.source_snapshot else {}
            if existing.page_id != page_id or existing.mode != req.mode or existing.scope != req.scope or req.ink_bundle_id is not None and source.get("ink_bundle_id") != str(req.ink_bundle_id) or source.get("region_ids") != [str(value) for value in req.region_ids] and req.region_ids:
                raise HTTPException(409, "Idempotency key belongs to another conversion")
            return _run_response(existing, repos, idempotent=True)
    selected = list(dict.fromkeys(req.region_ids))
    regions = [repos.document_regions.get(user.user_id, value) for value in selected]
    if any(region is None or region.page_id != page_id for region in regions):
        raise HTTPException(404, "One or more regions were not found")
    if req.scope == "region" and not selected:
        raise HTTPException(422, "Region conversion requires region_ids")
    if req.mode == "mixed" and not selected:
        raise HTTPException(422, "Mixed conversion requires regions labeled as text or formula")
    ink_id = req.ink_bundle_id or page.active_ink_bundle_id
    ink = repos.ink_bundles.get(user.user_id, ink_id) if ink_id else None
    if ink_id and (ink is None or ink.page_id != page_id):
        raise HTTPException(404, "Not found")
    snapshot = {"page_id": str(page_id), "raster_ref": page.raster_path,
                "ink_bundle_id": None if ink is None else str(ink.bundle_id),
                "ink_hash": None if ink is None else ink.content_hash,
                "ink_revision": None if ink is None else ink.revision_number,
                "coordinate_space": None if ink is None else ink.coordinate_space,
                "page_revision": page.page_revision, "region_ids": [str(value) for value in selected]}
    run = RecognitionRun(user_id=user.user_id, document_id=page.document_id, page_id=page_id,
                         scope=req.scope, mode=req.mode, idempotency_key=req.idempotency_key,
                         source_snapshot=[snapshot], state="queued")
    task = "formula" if req.mode in {"raster", "ink"} else req.mode
    needs_prose = task == "prose" or task == "mixed" and any(region.content_type == "text" for region in regions)
    needs_formula = task == "formula" or task == "mixed" and any(region.content_type != "text" for region in regions)
    available = (not needs_prose or get_prose_engine_status()["loaded"]) and (not needs_formula or get_formula_engine_status()["loaded"])
    if not available:
        run.state, run.error_message = "failed", "model_unavailable"
    else:
        try:
            raster, identity = page_raster(page, user.user_id, repos, store, bundle_id=ink_id)
            snapshot.update(identity)
            snapshot["raster_ref"] = store.put_page_raster(str(user.user_id), raster)
            snapshot["raster_sha256"] = hashlib.sha256(raster).hexdigest()
            snapshot["coordinate_transform"] = [1, 0, 0, 1, 0, 0]
            if not regions:
                for index, box in enumerate(detect_page_regions(raster)):
                    crop = crop_png(raster, box)
                    digest = hashlib.sha256(crop).hexdigest()
                    kind = "text" if task == "prose" else "math_display"
                    # Reuse the same source crop on retry without duplicating review blocks.
                    region = next((item for item in repos.document_regions.list_for_page(user.user_id, page_id)
                                   if item.crop_sha256 == digest and item.content_type == kind
                                   and item.page_revision == page.page_revision
                                   and item.coordinate_space == page.coordinate_space
                                   and [item.bbox_x, item.bbox_y, item.bbox_width, item.bbox_height]
                                   == [box.x, box.y, box.width, box.height]), None)
                    if region is None:
                        region = repos.document_regions.add(DocumentRegion(page_id=page_id, order_index=index,
                            content_type=kind, bbox_x=box.x, bbox_y=box.y, bbox_width=box.width, bbox_height=box.height,
                            coordinate_space=page.coordinate_space, page_revision=page.page_revision,
                            source_crop_path=store.put_region_crop(str(user.user_id), crop, suffix=".png"), crop_sha256=digest))
                    regions.append(region)
            frozen = []
            for region in regions:
                bbox = [region.bbox_x, region.bbox_y, region.bbox_width, region.bbox_height]
                crop = crop_png(raster, bbox)
                digest = hashlib.sha256(crop).hexdigest()
                if region.crop_sha256 != digest:
                    region.page_revision += 1
                    region.crop_sha256 = digest
                    region.source_crop_path = store.put_region_crop(str(user.user_id), crop, suffix=".png")
                frozen.append({"region_id": str(region.region_id), "bbox": bbox, "content_type": region.content_type,
                    "region_revision": region.page_revision, "original_crop_sha256": region.crop_sha256,
                    "crop_ref": store.put_region_crop(str(user.user_id), crop, suffix=".png"),
                    "crop_sha256": digest})
            snapshot["regions"] = frozen
            snapshot["region_ids"] = [item["region_id"] for item in frozen]
            if not frozen:
                run.state, run.error_message = "complete", "No text detected. Select a crop manually."
            else:
                job = repos.recognition_jobs.add(RecognitionJob(user_id=user.user_id, document_id=page.document_id,
                    page_id=page_id, image_id=page.image_id, mode=task, region_ids=snapshot["region_ids"],
                    progress_total=len(frozen), max_attempts=2))
                run.job_id = job.job_id
        except (ValueError, ObjectNotFoundError) as exc:
            raise HTTPException(422, str(exc)) from exc
    run.source_snapshot = [snapshot]
    repos.recognition_runs.add(run)
    repos.session.commit()
    response = _run_response(run, repos)
    if run.job_id:
        _enqueue(run.job_id, _session_factory_for(repos.session), store)
    return response


@router.get("/recognition-runs/{run_id}", response_model=RecognitionRunResponse)
def get_recognition_run(run_id: uuid.UUID, user: User = Depends(get_current_user),
                        repos: RepositoryFactory = Depends(get_repositories)):
    run = repos.recognition_runs.get(user.user_id, run_id)
    if run is None:
        raise HTTPException(404, "Not found")
    response = _run_response(run, repos)
    repos.session.commit()
    return response


@router.get("/recognition-runs/{run_id}/source")
def recognition_run_source(run_id: uuid.UUID, user: User = Depends(get_current_user),
                           repos: RepositoryFactory = Depends(get_repositories),
                           store: ObjectStore = Depends(get_object_store)):
    from fastapi import Response
    run = repos.recognition_runs.get(user.user_id, run_id)
    if run is None or not run.source_snapshot or not run.source_snapshot[0].get("raster_sha256"):
        raise HTTPException(404, "Not found")
    snapshot = run.source_snapshot[0]
    data = store.get(snapshot["raster_ref"])
    if hashlib.sha256(data).hexdigest() != snapshot["raster_sha256"]:
        raise HTTPException(409, "Source failed its integrity check")
    return Response(data, media_type="image/png", headers={"Cache-Control": "private, immutable", "X-Ink-Hash": snapshot.get("ink_hash") or ""})


@router.get("/recognition-runs/{run_id}/results")
def recognition_run_results(run_id: uuid.UUID, user: User = Depends(get_current_user),
                            repos: RepositoryFactory = Depends(get_repositories)) -> list[dict[str, Any]]:
    run = repos.recognition_runs.get(user.user_id, run_id)
    if run is None:
        raise HTTPException(404, "Not found")
    response = _run_response(run, repos)
    page = repos.document_pages.get(user.user_id, run.page_id)
    snapshot = run.source_snapshot[0] if run.source_snapshot else {}
    stale = page is None or page.page_revision != snapshot.get("page_revision")
    results = []
    frozen = {item["region_id"]: item for item in snapshot.get("regions", [])}
    for value in response.result_ids:
        prediction = repos.region_predictions.get(user.user_id, value)
        if prediction is None:
            continue
        region = repos.document_regions.get(user.user_id, prediction.region_id)
        if region is None:
            continue
        review = repos.review_revisions.latest_for_region(user.user_id, region.region_id)
        results.append({"region_id": str(region.region_id), "prediction_id": str(prediction.prediction_id),
            "bbox": prediction.source_bbox or [region.bbox_x, region.bbox_y, region.bbox_width, region.bbox_height],
            "content_type": region.content_type, "order_index": region.order_index,
            "prediction_text": prediction.raw_output, "reviewed_text": None if review is None else review.reviewed_text,
            "excluded": False if review is None else review.excluded, "review_version": None if review is None else review.version,
            "truncated": prediction.truncated, "engine_id": prediction.engine_id,
            "source_stale": stale or region.page_revision != frozen.get(str(region.region_id), {}).get("region_revision"),
            "crop_revision": region.page_revision})
    return results
