from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile

from app.models.content_review import (
    ContentItemMatchUpdate,
    ContentMetricImportCommitRequest,
    DouyinAnalyticsExportSyncRequest,
)
from app.services import content_review_service
from app.services.publishers.base import PublishError, PublishWorkerUnavailable
from app.services.publishers.worker_client import PublishWorkerClient


router = APIRouter(prefix="/api/content-review", tags=["content-review"])


def _raise_content_review_http(exc: content_review_service.ContentReviewError):
    raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("/accounts")
async def list_accounts() -> dict:
    return {"accounts": content_review_service.list_douyin_accounts()}


@router.post("/imports/preview")
async def preview_import(
    file: UploadFile = File(...),
    account_id: str = Form(default=""),
) -> dict:
    try:
        content = await file.read(content_review_service.MAX_IMPORT_BYTES + 1)
        return content_review_service.preview_metric_import(
            account_id=account_id,
            filename=file.filename or "data",
            content=content,
        )
    except content_review_service.ContentReviewError as exc:
        _raise_content_review_http(exc)
    finally:
        await file.close()


@router.post("/imports/{batch_id}/commit")
async def commit_import(
    batch_id: str,
    payload: ContentMetricImportCommitRequest,
) -> dict:
    if not payload.confirm:
        raise HTTPException(status_code=400, detail="请确认后再导入")
    try:
        return content_review_service.commit_metric_import(batch_id)
    except content_review_service.ContentReviewError as exc:
        _raise_content_review_http(exc)


@router.post("/douyin/export-sync")
async def export_sync_douyin_items(payload: DouyinAnalyticsExportSyncRequest) -> dict:
    try:
        account_id = content_review_service._resolve_douyin_account_id(payload.account_id)
        worker_result = PublishWorkerClient().analytics_export_sync(account_id=account_id)
        items = list(worker_result.get("items") or [])
        if int(worker_result.get("row_count") or 0) != len(items):
            raise content_review_service.ContentReviewError(
                "Windows Worker 返回的作品行数校验失败",
                status_code=502,
            )
        return content_review_service.commit_douyin_item_export(
            account_id=account_id,
            items=items,
            captured_at=str(worker_result.get("captured_at") or content_review_service._now_iso()),
            source_filename=str(worker_result.get("source_filename") or "作品列表导出.xlsx"),
        )
    except content_review_service.ContentReviewError as exc:
        _raise_content_review_http(exc)
    except (PublishError, PublishWorkerUnavailable) as exc:
        error_code = str(getattr(exc, "error_code", "") or "WORKER_UNAVAILABLE")
        status_code = {
            "LOGIN_REQUIRED": 409,
            "VERIFICATION_REQUIRED": 409,
            "RATE_LIMITED": 429,
            "PAGE_CHANGED": 422,
            "INVALID_EXPORT": 422,
            "DOWNLOAD_FAILED": 502,
            "WORKER_UNAVAILABLE": 503,
            "publish_worker_unavailable": 503,
        }.get(error_code, 409)
        raise HTTPException(
            status_code=status_code,
            detail={"message": str(exc), "error_code": error_code},
        ) from exc


@router.get("/summary")
async def summary(
    account_id: str = Query(default="", max_length=120),
    days: int = Query(default=28, ge=14, le=180),
) -> dict:
    try:
        return content_review_service.get_content_review_summary(account_id, days)
    except content_review_service.ContentReviewError as exc:
        _raise_content_review_http(exc)


@router.get("/works")
async def works(
    account_id: str = Query(default="", max_length=120),
    limit: int = Query(default=200, ge=1, le=200),
) -> dict:
    try:
        return {"works": content_review_service.list_content_review_works(account_id, limit)}
    except content_review_service.ContentReviewError as exc:
        _raise_content_review_http(exc)


@router.get("/prompt-comparison")
async def prompt_comparison(account_id: str = Query(default="", max_length=120)) -> dict:
    try:
        return content_review_service.get_prompt_comparison(account_id)
    except content_review_service.ContentReviewError as exc:
        _raise_content_review_http(exc)


@router.get("/imports")
async def imports(
    account_id: str = Query(default="", max_length=120),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict:
    try:
        return {"imports": content_review_service.list_import_batches(account_id, limit)}
    except content_review_service.ContentReviewError as exc:
        _raise_content_review_http(exc)


@router.put("/item-matches/{snapshot_id}")
async def update_item_match(snapshot_id: str, payload: ContentItemMatchUpdate) -> dict:
    try:
        return content_review_service.set_item_match(snapshot_id, payload.publish_job_id)
    except content_review_service.ContentReviewError as exc:
        _raise_content_review_http(exc)


@router.delete("/item-matches/{snapshot_id}")
async def remove_item_match(snapshot_id: str) -> dict:
    try:
        return content_review_service.delete_item_match(snapshot_id)
    except content_review_service.ContentReviewError as exc:
        _raise_content_review_http(exc)
