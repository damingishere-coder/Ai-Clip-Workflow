# ruff: noqa: F401
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import subprocess
from sqlite3 import Row
from typing import Any
from uuid import uuid4

from app.core.config import settings
from app.db.database import get_connection
from app.models.task import ClipCandidateBatchItem, ClipCandidateUpdate, TaskStatus
from app.services.ai_analysis_workflow_service import (
    AI_CLIP_MIN_RECOMMENDED_SECONDS,
    AIAnalysisConflictError,
    _ai_model_name,
    _ai_provider_label,
    _analysis_payload_to_preview,
    _analysis_run_row_to_dict,
    _analyze_with_provider,
    _append_ai_clip_quality_warnings,
    _clear_clip_candidates,
    _ensure_ai_analysis_history_from_current_file,
    _insert_ai_analysis_run,
    _insert_clip_candidates,
    _next_ai_analysis_run_number,
    _read_analysis_meta,
    _read_latest_ai_provider_from_log,
    _replace_clip_candidates,
    _summarize_ai_error,
    _summarize_analysis_clips,
    _write_analysis_payload,
    ensure_task_ai_analysis_artifact,
    get_ai_analysis_run,
    get_latest_ai_analysis_run,
    get_task_ai_analysis_meta,
    validate_ai_analysis_meta_for_cut,
    get_task_ai_analysis_status,
    get_task_ai_source_label,
    list_ai_analysis_runs,
    process_task_ai_analysis,
    queue_task_ai_analysis,
    restore_ai_analysis_run,
)
from app.services.clip_feedback_service import save_clip_feedback
from app.services.storage_service import (
    get_artifact_paths,
    get_source_video_path,
    resolve_video_file_path,
)
from app.services.publish_domain import TERMINAL_PUBLISH_STATUSES
from app.services.task_lifecycle_service import (
    TaskStatusConflictError,
    create_task_record,
    soft_delete_task,
    transition_task_status,
    update_task_ai_preference,
    update_task_candidate_clip_count,
    update_task_selection_settings,
    update_task_status,
)
from app.services.task_log_service import append_task_log as _append_task_log, read_task_log_tail as _read_task_log_tail
from app.services.subtitle_workflow_service import (
    WINDOWS_FONTS_DIR,
    SUBTITLE_CJK_FONT_FALLBACKS,
    SUBTITLE_FONT_FILE_CANDIDATES,
    SUBTITLE_STATUS_LABELS,
    _ass_time,
    _build_subtitle_rows,
    _escape_ass_text,
    _ffmpeg_filter_path,
    _ffmpeg_subtitles_filter,
    _hex_to_ass_color,
    _resolve_subtitle_font_family,
    _subtitle_font_exists,
    _subtitle_job_for_output,
    _create_subtitle_job,
    _activate_subtitle_job,
    _write_ass_file,
    get_default_subtitle_style,
    render_subtitles_for_output_clip,
    update_default_subtitle_style,
)
from app.services.transcript_service import read_transcript_progress, read_transcript_range
from app.services.local_transcription_runtime import TranscriptionOfflinePolicyError
from app.services.transcript_workflow_service import (
    TranscriptCancelledError,
    _can_retry_transcript_with_local,
    _finalize_cancelled_transcript_task,
    _is_transcript_progress_stale,
    _parse_progress_updated_at,
    _resolve_transcription_provider_choice,
    _run_task_transcript_background,
    _transcript_progress_age_seconds,
    _transcription_choice_label,
    cancel_task_transcript,
    get_task_transcript_status,
    get_transcript_preview,
    process_task_audio,
    process_task_transcript,
    process_task_transcript_workflow,
    validate_transcription_provider_choice,
)
from app.services.video_cut_workflow_service import process_task_video_cuts


WORKFLOW_STEPS = [
    "视频提交",
    "音频提取",
    "转写",
    "AI 分析",
    "AI 结果检查",
    "自动切割",
    "输出完成",
]

AUTO_WORKFLOW_STEPS = [
    "任务创建",
    "准备视频",
    "转写文本",
    "AI 分析",
    "自动选片",
    "原片切割",
    "字幕审核",
    "标题文案",
    "发送队列",
    "发布任务",
    "待发布",
]


STATUS_LABELS = {
    TaskStatus.CREATED.value: "全自动任务已创建",
    TaskStatus.PREPARING_SOURCE.value: "准备视频中",
    TaskStatus.TRANSCRIBING.value: "转写文本中",
    TaskStatus.AI_ANALYZING.value: "AI 分析中",
    TaskStatus.CLIP_SELECTING.value: "自动选片中",
    TaskStatus.VIDEO_CUTTING.value: "原片切割中",
    TaskStatus.SUBTITLE_DRAFTING.value: "生成字幕草稿中",
    TaskStatus.PENDING_SUBTITLE_REVIEW.value: "字幕待审核",
    TaskStatus.METADATA_GENERATING.value: "生成标题文案中",
    TaskStatus.SCHEDULE_CREATING.value: "准备发送队列中",
    TaskStatus.PUBLISH_JOB_CREATING.value: "创建发布任务中",
    TaskStatus.READY_TO_PUBLISH.value: "待人工确认发布",
    TaskStatus.COMPLETED.value: "全自动流程完成",
    TaskStatus.CANCELLED.value: "已取消",
    TaskStatus.FAILED_PREPARING_SOURCE.value: "准备视频失败",
    TaskStatus.FAILED_TRANSCRIBING.value: "转写失败",
    TaskStatus.FAILED_AI_ANALYZING.value: "AI 分析失败",
    TaskStatus.FAILED_CLIP_SELECTING.value: "自动选片失败",
    TaskStatus.FAILED_VIDEO_CUTTING.value: "原片切割失败",
    TaskStatus.FAILED_SUBTITLE_DRAFTING.value: "字幕草稿生成失败",
    TaskStatus.FAILED_METADATA_GENERATING.value: "标题文案生成失败",
    TaskStatus.FAILED_SCHEDULE_CREATING.value: "发送队列准备失败",
    TaskStatus.FAILED_PUBLISH_JOB_CREATING.value: "发布任务创建失败",
    TaskStatus.pending_video.value: "待提交视频",
    TaskStatus.pending_processing.value: "待处理",
    TaskStatus.audio_extracting.value: "音频提取中",
    TaskStatus.transcribing.value: "转写中",
    TaskStatus.pending_ai.value: "待 AI 分析",
    TaskStatus.ai_analyzing.value: "AI 分析中",
    TaskStatus.pending_review.value: "AI 结果待检查",
    TaskStatus.cutting.value: "切割中",
    TaskStatus.completed.value: "已完成",
    TaskStatus.completed_with_errors.value: "部分完成",
    TaskStatus.failed.value: "失败",
}

STATUS_PROGRESS = {
    TaskStatus.CREATED.value: 2,
    TaskStatus.PREPARING_SOURCE.value: 8,
    TaskStatus.TRANSCRIBING.value: 25,
    TaskStatus.AI_ANALYZING.value: 45,
    TaskStatus.CLIP_SELECTING.value: 58,
    TaskStatus.VIDEO_CUTTING.value: 70,
    TaskStatus.SUBTITLE_DRAFTING.value: 75,
    TaskStatus.PENDING_SUBTITLE_REVIEW.value: 78,
    TaskStatus.METADATA_GENERATING.value: 84,
    TaskStatus.SCHEDULE_CREATING.value: 90,
    TaskStatus.PUBLISH_JOB_CREATING.value: 95,
    TaskStatus.READY_TO_PUBLISH.value: 100,
    TaskStatus.COMPLETED.value: 100,
    TaskStatus.CANCELLED.value: 0,
    TaskStatus.FAILED_PREPARING_SOURCE.value: 8,
    TaskStatus.FAILED_TRANSCRIBING.value: 25,
    TaskStatus.FAILED_AI_ANALYZING.value: 45,
    TaskStatus.FAILED_CLIP_SELECTING.value: 58,
    TaskStatus.FAILED_VIDEO_CUTTING.value: 70,
    TaskStatus.FAILED_SUBTITLE_DRAFTING.value: 75,
    TaskStatus.FAILED_METADATA_GENERATING.value: 84,
    TaskStatus.FAILED_SCHEDULE_CREATING.value: 90,
    TaskStatus.FAILED_PUBLISH_JOB_CREATING.value: 95,
    TaskStatus.pending_video.value: 0,
    TaskStatus.pending_processing.value: 5,
    TaskStatus.audio_extracting.value: 20,
    TaskStatus.transcribing.value: 40,
    TaskStatus.pending_ai.value: 55,
    TaskStatus.ai_analyzing.value: 65,
    TaskStatus.pending_review.value: 72,
    TaskStatus.cutting.value: 88,
    TaskStatus.completed.value: 100,
    TaskStatus.completed_with_errors.value: 100,
    TaskStatus.failed.value: 0,
}

AUTO_PIPELINE_RUNNING_STATUSES = {
    TaskStatus.CREATED.value,
    TaskStatus.PREPARING_SOURCE.value,
    TaskStatus.TRANSCRIBING.value,
    TaskStatus.AI_ANALYZING.value,
    TaskStatus.CLIP_SELECTING.value,
    TaskStatus.VIDEO_CUTTING.value,
    TaskStatus.SUBTITLE_DRAFTING.value,
    TaskStatus.METADATA_GENERATING.value,
    TaskStatus.SCHEDULE_CREATING.value,
    TaskStatus.PUBLISH_JOB_CREATING.value,
    TaskStatus.transcribing.value,
    TaskStatus.ai_analyzing.value,
    TaskStatus.cutting.value,
}

AUTO_PIPELINE_FAILED_STATUSES = {
    TaskStatus.FAILED_PREPARING_SOURCE.value,
    TaskStatus.FAILED_TRANSCRIBING.value,
    TaskStatus.FAILED_AI_ANALYZING.value,
    TaskStatus.FAILED_CLIP_SELECTING.value,
    TaskStatus.FAILED_VIDEO_CUTTING.value,
    TaskStatus.FAILED_SUBTITLE_DRAFTING.value,
    TaskStatus.FAILED_METADATA_GENERATING.value,
    TaskStatus.FAILED_SCHEDULE_CREATING.value,
    TaskStatus.FAILED_PUBLISH_JOB_CREATING.value,
}

AUTO_PIPELINE_RESUMABLE_STATUSES = {
    TaskStatus.CANCELLED.value,
    TaskStatus.pending_review.value,
    TaskStatus.completed_with_errors.value,
    TaskStatus.failed.value,
}

PLATFORM_LABELS = {
    "douyin": "抖音",
    "bilibili": "B站",
    "general": "通用",
}

SOURCE_TYPE_LABELS = {
    "upload": "上传视频",
}

OUTPUT_STATUS_LABELS = {
    "pending": "等待中",
    "processing": "处理中",
    "completed": "已完成",
    "failed": "失败",
}


_TRANSCRIPT_STALE_AFTER = timedelta(minutes=10)


def get_workflow_steps() -> list[str]:
    return WORKFLOW_STEPS


def get_task_workflow_steps(task: dict) -> list[dict[str, str]]:
    status = task.get("status") or TaskStatus.pending_video.value
    if task.get("auto_mode"):
        status_step_index = {
            TaskStatus.CREATED.value: 1,
            TaskStatus.PREPARING_SOURCE.value: 2,
            TaskStatus.TRANSCRIBING.value: 3,
            TaskStatus.AI_ANALYZING.value: 4,
            TaskStatus.CLIP_SELECTING.value: 5,
            TaskStatus.VIDEO_CUTTING.value: 6,
            TaskStatus.SUBTITLE_DRAFTING.value: 7,
            TaskStatus.PENDING_SUBTITLE_REVIEW.value: 7,
            TaskStatus.METADATA_GENERATING.value: 8,
            TaskStatus.SCHEDULE_CREATING.value: 9,
            TaskStatus.PUBLISH_JOB_CREATING.value: 10,
            TaskStatus.READY_TO_PUBLISH.value: 11,
            TaskStatus.COMPLETED.value: 11,
            TaskStatus.CANCELLED.value: 1,
            TaskStatus.FAILED_PREPARING_SOURCE.value: 2,
            TaskStatus.FAILED_TRANSCRIBING.value: 3,
            TaskStatus.FAILED_AI_ANALYZING.value: 4,
            TaskStatus.FAILED_CLIP_SELECTING.value: 5,
            TaskStatus.FAILED_VIDEO_CUTTING.value: 6,
            TaskStatus.FAILED_SUBTITLE_DRAFTING.value: 7,
            TaskStatus.FAILED_METADATA_GENERATING.value: 8,
            TaskStatus.FAILED_SCHEDULE_CREATING.value: 9,
            TaskStatus.FAILED_PUBLISH_JOB_CREATING.value: 10,
            TaskStatus.pending_review.value: 5,
            TaskStatus.pending_processing.value: 2,
            TaskStatus.audio_extracting.value: 2,
            TaskStatus.transcribing.value: 3,
            TaskStatus.pending_ai.value: 4,
            TaskStatus.ai_analyzing.value: 4,
            TaskStatus.cutting.value: 6,
            TaskStatus.completed.value: 11,
            TaskStatus.completed_with_errors.value: 11,
            TaskStatus.failed.value: 1,
        }
        failed_statuses = {
            TaskStatus.FAILED_PREPARING_SOURCE.value,
            TaskStatus.FAILED_TRANSCRIBING.value,
            TaskStatus.FAILED_AI_ANALYZING.value,
            TaskStatus.FAILED_CLIP_SELECTING.value,
            TaskStatus.FAILED_VIDEO_CUTTING.value,
            TaskStatus.FAILED_SUBTITLE_DRAFTING.value,
            TaskStatus.FAILED_METADATA_GENERATING.value,
            TaskStatus.FAILED_SCHEDULE_CREATING.value,
            TaskStatus.FAILED_PUBLISH_JOB_CREATING.value,
            TaskStatus.completed_with_errors.value,
            TaskStatus.failed.value,
            TaskStatus.CANCELLED.value,
        }
        completed_statuses = {
            TaskStatus.READY_TO_PUBLISH.value,
            TaskStatus.COMPLETED.value,
            TaskStatus.completed.value,
        }
        current_index = status_step_index.get(status, 1)
        steps = []
        for index, name in enumerate(AUTO_WORKFLOW_STEPS, start=1):
            if status in failed_statuses and index == current_index:
                state = "warning"
            elif status in completed_statuses:
                state = "done"
            elif index < current_index:
                state = "done"
            elif index == current_index:
                state = "current"
            else:
                state = "pending"
            steps.append({"name": name, "index": str(index), "state": state})
        return steps

    status_step_index = {
        TaskStatus.pending_video.value: 1,
        TaskStatus.pending_processing.value: 2,
        TaskStatus.audio_extracting.value: 2,
        TaskStatus.transcribing.value: 3,
        TaskStatus.pending_ai.value: 4,
        TaskStatus.ai_analyzing.value: 4,
        TaskStatus.pending_review.value: 5,
        TaskStatus.cutting.value: 6,
        TaskStatus.completed.value: 7,
        TaskStatus.completed_with_errors.value: 7,
    }

    if status == TaskStatus.failed.value:
        if task.get("analysis_exists"):
            current_index = 5
        elif task.get("transcript_exists"):
            current_index = 4
        elif task.get("audio_exists"):
            current_index = 3
        elif task.get("source_exists"):
            current_index = 2
        else:
            current_index = 1
    else:
        current_index = status_step_index.get(status, 1)

    steps = []
    for index, name in enumerate(WORKFLOW_STEPS, start=1):
        if status == TaskStatus.failed.value and index == current_index:
            state = "warning"
        elif status == TaskStatus.completed_with_errors.value and index == len(WORKFLOW_STEPS):
            state = "warning"
        elif status in {TaskStatus.completed.value, TaskStatus.completed_with_errors.value}:
            state = "done"
        elif index < current_index:
            state = "done"
        elif index == current_index:
            state = "current"
        else:
            state = "pending"

        steps.append(
            {
                "name": name,
                "index": str(index),
                "state": state,
            }
        )
    return steps


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")



def _format_datetime(value: str | None) -> str:
    if not value:
        return "未知"
    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        return parsed.astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return value


def get_status_label(status: str) -> str:
    return STATUS_LABELS.get(status, status)


def get_platform_label(platform: str) -> str:
    return PLATFORM_LABELS.get(platform, platform or "通用")


def get_source_path(task: dict) -> str:
    return task.get("original_video_path") or "尚未上传视频"



def _format_file_size(size: int | None) -> str:
    if not size:
        return "尚未读取"
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def _format_duration(seconds: float | None) -> str:
    if not seconds:
        return "尚未读取"
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _parse_time_to_seconds(value: str) -> int:
    raw_value = (value or "").strip()
    parts = raw_value.split(":")
    if len(parts) not in {2, 3} or not all(part.isdigit() for part in parts):
        raise ValueError("时间格式不合法，请使用 MM:SS 或 HH:MM:SS，例如 01:23 或 00:01:23")

    numbers = [int(part) for part in parts]
    if len(numbers) == 2:
        minutes, seconds = numbers
        hours = 0
    else:
        hours, minutes, seconds = numbers

    if minutes >= 60 or seconds >= 60:
        raise ValueError("时间格式不合法，分钟和秒数都必须小于 60")
    return hours * 3600 + minutes * 60 + seconds


def _format_seconds_as_time(seconds: int) -> str:
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _probe_video(path: Path | None) -> dict[str, str]:
    if not path:
        return {"duration": "尚未读取", "video_size": "尚未读取"}
    try:
        file_size = path.stat().st_size
    except OSError:
        return {"duration": "读取失败", "video_size": "读取失败"}
    duration = None
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        try:
            result = subprocess.run(
                [
                    ffprobe,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(path),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=settings.ffprobe_timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            result = None
        if result and result.returncode == 0:
            try:
                duration = float(result.stdout.strip())
            except ValueError:
                duration = None
    return {"duration": _format_duration(duration), "video_size": _format_file_size(file_size)}


def _row_to_task(row: Row, include_video_probe: bool = False) -> dict:
    task = dict(row)
    task_name = task.get("task_name") or "未命名任务"
    platform = task.get("platform") or "general"
    status = task.get("status") or TaskStatus.pending_video.value
    progress = task.get("progress")
    if progress is None or progress <= 0:
        progress = STATUS_PROGRESS.get(status, 0)

    source = get_source_path(task)
    source_path = get_source_video_path(task)
    paths = get_artifact_paths(task["id"], task.get("task_dir_name"))
    source_exists = bool(source_path and source_path.exists())
    video_meta = _probe_video(source_path) if include_video_probe else {"duration": "尚未读取", "video_size": "尚未读取"}

    return {
        **task,
        "title": task_name,
        "task_name": task_name,
        "platform": platform,
        "platform_label": get_platform_label(platform),
        "platform_key": platform,
        "source_type_label": SOURCE_TYPE_LABELS.get(task.get("source_type"), "上传视频"),
        "source": source,
        "source_exists": source_exists,
        "status": status,
        "status_label": get_status_label(status),
        "progress": progress,
        "candidate_count": task.get("candidate_clip_count") or 0,
        "selection_profile": task.get("selection_profile") or "general",
        "selection_profile_label": {
            "general": "通用内容价值",
            "variety_comedy": "康熙笑点选片模式",
            "long_live_talk": "长直播高光（语言类）",
        }.get(task.get("selection_profile") or "general", "通用内容价值"),
        "final_clip_target": int(task.get("final_clip_target") or 5),
        "highlight_density_per_hour": int(task.get("highlight_density_per_hour") or 4),
        "highlight_total_limit": int(task.get("highlight_total_limit") or 30),
        "duration": video_meta["duration"],
        "video_size": video_meta["video_size"],
        "owner": "本地用户",
        "created_at": _format_datetime(task.get("created_at")),
        "updated_at": _format_datetime(task.get("updated_at")),
        "created_at_raw": task.get("created_at"),
        "updated_at_raw": task.get("updated_at"),
        "error_message": task.get("error_message") or "",
        "last_error": task.get("last_error") or task.get("error_message") or "",
        "auto_mode": bool(task.get("auto_mode")),
        "auto_config_json": task.get("auto_config_json") or "",
        "is_deleted": bool(task.get("is_deleted")),
        "deleted_at": _format_datetime(task.get("deleted_at")),
        "task_dir_name": task.get("task_dir_name") or task["id"],
        "storage_root": str(settings.storage_root),
        "task_dir": str(paths["task_dir"]),
        "audio_path": str(paths["audio_path"]),
        "audio_exists": paths["audio_path"].exists(),
        "transcript_path": str(paths["transcript_path"]),
        "transcript_exists": paths["transcript_path"].exists(),
        "transcript_progress": read_transcript_progress(paths["transcript_path"]),
        "analysis_path": str(paths["analysis_path"]),
        "analysis_exists": paths["analysis_path"].exists(),
        "clips_dir": str(paths["clips_dir"]),
        "output_clip_count": count_output_clips(task["id"]),
        "log_path": str(paths["log_path"]),
        "log_exists": paths["log_path"].exists(),
    }


def list_tasks(include_deleted: bool = False) -> list[dict]:
    where_clause = "" if include_deleted else "WHERE COALESCE(is_deleted, 0) = 0"
    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT
                id, task_name, task_dir_name, source_type, platform, original_video_path, nas_file_path,
                max_clip_duration, candidate_clip_count, selection_profile, final_clip_target,
                highlight_density_per_hour, highlight_total_limit,
                ai_preference, ai_prompt_preset_id, auto_mode,
                auto_config_json, status, progress, error_message, last_error,
                is_deleted, deleted_at, created_at, updated_at
            FROM tasks
            {where_clause}
            ORDER BY created_at DESC
            """
        ).fetchall()
    return [_row_to_task(row) for row in rows]


def list_task_name_history() -> list[str]:
    """返回可见任务的唯一非空任务名称，按最后创建时间从新到旧排列。"""
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT TRIM(task_name) AS task_name, MAX(created_at) AS latest_created_at
            FROM tasks
            WHERE COALESCE(is_deleted, 0) = 0
              AND task_name IS NOT NULL
              AND TRIM(task_name) <> ''
            GROUP BY TRIM(task_name)
            ORDER BY latest_created_at DESC, task_name ASC
            LIMIT 5
            """
        ).fetchall()
    return [str(row["task_name"]) for row in rows]


def get_task(
    task_id: str,
    include_video_probe: bool = True,
    *,
    include_deleted: bool = False,
) -> dict | None:
    deleted_clause = "" if include_deleted else " AND COALESCE(is_deleted, 0) = 0"
    with get_connection() as connection:
        row = connection.execute(
            f"""
            SELECT
                id, task_name, task_dir_name, source_type, platform, original_video_path, nas_file_path,
                max_clip_duration, candidate_clip_count, selection_profile, final_clip_target,
                highlight_density_per_hour, highlight_total_limit,
                ai_preference, ai_prompt_preset_id, auto_mode,
                auto_config_json, status, progress, error_message, last_error,
                is_deleted, deleted_at, created_at, updated_at
            FROM tasks
            WHERE id = ?{deleted_clause}
            """,
            (task_id,),
        ).fetchone()
    return _row_to_task(row, include_video_probe=include_video_probe) if row else None


def _get_active_workflow_job(task_id: str) -> dict:
    from app.services import job_service

    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT id, job_type, status, progress, message, updated_at
            FROM workflow_jobs
            WHERE task_id = ? AND status IN (?, ?)
            ORDER BY
                CASE status WHEN ? THEN 0 ELSE 1 END,
                updated_at DESC,
                created_at DESC,
                id DESC
            LIMIT 1
            """,
            (
                task_id,
                job_service.JOB_STATUS_RUNNING,
                job_service.JOB_STATUS_QUEUED,
                job_service.JOB_STATUS_RUNNING,
            ),
        ).fetchone()
    if not row:
        return {}
    job = dict(row)
    job["job_type_label"] = job_service.JOB_TYPE_LABELS.get(job["job_type"], job["job_type"])
    job["status_label"] = job_service.JOB_STATUS_LABELS.get(job["status"], job["status"])
    return job


def _get_publish_live_summary(task_id: str) -> dict:
    terminal_statuses = {status.upper() for status in TERMINAL_PUBLISH_STATUSES}
    with get_connection() as connection:
        rows = connection.execute(
            """
            WITH latest_publish_job AS (
                SELECT
                    publish_jobs.status,
                    ROW_NUMBER() OVER (
                        PARTITION BY publish_jobs.output_clip_id, publish_jobs.platform
                        ORDER BY publish_jobs.created_at DESC,
                                 publish_jobs.updated_at DESC,
                                 publish_jobs.id DESC
                    ) AS row_number
                FROM publish_jobs
                INNER JOIN output_clip
                  ON output_clip.id = publish_jobs.output_clip_id
                 AND output_clip.task_id = ?
                 AND output_clip.status = 'completed'
                 AND output_clip.is_active = 1
            )
            SELECT UPPER(status) AS status, COUNT(*) AS count
            FROM latest_publish_job
            WHERE row_number = 1
            GROUP BY UPPER(status)
            """,
            (task_id,),
        ).fetchall()

    statuses = {str(row["status"] or "").upper(): int(row["count"] or 0) for row in rows}
    total = sum(statuses.values())
    success = sum(statuses.get(status, 0) for status in {"PUBLISHED", "EXPORTED"})
    cancelled = statuses.get("CANCELLED", 0)
    resolved = sum(statuses.get(status, 0) for status in terminal_statuses)
    pending = sum(statuses.get(status, 0) for status in {"DRAFT", "WAITING", "SCHEDULED"})
    publishing = statuses.get("PUBLISHING", 0)
    failed = statuses.get("FAILED", 0)
    need_review = statuses.get("NEED_REVIEW", 0)
    attention = failed + need_review
    progress = round(success / total * 100) if total else 0

    if not total:
        state = "none"
        label = "尚未创建发布任务"
        message = "处理完成后可同步到发送中心。"
    elif attention:
        state = "attention"
        label = f"发布需处理 · {attention} 条"
        message = f"已成功 {success}/{total} 条，另有 {attention} 条失败或需要人工复核。"
    elif publishing:
        state = "publishing"
        label = f"正在发布 · {success}/{total}"
        message = f"平台正在处理 {publishing} 条，已成功 {success}/{total} 条。"
    elif pending:
        state = "scheduled"
        label = f"托管发布中 · {success}/{total}"
        message = f"已成功 {success}/{total} 条，另有 {pending} 条正在等待排期发送。"
    elif resolved == total and success == total:
        state = "completed"
        label = f"发布完成 · {success}/{total}"
        message = f"全部 {total} 条发布任务均已完成。"
    else:
        state = "resolved"
        label = f"发布已结束 · {success}/{total}"
        message = f"已成功 {success}/{total} 条，取消 {cancelled} 条。"

    return {
        "state": state,
        "label": label,
        "message": message,
        "progress": progress,
        "total": total,
        "success": success,
        "resolved": resolved,
        "pending": pending,
        "publishing": publishing,
        "failed": failed,
        "need_review": need_review,
        "cancelled": cancelled,
        "should_poll": bool(pending or publishing),
    }


def _build_live_activity(task: dict, active_job: dict, publish: dict) -> dict:
    status = task["status"]
    transcript = task.get("transcript_progress") or {}
    publish_task_statuses = {
        TaskStatus.READY_TO_PUBLISH.value,
        TaskStatus.COMPLETED.value,
        TaskStatus.completed.value,
        TaskStatus.completed_with_errors.value,
    }

    if publish["total"] and status in publish_task_statuses:
        return {
            "kind": "publish",
            "label": "托管发布",
            "status": publish["state"],
            "progress": publish["progress"],
            "message": publish["message"],
            "updated_at": "",
        }

    if status in {TaskStatus.TRANSCRIBING.value, TaskStatus.transcribing.value} and transcript:
        return {
            "kind": "transcript",
            "label": "转写文本",
            "status": str(transcript.get("status") or "running"),
            "progress": max(0, min(100, int(transcript.get("percent") or 0))),
            "message": str(transcript.get("message") or "正在转写文本"),
            "updated_at": str(transcript.get("updated_at") or ""),
        }

    if active_job:
        return {
            "kind": str(active_job.get("job_type") or "workflow"),
            "label": str(active_job.get("job_type_label") or "后台任务"),
            "status": str(active_job.get("status") or "running"),
            "progress": max(0, min(100, int(active_job.get("progress") or 0))),
            "message": str(active_job.get("message") or active_job.get("status_label") or "正在处理"),
            "updated_at": str(active_job.get("updated_at") or ""),
        }

    return {
        "kind": "task",
        "label": task["status_label"],
        "status": status,
        "progress": max(0, min(100, int(task.get("progress") or 0))),
        "message": task.get("error_message") or f"当前阶段：{task['status_label']}",
        "updated_at": str(task.get("updated_at_raw") or ""),
    }


def get_task_live_status(task_id: str) -> dict:
    task = get_task(task_id, include_video_probe=False)
    if not task:
        raise ValueError("任务不存在")

    status = task["status"]
    auto_mode = bool(task.get("auto_mode"))
    active_job = _get_active_workflow_job(task_id)
    publish = _get_publish_live_summary(task_id)
    activity = _build_live_activity(task, active_job, publish)
    task_is_running = status in AUTO_PIPELINE_RUNNING_STATUSES
    is_running = bool(task_is_running or active_job or publish["should_poll"])
    should_poll = is_running
    candidate_count = count_clip_candidates(task_id)
    output_clip_count = int(task.get("output_clip_count") or 0)
    workflow_steps = get_task_workflow_steps(task)

    publish_task_statuses = {
        TaskStatus.READY_TO_PUBLISH.value,
        TaskStatus.COMPLETED.value,
        TaskStatus.completed.value,
        TaskStatus.completed_with_errors.value,
    }
    display_status_label = task["status_label"]
    overall_progress = int(task.get("progress") or 0)
    if publish["total"] and status in publish_task_statuses:
        display_status_label = publish["label"]
        overall_progress = 100 if publish["state"] == "completed" else min(
            99,
            90 + round(publish["progress"] * 0.09),
        )
        if workflow_steps:
            workflow_steps[-1]["name"] = "平台发布"
            if publish["state"] == "attention":
                workflow_steps[-1]["state"] = "warning"
            elif publish["state"] in {"scheduled", "publishing"}:
                workflow_steps[-1]["state"] = "current"

    runtime_status = "running" if should_poll else ("completed" if overall_progress >= 100 else "idle")
    if task.get("error_message") or publish["state"] == "attention":
        runtime_status = "failed"

    primary_action = "none"
    if auto_mode:
        if status in AUTO_PIPELINE_FAILED_STATUSES:
            primary_action = "retry"
        elif status == TaskStatus.PENDING_SUBTITLE_REVIEW.value:
            primary_action = "subtitle_review"
        elif status in {
            TaskStatus.READY_TO_PUBLISH.value,
            TaskStatus.COMPLETED.value,
            TaskStatus.completed.value,
        }:
            primary_action = "publish"
        elif status in AUTO_PIPELINE_RESUMABLE_STATUSES:
            primary_action = "resume"
        elif is_running:
            primary_action = "processing"

    return {
        "task_id": task_id,
        "snapshot_at": _now_iso(),
        "status": status,
        "status_label": display_status_label,
        "task_status_label": task["status_label"],
        "progress": overall_progress,
        "task_progress": int(task.get("progress") or 0),
        "updated_at": task["updated_at"],
        "error_message": task.get("error_message") or "",
        "is_running": is_running,
        "should_poll": should_poll,
        "runtime_status": runtime_status,
        "runtime_status_label": display_status_label,
        "workflow_steps": workflow_steps,
        "active_operation": activity,
        "publish": publish,
        "log_lines": _read_task_log_tail(task_id),
        "counts": {
            "candidates": candidate_count,
            "outputs": output_clip_count,
        },
        "actions": {
            "primary": primary_action,
            "review": candidate_count > 0,
            "publish": output_clip_count > 0
            and status
            in {
                TaskStatus.READY_TO_PUBLISH.value,
                TaskStatus.COMPLETED.value,
                TaskStatus.completed.value,
            },
        },
    }


def list_clip_candidates(task_id: str) -> list[dict]:
    ai_source_label = get_task_ai_source_label(task_id)
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT id, task_id, clip_key, title, start_time, end_time, duration_seconds, cover_time_seconds,
                   summary, reason, highlight_reason, spread_value, suggested_editing, confidence_score,
                   quality_tier, quality_score, text_quality_score, humor_score, completeness_score,
                   audio_reaction_score, topic_key, key_moment_time, quality_evidence_json, rejection_reason,
                   selected_by_default, enabled, reviewed, is_deleted, deleted_at, created_at, updated_at
            FROM clip_candidates
            WHERE task_id = ? AND is_deleted = 0
            ORDER BY start_time ASC
            """,
            (task_id,),
        ).fetchall()
    clips = []
    for row in rows:
        clip = dict(row)
        highlight_reason = clip.get("highlight_reason") or clip.get("reason") or ""
        confidence_score = clip.get("confidence_score") or 0
        try:
            quality_evidence = json.loads(clip.get("quality_evidence_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            quality_evidence = {}
        if not isinstance(quality_evidence, dict):
            quality_evidence = {}
        clips.append(
            {
                **clip,
                "summary": clip.get("summary") or "",
                "highlight_reason": highlight_reason,
                "reason": highlight_reason,
                "spread_value": clip.get("spread_value") or "",
                "suggested_editing": clip.get("suggested_editing") or "",
                "confidence_score": float(confidence_score),
                "confidence_percent": int(round(float(confidence_score) * 100)),
                "quality_tier": clip.get("quality_tier") or "",
                "quality_score": float(clip.get("quality_score") or 0),
                "text_quality_score": float(clip.get("text_quality_score") or 0),
                "humor_score": float(clip.get("humor_score") or 0),
                "completeness_score": float(clip.get("completeness_score") or 0),
                "audio_reaction_score": float(clip.get("audio_reaction_score") or 0),
                "topic_key": clip.get("topic_key") or "",
                "key_moment_time": clip.get("key_moment_time") or "",
                "quality_evidence": quality_evidence,
                "rejection_reason": clip.get("rejection_reason") or "",
                "ai_source_label": ai_source_label,
                "selected_by_default": bool(clip.get("selected_by_default")),
                "enabled": bool(clip.get("enabled")),
                "reviewed": bool(clip.get("reviewed")),
                "is_deleted": bool(clip.get("is_deleted")),
                "deleted_at": clip.get("deleted_at"),
                "start_seconds": _parse_time_to_seconds(clip["start_time"]),
                "end_seconds": _parse_time_to_seconds(clip["end_time"]),
            }
        )
    return clips


def get_clip_transcript_excerpt(
    task_id: str,
    clip_id: str,
    start_time: str | None = None,
    end_time: str | None = None,
) -> dict:
    clip = get_clip_candidate(task_id, clip_id)
    start_seconds = int(clip["start_seconds"])
    end_seconds = int(clip["end_seconds"])
    start_label = clip["start_time"]
    end_label = clip["end_time"]
    if start_time and end_time:
        start_seconds = _parse_time_to_seconds(start_time)
        end_seconds = _parse_time_to_seconds(end_time)
        if end_seconds <= start_seconds:
            raise ValueError("结束时间必须大于开始时间")
        start_label = _format_seconds_as_time(start_seconds)
        end_label = _format_seconds_as_time(end_seconds)
    paths = get_artifact_paths(task_id)
    rows = read_transcript_range(
        paths["transcript_path"],
        start_seconds,
        end_seconds,
    )
    return {
        "task_id": task_id,
        "clip_id": clip_id,
        "title": clip["title"],
        "start_time": start_label,
        "end_time": end_label,
        "rows": rows,
    }


def count_clip_candidates(task_id: str) -> int:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS total FROM clip_candidates WHERE task_id = ? AND is_deleted = 0",
            (task_id,),
        ).fetchone()
    return int(row["total"]) if row else 0


def count_enabled_clip_candidates(task_id: str) -> int:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS total FROM clip_candidates WHERE task_id = ? AND enabled = 1 AND is_deleted = 0",
            (task_id,),
        ).fetchone()
    return int(row["total"]) if row else 0


def _validate_clip_update(task: dict, payload: ClipCandidateUpdate) -> dict:
    start_seconds = _parse_time_to_seconds(payload.start_time)
    end_seconds = _parse_time_to_seconds(payload.end_time)
    if end_seconds <= start_seconds:
        raise ValueError("结束时间必须大于开始时间")

    duration_seconds = end_seconds - start_seconds
    max_duration_seconds = int(task["max_clip_duration"]) * 60
    if duration_seconds > max_duration_seconds:
        raise ValueError(
            f"片段时长不能超过该任务设置的最大切片时长：{task['max_clip_duration']} 分钟"
        )

    return {
        "title": payload.title.strip(),
        "start_time": _format_seconds_as_time(start_seconds),
        "end_time": _format_seconds_as_time(end_seconds),
        "duration_seconds": duration_seconds,
        "enabled": 1 if payload.enabled else 0,
        "summary": (payload.summary or "").strip(),
    }


def update_clip_candidate(task_id: str, clip_id: str, payload: ClipCandidateUpdate) -> dict:
    task = get_task(task_id, include_video_probe=False)
    if not task:
        raise ValueError("任务不存在")

    data = _validate_clip_update(task, payload)
    now = _now_iso()
    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE clip_candidates
            SET title = ?, start_time = ?, end_time = ?, duration_seconds = ?,
                enabled = ?, summary = ?, reviewed = 1, updated_at = ?
            WHERE id = ? AND task_id = ? AND is_deleted = 0
            """,
            (
                data["title"],
                data["start_time"],
                data["end_time"],
                data["duration_seconds"],
                data["enabled"],
                data["summary"],
                now,
                clip_id,
                task_id,
            ),
        )
        connection.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", (now, task_id))
        connection.commit()

    if cursor.rowcount == 0:
        raise ValueError("候选片段不存在")
    _append_task_log(task_id, f"已保存候选片段审核修改：{clip_id}")
    return get_clip_candidate(task_id, clip_id)


def update_clip_candidates_batch(task_id: str, payloads: list[ClipCandidateBatchItem]) -> dict:
    task = get_task(task_id, include_video_probe=False)
    if not task:
        raise ValueError("任务不存在")
    if not payloads:
        raise ValueError("没有收到需要保存的候选片段")

    validated = []
    for payload in payloads:
        validated.append((payload.id, _validate_clip_update(task, payload)))

    now = _now_iso()
    changed_count = 0
    with get_connection() as connection:
        for clip_id, data in validated:
            current = connection.execute(
                """
                SELECT title, start_time, end_time, duration_seconds, enabled, summary
                FROM clip_candidates
                WHERE id = ? AND task_id = ? AND is_deleted = 0
                """,
                (clip_id, task_id),
            ).fetchone()
            if current is None:
                raise ValueError(f"候选片段不存在：{clip_id}")
            if any(
                (
                    str(current["title"] or "") != data["title"],
                    str(current["start_time"] or "") != data["start_time"],
                    str(current["end_time"] or "") != data["end_time"],
                    int(current["duration_seconds"] or 0) != int(data["duration_seconds"]),
                    int(current["enabled"] or 0) != int(data["enabled"]),
                    str(current["summary"] or "") != data["summary"],
                )
            ):
                changed_count += 1
            cursor = connection.execute(
                """
                UPDATE clip_candidates
                SET title = ?, start_time = ?, end_time = ?, duration_seconds = ?,
                    enabled = ?, summary = ?, reviewed = 1, updated_at = ?
                WHERE id = ? AND task_id = ? AND is_deleted = 0
                """,
                (
                    data["title"],
                    data["start_time"],
                    data["end_time"],
                    data["duration_seconds"],
                    data["enabled"],
                    data["summary"],
                    now,
                    clip_id,
                    task_id,
                ),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"候选片段不存在：{clip_id}")
        connection.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", (now, task_id))
        connection.commit()

    _append_task_log(task_id, f"已批量保存 {len(validated)} 条候选片段审核修改")
    return {
        "message": f"已保存 {len(validated)} 条候选片段，任务状态仍保持 AI 结果待检查。",
        "changed_count": changed_count,
        "task": get_task(task_id, include_video_probe=False),
        "clips": list_clip_candidates(task_id),
    }


def _active_outputs_match_enabled_candidates(task_id: str) -> bool:
    with get_connection() as connection:
        enabled_rows = connection.execute(
            """
            SELECT id
            FROM clip_candidates
            WHERE task_id = ? AND enabled = 1 AND is_deleted = 0
            """,
            (task_id,),
        ).fetchall()
        output_rows = connection.execute(
            """
            SELECT clip_candidate_id, output_file_path
            FROM output_clip
            WHERE task_id = ? AND is_active = 1 AND status = 'completed'
            """,
            (task_id,),
        ).fetchall()

    enabled_ids = {str(row["id"]) for row in enabled_rows}
    if not enabled_ids or len(output_rows) != len(enabled_ids):
        return False

    output_ids = {str(row["clip_candidate_id"] or "") for row in output_rows}
    if output_ids != enabled_ids:
        return False

    return all(
        bool(
            (resolved := resolve_video_file_path(str(row["output_file_path"] or "")))
            and resolved.exists()
            and resolved.is_file()
        )
        for row in output_rows
    )


def sync_reviewed_clips_to_publish_center(
    task_id: str,
    payloads: list[ClipCandidateBatchItem],
) -> dict:
    with get_connection() as connection:
        task = connection.execute(
            "SELECT auto_mode, status FROM tasks WHERE id = ? AND COALESCE(is_deleted, 0) = 0",
            (task_id,),
        ).fetchone()
    if task and bool(task["auto_mode"]) and task["status"] == TaskStatus.PENDING_SUBTITLE_REVIEW.value:
        raise ValueError("自动流水线正在等待字幕审核，请先完成字幕决定，再同步发送中心")
    save_result = update_clip_candidates_batch(task_id, payloads)
    needs_regeneration = bool(save_result["changed_count"]) or not _active_outputs_match_enabled_candidates(task_id)

    if needs_regeneration:
        cut_result = process_task_video_cuts(task_id)
        publish_sync = cut_result.get("publish_sync") or {
            "status": "partial",
            "message": "最新切片已生成，但没有取得发送中心同步结果。",
            "errors": ["发送中心同步结果缺失"],
        }
        action_message = "已保存当前审核选择，并重新生成最新切片。"
    else:
        from app.services.publish_service import sync_task_publish_jobs

        cut_result = None
        publish_sync = sync_task_publish_jobs(
            task_id,
            prefer_subtitled=False,
            restore_removed=True,
        )
        action_message = "已保存当前审核选择，现有切片与选择一致，无需重复生成。"

    link_state = publish_sync.get("link_state") or {}
    publish_sync_ok = (
        publish_sync.get("status") == "ok"
        and not (publish_sync.get("errors") or [])
        and link_state.get("state") == "linked"
        and int(link_state.get("missing_count") or 0) == 0
    )
    current_task = get_task(task_id, include_video_probe=False)
    if publish_sync_ok and current_task and current_task.get("status") == TaskStatus.pending_review.value:
        transition_task_status(task_id, TaskStatus.completed)
    elif not publish_sync_ok and current_task and current_task.get("status") in {
        TaskStatus.completed.value,
        TaskStatus.completed_with_errors.value,
    }:
        update_task_status(task_id, TaskStatus.pending_review)

    return {
        "status": publish_sync.get("status") or "ok",
        "message": f"{action_message}{publish_sync.get('message') or '发送中心同步完成。'}",
        "regenerated": needs_regeneration,
        "saved_count": len(payloads),
        "changed_count": save_result["changed_count"],
        "review_save": save_result,
        "cut_result": cut_result,
        "publish_sync": publish_sync,
        "link_state": link_state,
        "errors": publish_sync.get("errors") or [],
        "warnings": publish_sync.get("warnings") or [],
    }


def get_clip_candidate(task_id: str, clip_id: str) -> dict:
    for clip in list_clip_candidates(task_id):
        if clip["id"] == clip_id:
            return clip
    raise ValueError("候选片段不存在")


def delete_clip_candidate(task_id: str, clip_id: str) -> dict:
    task = get_task(task_id, include_video_probe=False)
    if not task:
        raise ValueError("Task does not exist")

    now = _now_iso()
    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE clip_candidates
            SET is_deleted = 1,
                deleted_at = ?,
                enabled = 0,
                reviewed = 1,
                updated_at = ?
            WHERE id = ? AND task_id = ? AND is_deleted = 0
            """,
            (now, now, clip_id, task_id),
        )
        connection.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", (now, task_id))
        connection.commit()

    if cursor.rowcount == 0:
        raise ValueError("Clip candidate does not exist")

    _append_task_log(task_id, f"Deleted clip candidate: {clip_id}")
    return {
        "message": "Clip candidate deleted. Future cutting will not use it.",
        "task": get_task(task_id, include_video_probe=False),
        "clip_count": count_clip_candidates(task_id),
        "enabled_clip_count": count_enabled_clip_candidates(task_id),
    }


def list_enabled_clip_candidates(task_id: str) -> list[dict]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT id, task_id, clip_key, title, start_time, end_time, duration_seconds, cover_time_seconds,
                   summary, reason, highlight_reason, spread_value, suggested_editing, confidence_score,
                   quality_tier, quality_score, text_quality_score, humor_score, completeness_score,
                   audio_reaction_score, topic_key, key_moment_time, quality_evidence_json, rejection_reason,
                   selected_by_default, enabled, reviewed, is_deleted, deleted_at, created_at, updated_at
            FROM clip_candidates
            WHERE task_id = ? AND enabled = 1 AND is_deleted = 0
            ORDER BY start_time ASC
            """,
            (task_id,),
        ).fetchall()
    return [
        {
            **dict(row),
            "reason": row["highlight_reason"] or row["reason"] or "",
            "highlight_reason": row["highlight_reason"] or row["reason"] or "",
            "selected_by_default": bool(row["selected_by_default"]),
            "enabled": bool(row["enabled"]),
            "reviewed": bool(row["reviewed"]),
            "is_deleted": bool(row["is_deleted"]),
            "deleted_at": row["deleted_at"],
        }
        for row in rows
    ]


def count_output_clips(task_id: str) -> int:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS total FROM output_clip WHERE task_id = ? AND is_active = 1",
            (task_id,),
        ).fetchone()
    return int(row["total"]) if row else 0


def count_completed_output_clips(task_id: str) -> int:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS total FROM output_clip WHERE task_id = ? AND status = 'completed' AND is_active = 1",
            (task_id,),
        ).fetchone()
    return int(row["total"]) if row else 0


def list_output_clips(task_id: str) -> list[dict]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                output_clip.id, output_clip.task_id, output_clip.clip_candidate_id,
                output_clip.output_file_path, output_clip.output_file_name,
                output_clip.status, output_clip.error_message, output_clip.created_at, output_clip.updated_at,
                clip_candidates.title AS clip_title,
                clip_candidates.start_time AS clip_start_time,
                clip_candidates.end_time AS clip_end_time,
                clip_candidates.duration_seconds AS clip_duration_seconds,
                clip_candidates.cover_time_seconds AS cover_time_seconds,
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
            WHERE output_clip.task_id = ? AND output_clip.is_active = 1
            ORDER BY
                CASE WHEN output_clip.output_file_name IS NULL OR output_clip.output_file_name = '' THEN 1 ELSE 0 END,
                output_clip.output_file_name ASC,
                output_clip.created_at ASC
            """,
            (task_id,),
        ).fetchall()
    clips = []
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
        clips.append(
            {
                **output,
                "status_label": OUTPUT_STATUS_LABELS.get(output["status"], output["status"]),
                "file_exists": bool(output_path and output_path.exists() and output_path.is_file()),
                "media_url": f"/media/tasks/{task_id}/output-clips/{output['id']}",
                "source_media_url": f"/media/tasks/{task_id}/source-video",
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
                "subtitled_media_url": f"/media/tasks/{task_id}/subtitled-clips/{output['id']}",
                "publish_stage": "待推送配置" if subtitle_status == "completed" else "待字幕确认",
            }
        )
    return clips


def get_output_clip(task_id: str, output_clip_id: str) -> dict | None:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT id, task_id, clip_candidate_id, output_file_path, output_file_name,
                   status, error_message, created_at, updated_at
            FROM output_clip
            WHERE task_id = ? AND id = ? AND is_active = 1
            """,
            (task_id, output_clip_id),
        ).fetchone()
    if not row:
        return None
    output = {**dict(row), "status_label": OUTPUT_STATUS_LABELS.get(row["status"], row["status"])}
    with get_connection() as connection:
        subtitle_row = connection.execute(
            """
            SELECT subtitle_jobs.*, subtitle_revisions.status AS subtitle_revision_status
            FROM subtitle_jobs
            LEFT JOIN subtitle_revisions ON subtitle_revisions.id = subtitle_jobs.revision_id
            WHERE task_id = ? AND output_clip_id = ? AND is_active = 1
            """,
            (task_id, output_clip_id),
        ).fetchone()
    if subtitle_row:
        output.update(
            {
                "subtitle_job_id": subtitle_row["id"],
                "subtitle_status": subtitle_row["status"],
                "subtitle_file_path": subtitle_row["subtitle_file_path"],
                "subtitled_output_file_path": subtitle_row["output_file_path"],
                "subtitle_error_message": subtitle_row["error_message"],
                "subtitle_revision_id": subtitle_row["revision_id"],
                "subtitle_revision_status": subtitle_row["subtitle_revision_status"],
                "subtitle_validation_status": subtitle_row["validation_status"],
                "subtitle_validation_json": subtitle_row["validation_json"],
                "subtitle_encoder": subtitle_row["encoder"],
                "subtitle_verified_at": subtitle_row["verified_at"],
                "subtitle_status_label": SUBTITLE_STATUS_LABELS.get(subtitle_row["status"], subtitle_row["status"]),
            }
        )
    else:
        output.update({"subtitle_status": "pending", "subtitle_status_label": SUBTITLE_STATUS_LABELS["pending"]})
    return output


def _batch_output_clip_counts(task_ids: list[str]) -> dict[str, int]:
    from app.services.task_query_service import _batch_output_clip_counts as batch_output_clip_counts

    return batch_output_clip_counts(task_ids)


def _batch_completed_output_clip_counts(task_ids: list[str]) -> dict[str, int]:
    from app.services.task_query_service import (
        _batch_completed_output_clip_counts as batch_completed_output_clip_counts,
    )

    return batch_completed_output_clip_counts(task_ids)


def _batch_clip_candidate_counts(task_ids: list[str]) -> dict[str, dict[str, int]]:
    from app.services.task_query_service import _batch_clip_candidate_counts as batch_clip_candidate_counts

    return batch_clip_candidate_counts(task_ids)


def _batch_all_output_clips(task_ids: list[str]) -> dict[str, list[dict]]:
    from app.services.task_query_service import _batch_all_output_clips as batch_all_output_clips

    return batch_all_output_clips(task_ids)


def get_dashboard_context() -> dict:
    from app.services.task_query_service import get_dashboard_context as dashboard_context

    return dashboard_context()


def get_clips_overview_context() -> dict:
    from app.services.task_query_service import get_clips_overview_context as clips_overview_context

    return clips_overview_context()


def get_subtitle_workflow_context() -> dict:
    from app.services.task_query_service import get_subtitle_workflow_context as subtitle_workflow_context

    return subtitle_workflow_context()


def get_subtitle_task_context(task_id: str) -> dict:
    from app.services.task_query_service import get_subtitle_task_context as subtitle_task_context

    return subtitle_task_context(task_id)


def get_system_status_context() -> dict:
    from app.services.task_query_service import get_system_status_context as system_status_context

    return system_status_context()
