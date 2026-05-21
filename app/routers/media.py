from pathlib import Path
import mimetypes

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.services import task_service
from app.services.storage_service import get_source_video_path, validate_source_video_path


router = APIRouter(prefix="/media/tasks", tags=["media"])


def _video_response(path: Path) -> FileResponse:
    media_type = mimetypes.guess_type(path.name)[0] or "video/mp4"
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
