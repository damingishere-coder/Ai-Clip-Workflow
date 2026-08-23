from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Query, Response, UploadFile

from app.models.subtitle import (
    SubtitleApproveRequest,
    SubtitleAISuggestionAcceptRequest,
    SubtitleAIRevisionRequest,
    SubtitleOperationsRequest,
    SubtitleRevisionCreate,
    SubtitleTaskRenderRequest,
    SubtitleSyncRequest,
)
from app.services import job_service
from app.services.subtitle_ai_service import generate_subtitle_suggestions
from app.services.subtitle_auto_workflow_service import (
    enqueue_task_subtitle_render,
    prepare_task_subtitle_review,
    skip_task_subtitles_and_resume,
)
from app.services.subtitle_data_service import (
    SubtitleRevisionConflict,
    accept_suggestion_revision,
    apply_revision_operations,
    approve_revision,
    create_manual_revision,
    ensure_source_track,
    export_subtitle_text,
    get_revision_cues,
    get_track,
    get_waveform_peaks,
    import_subtitle_text,
    list_revisions,
    list_task_tracks,
    sync_clip_track,
)


router = APIRouter(prefix="/api/subtitles", tags=["subtitles"])


@router.get("/tasks/{task_id}/tracks")
def list_tracks(task_id: str, ensure: bool = Query(default=True)) -> dict:
    return _call(lambda: {"tracks": list_task_tracks(task_id, ensure=ensure)})


@router.post("/tasks/{task_id}/source-track")
def generate_source_track(task_id: str, payload: SubtitleSyncRequest) -> dict:
    return _call(lambda: {"track": ensure_source_track(task_id, force=payload.force)})


@router.post("/tasks/{task_id}/prepare-review")
def prepare_review(task_id: str) -> dict:
    return _call(lambda: prepare_task_subtitle_review(task_id))


@router.post("/tasks/{task_id}/approve-and-render")
def approve_and_render(task_id: str, payload: SubtitleTaskRenderRequest) -> dict:
    return _call(
        lambda: enqueue_task_subtitle_render(
            task_id,
            approve_active_revisions=payload.approve_active_revisions,
            continue_pipeline=True,
        )
    )


@router.post("/tasks/{task_id}/skip-and-resume")
def skip_and_resume(task_id: str) -> dict:
    return _call(lambda: skip_task_subtitles_and_resume(task_id))


@router.get("/tasks/{task_id}/jobs")
def list_subtitle_jobs(task_id: str) -> dict:
    jobs = [
        job
        for job in job_service.list_jobs(task_id=task_id)
        if job.get("job_type") == job_service.JOB_TYPE_SUBTITLE
    ]
    return {"task_id": task_id, "jobs": jobs, "count": len(jobs)}


@router.get("/tracks/{track_id}")
def read_track(track_id: str) -> dict:
    return _call(lambda: {"track": get_track(track_id)})


@router.get("/tracks/{track_id}/revisions")
def read_revisions(track_id: str) -> dict:
    return _call(lambda: {"revisions": list_revisions(track_id)})


@router.get("/tracks/{track_id}/cues")
def read_cues(
    track_id: str,
    revision_id: str | None = Query(default=None),
    start_ms: int | None = Query(default=None, ge=0),
    end_ms: int | None = Query(default=None, ge=0),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=2000),
) -> dict:
    return _call(
        lambda: get_revision_cues(
            track_id,
            revision_id=revision_id,
            start_ms=start_ms,
            end_ms=end_ms,
            offset=offset,
            limit=limit,
        )
    )


@router.post("/tracks/{track_id}/revisions")
def save_revision(track_id: str, payload: SubtitleRevisionCreate) -> dict:
    return _call(
        lambda: {
            "revision": create_manual_revision(
                track_id,
                base_revision_id=payload.base_revision_id,
                cues=payload.cues,
                note=payload.note,
            )
        }
    )


@router.post("/tracks/{track_id}/operations")
def apply_operations(track_id: str, payload: SubtitleOperationsRequest) -> dict:
    return _call(
        lambda: {
            "revision": apply_revision_operations(
                track_id,
                base_revision_id=payload.base_revision_id,
                operations=payload.operations,
                note=payload.note,
            )
        }
    )


@router.post("/tracks/{track_id}/approve")
def approve(track_id: str, payload: SubtitleApproveRequest) -> dict:
    return _call(lambda: {"revision": approve_revision(track_id, payload.revision_id)})


@router.post("/tracks/{track_id}/sync-source")
def sync_source(track_id: str, payload: SubtitleSyncRequest) -> dict:
    return _call(lambda: {"track": sync_clip_track(track_id, force=payload.force)})


@router.post("/tracks/{track_id}/ai-suggestions")
def ai_suggestions(track_id: str, payload: SubtitleAIRevisionRequest) -> dict:
    return _call(
        lambda: generate_subtitle_suggestions(
            track_id,
            revision_id=payload.revision_id,
            cue_ids=payload.cue_ids,
            instructions=payload.instructions,
        )
    )


@router.post("/tracks/{track_id}/ai-suggestions/{suggestion_revision_id}/accept")
def accept_ai_suggestions(
    track_id: str,
    suggestion_revision_id: str,
    payload: SubtitleAISuggestionAcceptRequest,
) -> dict:
    return _call(
        lambda: {
            "revision": accept_suggestion_revision(
                track_id,
                suggestion_revision_id=suggestion_revision_id,
                base_revision_id=payload.base_revision_id,
                cue_ids=payload.cue_ids,
            )
        }
    )


@router.post("/tracks/{track_id}/import")
async def import_subtitle(
    track_id: str,
    file: UploadFile = File(...),
    format_name: str | None = Form(default=None),
) -> dict:
    raw = await file.read(10 * 1024 * 1024 + 1)
    if len(raw) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="字幕文件不能超过 10 MB")
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            content = raw.decode("gb18030")
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=422, detail="字幕文件必须是 UTF-8 或 GB18030 文本") from exc
    resolved_format = format_name or (file.filename or "").rsplit(".", 1)[-1]
    return _call(
        lambda: {
            "revision": import_subtitle_text(
                track_id,
                content=content,
                format_name=resolved_format,
                note=f"导入文件：{file.filename or 'subtitle'}",
            )
        }
    )


@router.get("/tracks/{track_id}/export")
def export_subtitle(
    track_id: str,
    format_name: str = Query(pattern=r"^(srt|vtt|ass)$"),
    revision_id: str | None = Query(default=None),
) -> Response:
    try:
        content, media_type, filename = export_subtitle_text(
            track_id,
            revision_id=revision_id,
            format_name=format_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return Response(
        content=content.encode("utf-8-sig"),
        media_type=f"{media_type}; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@router.get("/tracks/{track_id}/peaks")
def waveform_peaks(
    track_id: str,
    max_points: int = Query(default=12000, ge=1000, le=50000),
) -> dict:
    return _call(lambda: get_waveform_peaks(track_id, max_points=max_points))


def _call(callback):
    try:
        return callback()
    except SubtitleRevisionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
