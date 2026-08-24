from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Query, UploadFile
from starlette.concurrency import run_in_threadpool

from app.models.task import (
    ClipCandidateBatchUpdate,
    ClipFeedbackCreate,
    ClipCandidateUpdate,
    SubtitleStyleUpdate,
    TaskAIPreferenceUpdate,
    TaskAIPromptPresetUpdate,
    TaskCandidateClipCountUpdate,
    TaskCreate,
    TaskSelectionSettingsUpdate,
    TaskStatus,
    TaskStatusUpdate,
)
from app.services import task_service
from app.services.ai_prompt_preset_service import update_task_ai_prompt_preset
from app.services.pipeline_engine import start_auto_pipeline
from app.services.storage_service import (
    allocate_task_dir_name,
    remove_failed_task_directory,
    save_uploaded_video,
    StorageSafetyError,
)
from app.services.task_lifecycle_service import TaskDeletionConflictError
from app.services import job_service


router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.get("")
async def list_tasks() -> list[dict]:
    return task_service.list_tasks()


@router.post("")
async def create_task(payload: TaskCreate, background_tasks: BackgroundTasks) -> dict:
    try:
        result = task_service.create_task_record(payload)
        if payload.auto_mode:
            result["auto_pipeline"] = start_auto_pipeline(result["id"], background_tasks=background_tasks)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/upload")
async def create_upload_task(
    background_tasks: BackgroundTasks,
    task_name: str = Form(...),
    platform: str = Form("general"),
    max_clip_duration: int = Form(10),
    candidate_clip_count: int = Form(12),
    selection_profile: str | None = Form(None),
    final_clip_target: int = Form(5),
    highlight_density_per_hour: int = Form(4),
    highlight_total_limit: int = Form(30),
    ai_preference: str | None = Form(None),
    auto_mode: bool = Form(False),
    auto_clip_count: str = Form("auto"),
    auto_min_clip_seconds: int = Form(15),
    auto_max_clip_seconds: int = Form(300),
    auto_schedule_mode: str = Form("default"),
    auto_schedule_start_at: str | None = Form(""),
    auto_schedule_interval_hours: int = Form(3),
    auto_schedule_daily_start_time: str = Form("07:00"),
    auto_schedule_daily_end_time: str = Form("00:00"),
    auto_metadata_use_ai: bool = Form(False),
    video_file: UploadFile = File(...),
) -> dict:
    if not selection_profile:
        raise HTTPException(status_code=422, detail="请选择选片模式")
    task_id = uuid4().hex[:12]
    task_dir_name = allocate_task_dir_name(task_name, exclude_task_id=task_id)
    task_record_created = False
    try:
        saved_path = await run_in_threadpool(
            save_uploaded_video,
            task_id,
            video_file.filename or "source_video.mp4",
            video_file.file,
            task_dir_name,
        )
        payload = TaskCreate(
            task_name=task_name,
            source_type="upload",
            platform=platform,
            original_video_path=str(saved_path),
            max_clip_duration=max_clip_duration,
            candidate_clip_count=candidate_clip_count,
            selection_profile=selection_profile,
            final_clip_target=final_clip_target,
            highlight_density_per_hour=highlight_density_per_hour,
            highlight_total_limit=highlight_total_limit,
            ai_preference=ai_preference,
            auto_mode=auto_mode,
            auto_clip_count=auto_clip_count,
            auto_min_clip_seconds=auto_min_clip_seconds,
            auto_max_clip_seconds=auto_max_clip_seconds,
            auto_schedule_mode=auto_schedule_mode,
            auto_schedule_start_at=auto_schedule_start_at,
            auto_schedule_interval_hours=auto_schedule_interval_hours,
            auto_schedule_daily_start_time=auto_schedule_daily_start_time,
            auto_schedule_daily_end_time=auto_schedule_daily_end_time,
            auto_metadata_use_ai=auto_metadata_use_ai,
        )
        result = task_service.create_task_record(payload, task_id=task_id, task_dir_name=task_dir_name)
        task_record_created = True
        if payload.auto_mode:
            result["auto_pipeline"] = start_auto_pipeline(task_id, background_tasks=background_tasks)
        return result
    except ValueError as exc:
        if not task_record_created:
            await run_in_threadpool(remove_failed_task_directory, task_id, task_dir_name)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        if not task_record_created:
            await run_in_threadpool(remove_failed_task_directory, task_id, task_dir_name)
        raise
    finally:
        await video_file.close()


@router.get("/{task_id}")
async def get_task_detail(task_id: str) -> dict:
    task = task_service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


@router.get("/{task_id}/live-status")
async def get_task_live_status(task_id: str) -> dict:
    try:
        return task_service.get_task_live_status(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{task_id}/transcript-status")
async def get_transcript_status(task_id: str) -> dict:
    try:
        return task_service.get_task_transcript_status(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{task_id}/ai-analysis-status")
async def get_ai_analysis_status(task_id: str) -> dict:
    try:
        return task_service.get_task_ai_analysis_status(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/{task_id}")
async def delete_task(task_id: str) -> dict:
    try:
        return task_service.soft_delete_task(task_id)
    except TaskDeletionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except StorageSafetyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.patch("/{task_id}/status")
async def patch_task_status(task_id: str, payload: TaskStatusUpdate) -> dict:
    try:
        task = task_service.transition_task_status(task_id, payload.status, payload.error_message)
    except task_service.TaskStatusConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


@router.patch("/{task_id}/ai-preference")
async def patch_task_ai_preference(task_id: str, payload: TaskAIPreferenceUpdate) -> dict:
    try:
        return task_service.update_task_ai_preference(task_id, payload.ai_preference)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/{task_id}/ai-prompt-preset")
async def patch_task_ai_prompt_preset(task_id: str, payload: TaskAIPromptPresetUpdate) -> dict:
    try:
        return update_task_ai_prompt_preset(task_id, payload.ai_prompt_preset_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/{task_id}/candidate-clip-count")
async def patch_task_candidate_clip_count(task_id: str, payload: TaskCandidateClipCountUpdate) -> dict:
    try:
        return task_service.update_task_candidate_clip_count(task_id, payload.candidate_clip_count)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/{task_id}/selection-settings")
async def patch_task_selection_settings(task_id: str, payload: TaskSelectionSettingsUpdate) -> dict:
    try:
        return task_service.update_task_selection_settings(
            task_id,
            payload.selection_profile,
            payload.final_clip_target,
            payload.highlight_density_per_hour,
            payload.highlight_total_limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{task_id}/process/audio")
async def process_audio(task_id: str) -> dict:
    try:
        return task_service.process_task_audio(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/{task_id}/process/transcript")
async def process_transcript(
    task_id: str,
    background_tasks: BackgroundTasks,
    provider: str | None = Query(default=None, pattern="^(remote|local)$"),
) -> dict:
    try:
        return task_service.process_task_transcript(task_id, background_tasks=background_tasks, provider=provider)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/{task_id}/process/transcript-workflow")
async def process_transcript_workflow(
    task_id: str,
    background_tasks: BackgroundTasks,
    force: bool = Query(default=False),
    provider: str | None = Query(default=None, pattern="^(remote|local)$"),
) -> dict:
    try:
        task = task_service.get_task(task_id, include_video_probe=False)
        if not task:
            raise ValueError("任务不存在")
        if task.get("transcript_exists") and not force:
            return {"status": "completed", "message": "转写已经生成，无需重复处理。", "task": task}
        job, created = job_service.create_or_get_active_job(
            task_id=task_id,
            job_type=job_service.JOB_TYPE_TRANSCRIPT,
            payload={"force": force, "provider": provider},
        )
        return {
            "status": job["status"],
            "message": "转写任务已加入持久化队列" if created else "已有转写任务正在排队或运行",
            "job_id": job["id"],
            "job": job,
            "task": task,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/{task_id}/process/transcript-cancel")
async def cancel_transcript(task_id: str) -> dict:
    try:
        active_jobs = [
            job for job in job_service.list_jobs(task_id=task_id)
            if job.get("job_type") == job_service.JOB_TYPE_TRANSCRIPT
            and job.get("status") in {job_service.JOB_STATUS_QUEUED, job_service.JOB_STATUS_RUNNING}
        ]
        if active_jobs:
            job_service.request_job_cancel(active_jobs[0]["id"])
        return task_service.cancel_task_transcript(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{task_id}/process/ai")
async def process_ai_analysis(
    task_id: str,
    provider: str | None = Query(default=None, pattern="^(codex|remote|local)$"),
) -> dict:
    try:
        return await run_in_threadpool(task_service.process_task_ai_analysis, task_id, provider=provider)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{task_id}/ai-analysis-runs")
async def get_ai_analysis_runs(task_id: str) -> dict:
    try:
        return {
            "status": "ok",
            "latest": task_service.get_latest_ai_analysis_run(task_id),
            "runs": task_service.list_ai_analysis_runs(task_id),
        }
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{task_id}/ai-analysis-runs/{run_id}/restore")
async def restore_ai_analysis_run(task_id: str, run_id: str) -> dict:
    try:
        return task_service.restore_ai_analysis_run(task_id, run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{task_id}/clips/{clip_id}/update")
async def update_clip_candidate(
    task_id: str,
    clip_id: str,
    payload: ClipCandidateUpdate,
) -> dict:
    try:
        return task_service.update_clip_candidate(task_id, clip_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{task_id}/clips/{clip_id}")
async def delete_clip_candidate(task_id: str, clip_id: str) -> dict:
    try:
        return task_service.delete_clip_candidate(task_id, clip_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{task_id}/clips/batch-update")
async def batch_update_clip_candidates(
    task_id: str,
    payload: ClipCandidateBatchUpdate,
) -> dict:
    try:
        return task_service.update_clip_candidates_batch(task_id, payload.clips)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{task_id}/clips/sync-publish")
async def sync_reviewed_clips_to_publish_center(
    task_id: str,
    payload: ClipCandidateBatchUpdate,
) -> dict:
    try:
        return await run_in_threadpool(
            task_service.sync_reviewed_clips_to_publish_center,
            task_id,
            payload.clips,
        )
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/{task_id}/clips/{clip_id}/transcript-excerpt")
async def get_clip_transcript_excerpt(
    task_id: str,
    clip_id: str,
    start_time: str | None = None,
    end_time: str | None = None,
) -> dict:
    try:
        return task_service.get_clip_transcript_excerpt(task_id, clip_id, start_time, end_time)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{task_id}/clips/{clip_id}/feedback")
async def save_clip_feedback(task_id: str, clip_id: str, payload: ClipFeedbackCreate) -> dict:
    try:
        return task_service.save_clip_feedback(task_id, clip_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{task_id}/process/cuts")
async def process_video_cuts(task_id: str) -> dict:
    try:
        return task_service.process_task_video_cuts(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{task_id}/process/auto")
async def process_auto_pipeline(
    task_id: str,
    background_tasks: BackgroundTasks,
    retry: bool = Query(default=False),
) -> dict:
    try:
        return start_auto_pipeline(task_id, background_tasks=background_tasks, retry=retry)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{task_id}/process/auto-retry")
async def retry_auto_pipeline(task_id: str, background_tasks: BackgroundTasks) -> dict:
    try:
        return start_auto_pipeline(task_id, background_tasks=background_tasks, retry=True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{task_id}/process/auto-resume")
async def resume_auto_pipeline(task_id: str, background_tasks: BackgroundTasks) -> dict:
    try:
        task = task_service.get_task(task_id, include_video_probe=False)
        if not task:
            raise ValueError("任务不存在")
        if not task.get("auto_mode"):
            raise ValueError("该任务未开启全自动模式")
        if not task.get("analysis_exists"):
            raise ValueError("还没有可恢复的 AI 分析结果")
        return start_auto_pipeline(
            task_id,
            background_tasks=background_tasks,
            start_step=TaskStatus.CLIP_SELECTING,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{task_id}/output-clips/{output_clip_id}/subtitles")
async def render_output_clip_subtitles(task_id: str, output_clip_id: str) -> dict:
    try:
        from app.services.subtitle_auto_workflow_service import enqueue_task_subtitle_render

        return enqueue_task_subtitle_render(
            task_id,
            output_clip_ids=[output_clip_id],
            approve_active_revisions=False,
            continue_pipeline=False,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/subtitle-style")
async def save_subtitle_style(payload: SubtitleStyleUpdate) -> dict:
    try:
        return task_service.update_default_subtitle_style(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ── Job 队列相关端点 ──────────────────────────────────────────────

@router.post("/{task_id}/process/cuts-async")
async def process_video_cuts_async(
    task_id: str,
) -> dict:
    """自动切片异步版：创建 job 后立即返回，后台执行切割"""
    # 先验证任务存在
    task = task_service.get_task(task_id, include_video_probe=False)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    # 同一任务只保留一个 queued/running 的 video_cut job。
    try:
        job, created = job_service.create_or_get_active_job(
            task_id=task_id,
            job_type=job_service.JOB_TYPE_VIDEO_CUT,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"创建切片任务失败：{exc}") from exc

    return {
        "status": job["status"],
        "message": (
            "切片任务已加入后台队列，可通过 job id 查询进度"
            if created
            else "已有切片任务正在运行，已继续显示原任务进度"
        ),
        "job_id": job["id"],
        "job": job,
        "created": created,
    }


@router.get("/jobs/{job_id}")
async def get_job_status(job_id: str) -> dict:
    """查询 job 的执行状态和进度"""
    job = job_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job 不存在")
    return job


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str) -> dict:
    job = job_service.request_job_cancel(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job 不存在")
    return {"status": job["status"], "message": job.get("message"), "job": job}


@router.post("/jobs/{job_id}/retry")
async def retry_job(job_id: str) -> dict:
    job = job_service.retry_job(job_id)
    if not job:
        raise HTTPException(status_code=409, detail="只有失败或已取消的 job 可以重试")
    return {"status": job["status"], "message": job.get("message"), "job": job}


@router.get("/{task_id}/jobs")
async def list_task_jobs(task_id: str, status: str | None = Query(default=None)) -> dict:
    """查看某个任务下的所有 job 记录"""
    task = task_service.get_task(task_id, include_video_probe=False)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    jobs = job_service.list_jobs(task_id=task_id, status=status)
    return {
        "task_id": task_id,
        "jobs": jobs,
        "count": len(jobs),
    }
