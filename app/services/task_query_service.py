"""页面查询 / 统计上下文服务

从 task_service 中拆分出来的偏页面展示、统计类函数，
不参与任务生命周期、状态变更等核心逻辑。
"""

from datetime import datetime
import shutil

from app.core.config import settings
from app.models.task import TaskStatus
from app.services.ai_config_service import get_ai_config_context
from app.services.task_service import (
    count_clip_candidates,
    count_completed_output_clips,
    count_enabled_clip_candidates,
    get_default_subtitle_style,
    get_task,
    list_output_clips,
    list_tasks,
    WORKFLOW_STEPS,
)


def get_dashboard_context() -> dict:
    """Dashboard 首页统计上下文"""
    tasks = list_tasks()
    today = datetime.now().date()
    today_count = 0
    for task in tasks:
        raw_created_at = task.get("created_at_raw")
        if not raw_created_at:
            continue
        try:
            if datetime.fromisoformat(raw_created_at).astimezone().date() == today:
                today_count += 1
        except ValueError:
            continue

    pending_count = sum(
        1
        for task in tasks
        if task["status"] in {TaskStatus.pending_video.value, TaskStatus.pending_processing.value}
    )
    review_count = sum(1 for task in tasks if task["status"] == TaskStatus.pending_review.value)
    completed_count = sum(
        1
        for task in tasks
        if task["status"] in {TaskStatus.completed.value, TaskStatus.completed_with_errors.value}
    )
    output_clip_count = sum(int(task.get("output_clip_count") or 0) for task in tasks)
    ready_for_subtitle_count = sum(count_completed_output_clips(task["id"]) for task in tasks)
    failed_count = sum(1 for task in tasks if task["status"] == TaskStatus.failed.value)

    return {
        "stats": [
            {"label": "今日新增任务", "value": today_count, "note": "来自 SQLite", "tone": "blue"},
            {"label": "待处理", "value": pending_count, "note": "可继续推进", "tone": "amber"},
            {"label": "待检查", "value": review_count, "note": "AI 结果可生成切片", "tone": "purple"},
            {"label": "已切片任务", "value": completed_count, "note": f"输出 {output_clip_count} 条切片", "tone": "green"},
            {"label": "待加字幕", "value": ready_for_subtitle_count, "note": "切片后工作流", "tone": "blue"},
            {"label": "待推送", "value": ready_for_subtitle_count, "note": "需字幕和发布确认", "tone": "red"},
            {"label": "失败任务", "value": failed_count, "note": "需排查", "tone": "red"},
        ],
        "focus_stats": [
            {"label": "输出切片", "value": output_clip_count, "description": "条短视频已生成记录"},
            {"label": "待加字幕", "value": ready_for_subtitle_count, "description": "条切片可进入字幕工作台"},
            {"label": "待推送", "value": ready_for_subtitle_count, "description": "条切片等待发布前确认"},
        ],
        "workflow_steps": WORKFLOW_STEPS,
        "recent_tasks": tasks[:5],
    }


def get_clips_overview_context() -> dict:
    """片段总览页统计上下文"""
    tasks = list_tasks()
    enriched_tasks = []
    for task in tasks:
        clip_count = count_clip_candidates(task["id"])
        enabled_count = count_enabled_clip_candidates(task["id"])
        review_ready = clip_count > 0
        can_cut = enabled_count > 0 and task["source_exists"]
        if task["status"] == TaskStatus.failed.value:
            review_stage = "异常"
            review_tone = "red"
        elif task["status"] == TaskStatus.completed.value:
            review_stage = "已完成"
            review_tone = "green"
        elif task["status"] == TaskStatus.completed_with_errors.value:
            review_stage = "部分完成"
            review_tone = "amber"
        elif task["status"] == TaskStatus.pending_review.value or review_ready:
            review_stage = "待检查"
            review_tone = "purple"
        elif task["status"] in {TaskStatus.pending_ai.value, TaskStatus.ai_analyzing.value}:
            review_stage = "待 AI"
            review_tone = "blue"
        else:
            review_stage = "待前置流程"
            review_tone = "amber"

        enriched_tasks.append(
            {
                **task,
                "real_clip_count": clip_count,
                "enabled_clip_count": enabled_count,
                "review_stage": review_stage,
                "review_tone": review_tone,
                "can_cut": can_cut,
                "review_ready": review_ready,
            }
        )

    return {
        "tasks": enriched_tasks,
        "stats": [
            {
                "label": "待 AI 分析",
                "value": sum(
                    1
                    for task in tasks
                    if task["status"] in {TaskStatus.pending_ai.value, TaskStatus.ai_analyzing.value}
                ),
                "tone": "blue",
            },
            {
                "label": "待检查",
                "value": sum(1 for task in enriched_tasks if task["review_stage"] == "待检查"),
                "tone": "purple",
            },
            {
                "label": "可生成切片",
                "value": sum(1 for task in enriched_tasks if task["can_cut"]),
                "tone": "green",
            },
            {
                "label": "已完成",
                "value": sum(
                    1
                    for task in tasks
                    if task["status"] in {TaskStatus.completed.value, TaskStatus.completed_with_errors.value}
                ),
                "tone": "green",
            },
            {
                "label": "异常任务",
                "value": sum(1 for task in tasks if task["status"] == TaskStatus.failed.value),
                "tone": "red",
            },
        ],
    }


def _resolve_task_subtitle_stage(output_clips: list[dict]) -> tuple[str, str]:
    """根据输出切片列表判断字幕整体阶段"""
    if not output_clips:
        return "无切片", "amber"
    completed = sum(1 for output in output_clips if output.get("subtitle_status") == "completed")
    if completed == len(output_clips):
        return "字幕完成", "green"
    if completed:
        return "部分完成", "amber"
    return "待加字幕", "blue"


def get_subtitle_workflow_context() -> dict:
    """字幕工作台总览页上下文"""
    tasks = list_tasks()
    workflow_tasks = []
    total_output_records = 0
    ready_output_clips = 0
    completed_subtitles = 0
    playable_output_clips = 0

    for task in tasks:
        output_clips = []
        for output in list_output_clips(task["id"]):
            total_output_records += 1
            if output.get("status") == "completed":
                ready_output_clips += 1
            if output.get("subtitle_status") == "completed":
                completed_subtitles += 1
            if output.get("file_exists"):
                playable_output_clips += 1
            output_clips.append(
                {
                    **output,
                }
            )

        if output_clips:
            task_completed_subtitles = sum(1 for output in output_clips if output.get("subtitle_status") == "completed")
            if task_completed_subtitles == len(output_clips):
                subtitle_stage = "字幕完成"
                subtitle_tone = "green"
            elif task_completed_subtitles:
                subtitle_stage = "部分完成"
                subtitle_tone = "amber"
            else:
                subtitle_stage = "待加字幕"
                subtitle_tone = "blue"
            workflow_tasks.append(
                {
                    **task,
                    "subtitle_stage": subtitle_stage,
                    "subtitle_tone": subtitle_tone,
                    "subtitle_done_count": task_completed_subtitles,
                    "output_clips": output_clips,
                }
            )

    return {
        "tasks": workflow_tasks,
        "stats": [
            {"label": "输出切片记录", "value": total_output_records, "tone": "green"},
            {"label": "待加字幕切片", "value": ready_output_clips, "tone": "blue"},
            {"label": "已加字幕成片", "value": completed_subtitles, "tone": "green"},
            {"label": "可预览视频", "value": playable_output_clips, "tone": "purple"},
            {"label": "待一键推送", "value": completed_subtitles, "tone": "red"},
        ],
    }


def get_subtitle_task_context(task_id: str) -> dict:
    """单个任务的字幕页上下文"""
    task = get_task(task_id)
    if not task:
        raise ValueError("任务不存在")
    output_clips = list_output_clips(task_id)
    return {
        "task": {
            **task,
            "subtitle_stage": _resolve_task_subtitle_stage(output_clips)[0],
            "subtitle_tone": _resolve_task_subtitle_stage(output_clips)[1],
        },
        "output_clips": output_clips,
        "subtitle_style": get_default_subtitle_style(),
        "stats": [
            {"label": "输出切片", "value": len(output_clips), "tone": "green"},
            {
                "label": "待加字幕",
                "value": sum(1 for output in output_clips if output.get("subtitle_status") != "completed"),
                "tone": "blue",
            },
            {
                "label": "已加字幕",
                "value": sum(1 for output in output_clips if output.get("subtitle_status") == "completed"),
                "tone": "green",
            },
        ],
    }


def get_system_status_context() -> dict:
    """系统状态页上下文"""
    tasks = list_tasks()
    ffmpeg_path = shutil.which("ffmpeg")
    ffprobe_path = shutil.which("ffprobe")
    failed_tasks = [task for task in tasks if task["status"] == TaskStatus.failed.value]
    ai_config = get_ai_config_context()
    pending_tasks = [
        task
        for task in tasks
        if task["status"] in {TaskStatus.pending_video.value, TaskStatus.pending_processing.value}
    ]
    review_tasks = [task for task in tasks if task["status"] == TaskStatus.pending_review.value]
    completed_tasks = [
        task
        for task in tasks
        if task["status"] in {TaskStatus.completed.value, TaskStatus.completed_with_errors.value}
    ]
    return {
        "storage_root": str(settings.storage_root),
        "storage_exists": settings.storage_root.exists(),
        "database_path": str(settings.database_path),
        "database_exists": settings.database_path.exists(),
        "ffmpeg_path": ffmpeg_path or "未找到",
        "ffmpeg_available": bool(ffmpeg_path),
        "ffprobe_path": ffprobe_path or "未找到",
        "ffprobe_available": bool(ffprobe_path),
        "task_count": len(tasks),
        "failed_count": len(failed_tasks),
        "pending_count": len(pending_tasks),
        "review_count": len(review_tasks),
        "completed_count": len(completed_tasks),
        "recent_errors": failed_tasks[:5],
        "ai_config": ai_config,
        "expected_server_url": "http://127.0.0.1:8001",
    }
