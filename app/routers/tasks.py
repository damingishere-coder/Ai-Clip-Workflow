from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Query, UploadFile

from app.models.task import (
    ClipCandidateBatchUpdate,
    ClipCandidateUpdate,
    TaskAIPreferenceUpdate,
    TaskCreate,
    TaskStatusUpdate,
)
from app.services import task_service
from app.services.storage_service import save_uploaded_video


router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.get("")
async def list_tasks() -> list[dict]:
    return task_service.list_tasks()


@router.post("")
async def create_task(payload: TaskCreate) -> dict:
    try:
        return task_service.create_task_record(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/upload")
async def create_upload_task(
    task_name: str = Form(...),
    platform: str = Form("general"),
    max_clip_duration: int = Form(2),
    candidate_clip_count: int = Form(8),
    ai_preference: str | None = Form(None),
    video_file: UploadFile = File(...),
) -> dict:
    task_id = uuid4().hex[:12]
    saved_path = save_uploaded_video(task_id, video_file.filename or "source_video.mp4", video_file.file)
    payload = TaskCreate(
        task_name=task_name,
        source_type="upload",
        platform=platform,
        original_video_path=str(saved_path),
        max_clip_duration=max_clip_duration,
        candidate_clip_count=candidate_clip_count,
        ai_preference=ai_preference,
    )
    try:
        return task_service.create_task_record(payload, task_id=task_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{task_id}")
async def get_task_detail(task_id: str) -> dict:
    task = task_service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


@router.get("/{task_id}/transcript-status")
async def get_transcript_status(task_id: str) -> dict:
    try:
        return task_service.get_task_transcript_status(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/{task_id}")
async def delete_task(task_id: str) -> dict:
    try:
        return task_service.soft_delete_task(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/{task_id}/status")
async def patch_task_status(task_id: str, payload: TaskStatusUpdate) -> dict:
    task = task_service.update_task_status(task_id, payload.status, payload.error_message)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


@router.patch("/{task_id}/ai-preference")
async def patch_task_ai_preference(task_id: str, payload: TaskAIPreferenceUpdate) -> dict:
    try:
        return task_service.update_task_ai_preference(task_id, payload.ai_preference)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{task_id}/process/audio")
async def process_audio(task_id: str) -> dict:
    try:
        return task_service.process_task_audio(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/{task_id}/process/transcript")
async def process_transcript(task_id: str, background_tasks: BackgroundTasks) -> dict:
    try:
        return task_service.process_task_transcript(task_id, background_tasks=background_tasks)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/{task_id}/process/transcript-workflow")
async def process_transcript_workflow(
    task_id: str,
    background_tasks: BackgroundTasks,
    force: bool = Query(default=False),
) -> dict:
    try:
        return task_service.process_task_transcript_workflow(
            task_id,
            background_tasks=background_tasks,
            force=force,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/{task_id}/process/ai")
async def process_ai_analysis(
    task_id: str,
    provider: str | None = Query(default=None, pattern="^(remote|local)$"),
) -> dict:
    try:
        return task_service.process_task_ai_analysis(task_id, provider=provider)
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


@router.post("/{task_id}/clips/batch-update")
async def batch_update_clip_candidates(
    task_id: str,
    payload: ClipCandidateBatchUpdate,
) -> dict:
    try:
        return task_service.update_clip_candidates_batch(task_id, payload.clips)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{task_id}/clips/{clip_id}/transcript-excerpt")
async def get_clip_transcript_excerpt(task_id: str, clip_id: str) -> dict:
    try:
        return task_service.get_clip_transcript_excerpt(task_id, clip_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{task_id}/clips/generate")
async def generate_clips_placeholder(task_id: str) -> dict:
    try:
        return task_service.request_clip_generation_placeholder(task_id)
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
