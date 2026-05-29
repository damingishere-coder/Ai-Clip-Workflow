from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

from app.models.task import (
    PublishAccountCreate,
    PublishBatchJobCreate,
    PublishCoverCreate,
    PublishJobCreate,
    PublishPlatformConfigUpdate,
)
from app.services import publish_service


router = APIRouter(prefix="/api/publish", tags=["publish"])


@router.get("/platforms")
async def list_platform_configs() -> dict:
    return {"platforms": publish_service.list_platform_configs()}


@router.post("/platforms/{platform}/config")
async def update_platform_config(platform: str, payload: PublishPlatformConfigUpdate) -> dict:
    try:
        return publish_service.update_platform_config(platform, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/platforms/{platform}/test")
async def test_platform_config(platform: str) -> dict:
    try:
        return publish_service.test_platform_config(platform)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/douyin/oauth-url")
async def get_douyin_oauth_url() -> dict:
    try:
        return publish_service.build_douyin_oauth_url()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/douyin/oauth/callback")
async def douyin_oauth_callback(code: str = "", state: str = ""):
    if not code:
        return RedirectResponse(url=f"/publish?publish_message={quote('抖音授权失败：没有收到授权 code')}")
    try:
        publish_service.save_douyin_oauth_account(code)
    except Exception as exc:
        return RedirectResponse(url=f"/publish?publish_message={quote(f'抖音授权失败：{str(exc)}')}")
    return RedirectResponse(url=f"/publish?publish_message={quote('抖音授权账号已保存')}")


@router.get("/accounts")
async def list_accounts(platform: str | None = None) -> dict:
    return {"accounts": publish_service.list_accounts(platform)}


@router.post("/accounts")
async def create_account(payload: PublishAccountCreate) -> dict:
    try:
        return publish_service.create_account(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/jobs")
async def list_publish_jobs() -> dict:
    return {"jobs": publish_service.list_publish_jobs()}


@router.post("/jobs")
async def create_publish_job(payload: PublishJobCreate) -> dict:
    try:
        return publish_service.create_publish_job(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/jobs/batch")
async def create_batch_publish_jobs(payload: PublishBatchJobCreate) -> dict:
    try:
        return publish_service.create_batch_publish_jobs(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/covers")
async def generate_publish_cover(payload: PublishCoverCreate) -> dict:
    try:
        return publish_service.generate_publish_cover(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/cover")
async def generate_publish_job_cover(job_id: str, payload: PublishCoverCreate) -> dict:
    try:
        return publish_service.generate_publish_job_cover(job_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/retry")
async def retry_publish_job(job_id: str) -> dict:
    try:
        return publish_service.retry_publish_job(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/mark-published")
async def mark_publish_job_published(job_id: str) -> dict:
    try:
        return publish_service.update_publish_job_status(job_id, "published")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/mark-failed")
async def mark_publish_job_failed(job_id: str) -> dict:
    try:
        return publish_service.update_publish_job_status(job_id, "failed", "人工标记失败")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/cancel")
async def cancel_publish_job(job_id: str) -> dict:
    try:
        return publish_service.update_publish_job_status(job_id, "cancelled")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
