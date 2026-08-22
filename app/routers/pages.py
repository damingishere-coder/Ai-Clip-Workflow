from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.templating import Jinja2Templates

from app.core.config import settings
from app.services.ai_prompt_preset_service import list_ai_prompt_presets
from app.services.publish_service import (
    get_publish_center_context,
    get_publish_link_states,
    get_task_publish_link_state,
)
from app.services.task_query_service import (
    get_clips_overview_context,
    get_dashboard_context,
    get_subtitle_task_context,
    get_subtitle_workflow_context,
    get_system_status_context,
)
from app.services.task_service import (
    get_artifact_paths,
    get_latest_ai_analysis_run,
    get_task,
    get_task_workflow_steps,
    get_transcript_preview,
    get_workflow_steps,
    list_clip_candidates,
    list_ai_analysis_runs,
    list_output_clips,
    list_task_name_history,
    list_tasks,
)


router = APIRouter(tags=["pages"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))


@router.get("/")
async def dashboard(request: Request):
    context = get_dashboard_context()
    return templates.TemplateResponse(
        name="index.html",
        request=request,
        context={
            "request": request,
            "active_page": "dashboard",
            "settings": settings,
            **context,
        },
    )


@router.get("/tasks")
async def tasks_page(request: Request):
    tasks = list_tasks()
    link_states = get_publish_link_states([task["id"] for task in tasks])
    for task in tasks:
        task["publish_link_state"] = link_states.get(task["id"], {})
    return templates.TemplateResponse(
        name="tasks.html",
        request=request,
        context={
            "request": request,
            "active_page": "tasks",
            "settings": settings,
            "tasks": tasks,
        },
    )


@router.get("/tasks/new")
async def new_task_page(request: Request):
    return templates.TemplateResponse(
        name="new_task.html",
        request=request,
        context={
            "request": request,
            "active_page": "new_task",
            "settings": settings,
            "candidate_count_options": [5, 8, 12, 20],
            "workflow_steps": get_workflow_steps(),
            "task_name_history": list_task_name_history(),
        },
    )


@router.get("/tasks/{task_id}")
async def task_detail_page(request: Request, task_id: str):
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    return templates.TemplateResponse(
        name="task_detail.html",
        request=request,
        context={
            "request": request,
            "active_page": "tasks",
            "settings": settings,
            "task": task,
            "publish_link_state": get_task_publish_link_state(task_id),
            "workflow_steps": get_task_workflow_steps(task),
            "transcript_lines": get_transcript_preview(task_id),
            "output_clips": list_output_clips(task_id),
            "ai_prompt_presets": list_ai_prompt_presets(),
            "latest_ai_analysis_run": get_latest_ai_analysis_run(task_id),
            "ai_analysis_runs": list_ai_analysis_runs(task_id),
        },
    )


@router.get("/tasks/{task_id}/transcript")
async def task_transcript_page(request: Request, task_id: str):
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    paths = get_artifact_paths(task_id)
    transcript_path = paths["transcript_path"]
    transcript_text = ""
    if transcript_path.exists():
        transcript_text = transcript_path.read_text(encoding="utf-8", errors="replace")

    return templates.TemplateResponse(
        name="transcript_full.html",
        request=request,
        context={
            "request": request,
            "active_page": "tasks",
            "settings": settings,
            "task": task,
            "transcript_path": str(transcript_path),
            "transcript_text": transcript_text,
            "transcript_exists": transcript_path.exists(),
        },
    )


def _filter_and_sort_clips(clips: list[dict], clip_filter: str, sort_by: str) -> list[dict]:
    if clip_filter == "enabled":
        clips = [clip for clip in clips if clip["enabled"]]
    elif clip_filter == "high":
        clips = [
            clip
            for clip in clips
            if clip.get("quality_tier") == "A"
            or "高" in clip.get("spread_value", "")
            or clip.get("spread_value", "").lower() == "high"
        ]

    if sort_by == "time":
        return sorted(clips, key=lambda clip: clip.get("start_seconds", 0))
    return sorted(
        clips,
        key=lambda clip: clip.get("quality_score") or clip.get("confidence_score", 0),
        reverse=True,
    )


async def _render_clip_review_page(
    request: Request,
    task_id: str,
    clip_filter: str = "all",
    sort_by: str = "confidence",
):
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    all_clips = list_clip_candidates(task_id)
    visible_clips = _filter_and_sort_clips(all_clips, clip_filter, sort_by)

    return templates.TemplateResponse(
        name="clip_review.html",
        request=request,
        context={
            "request": request,
            "active_page": "clips",
            "settings": settings,
            "task": task,
            "clips": visible_clips,
            "clip_count": len(all_clips),
            "enabled_clip_count": sum(1 for clip in all_clips if clip["enabled"]),
            "clip_filter": clip_filter,
            "sort_by": sort_by,
            "output_clips": list_output_clips(task_id),
            "publish_link_state": get_task_publish_link_state(task_id),
        },
    )


@router.get("/tasks/{task_id}/clips")
async def clip_review_page(
    request: Request,
    task_id: str,
    clip_filter: str = "all",
    sort_by: str = "confidence",
):
    return await _render_clip_review_page(request, task_id, clip_filter, sort_by)


@router.get("/tasks/{task_id}/clips/review")
async def clip_review_page_v2(
    request: Request,
    task_id: str,
    clip_filter: str = "all",
    sort_by: str = "confidence",
):
    return await _render_clip_review_page(request, task_id, clip_filter, sort_by)


@router.get("/clips")
async def clips_overview_page(request: Request):
    context = get_clips_overview_context()
    return templates.TemplateResponse(
        name="clips_overview.html",
        request=request,
        context={
            "request": request,
            "active_page": "clips",
            "settings": settings,
            **context,
        },
    )


@router.get("/subtitles")
async def subtitle_workflow_page(request: Request):
    context = get_subtitle_workflow_context()
    return templates.TemplateResponse(
        name="subtitle_workflow.html",
        request=request,
        context={
            "request": request,
            "active_page": "subtitles",
            "settings": settings,
            **context,
        },
    )


@router.get("/subtitles/{task_id}")
async def subtitle_task_page(request: Request, task_id: str):
    try:
        context = get_subtitle_task_context(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return templates.TemplateResponse(
        name="subtitle_workflow.html",
        request=request,
        context={
            "request": request,
            "active_page": "subtitles",
            "settings": settings,
            "subtitle_task_mode": True,
            "publish_link_state": get_task_publish_link_state(task_id),
            **context,
        },
    )


@router.get("/publish")
async def publish_center_page(request: Request):
    focus_task_id = request.query_params.get("task_id", "")
    return templates.TemplateResponse(
        name="publish.html",
        request=request,
        context={
            "request": request,
            "active_page": "publish",
            "settings": settings,
            "publish_message": request.query_params.get("publish_message", ""),
            "focus_task_id": focus_task_id,
            "focus_platform": "douyin",
            "focus_tab": request.query_params.get("tab", ""),
            **get_publish_center_context(focus_task_id=focus_task_id),
        },
    )


@router.get("/system")
async def system_status_page(request: Request):
    return templates.TemplateResponse(
        name="system_status.html",
        request=request,
        context={
            "request": request,
            "active_page": "system",
            "settings": settings,
            **get_system_status_context(),
        },
    )
