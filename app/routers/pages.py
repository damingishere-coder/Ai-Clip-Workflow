from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

from app.core.config import settings
from app.services.task_service import (
    get_dashboard_context,
    get_mock_task,
    get_workflow_steps,
    list_mock_clips,
    list_mock_tasks,
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
    return templates.TemplateResponse(
        name="tasks.html",
        request=request,
        context={
            "request": request,
            "active_page": "tasks",
            "settings": settings,
            "tasks": list_mock_tasks(),
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
            "clip_length_options": [2, 5, 10],
            "candidate_count_options": [5, 8, 12, 20],
            "workflow_steps": get_workflow_steps(),
        },
    )


@router.get("/tasks/{task_id}")
async def task_detail_page(request: Request, task_id: str):
    return templates.TemplateResponse(
        name="task_detail.html",
        request=request,
        context={
            "request": request,
            "active_page": "tasks",
            "settings": settings,
            "task": get_mock_task(task_id),
            "workflow_steps": get_workflow_steps(),
            "transcript_lines": [
                {"time": "00:12:08", "text": "这里是转写文本预览，后续会显示真实时间戳内容。"},
                {"time": "00:13:20", "text": "系统会把长视频拆成分钟级或分段级时间线。"},
                {"time": "00:14:05", "text": "AI 将基于这些文本推荐适合传播的短视频片段。"},
            ],
        },
    )


@router.get("/tasks/{task_id}/clips")
async def clip_review_page(request: Request, task_id: str):
    return templates.TemplateResponse(
        name="clip_review.html",
        request=request,
        context={
            "request": request,
            "active_page": "clips",
            "settings": settings,
            "task": get_mock_task(task_id),
            "clips": list_mock_clips(task_id),
        },
    )
