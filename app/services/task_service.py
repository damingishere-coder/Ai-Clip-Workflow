from datetime import datetime, timedelta, timezone
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
    _summarize_ai_error,
    _summarize_analysis_clips,
    _write_analysis_payload,
    get_ai_analysis_run,
    get_latest_ai_analysis_run,
    get_task_ai_analysis_status,
    get_task_ai_source_label,
    list_ai_analysis_runs,
    process_task_ai_analysis,
    restore_ai_analysis_run,
)
from app.services.storage_service import (
    get_artifact_paths,
    get_source_video_path,
    resolve_video_file_path,
)
from app.services.task_lifecycle_service import (
    create_task_record,
    soft_delete_task,
    update_task_ai_preference,
    update_task_candidate_clip_count,
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
    _upsert_subtitle_job,
    _write_ass_file,
    get_default_subtitle_style,
    render_subtitles_for_output_clip,
    update_default_subtitle_style,
)
from app.services.transcript_service import read_transcript_progress, read_transcript_range
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


STATUS_LABELS = {
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

PLATFORM_LABELS = {
    "douyin": "抖音",
    "bilibili": "B站",
    "general": "通用",
}

SOURCE_TYPE_LABELS = {
    "upload": "上传视频",
    "nas": "选择 NAS / 本地文件",
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
    if task.get("source_type") == "nas":
        return task.get("nas_file_path") or "尚未选择 NAS / 本地文件"
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
    if not path or not path.exists():
        return {"duration": "尚未读取", "video_size": "尚未读取"}
    duration = None
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
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
        )
        if result.returncode == 0:
            try:
                duration = float(result.stdout.strip())
            except ValueError:
                duration = None
    return {"duration": _format_duration(duration), "video_size": _format_file_size(path.stat().st_size)}


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
        "duration": video_meta["duration"],
        "video_size": video_meta["video_size"],
        "owner": "本地用户",
        "created_at": _format_datetime(task.get("created_at")),
        "updated_at": _format_datetime(task.get("updated_at")),
        "created_at_raw": task.get("created_at"),
        "updated_at_raw": task.get("updated_at"),
        "error_message": task.get("error_message") or "",
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
                max_clip_duration, candidate_clip_count, ai_preference, ai_prompt_preset_id, status, progress,
                error_message, is_deleted, deleted_at, created_at, updated_at
            FROM tasks
            {where_clause}
            ORDER BY created_at DESC
            """
        ).fetchall()
    return [_row_to_task(row) for row in rows]


def get_task(task_id: str, include_video_probe: bool = True) -> dict | None:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT
                id, task_name, task_dir_name, source_type, platform, original_video_path, nas_file_path,
                max_clip_duration, candidate_clip_count, ai_preference, ai_prompt_preset_id, status, progress,
                error_message, is_deleted, deleted_at, created_at, updated_at
            FROM tasks
            WHERE id = ?
            """,
            (task_id,),
        ).fetchone()
    return _row_to_task(row, include_video_probe=include_video_probe) if row else None


def list_clip_candidates(task_id: str) -> list[dict]:
    ai_source_label = get_task_ai_source_label(task_id)
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT id, task_id, clip_key, title, start_time, end_time, duration_seconds, summary,
                   reason, highlight_reason, spread_value, suggested_editing, confidence_score,
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
    with get_connection() as connection:
        for clip_id, data in validated:
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
        "task": get_task(task_id, include_video_probe=False),
        "clips": list_clip_candidates(task_id),
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
            SELECT id, task_id, clip_key, title, start_time, end_time, duration_seconds, summary,
                   reason, highlight_reason, spread_value, suggested_editing, confidence_score,
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
            "SELECT COUNT(*) AS total FROM output_clip WHERE task_id = ?",
            (task_id,),
        ).fetchone()
    return int(row["total"]) if row else 0


def count_completed_output_clips(task_id: str) -> int:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS total FROM output_clip WHERE task_id = ? AND status = 'completed'",
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
                clip_candidates.summary AS clip_summary,
                clip_candidates.enabled AS clip_enabled,
                subtitle_jobs.id AS subtitle_job_id,
                subtitle_jobs.status AS subtitle_status,
                subtitle_jobs.subtitle_file_path,
                subtitle_jobs.output_file_path AS subtitled_output_file_path,
                subtitle_jobs.error_message AS subtitle_error_message,
                subtitle_jobs.updated_at AS subtitle_updated_at
            FROM output_clip
            LEFT JOIN clip_candidates ON clip_candidates.id = output_clip.clip_candidate_id
            LEFT JOIN subtitle_jobs ON subtitle_jobs.output_clip_id = output_clip.id
            WHERE output_clip.task_id = ?
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
            WHERE task_id = ? AND id = ?
            """,
            (task_id, output_clip_id),
        ).fetchone()
    if not row:
        return None
    output = {**dict(row), "status_label": OUTPUT_STATUS_LABELS.get(row["status"], row["status"])}
    with get_connection() as connection:
        subtitle_row = connection.execute(
            """
            SELECT *
            FROM subtitle_jobs
            WHERE task_id = ? AND output_clip_id = ?
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
                "subtitle_status_label": SUBTITLE_STATUS_LABELS.get(subtitle_row["status"], subtitle_row["status"]),
            }
        )
    else:
        output.update({"subtitle_status": "pending", "subtitle_status_label": SUBTITLE_STATUS_LABELS["pending"]})
    return output






