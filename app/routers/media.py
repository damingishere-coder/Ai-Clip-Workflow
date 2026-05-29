from pathlib import Path
import mimetypes

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.services import task_service
from app.services.storage_service import get_artifact_paths, get_source_video_path, resolve_video_file_path, validate_source_video_path


router = APIRouter(prefix="/media/tasks", tags=["media"])


def _video_response(path: Path) -> FileResponse:
    media_type = mimetypes.guess_type(path.name)[0] or "video/mp4"
    return FileResponse(
        path=path,
        media_type=media_type,
        filename=path.name,
        content_disposition_type="inline",
    )


def _image_response(path: Path) -> FileResponse:
    media_type = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    return FileResponse(
        path=path,
        media_type=media_type,
        filename=path.name,
        content_disposition_type="inline",
    )


@router.get("/{task_id}/source-video")
async def get_task_source_video(task_id: str) -> FileResponse:
    task = task_service.get_task(task_id, include_video_probe=False)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    source_path = get_source_video_path(task)
    valid, error_message = validate_source_video_path(str(source_path) if source_path else None)
    if not valid or source_path is None:
        raise HTTPException(status_code=404, detail=error_message or "源视频不存在")

    return _video_response(source_path)


@router.get("/{task_id}/output-clips/{output_clip_id}")
async def get_task_output_clip(task_id: str, output_clip_id: str) -> FileResponse:
    task = task_service.get_task(task_id, include_video_probe=False)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    output_clip = task_service.get_output_clip(task_id, output_clip_id)
    if not output_clip:
        raise HTTPException(status_code=404, detail="切片记录不存在")

    output_path = resolve_video_file_path(output_clip.get("output_file_path")) or Path(output_clip.get("output_file_path") or "")
    if not output_path.exists() or not output_path.is_file():
        raise HTTPException(status_code=404, detail="切片视频文件不存在")

    return _video_response(output_path)


@router.get("/{task_id}/subtitled-clips/{output_clip_id}")
async def get_task_subtitled_clip(task_id: str, output_clip_id: str) -> FileResponse:
    task = task_service.get_task(task_id, include_video_probe=False)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    output_clip = task_service.get_output_clip(task_id, output_clip_id)
    if not output_clip:
        raise HTTPException(status_code=404, detail="切片记录不存在")

    output_path = resolve_video_file_path(output_clip.get("subtitled_output_file_path")) or Path(output_clip.get("subtitled_output_file_path") or "")
    if not output_path.exists() or not output_path.is_file():
        raise HTTPException(status_code=404, detail="带字幕视频文件不存在")

    return _video_response(output_path)


@router.get("/{task_id}/covers/{file_name}")
async def get_task_cover(task_id: str, file_name: str) -> FileResponse:
    task = task_service.get_task(task_id, include_video_probe=False)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    safe_name = Path(file_name).name
    cover_path = get_artifact_paths(task_id, task.get("task_dir_name"))["covers_dir"] / safe_name
    if not cover_path.exists() or not cover_path.is_file():
        raise HTTPException(status_code=404, detail="封面文件不存在")

    return _image_response(cover_path)
