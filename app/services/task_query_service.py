"""页面查询 / 统计上下文服务

从 task_service 中拆分出来的偏页面展示、统计类函数，
不参与任务生命周期、状态变更等核心逻辑。
"""

from datetime import datetime, timedelta
import shutil

from app.core.config import settings
from app.db.database import get_connection
from app.models.task import TaskStatus
from app.services.ai_config_service import get_ai_config_context
from app.services.publish_domain import TERMINAL_PUBLISH_STATUSES
from app.services.publish_time import app_zone, parse_datetime
from app.services.storage_service import resolve_video_file_path
from app.services.subtitle_workflow_service import SUBTITLE_STATUS_LABELS
from app.services.task_service import (
    OUTPUT_STATUS_LABELS,
    _parse_time_to_seconds,
    get_default_subtitle_style,
    get_task,
    list_output_clips,
    list_tasks,
)


DASHBOARD_WEEKDAY_LABELS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def _batch_output_clip_counts(task_ids: list[str]) -> dict[str, int]:
    """一次查询获得多个任务的 output_clip 总数。"""
    if not task_ids:
        return {}
    placeholders = ",".join("?" for _ in task_ids)
    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT task_id, COUNT(*) AS cnt
            FROM output_clip
            WHERE task_id IN ({placeholders}) AND is_active = 1
            GROUP BY task_id
            """,
            task_ids,
        ).fetchall()
    return {row["task_id"]: int(row["cnt"] or 0) for row in rows}


def _batch_completed_output_clip_counts(task_ids: list[str]) -> dict[str, int]:
    """一次查询获得多个任务的已完成 output_clip 数量。"""
    if not task_ids:
        return {}
    placeholders = ",".join("?" for _ in task_ids)
    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT task_id, COUNT(*) AS cnt
            FROM output_clip
            WHERE task_id IN ({placeholders}) AND status = 'completed' AND is_active = 1
            GROUP BY task_id
            """,
            task_ids,
        ).fetchall()
    return {row["task_id"]: int(row["cnt"] or 0) for row in rows}


def _dashboard_pending_publish_counts(task_ids: list[str]) -> dict[str, int]:
    """统计仍有有效切片未进入发布终态的任务和切片。"""
    if not task_ids:
        return {"task_count": 0, "clip_count": 0}

    task_placeholders = ",".join("?" for _ in task_ids)
    terminal_statuses = sorted(TERMINAL_PUBLISH_STATUSES)
    terminal_placeholders = ",".join("?" for _ in terminal_statuses)
    with get_connection() as connection:
        row = connection.execute(
            f"""
            WITH latest_publish_job AS (
                SELECT
                    output_clip_id,
                    UPPER(status) AS status,
                    ROW_NUMBER() OVER (
                        PARTITION BY output_clip_id
                        ORDER BY created_at DESC, updated_at DESC, id DESC
                    ) AS row_number
                FROM publish_jobs
            )
            SELECT
                COUNT(DISTINCT output_clip.task_id) AS task_count,
                COUNT(*) AS clip_count
            FROM output_clip
            LEFT JOIN latest_publish_job
              ON latest_publish_job.output_clip_id = output_clip.id
             AND latest_publish_job.row_number = 1
            WHERE output_clip.task_id IN ({task_placeholders})
              AND output_clip.status = 'completed'
              AND output_clip.is_active = 1
              AND (
                    latest_publish_job.status IS NULL
                    OR latest_publish_job.status NOT IN ({terminal_placeholders})
              )
            """,
            [*task_ids, *terminal_statuses],
        ).fetchone()
    return {
        "task_count": int(row["task_count"] or 0) if row else 0,
        "clip_count": int(row["clip_count"] or 0) if row else 0,
    }


def _dashboard_weekly_chart(tasks: list[dict], *, now: datetime | None = None) -> dict:
    """按应用时区生成本周一至周日的每日新增任务统计。"""
    zone = app_zone(settings.app_timezone)
    current = now or datetime.now(zone)
    current = current.replace(tzinfo=zone) if current.tzinfo is None else current.astimezone(zone)
    week_start = (current - timedelta(days=current.weekday())).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    week_end = week_start + timedelta(days=7)
    counts = [0] * 7

    for task in tasks:
        raw_created_at = task.get("created_at_raw")
        if not raw_created_at:
            continue
        try:
            created_at = parse_datetime(raw_created_at, settings.app_timezone).astimezone(zone)
        except ValueError:
            continue
        if week_start <= created_at < week_end:
            counts[(created_at.date() - week_start.date()).days] += 1

    highest_count = max(counts, default=0)
    days = []
    for index, count in enumerate(counts):
        date_value = week_start + timedelta(days=index)
        days.append(
            {
                "label": DASHBOARD_WEEKDAY_LABELS[index],
                "date": date_value.strftime("%m/%d"),
                "count": count,
                "percent": round((count / highest_count) * 100) if highest_count else 0,
                "is_today": date_value.date() == current.date(),
            }
        )

    return {
        "days": days,
        "total": sum(counts),
        "range_label": f"{week_start:%m.%d} - {(week_end - timedelta(days=1)):%m.%d}",
        "timezone_label": settings.app_timezone,
    }


def _batch_clip_candidate_counts(task_ids: list[str]) -> dict[str, dict[str, int]]:
    """一次查询获得多个任务的候选片段总数和已启用数量。"""
    if not task_ids:
        return {}
    placeholders = ",".join("?" for _ in task_ids)
    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT task_id,
                   COUNT(*) AS total,
                   SUM(CASE WHEN enabled = 1 THEN 1 ELSE 0 END) AS enabled_count
            FROM clip_candidates
            WHERE task_id IN ({placeholders}) AND is_deleted = 0
            GROUP BY task_id
            """,
            task_ids,
        ).fetchall()
    return {
        row["task_id"]: {
            "total": int(row["total"] or 0),
            "enabled": int(row["enabled_count"] or 0),
        }
        for row in rows
    }


def _batch_all_output_clips(task_ids: list[str]) -> dict[str, list[dict]]:
    """一次查询获得所有任务的 output_clip（含字幕信息），按 task_id 分组。"""
    if not task_ids:
        return {}
    placeholders = ",".join("?" for _ in task_ids)
    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT
                output_clip.id, output_clip.task_id, output_clip.clip_candidate_id,
                output_clip.output_file_path, output_clip.output_file_name,
                output_clip.status, output_clip.error_message, output_clip.created_at, output_clip.updated_at,
                clip_candidates.title AS clip_title,
                clip_candidates.start_time AS clip_start_time,
                clip_candidates.end_time AS clip_end_time,
                clip_candidates.duration_seconds AS clip_duration_seconds,
                clip_candidates.summary AS clip_summary,
                clip_candidates.enabled AS clip_enabled,
                subtitle_jobs.id AS subtitle_job_id,
                subtitle_jobs.status AS subtitle_status,
                subtitle_jobs.revision_id AS subtitle_revision_id,
                subtitle_jobs.subtitle_file_path,
                subtitle_jobs.output_file_path AS subtitled_output_file_path,
                subtitle_jobs.error_message AS subtitle_error_message,
                subtitle_jobs.validation_status AS subtitle_validation_status,
                subtitle_jobs.validation_json AS subtitle_validation_json,
                subtitle_jobs.encoder AS subtitle_encoder,
                subtitle_jobs.verified_at AS subtitle_verified_at,
                subtitle_revisions.status AS subtitle_revision_status,
                subtitle_jobs.updated_at AS subtitle_updated_at
            FROM output_clip
            LEFT JOIN clip_candidates ON clip_candidates.id = output_clip.clip_candidate_id
            LEFT JOIN subtitle_jobs ON subtitle_jobs.output_clip_id = output_clip.id AND subtitle_jobs.is_active = 1
            LEFT JOIN subtitle_revisions ON subtitle_revisions.id = subtitle_jobs.revision_id
            WHERE output_clip.task_id IN ({placeholders}) AND output_clip.is_active = 1
            ORDER BY
                CASE WHEN output_clip.output_file_name IS NULL OR output_clip.output_file_name = '' THEN 1 ELSE 0 END,
                output_clip.output_file_name ASC,
                output_clip.created_at ASC
            """,
            task_ids,
        ).fetchall()
    result: dict[str, list[dict]] = {task_id: [] for task_id in task_ids}
    for row in rows:
        output = dict(row)
        raw_output_path = (output.get("output_file_path") or "").strip()
        output_path = resolve_video_file_path(raw_output_path) if raw_output_path else None
        raw_subtitled_path = (output.get("subtitled_output_file_path") or "").strip()
        subtitled_path = resolve_video_file_path(raw_subtitled_path) if raw_subtitled_path else None
        subtitle_status = output.get("subtitle_status") or "pending"
        clip_start_time = output.get("clip_start_time") or ""
        clip_end_time = output.get("clip_end_time") or ""
        try:
            clip_start_seconds = _parse_time_to_seconds(clip_start_time) if clip_start_time else 0
            clip_end_seconds = _parse_time_to_seconds(clip_end_time) if clip_end_time else 0
        except ValueError:
            clip_start_seconds = 0
            clip_end_seconds = 0
        result[output["task_id"]].append(
            {
                **output,
                "status_label": OUTPUT_STATUS_LABELS.get(output["status"], output["status"]),
                "file_exists": bool(output_path and output_path.exists() and output_path.is_file()),
                "media_url": f"/media/tasks/{output['task_id']}/output-clips/{output['id']}",
                "source_media_url": f"/media/tasks/{output['task_id']}/source-video",
                "clip_start_seconds": clip_start_seconds,
                "clip_end_seconds": clip_end_seconds,
                "subtitle_status": subtitle_status,
                "subtitle_status_label": SUBTITLE_STATUS_LABELS.get(subtitle_status, subtitle_status),
                "subtitle_publish_ready": bool(
                    subtitle_status == "completed"
                    and output.get("subtitle_validation_status") == "verified"
                    and output.get("subtitle_revision_status") == "approved"
                    and subtitled_path
                    and subtitled_path.exists()
                    and subtitled_path.is_file()
                ),
                "subtitle_stage": SUBTITLE_STATUS_LABELS.get(subtitle_status, subtitle_status),
                "subtitled_file_exists": bool(subtitled_path and subtitled_path.exists() and subtitled_path.is_file()),
                "subtitled_media_url": f"/media/tasks/{output['task_id']}/subtitled-clips/{output['id']}",
                "publish_stage": "待推送配置" if subtitle_status == "completed" else "待字幕确认",
            }
        )
    return result


def get_dashboard_context(*, now: datetime | None = None) -> dict:
    """Dashboard 首页统计上下文"""
    tasks = list_tasks()
    task_ids = [task["id"] for task in tasks]
    weekly_chart = _dashboard_weekly_chart(tasks, now=now)
    completed_oc_map = _batch_completed_output_clip_counts(task_ids)
    completed_task_count = sum(1 for task in tasks if completed_oc_map.get(task["id"], 0) > 0)
    completed_clip_count = sum(completed_oc_map.values())
    pending_publish = _dashboard_pending_publish_counts(task_ids)
    failed_count = sum(
        1
        for task in tasks
        if task["status"] == TaskStatus.failed.value or str(task["status"]).startswith("FAILED_")
    )

    return {
        "stats": [
            {
                "label": "本周新增任务",
                "value": weekly_chart["total"],
                "note": f"{weekly_chart['range_label']} · 上海时间",
                "tone": "blue",
            },
            {
                "label": "已切片任务",
                "value": completed_task_count,
                "note": f"共 {completed_clip_count} 条有效切片",
                "tone": "green",
            },
            {
                "label": "待推送任务",
                "value": pending_publish["task_count"],
                "note": f"涉及 {pending_publish['clip_count']} 条待发送或复核切片",
                "tone": "amber",
            },
            {"label": "失败任务", "value": failed_count, "note": "需排查", "tone": "red"},
        ],
        "weekly_chart": weekly_chart,
        "recent_tasks": tasks[:5],
    }


def get_clips_overview_context() -> dict:
    """片段总览页统计上下文"""
    tasks = list_tasks()
    clip_counts_map = _batch_clip_candidate_counts([task["id"] for task in tasks])
    enriched_tasks = []
    for task in tasks:
        counts = clip_counts_map.get(task["id"], {"total": 0, "enabled": 0})
        clip_count = counts["total"]
        enabled_count = counts["enabled"]
        review_ready = clip_count > 0
        can_cut = enabled_count > 0 and task["source_exists"]
        normalized_status = str(task.get("status") or "").lower()
        if normalized_status == TaskStatus.failed.value:
            review_stage = "异常"
            review_tone = "red"
        elif normalized_status == TaskStatus.completed.value:
            review_stage = "已完成"
            review_tone = "green"
        elif normalized_status == TaskStatus.completed_with_errors.value:
            review_stage = "部分完成"
            review_tone = "amber"
        elif normalized_status == TaskStatus.pending_review.value or review_ready:
            review_stage = "待检查"
            review_tone = "purple"
        elif normalized_status in {TaskStatus.pending_ai.value, TaskStatus.ai_analyzing.value}:
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

    completed_statuses = {
        TaskStatus.completed.value,
        TaskStatus.completed_with_errors.value,
    }
    reviewed_task_count = sum(1 for task in enriched_tasks if task["real_clip_count"] > 0)
    passed_clip_count = sum(task["enabled_clip_count"] for task in enriched_tasks)
    completed_task_count = sum(
        1
        for task in tasks
        if str(task.get("status") or "").lower() in completed_statuses
    )

    return {
        "tasks": enriched_tasks,
        "stats": [
            {
                "label": "累计审核任务",
                "value": reviewed_task_count,
                "note": "已进入候选片段审核流程",
                "tone": "blue",
            },
            {
                "label": "已通过视频",
                "value": passed_clip_count,
                "note": "当前启用的视频片段",
                "tone": "green",
            },
            {
                "label": "已完成任务",
                "value": completed_task_count,
                "note": "含手动与全自动完成状态",
                "tone": "green",
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
    all_outputs = _batch_all_output_clips([task["id"] for task in tasks])
    workflow_tasks = []
    total_output_records = 0
    ready_output_clips = 0
    completed_subtitles = 0
    playable_output_clips = 0

    for task in tasks:
        output_clips = all_outputs.get(task["id"], [])
        for output in output_clips:
            total_output_records += 1
            if output.get("status") == "completed":
                ready_output_clips += 1
            if output.get("subtitle_status") == "completed":
                completed_subtitles += 1
            if output.get("file_exists"):
                playable_output_clips += 1

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
        "tasks_dir": str(settings.tasks_dir),
        "tasks_dir_exists": settings.tasks_dir.exists(),
        "upload_temp_dir": str(settings.upload_temp_dir),
        "upload_temp_dir_exists": settings.upload_temp_dir.exists(),
        "publish_export_dir": str(settings.publish_scheduler_export_dir),
        "publish_export_dir_exists": settings.publish_scheduler_export_dir.exists(),
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
