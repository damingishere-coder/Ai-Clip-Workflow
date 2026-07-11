from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from fastapi.responses import RedirectResponse

from app.models.task import (
    PublishAccountCreate,
    PublishBatchJobCreate,
    PublishBatchScheduleUpdate,
    PublishCoverCreate,
    PublishCoverFrameBatchCreate,
    PublishJobContentUpdate,
    PublishJobCreate,
    PublishJobScheduleUpdate,
    PublishPlatformConfigUpdate,
    PublishSendJobUpdate,
    PublishSendStart,
)
from app.services import publish_service
from app.services.publish_scheduler import PublishScheduler, queue_snapshot, scheduler_health


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
        publish_service.save_douyin_oauth_account(code, state=state)
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


@router.get("/queue")
async def get_send_queue() -> dict:
    return publish_service.get_publish_center_context()


@router.get("/queue/snapshot")
async def get_publish_queue_snapshot(task_id: str | None = None) -> dict:
    return queue_snapshot(task_id=task_id)


@router.post("/scheduler/run-once")
async def run_publish_scheduler_once() -> dict:
    import asyncio

    return await asyncio.to_thread(PublishScheduler().run_once)


@router.get("/scheduler/health")
async def get_publish_scheduler_health() -> dict:
    return scheduler_health()


@router.post("/queue/refresh")
async def refresh_send_queue(use_ai: bool = Query(default=False)) -> dict:
    try:
        return publish_service.refresh_send_queue(use_ai=use_ai)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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


@router.post("/covers/frames")
async def generate_publish_cover_frames(payload: PublishCoverFrameBatchCreate) -> dict:
    try:
        return publish_service.generate_publish_cover_frames(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/cover")
async def generate_publish_job_cover(job_id: str, payload: PublishCoverCreate) -> dict:
    try:
        return publish_service.generate_publish_job_cover(job_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/jobs/{job_id}/send-content")
async def update_send_job(job_id: str, payload: PublishSendJobUpdate) -> dict:
    try:
        return publish_service.update_send_job(job_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/metadata")
async def regenerate_send_job_metadata(job_id: str, use_ai: bool = Query(default=True)) -> dict:
    try:
        return publish_service.regenerate_send_job_metadata(job_id, use_ai=use_ai)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/send")
async def send_publish_job(job_id: str, background_tasks: BackgroundTasks) -> dict:
    try:
        return publish_service.start_opencli_send_batch(
            PublishSendStart(job_ids=[job_id]),
            background_tasks=background_tasks,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/send/start")
async def start_send_queue(payload: PublishSendStart, background_tasks: BackgroundTasks) -> dict:
    try:
        return publish_service.start_opencli_send_batch(payload, background_tasks=background_tasks)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/retry")
async def retry_publish_job(job_id: str) -> dict:
    try:
        return publish_service.retry_publish_job(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/publish-now")
async def publish_job_now(job_id: str) -> dict:
    import asyncio

    try:
        return await asyncio.to_thread(PublishScheduler().publish_now, job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/skip")
async def skip_publish_job(job_id: str) -> dict:
    try:
        return PublishScheduler().skip_job(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/approve-review")
async def approve_review_publish_job(job_id: str) -> dict:
    try:
        return PublishScheduler().approve_review(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/jobs/{job_id}/schedule")
async def update_publish_job_schedule(job_id: str, payload: PublishJobScheduleUpdate) -> dict:
    try:
        return publish_service.update_publish_job_schedule(job_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/jobs/schedule-batch")
async def update_publish_jobs_schedule_batch(payload: PublishBatchScheduleUpdate) -> dict:
    try:
        return PublishScheduler().update_batch_schedule(
            payload.job_ids,
            action=payload.action,
            start_at=payload.start_at or "",
            interval_hours=payload.interval_hours,
            daily_start_time=payload.daily_start_time,
            daily_end_time=payload.daily_end_time,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/jobs/{job_id}/content")
async def update_publish_job_content(job_id: str, payload: PublishJobContentUpdate) -> dict:
    try:
        return publish_service.update_publish_job_content(job_id, payload)
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
        return PublishScheduler().cancel_job(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
