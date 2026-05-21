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
from app.models.task import ClipCandidateBatchItem, ClipCandidateUpdate, TaskCreate, TaskStatus
from app.services.ai_config_service import get_ai_config_context
from app.services.ai.ai_clip_analyzer import (
    AIAnalysisError,
    AnalysisRequest,
    analyze_task_transcript,
    inspect_local_analysis_plan,
    result_to_jsonable,
)
from app.services.ai.diagnostics import ensure_local_ai_ready
from app.services.storage_service import (
    create_task_directory,
    get_artifact_paths,
    get_expected_subdirectories,
    get_source_video_path,
    validate_source_video_path,
)
from app.services.transcript_service import (
    cleanup_transcript_chunk_dirs,
    read_transcript_range,
    read_transcript_progress,
    read_transcript_preview,
    run_ffmpeg_audio_extract,
    write_transcript_progress,
    write_transcript_markdown,
)
from app.services.video_cut_service import CutResult, cut_clips


WORKFLOW_STEPS = [
    "视频提交",
    "音频提取",
    "转写",
    "AI 分析",
    "人工审核",
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
    TaskStatus.pending_review.value: "待人工审核",
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

_RUNNING_TRANSCRIPT_TASKS: set[str] = set()
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


def _parse_progress_updated_at(progress: dict) -> datetime | None:
    value = progress.get("updated_at")
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.astimezone()
    return parsed.astimezone()


def _transcript_progress_age_seconds(progress: dict) -> int | None:
    updated_at = _parse_progress_updated_at(progress)
    if not updated_at:
        return None
    return max(0, int((datetime.now().astimezone() - updated_at).total_seconds()))


def _is_transcript_progress_stale(progress: dict) -> bool:
    if progress.get("status") != "running":
        return False
    updated_at = _parse_progress_updated_at(progress)
    if not updated_at:
        return False
    return datetime.now().astimezone() - updated_at > _TRANSCRIPT_STALE_AFTER


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


def _append_task_log(task_id: str, message: str) -> None:
    paths = get_artifact_paths(task_id)
    paths["log_path"].parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with paths["log_path"].open("a", encoding="utf-8") as log_file:
        log_file.write(f"[{timestamp}] {message}\n")


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
    paths = get_artifact_paths(task["id"])
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
                id, task_name, source_type, platform, original_video_path, nas_file_path,
                max_clip_duration, candidate_clip_count, ai_preference, status, progress,
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
                id, task_name, source_type, platform, original_video_path, nas_file_path,
                max_clip_duration, candidate_clip_count, ai_preference, status, progress,
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
                   selected_by_default, enabled, reviewed, created_at, updated_at
            FROM clip_candidates
            WHERE task_id = ?
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
                "start_seconds": _parse_time_to_seconds(clip["start_time"]),
                "end_seconds": _parse_time_to_seconds(clip["end_time"]),
            }
        )
    return clips


def _ai_model_name(provider_name: str) -> str:
    if provider_name == "local":
        return settings.ai_local_model
    return settings.ai_remote_model


def _ai_provider_label(provider_name: str) -> str:
    if provider_name == "local":
        return "本地 Ollama"
    if provider_name == "remote":
        return "远程 AI"
    return provider_name or "AI"


def _read_analysis_meta(task_id: str) -> dict:
    paths = get_artifact_paths(task_id)
    if not paths["analysis_path"].exists():
        return {}
    try:
        payload = json.loads(paths["analysis_path"].read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload.get("analysis_meta") or {}


def _read_latest_ai_provider_from_log(task_id: str) -> str:
    paths = get_artifact_paths(task_id)
    if not paths["log_path"].exists():
        return ""
    try:
        lines = paths["log_path"].read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    for line in reversed(lines):
        if "AI 分析完成，Provider：" in line:
            provider = line.split("Provider：", 1)[-1].split("，", 1)[0].strip().lower()
            if provider:
                return provider
        if "开始 AI 片段分析，Provider：" in line:
            provider = line.split("Provider：", 1)[-1].strip().lower()
            if provider:
                return provider
    return ""


def get_task_ai_source_label(task_id: str) -> str:
    meta = _read_analysis_meta(task_id)
    provider_name = str(meta.get("provider") or "").lower() or _read_latest_ai_provider_from_log(task_id)
    provider_name = provider_name or settings.ai_default_provider.lower()
    model_name = str(meta.get("model") or "") or _ai_model_name(provider_name)
    return f"{_ai_provider_label(provider_name)} · 模型 {model_name}"


def get_clip_transcript_excerpt(task_id: str, clip_id: str) -> dict:
    clip = get_clip_candidate(task_id, clip_id)
    paths = get_artifact_paths(task_id)
    rows = read_transcript_range(
        paths["transcript_path"],
        int(clip["start_seconds"]),
        int(clip["end_seconds"]),
    )
    return {
        "task_id": task_id,
        "clip_id": clip_id,
        "title": clip["title"],
        "start_time": clip["start_time"],
        "end_time": clip["end_time"],
        "rows": rows,
    }


def count_clip_candidates(task_id: str) -> int:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS total FROM clip_candidates WHERE task_id = ?",
            (task_id,),
        ).fetchone()
    return int(row["total"]) if row else 0


def count_enabled_clip_candidates(task_id: str) -> int:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS total FROM clip_candidates WHERE task_id = ? AND enabled = 1",
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
            WHERE id = ? AND task_id = ?
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
                WHERE id = ? AND task_id = ?
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
        "message": f"已保存 {len(validated)} 条候选片段，任务状态仍保持待人工审核。",
        "task": get_task(task_id, include_video_probe=False),
        "clips": list_clip_candidates(task_id),
    }


def get_clip_candidate(task_id: str, clip_id: str) -> dict:
    for clip in list_clip_candidates(task_id):
        if clip["id"] == clip_id:
            return clip
    raise ValueError("候选片段不存在")


def request_clip_generation_placeholder(task_id: str) -> dict:
    task = get_task(task_id, include_video_probe=False)
    if not task:
        raise ValueError("任务不存在")
    enabled_count = sum(1 for clip in list_clip_candidates(task_id) if clip["enabled"])
    _append_task_log(task_id, "用户点击生成切片，当前等待视频切割模块接入")
    return {
        "message": f"已收到生成切片请求。当前共有 {enabled_count} 条启用片段，待视频切割模块接入后会进入切割流程。",
        "status": task["status"],
        "status_label": task["status_label"],
        "next_step": "待视频切割模块接入",
    }


def list_enabled_clip_candidates(task_id: str) -> list[dict]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT id, task_id, clip_key, title, start_time, end_time, duration_seconds, summary,
                   reason, highlight_reason, spread_value, suggested_editing, confidence_score,
                   selected_by_default, enabled, reviewed, created_at, updated_at
            FROM clip_candidates
            WHERE task_id = ? AND enabled = 1
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


def list_output_clips(task_id: str) -> list[dict]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT id, task_id, clip_candidate_id, output_file_path, output_file_name,
                   status, error_message, created_at, updated_at
            FROM output_clip
            WHERE task_id = ?
            ORDER BY
                CASE WHEN output_file_name IS NULL OR output_file_name = '' THEN 1 ELSE 0 END,
                output_file_name ASC,
                created_at ASC
            """,
            (task_id,),
        ).fetchall()
    return [
        {**dict(row), "status_label": OUTPUT_STATUS_LABELS.get(row["status"], row["status"])}
        for row in rows
    ]


def get_transcript_preview(task_id: str) -> list[dict[str, str]]:
    paths = get_artifact_paths(task_id)
    return read_transcript_preview(paths["transcript_path"])


def get_task_transcript_status(task_id: str) -> dict:
    task = get_task(task_id)
    if not task:
        raise ValueError("任务不存在")

    paths = get_artifact_paths(task_id)
    progress = read_transcript_progress(paths["transcript_path"])
    age_seconds = _transcript_progress_age_seconds(progress)
    is_stale = _is_transcript_progress_stale(progress)
    if is_stale and progress.get("status") == "running":
        progress = {
            **progress,
            "status": "stale",
            "message": "转写进度长时间没有更新，可能已经卡住。请重新点击“生成转写 MD”。",
        }

    return {
        "task_id": task_id,
        "task_status": task.get("status"),
        "task_status_label": task.get("status_label"),
        "task_progress": task.get("progress"),
        "transcript_exists": paths["transcript_path"].exists(),
        "progress": progress,
        "progress_age_seconds": age_seconds,
        "is_stale": is_stale,
        "preview": read_transcript_preview(paths["transcript_path"]),
        "error_message": task.get("error_message") or "",
    }


def get_dashboard_context() -> dict:
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
    completed_count = sum(1 for task in tasks if task["status"] == TaskStatus.completed.value)
    failed_count = sum(1 for task in tasks if task["status"] == TaskStatus.failed.value)

    return {
        "stats": [
            {"label": "今日新增任务", "value": today_count, "note": "来自数据库", "tone": "blue"},
            {"label": "待处理", "value": pending_count, "note": "可继续推进", "tone": "amber"},
            {"label": "待审核", "value": review_count, "note": "等待确认", "tone": "purple"},
            {"label": "已完成", "value": completed_count, "note": "已输出", "tone": "green"},
            {"label": "失败任务", "value": failed_count, "note": "需排查", "tone": "red"},
        ],
        "workflow_steps": WORKFLOW_STEPS,
        "recent_tasks": tasks[:5],
    }


def get_clips_overview_context() -> dict:
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
            review_stage = "待审核"
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
                "label": "待人工审核",
                "value": sum(1 for task in enriched_tasks if task["review_stage"] == "待审核"),
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


def create_task_record(payload: TaskCreate, task_id: str | None = None) -> dict:
    resolved_task_id = task_id or uuid4().hex[:12]
    now = _now_iso()
    create_task_directory(resolved_task_id)

    source_path = payload.nas_file_path if payload.source_type == "nas" else payload.original_video_path
    has_source_file = bool(source_path)
    if source_path:
        valid, error_message = validate_source_video_path(source_path)
        if not valid:
            raise ValueError(error_message)

    initial_status = (
        TaskStatus.pending_processing.value if has_source_file else TaskStatus.pending_video.value
    )
    progress = STATUS_PROGRESS[initial_status]

    with get_connection() as connection:
        existing_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(tasks)").fetchall()
        }
        insert_data = {
            "id": resolved_task_id,
            "task_name": payload.task_name,
            "source_type": payload.source_type,
            "platform": payload.platform,
            "original_video_path": payload.original_video_path,
            "nas_file_path": payload.nas_file_path,
            "max_clip_duration": payload.max_clip_duration,
            "candidate_clip_count": payload.candidate_clip_count,
            "ai_preference": payload.ai_preference,
            "status": initial_status,
            "progress": progress,
            "error_message": None,
            "is_deleted": 0,
            "deleted_at": None,
            "created_at": now,
            "updated_at": now,
        }

        if "title" in existing_columns:
            insert_data["title"] = payload.task_name
        if "source_path" in existing_columns:
            insert_data["source_path"] = payload.nas_file_path or payload.original_video_path
        if "max_clip_minutes" in existing_columns:
            insert_data["max_clip_minutes"] = payload.max_clip_duration
        if "target_clip_count" in existing_columns:
            insert_data["target_clip_count"] = payload.candidate_clip_count

        columns = [column for column in insert_data if column in existing_columns]
        placeholders = ", ".join("?" for _ in columns)
        connection.execute(
            f"INSERT INTO tasks ({', '.join(columns)}) VALUES ({placeholders})",
            tuple(insert_data[column] for column in columns),
        )
        connection.commit()

    _append_task_log(resolved_task_id, "任务已创建")
    return {
        "id": resolved_task_id,
        "task_name": payload.task_name,
        "status": initial_status,
        "status_label": get_status_label(initial_status),
        "detail_url": f"/tasks/{resolved_task_id}",
        "message": "任务已创建并写入数据库。",
    }


def update_task_status(
    task_id: str,
    new_status: TaskStatus,
    error_message: str | None = None,
) -> dict | None:
    now = _now_iso()
    status_value = new_status.value
    progress = STATUS_PROGRESS.get(status_value, 0)

    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE tasks
            SET status = ?, progress = ?, error_message = ?, updated_at = ?
            WHERE id = ?
            """,
            (status_value, progress, error_message, now, task_id),
        )
        connection.commit()

    if cursor.rowcount == 0:
        return None
    return get_task(task_id)


def update_task_ai_preference(task_id: str, ai_preference: str | None) -> dict:
    task = get_task(task_id, include_video_probe=False)
    if not task:
        raise ValueError("任务不存在")

    now = _now_iso()
    normalized_preference = (ai_preference or "").strip()
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE tasks
            SET ai_preference = ?, updated_at = ?
            WHERE id = ?
            """,
            (normalized_preference, now, task_id),
        )
        connection.commit()

    _append_task_log(task_id, "已保存 AI 分析偏好")
    return {
        "status": "ok",
        "message": "AI 偏好已保存。",
        "task": get_task(task_id, include_video_probe=False),
    }


def soft_delete_task(task_id: str) -> dict:
    task = get_task(task_id, include_video_probe=False)
    if not task:
        raise ValueError("任务不存在")
    if task.get("is_deleted"):
        return {
            "message": "任务已隐藏，无需重复操作。",
            "task_id": task_id,
            "task_dir": task["task_dir"],
        }

    now = _now_iso()
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE tasks
            SET is_deleted = 1, deleted_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (now, now, task_id),
        )
        connection.commit()

    _append_task_log(task_id, "任务已从列表隐藏，文件未删除")
    return {
        "message": "任务已隐藏，原视频、切片和任务目录都已保留。",
        "task_id": task_id,
        "task_dir": task["task_dir"],
    }


def process_task_audio(task_id: str) -> dict:
    task = get_task(task_id)
    if not task:
        raise ValueError("任务不存在")
    source_path = get_source_video_path(task)
    valid, error_message = validate_source_video_path(str(source_path) if source_path else None)
    if not valid:
        update_task_status(task_id, TaskStatus.failed, error_message)
        _append_task_log(task_id, f"音频提取失败：{error_message}")
        raise ValueError(error_message)

    paths = get_artifact_paths(task_id)
    update_task_status(task_id, TaskStatus.audio_extracting)
    _append_task_log(task_id, "开始使用 FFmpeg 提取音频")
    try:
        result = run_ffmpeg_audio_extract(source_path, paths["audio_path"])
    except Exception as exc:
        error = str(exc)
        update_task_status(task_id, TaskStatus.failed, error)
        _append_task_log(task_id, f"音频提取失败：{error}")
        raise

    update_task_status(task_id, TaskStatus.transcribing)
    _append_task_log(task_id, f"音频提取完成：{paths['audio_path']}")
    return {**result, "task": get_task(task_id)}


def process_task_transcript(task_id: str, background_tasks: Any | None = None) -> dict:
    task = get_task(task_id)
    if not task:
        raise ValueError("任务不存在")
    paths = get_artifact_paths(task_id)
    if not paths["audio_path"].exists():
        error = "请先完成音频提取，再生成转写 Markdown"
        update_task_status(task_id, TaskStatus.failed, error)
        _append_task_log(task_id, f"转写失败：{error}")
        raise ValueError(error)

    last_progress = read_transcript_progress(paths["transcript_path"])
    if task_id in _RUNNING_TRANSCRIPT_TASKS and not _is_transcript_progress_stale(last_progress):
        return {
            "status": "running",
            "message": "分段转写已经在后台运行，请稍后刷新查看进度。",
            "task": get_task(task_id),
        }
    if task_id in _RUNNING_TRANSCRIPT_TASKS:
        _RUNNING_TRANSCRIPT_TASKS.discard(task_id)
        _append_task_log(task_id, "发现过期的后台转写状态，准备重新开始转写")

    removed_dirs = cleanup_transcript_chunk_dirs(paths["transcript_path"])
    if removed_dirs:
        _append_task_log(task_id, f"已清理旧的转写临时目录：{removed_dirs} 个")
    update_task_status(task_id, TaskStatus.transcribing)
    write_transcript_progress(
        paths["transcript_path"],
        status="running",
        current_chunk=0,
        total_chunks=0,
        percent=1,
        message="后台分段转写已启动，正在准备环境",
    )
    _append_task_log(task_id, "开始后台分段语音转写")
    _RUNNING_TRANSCRIPT_TASKS.add(task_id)
    if background_tasks is not None:
        background_tasks.add_task(_run_task_transcript_background, task_id)
    else:
        _run_task_transcript_background(task_id)
    return {
        "status": "started",
        "message": "已开始后台分段转写，请稍后刷新查看进度。",
        "task": get_task(task_id),
    }


def process_task_transcript_workflow(
    task_id: str,
    background_tasks: Any | None = None,
    force: bool = False,
) -> dict:
    task = get_task(task_id)
    if not task:
        raise ValueError("任务不存在")
    paths = get_artifact_paths(task_id)

    if paths["transcript_path"].exists() and not force:
        return {
            "status": "completed",
            "message": "转写 Markdown 已经生成，无需重复处理。如需重做，请点击“重新生成转写”。",
            "task": get_task(task_id),
        }

    if not paths["audio_path"].exists():
        _append_task_log(task_id, "一键处理：未发现音频文件，先自动提取音频")
        process_task_audio(task_id)

    if force:
        _append_task_log(task_id, "用户明确要求重新生成转写 Markdown")

    return process_task_transcript(task_id, background_tasks=background_tasks)


def _run_task_transcript_background(task_id: str) -> None:
    task = get_task(task_id)
    if not task:
        _RUNNING_TRANSCRIPT_TASKS.discard(task_id)
        return
    paths = get_artifact_paths(task_id)

    def progress_callback(progress: dict) -> None:
        message = progress.get("message") or "转写进度已更新"
        current_chunk = int(progress.get("current_chunk") or 0)
        total_chunks = int(progress.get("total_chunks") or 0)
        percent = int(progress.get("percent") or 0)
        if total_chunks:
            _append_task_log(task_id, f"{message}（{current_chunk}/{total_chunks}，{percent}%）")
        else:
            _append_task_log(task_id, message)

    try:
        write_transcript_markdown(
            task,
            paths["audio_path"],
            paths["transcript_path"],
            progress_callback=progress_callback,
        )
    except Exception as exc:
        error = str(exc)
        last_progress = read_transcript_progress(paths["transcript_path"])
        write_transcript_progress(
            paths["transcript_path"],
            status="failed",
            current_chunk=int(last_progress.get("current_chunk") or 0),
            total_chunks=int(last_progress.get("total_chunks") or 0),
            percent=int(last_progress.get("percent") or 0),
            message=f"分段转写失败：{error}",
        )
        update_task_status(task_id, TaskStatus.failed, error)
        _append_task_log(task_id, f"转写失败：{error}")
        return
    finally:
        _RUNNING_TRANSCRIPT_TASKS.discard(task_id)

    update_task_status(task_id, TaskStatus.pending_ai)
    _append_task_log(task_id, f"真实转写 Markdown 已生成：{paths['transcript_path']}")


def _clear_clip_candidates(task_id: str) -> None:
    with get_connection() as connection:
        connection.execute("DELETE FROM clip_candidates WHERE task_id = ?", (task_id,))
        connection.commit()


def _insert_clip_candidates(task_id: str, clips: list[dict]) -> None:
    now = _now_iso()
    with get_connection() as connection:
        for index, clip in enumerate(clips, start=1):
            clip_key = str(clip["clip_id"])
            database_id = f"{task_id}_{clip_key}"[:120]
            selected_by_default = bool(clip.get("selected_by_default", True))
            connection.execute(
                """
                INSERT INTO clip_candidates (
                    id, task_id, clip_key, title, start_time, end_time, duration_seconds,
                    summary, reason, highlight_reason, spread_value, suggested_editing,
                    confidence_score, selected_by_default, enabled, reviewed, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    database_id or f"{task_id}_clip_{index:03d}",
                    task_id,
                    clip_key,
                    clip["title"],
                    clip["start_time"],
                    clip["end_time"],
                    clip["duration_seconds"],
                    clip["summary"],
                    clip["highlight_reason"],
                    clip["highlight_reason"],
                    clip["spread_value"],
                    clip["suggested_editing"],
                    clip["confidence_score"],
                    1 if selected_by_default else 0,
                    1 if selected_by_default else 0,
                    now,
                    now,
                ),
            )
        connection.commit()


def _analyze_with_provider(task_id: str, task: dict, paths: dict[str, Path], provider_name: str):
    request = AnalysisRequest(
        task_id=task_id,
        transcript_path=paths["transcript_path"],
        max_clip_duration_minutes=int(task["max_clip_duration"]),
        target_clip_count=int(task["candidate_clip_count"]),
        ai_preference=task.get("ai_preference") or "",
        provider_name=provider_name,
    )
    if provider_name == "local":
        ensure_local_ai_ready()
        plan = inspect_local_analysis_plan(request)
        _append_task_log(
            task_id,
            "本地 AI 将使用分段分析："
            f"{plan['chunk_count']} 段，单段约 {plan['chunk_seconds']} 秒，"
            f"最大 prompt 约 {plan['max_prompt_chars']} 字",
        )
    return analyze_task_transcript(request)


def _should_fallback_to_local_ai(error: str) -> bool:
    lowered = error.lower()
    markers = (
        "http 403",
        "error code: 1010",
        "unauthorized",
        "forbidden",
        "api key",
        "openai_api_key",
        "ai_remote_api_key",
        "key 看起来无效",
        "密钥",
        "quota",
        "balance",
        "timeout",
        "timed out",
        "无法连接",
        "连接超时",
        "权限",
        "余额",
        "费用",
        "网络",
    )
    return any(marker in lowered for marker in markers)


def process_task_ai_analysis(task_id: str, provider: str | None = None) -> dict:
    task = get_task(task_id, include_video_probe=False)
    if not task:
        raise ValueError("任务不存在")

    paths = get_artifact_paths(task_id)
    if not paths["transcript_path"].exists():
        error = "请先生成带时间戳的转写 Markdown，再开始 AI 分析"
        update_task_status(task_id, TaskStatus.failed, error)
        _append_task_log(task_id, f"AI 分析失败：{error}")
        raise ValueError(error)

    provider_name = (provider or settings.ai_default_provider).lower()
    update_task_status(task_id, TaskStatus.ai_analyzing)
    _append_task_log(task_id, f"开始 AI 片段分析，Provider：{provider_name}")

    used_provider = provider_name
    fallback_notice = ""
    try:
        try:
            analysis = _analyze_with_provider(task_id, task, paths, provider_name)
        except Exception as remote_exc:
            remote_error = str(remote_exc)
            if provider_name == "remote" and _should_fallback_to_local_ai(remote_error):
                _append_task_log(task_id, f"远程 AI 不可用，准备自动降级到本地 AI：{remote_error}")
                try:
                    analysis = _analyze_with_provider(task_id, task, paths, "local")
                    used_provider = "local"
                    fallback_notice = f"远程 AI 不可用，已自动改用本地 AI。远程错误：{remote_error}"
                except Exception as local_exc:
                    raise AIAnalysisError(
                        f"远程 AI 和本地 AI 都失败。远程错误：{remote_error}；本地错误：{local_exc}"
                    ) from local_exc
            else:
                raise
        analysis_payload = result_to_jsonable(analysis)
        analysis_payload["analysis_meta"] = {
            "provider": used_provider,
            "provider_label": _ai_provider_label(used_provider),
            "model": _ai_model_name(used_provider),
            "generated_at": _now_iso(),
        }
        paths["analysis_path"].parent.mkdir(parents=True, exist_ok=True)
        paths["analysis_path"].write_text(
            json.dumps(analysis_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        _clear_clip_candidates(task_id)
        _insert_clip_candidates(task_id, analysis_payload["clips"])
    except (AIAnalysisError, Exception) as exc:
        error = str(exc)
        update_task_status(task_id, TaskStatus.failed, error)
        _append_task_log(task_id, f"AI 分析失败：{error}")
        raise ValueError(error) from exc

    update_task_status(task_id, TaskStatus.pending_review)
    _append_task_log(task_id, f"AI 分析完成，Provider：{used_provider}，生成候选片段：{len(analysis_payload['clips'])} 条")
    message = f"AI 分析完成，生成 {len(analysis_payload['clips'])} 条候选片段。"
    if fallback_notice:
        message = f"{fallback_notice} {message}"
    return {
        "status": "ok",
        "message": message,
        "provider": used_provider,
        "fallback_notice": fallback_notice,
        "analysis_path": str(paths["analysis_path"]),
        "review_url": f"/tasks/{task_id}/clips",
        "task": get_task(task_id, include_video_probe=False),
        "clips": list_clip_candidates(task_id),
    }


def _insert_output_clip_record(task_id: str, result: CutResult) -> None:
    now = _now_iso()
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO output_clip (
                id, task_id, clip_candidate_id, output_file_path, output_file_name,
                status, error_message, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid4().hex[:12],
                task_id,
                result.clip_candidate_id,
                result.output_file_path,
                result.output_file_name,
                result.status,
                result.error_message,
                now,
                now,
            ),
        )
        connection.commit()


def _clear_previous_output_clip_records(task_id: str) -> None:
    with get_connection() as connection:
        connection.execute("DELETE FROM output_clip WHERE task_id = ?", (task_id,))
        connection.commit()


def _resolve_final_cut_status(results: list[CutResult]) -> tuple[TaskStatus, str | None]:
    success_count = sum(1 for result in results if result.status == "completed")
    failed_results = [result for result in results if result.status == "failed"]

    if not failed_results:
        return TaskStatus.completed, None

    error_summary = "；".join(
        result.error_message or f"{result.clip_candidate_id} 切割失败"
        for result in failed_results[:3]
    )
    if success_count > 0:
        return TaskStatus.completed_with_errors, f"部分切片失败：{error_summary}"
    return TaskStatus.failed, f"全部切片失败：{error_summary}"


def process_task_video_cuts(task_id: str) -> dict:
    task = get_task(task_id)
    if not task:
        raise ValueError("任务不存在")

    source_path = get_source_video_path(task)
    valid, error_message = validate_source_video_path(str(source_path) if source_path else None)
    if not valid:
        update_task_status(task_id, TaskStatus.failed, error_message)
        _append_task_log(task_id, f"视频切割失败：{error_message}")
        raise ValueError(error_message)

    enabled_clips = list_enabled_clip_candidates(task_id)
    if not enabled_clips:
        error = "没有任何启用片段，请先在片段审核页启用至少一条候选片段"
        update_task_status(task_id, TaskStatus.failed, error)
        _append_task_log(task_id, f"视频切割失败：{error}")
        raise ValueError(error)

    paths = get_artifact_paths(task_id)
    update_task_status(task_id, TaskStatus.cutting)
    _append_task_log(task_id, f"开始自动切割视频，启用片段数：{len(enabled_clips)}")
    _clear_previous_output_clip_records(task_id)

    try:
        results = cut_clips(
            source_video=source_path,
            clips=enabled_clips,
            output_dir=paths["clips_dir"],
            strategy=settings.default_cut_strategy,
        )
    except Exception as exc:
        error = str(exc)
        update_task_status(task_id, TaskStatus.failed, error)
        _append_task_log(task_id, f"视频切割失败：{error}")
        raise

    for result in results:
        _insert_output_clip_record(task_id, result)
        if result.status == "completed":
            _append_task_log(task_id, f"切片完成：{result.output_file_name}")
        else:
            _append_task_log(task_id, f"切片失败：{result.clip_candidate_id}，原因：{result.error_message}")

    final_status, final_error = _resolve_final_cut_status(results)
    update_task_status(task_id, final_status, final_error)
    _append_task_log(task_id, f"自动切割结束：{get_status_label(final_status.value)}")

    return {
        "status": final_status.value,
        "status_label": get_status_label(final_status.value),
        "message": "视频切割流程已完成",
        "output_dir": str(paths["clips_dir"]),
        "results": [result.__dict__ for result in results],
        "task": get_task(task_id),
    }


def get_system_status_context() -> dict:
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
