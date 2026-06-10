from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import shutil
import subprocess
from sqlite3 import Row
from typing import Any
from uuid import uuid4

from app.core.config import settings
from app.db.database import get_connection
from app.models.task import ClipCandidateBatchItem, ClipCandidateUpdate, SubtitleStyleUpdate, TaskCreate, TaskStatus
from app.services.ai.ai_clip_analyzer import (
    AIAnalysisError,
    AnalysisRequest,
    analyze_task_transcript,
    inspect_local_analysis_plan,
    result_to_jsonable,
)
from app.services.ai.diagnostics import ensure_local_ai_ready
from app.services.ai_prompt_preset_service import get_task_ai_prompt_preset
from app.services.storage_service import (
    create_task_directory,
    get_artifact_paths,
    get_expected_subdirectories,
    get_source_video_path,
    resolve_video_file_path,
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
    "AI 结果检查",
    "自动切割",
    "输出完成",
]

WINDOWS_FONTS_DIR = Path("C:/Windows/Fonts")
SUBTITLE_FONT_FILE_CANDIDATES = {
    "Microsoft YaHei": ("msyh.ttc", "msyhbd.ttc", "msyhl.ttc"),
    "SimHei": ("simhei.ttf",),
    "Noto Sans SC": ("NotoSansSC-VF.ttf",),
    "Source Han Sans CN": ("SourceHanSansCN-Regular.otf", "SourceHanSansCN-Normal.otf", "SourceHanSansCN-Medium.otf"),
    "SimSun": ("simsun.ttc",),
    "DengXian": ("Deng.ttf",),
}
SUBTITLE_CJK_FONT_FALLBACKS = ("Microsoft YaHei", "SimHei", "Noto Sans SC", "Source Han Sans CN", "SimSun", "DengXian")
AI_CLIP_MIN_RECOMMENDED_SECONDS = 45

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

SUBTITLE_STATUS_LABELS = {
    "pending": "待加字幕",
    "processing": "字幕生成中",
    "completed": "已加字幕",
    "failed": "字幕失败",
}

_RUNNING_TRANSCRIPT_TASKS: set[str] = set()
_CANCEL_TRANSCRIPT_TASKS: set[str] = set()


class TranscriptCancelledError(RuntimeError):
    pass
_TRANSCRIPT_STALE_AFTER = timedelta(minutes=10)
_DEFAULT_REMOTE_TRANSCRIPTION_PROVIDER = "volcengine"


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


def _resolve_transcription_provider_choice(provider: str | None = None) -> str:
    choice = (provider or "remote").strip().lower()
    if choice == "local":
        return "local"
    configured_provider = (settings.transcription_provider or "").strip().lower()
    if configured_provider and configured_provider != "local":
        return configured_provider
    return _DEFAULT_REMOTE_TRANSCRIPTION_PROVIDER


def _transcription_choice_label(provider: str) -> str:
    if provider == "local":
        return "本地 faster-whisper"
    if provider == "volcengine":
        return "火山引擎远程转写"
    return provider or "远程转写"


def _can_retry_transcript_with_local(progress: dict, transcript_exists: bool) -> bool:
    provider = str(progress.get("provider") or "").strip().lower()
    return not transcript_exists and progress.get("status") == "failed" and provider != "local"


def _finalize_cancelled_transcript_task(task_id: str, paths: dict[str, Path], progress: dict) -> dict:
    cancelled_progress = write_transcript_progress(
        paths["transcript_path"],
        status="cancelled",
        current_chunk=int(progress.get("current_chunk") or 0),
        total_chunks=int(progress.get("total_chunks") or 0),
        percent=int(progress.get("percent") or 0),
        message="转写已停止，可以重新生成转写。",
    )
    update_task_status(task_id, TaskStatus.pending_processing)
    _RUNNING_TRANSCRIPT_TASKS.discard(task_id)
    _CANCEL_TRANSCRIPT_TASKS.discard(task_id)
    _append_task_log(task_id, "已自动收尾停止转写请求，任务可重新生成转写")
    return cancelled_progress


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


def _ai_model_name(provider_name: str) -> str:
    if provider_name == "local":
        return settings.ai_local_model
    if provider_name == "remote":
        return settings.ai_analysis_remote_model
    return settings.ai_analysis_remote_model


def _ai_provider_label(provider_name: str) -> str:
    if provider_name == "local":
        return "本地 Ollama"
    if provider_name == "remote":
        return "远程 AI"
    return provider_name or "AI"


def _summarize_ai_error(error: str) -> str:
    text = " ".join(str(error or "").split())
    if not text:
        return "AI 分析失败，请查看任务日志。"
    if "AI 分段分析没有生成可用候选片段" in text:
        return "AI 分析失败：所有分段都没有生成可用候选片段，详细原因已写入任务日志。"
    if "JSON 解析失败" in text or "JSON 字段校验失败" in text or "AI 返回非法 JSON" in text:
        return "AI 分析失败：AI 返回的 JSON 不完整或字段不符合要求，详细原因已写入任务日志。"
    if len(text) > 220:
        return f"{text[:220]}...（详细原因已写入任务日志）"
    return text


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


def get_default_subtitle_style() -> dict:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT *
            FROM subtitle_style_presets
            WHERE is_default = 1
            ORDER BY updated_at DESC
            LIMIT 1
            """
        ).fetchone()
    if not row:
        return {
            "id": "default",
            "name": "默认字幕样式",
            "font_family": "Microsoft YaHei",
            "font_size": 42,
            "position": "bottom_center",
            "font_color": "#ffffff",
            "stroke_color": "#111827",
            "shadow_enabled": True,
        }
    style = dict(row)
    style["shadow_enabled"] = bool(style.get("shadow_enabled"))
    return style


def update_default_subtitle_style(payload: SubtitleStyleUpdate) -> dict:
    now = _now_iso()
    with get_connection() as connection:
        existing = connection.execute(
            "SELECT id FROM subtitle_style_presets WHERE id = ?",
            ("default",),
        ).fetchone()
        if existing:
            connection.execute(
                """
                UPDATE subtitle_style_presets
                SET font_family = ?, font_size = ?, position = ?, font_color = ?,
                    stroke_color = ?, shadow_enabled = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    payload.font_family,
                    payload.font_size,
                    payload.position,
                    payload.font_color,
                    payload.stroke_color,
                    1 if payload.shadow_enabled else 0,
                    now,
                    "default",
                ),
            )
        else:
            connection.execute(
                """
                INSERT INTO subtitle_style_presets (
                    id, name, font_family, font_size, position, font_color,
                    stroke_color, shadow_enabled, is_default, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "default",
                    "默认字幕样式",
                    payload.font_family,
                    payload.font_size,
                    payload.position,
                    payload.font_color,
                    payload.stroke_color,
                    1 if payload.shadow_enabled else 0,
                    1,
                    now,
                    now,
                ),
            )
        connection.commit()
    return {
        "status": "ok",
        "message": "字幕样式已保存到数据库。",
        "style": get_default_subtitle_style(),
    }


def _subtitle_job_for_output(task_id: str, output_clip_id: str) -> dict | None:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT *
            FROM subtitle_jobs
            WHERE task_id = ? AND output_clip_id = ?
            """,
            (task_id, output_clip_id),
        ).fetchone()
    return dict(row) if row else None


def _upsert_subtitle_job(
    task_id: str,
    output_clip_id: str,
    status: str,
    subtitle_file_path: str = "",
    output_file_path: str = "",
    error_message: str = "",
) -> dict:
    now = _now_iso()
    existing = _subtitle_job_for_output(task_id, output_clip_id)
    with get_connection() as connection:
        if existing:
            connection.execute(
                """
                UPDATE subtitle_jobs
                SET status = ?, style_preset_id = ?, subtitle_file_path = ?,
                    output_file_path = ?, error_message = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    "default",
                    subtitle_file_path or existing.get("subtitle_file_path") or "",
                    output_file_path or existing.get("output_file_path") or "",
                    error_message,
                    now,
                    existing["id"],
                ),
            )
            job_id = existing["id"]
        else:
            job_id = uuid4().hex[:12]
            connection.execute(
                """
                INSERT INTO subtitle_jobs (
                    id, task_id, output_clip_id, style_preset_id, status,
                    subtitle_file_path, output_file_path, error_message, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    task_id,
                    output_clip_id,
                    "default",
                    status,
                    subtitle_file_path,
                    output_file_path,
                    error_message,
                    now,
                    now,
                ),
            )
        connection.commit()
    return _subtitle_job_for_output(task_id, output_clip_id) or {"id": job_id, "status": status}


def _hex_to_ass_color(value: str) -> str:
    cleaned = (value or "#ffffff").lstrip("#")
    if len(cleaned) != 6:
        cleaned = "ffffff"
    red, green, blue = cleaned[0:2], cleaned[2:4], cleaned[4:6]
    return f"&H00{blue}{green}{red}".upper()


def _ass_time(seconds: float) -> str:
    total_centiseconds = max(0, int(round(seconds * 100)))
    total_seconds, centiseconds = divmod(total_centiseconds, 100)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}.{centiseconds:02d}"


def _escape_ass_text(text: str) -> str:
    return (text or "").replace("\\", "\\\\").replace("{", r"\{").replace("}", r"\}").replace("\n", r"\N")


def _subtitle_font_exists(font_family: str) -> bool:
    candidates = SUBTITLE_FONT_FILE_CANDIDATES.get(font_family, ())
    return any((WINDOWS_FONTS_DIR / file_name).exists() for file_name in candidates)


def _resolve_subtitle_font_family(requested_font_family: str | None) -> str:
    font_family = (requested_font_family or "").strip()
    if font_family and _subtitle_font_exists(font_family):
        return font_family
    for fallback_font_family in SUBTITLE_CJK_FONT_FALLBACKS:
        if _subtitle_font_exists(fallback_font_family):
            return fallback_font_family
    return font_family or "Microsoft YaHei"


def _build_subtitle_rows(task_id: str, output_clip: dict) -> tuple[int, list[dict[str, Any]]]:
    clip = get_clip_candidate(task_id, output_clip["clip_candidate_id"]) if output_clip.get("clip_candidate_id") else None
    if not clip:
        return 0, [{"start_seconds": 0, "end_seconds": 3, "text": output_clip.get("output_file_name") or "精彩片段"}]

    clip_start = int(clip["start_seconds"])
    clip_end = int(clip["end_seconds"])
    rows = read_transcript_range(get_artifact_paths(task_id)["transcript_path"], clip_start, clip_end, max_rows=120)
    subtitle_rows = []
    for row in rows:
        row_start = _parse_time_to_seconds(row["start_time"])
        row_end = _parse_time_to_seconds(row["end_time"])
        start_seconds = max(0, row_start - clip_start)
        end_seconds = max(start_seconds + 1, min(clip_end, row_end) - clip_start)
        subtitle_rows.append({"start_seconds": start_seconds, "end_seconds": end_seconds, "text": row["text"]})
    if subtitle_rows:
        return clip_start, subtitle_rows

    fallback_text = clip.get("summary") or clip.get("title") or "精彩片段"
    return clip_start, [{"start_seconds": 0, "end_seconds": min(5, max(3, clip_end - clip_start)), "text": fallback_text}]


def _write_ass_file(task_id: str, output_clip: dict, style: dict) -> Path:
    paths = get_artifact_paths(task_id)
    paths["subtitled_dir"].mkdir(parents=True, exist_ok=True)
    subtitle_path = paths["subtitled_dir"] / f"{Path(output_clip.get('output_file_name') or output_clip['id']).stem}.ass"
    _, rows = _build_subtitle_rows(task_id, output_clip)

    alignment = "8" if style.get("position") == "top_center" else "2"
    margin_v = "92" if style.get("position") == "bottom_center" else "190"
    if style.get("position") == "top_center":
        margin_v = "70"
    outline = "3" if style.get("shadow_enabled") else "1"
    shadow = "1" if style.get("shadow_enabled") else "0"
    font_family = _resolve_subtitle_font_family(style.get("font_family"))
    font_size = int(style.get("font_size") or 42)
    primary_color = _hex_to_ass_color(style.get("font_color") or "#ffffff")
    outline_color = _hex_to_ass_color(style.get("stroke_color") or "#111827")
    events = "\n".join(
        f"Dialogue: 0,{_ass_time(row['start_seconds'])},{_ass_time(row['end_seconds'])},Default,,0,0,0,,{_escape_ass_text(row['text'])}"
        for row in rows
    )
    content = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_family},{font_size},{primary_color},&H000000FF,{outline_color},&H7F000000,-1,0,0,0,100,100,0,0,1,{outline},{shadow},{alignment},60,60,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
{events}
"""
    subtitle_path.write_text(content, encoding="utf-8")
    return subtitle_path


def _ffmpeg_filter_path(path: Path) -> str:
    normalized = str(path.resolve()).replace("\\", "/")
    return normalized.replace(":", r"\:").replace("'", r"\'")


def _ffmpeg_subtitles_filter(subtitle_path: Path) -> str:
    filter_parts = [f"filename='{_ffmpeg_filter_path(subtitle_path)}'"]
    if WINDOWS_FONTS_DIR.exists():
        filter_parts.append(f"fontsdir='{_ffmpeg_filter_path(WINDOWS_FONTS_DIR)}'")
    return f"subtitles={':'.join(filter_parts)}"


def render_subtitles_for_output_clip(task_id: str, output_clip_id: str) -> dict:
    task = get_task(task_id, include_video_probe=False)
    if not task:
        raise ValueError("任务不存在")
    output_clip = get_output_clip(task_id, output_clip_id)
    if not output_clip:
        raise ValueError("切片记录不存在")
    input_path = resolve_video_file_path(output_clip.get("output_file_path")) or Path(output_clip.get("output_file_path") or "")
    if output_clip.get("status") != "completed" or not input_path.exists():
        raise ValueError("切片视频文件不存在，不能加字幕")
    if not shutil.which("ffmpeg"):
        raise RuntimeError("FFmpeg 不可用，无法生成字幕视频")

    style = get_default_subtitle_style()
    paths = get_artifact_paths(task_id)
    paths["subtitled_dir"].mkdir(parents=True, exist_ok=True)
    output_path = paths["subtitled_dir"] / f"{input_path.stem}_subtitled.mp4"
    job = _upsert_subtitle_job(task_id, output_clip_id, "processing")
    _append_task_log(task_id, f"开始自动加字幕：{input_path.name}")

    try:
        subtitle_path = _write_ass_file(task_id, output_clip, style)
        command = [
            "ffmpeg",
            "-y",
            "-i",
            str(input_path),
            "-vf",
            _ffmpeg_subtitles_filter(subtitle_path),
            "-c:a",
            "copy",
            str(output_path),
        ]
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "FFmpeg 字幕生成失败")
    except Exception as exc:
        error = str(exc)
        _upsert_subtitle_job(task_id, output_clip_id, "failed", error_message=error)
        _append_task_log(task_id, f"自动加字幕失败：{input_path.name}，原因：{error}")
        raise

    job = _upsert_subtitle_job(
        task_id,
        output_clip_id,
        "completed",
        subtitle_file_path=str(subtitle_path),
        output_file_path=str(output_path),
    )
    _append_task_log(task_id, f"自动加字幕完成：{output_path.name}")
    return {
        "status": "ok",
        "message": "自动加字幕完成，已生成带字幕视频。",
        "job": job,
        "output_clip": get_output_clip(task_id, output_clip_id),
        "media_url": f"/media/tasks/{task_id}/subtitled-clips/{output_clip_id}",
    }


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
    if progress.get("status") == "cancelling" and task_id not in _RUNNING_TRANSCRIPT_TASKS:
        progress = _finalize_cancelled_transcript_task(task_id, paths, progress)
        task = get_task(task_id)
    is_stale = _is_transcript_progress_stale(progress)
    if is_stale and progress.get("status") == "running":
        progress = {
            **progress,
            "status": "stale",
            "message": "转写进度长时间没有更新，可能已经卡住。请重新点击“生成转写 MD”。",
        }

    transcript_exists = paths["transcript_path"].exists()
    return {
        "task_id": task_id,
        "task_status": task.get("status"),
        "task_status_label": task.get("status_label"),
        "task_progress": task.get("progress"),
        "transcript_exists": transcript_exists,
        "progress": progress,
        "progress_age_seconds": age_seconds,
        "is_stale": is_stale,
        "preview": read_transcript_preview(paths["transcript_path"]),
        "error_message": task.get("error_message") or "",
        "local_retry_available": _can_retry_transcript_with_local(progress, transcript_exists),
    }


def _read_task_log_tail(task_id: str, limit: int = 80) -> list[str]:
    paths = get_artifact_paths(task_id)
    log_path = paths["log_path"]
    if not log_path.exists():
        return []
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    return lines[-limit:]


def get_task_ai_analysis_status(task_id: str) -> dict:
    task = get_task(task_id, include_video_probe=False)
    if not task:
        raise ValueError("任务不存在")

    paths = get_artifact_paths(task_id)
    log_lines = _read_task_log_tail(task_id)
    is_running = task.get("status") == TaskStatus.ai_analyzing.value
    has_analysis = paths["analysis_path"].exists()

    percent = 0
    message = "等待开始 AI 分析"
    status = "idle"
    if is_running:
        status = "running"
        percent = 48
        message = "AI 正在分析转写文本，请保持页面打开。"
        if any("将使用分段分析" in line for line in log_lines):
            percent = 62
            message = "AI 已读取 Prompt 和转写文本，正在分段生成候选片段。"
        if any("远程 AI 分析接口不可用" in line for line in log_lines):
            percent = 72
            message = "远程 AI 分析接口暂不可用，已暂停等待你确认下一步。"
    elif task.get("status") == TaskStatus.pending_review.value and has_analysis:
        status = "completed"
        percent = 100
        message = "AI 分析完成，候选片段已生成，可检查后直接生成切片。"
    elif task.get("status") == TaskStatus.failed.value and any("AI 分析失败" in line for line in log_lines):
        status = "failed"
        percent = 100
        message = task.get("error_message") or "AI 分析失败，请查看右侧运行日志。"
    elif has_analysis:
        status = "completed"
        percent = 100
        message = "已找到 AI 分析结果文件。"

    return {
        "task_id": task_id,
        "status": status,
        "message": message,
        "percent": percent,
        "is_running": is_running,
        "task_status": task.get("status"),
        "task_status_label": task.get("status_label"),
        "analysis_exists": has_analysis,
        "log_path": str(paths["log_path"]),
        "log_lines": log_lines,
        "error_message": task.get("error_message") or "",
    }


def create_task_record(payload: TaskCreate, task_id: str | None = None, task_dir_name: str | None = None) -> dict:
    resolved_task_id = task_id or uuid4().hex[:12]
    resolved_task_dir_name = task_dir_name or allocate_task_dir_name(
        payload.task_name,
        exclude_task_id=resolved_task_id,
    )
    now = _now_iso()
    create_task_directory(resolved_task_id, resolved_task_dir_name)

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
            "task_dir_name": resolved_task_dir_name,
            "source_type": payload.source_type,
            "platform": payload.platform,
            "original_video_path": payload.original_video_path,
            "nas_file_path": payload.nas_file_path,
            "max_clip_duration": payload.max_clip_duration,
            "candidate_clip_count": payload.candidate_clip_count,
            "ai_preference": payload.ai_preference,
            "ai_prompt_preset_id": "preset_001",
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
        "task_dir_name": resolved_task_dir_name,
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


def update_task_candidate_clip_count(task_id: str, candidate_clip_count: int) -> dict:
    task = get_task(task_id, include_video_probe=False)
    if not task:
        raise ValueError("任务不存在")
    if candidate_clip_count < 1 or candidate_clip_count > 50:
        raise ValueError("候选片段数量必须在 1 到 50 条之间")

    now = _now_iso()
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE tasks
            SET candidate_clip_count = ?, updated_at = ?
            WHERE id = ?
            """,
            (candidate_clip_count, now, task_id),
        )
        connection.commit()

    _append_task_log(task_id, f"已更新 AI 候选片段数量：{candidate_clip_count} 条")
    return {
        "status": "ok",
        "message": f"候选片段数量已更新为 {candidate_clip_count} 条。",
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


def process_task_transcript(
    task_id: str,
    background_tasks: Any | None = None,
    provider: str | None = None,
) -> dict:
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

    provider_name = _resolve_transcription_provider_choice(provider)
    provider_label = _transcription_choice_label(provider_name)
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
        message=f"后台分段转写已启动，正在准备{provider_label}环境",
    )
    _append_task_log(task_id, f"开始后台分段语音转写，Provider：{provider_name}")
    _CANCEL_TRANSCRIPT_TASKS.discard(task_id)
    _RUNNING_TRANSCRIPT_TASKS.add(task_id)
    if background_tasks is not None:
        background_tasks.add_task(_run_task_transcript_background, task_id, provider_name)
    else:
        _run_task_transcript_background(task_id, provider_name)
    return {
        "status": "started",
        "message": f"已开始{provider_label}分段转写，请稍后刷新查看进度。",
        "provider": provider_name,
        "provider_label": provider_label,
        "task": get_task(task_id),
    }


def cancel_task_transcript(task_id: str) -> dict:
    task = get_task(task_id)
    if not task:
        raise ValueError("任务不存在")
    paths = get_artifact_paths(task_id)
    progress = read_transcript_progress(paths["transcript_path"])
    if progress.get("status") == "cancelling" and task_id not in _RUNNING_TRANSCRIPT_TASKS:
        _finalize_cancelled_transcript_task(task_id, paths, progress)
        return {
            "status": "cancelled",
            "message": "转写已停止，可以重新生成转写。",
            "task": get_task(task_id),
        }
    if task_id not in _RUNNING_TRANSCRIPT_TASKS and progress.get("status") != "running":
        return {
            "status": "not_running",
            "message": "当前没有正在运行的转写任务。",
            "task": get_task(task_id),
        }

    _CANCEL_TRANSCRIPT_TASKS.add(task_id)
    write_transcript_progress(
        paths["transcript_path"],
        status="cancelling",
        current_chunk=int(progress.get("current_chunk") or 0),
        total_chunks=int(progress.get("total_chunks") or 0),
        percent=int(progress.get("percent") or 0),
        message="已请求停止转写，当前分段结束后会停止。",
    )
    _append_task_log(task_id, "用户请求停止当前转写任务")
    return {
        "status": "cancelling",
        "message": "已请求停止转写，当前分段结束后会停止。",
        "task": get_task(task_id),
    }


def process_task_transcript_workflow(
    task_id: str,
    background_tasks: Any | None = None,
    force: bool = False,
    provider: str | None = None,
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

    return process_task_transcript(task_id, background_tasks=background_tasks, provider=provider)


def _run_task_transcript_background(task_id: str, provider: str | None = None) -> None:
    task = get_task(task_id)
    if not task:
        _RUNNING_TRANSCRIPT_TASKS.discard(task_id)
        return
    paths = get_artifact_paths(task_id)
    provider_name = _resolve_transcription_provider_choice(provider)
    provider_label = _transcription_choice_label(provider_name)

    def progress_callback(progress: dict) -> None:
        if task_id in _CANCEL_TRANSCRIPT_TASKS:
            raise TranscriptCancelledError("用户已停止当前转写任务")
        message = progress.get("message") or "转写进度已更新"
        current_chunk = int(progress.get("current_chunk") or 0)
        total_chunks = int(progress.get("total_chunks") or 0)
        percent = int(progress.get("percent") or 0)
        if total_chunks:
            _append_task_log(task_id, f"{message}（{current_chunk}/{total_chunks}，{percent}%）")
        else:
            _append_task_log(task_id, message)

    try:
        if task_id in _CANCEL_TRANSCRIPT_TASKS:
            raise TranscriptCancelledError("用户已停止当前转写任务")
        write_transcript_markdown(
            task,
            paths["audio_path"],
            paths["transcript_path"],
            progress_callback=progress_callback,
            provider=provider_name,
        )
    except TranscriptCancelledError as exc:
        last_progress = read_transcript_progress(paths["transcript_path"])
        write_transcript_progress(
            paths["transcript_path"],
            status="cancelled",
            current_chunk=int(last_progress.get("current_chunk") or 0),
            total_chunks=int(last_progress.get("total_chunks") or 0),
            percent=int(last_progress.get("percent") or 0),
            message="转写已停止，可以重新生成转写。",
        )
        update_task_status(task_id, TaskStatus.pending_processing)
        _append_task_log(task_id, str(exc))
        return
    except Exception as exc:
        error = str(exc)
        last_progress = read_transcript_progress(paths["transcript_path"])
        if provider_name == "local":
            user_message = f"本地转写失败：{error}"
        else:
            user_message = f"{provider_label}不可用，已暂停转写：{error}。如需改用本地模型，请点击“改用本地模型转写”。"
        write_transcript_progress(
            paths["transcript_path"],
            status="failed",
            current_chunk=int(last_progress.get("current_chunk") or 0),
            total_chunks=int(last_progress.get("total_chunks") or 0),
            percent=int(last_progress.get("percent") or 0),
            message=user_message,
        )
        update_task_status(task_id, TaskStatus.failed, user_message)
        _append_task_log(task_id, f"转写失败：{user_message}")
        return
    finally:
        _RUNNING_TRANSCRIPT_TASKS.discard(task_id)
        _CANCEL_TRANSCRIPT_TASKS.discard(task_id)

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


def _summarize_analysis_clips(clips: list[dict]) -> list[dict]:
    summaries = []
    for clip in clips:
        summaries.append(
            {
                "title": clip.get("title") or "",
                "start_time": clip.get("start_time") or "",
                "end_time": clip.get("end_time") or "",
                "duration_seconds": int(clip.get("duration_seconds") or 0),
            }
        )
    return summaries


def _analysis_run_row_to_dict(row: Row, include_payload: bool = False) -> dict:
    run = dict(row)
    payload = {}
    if include_payload:
        try:
            payload = json.loads(run.get("analysis_payload_json") or "{}")
        except json.JSONDecodeError:
            payload = {}

    clips = payload.get("clips") or []
    return {
        "id": run.get("id"),
        "task_id": run.get("task_id"),
        "run_number": int(run.get("run_number") or 0),
        "title": f"第 {int(run.get('run_number') or 0)} 次分析",
        "provider": run.get("provider") or "",
        "provider_label": run.get("provider_label") or _ai_provider_label(run.get("provider") or ""),
        "model": run.get("model") or "",
        "ai_prompt_preset_id": run.get("ai_prompt_preset_id") or "",
        "ai_prompt_preset_name": run.get("ai_prompt_preset_name") or "",
        "requested_clip_count": int(run.get("requested_clip_count") or 0),
        "clip_count": int(run.get("clip_count") or 0),
        "analysis_summary": run.get("analysis_summary") or "",
        "fallback_notice": run.get("fallback_notice") or "",
        "created_at": _format_datetime(run.get("created_at")),
        "created_at_raw": run.get("created_at") or "",
        "review_url": f"/tasks/{run.get('task_id')}/clips/review",
        "clips": clips if include_payload else [],
        "clip_summaries": _summarize_analysis_clips(clips) if include_payload else [],
    }


def _analysis_payload_to_preview(task_id: str, payload: dict, fallback: dict | None = None) -> dict:
    fallback = fallback or {}
    meta = payload.get("analysis_meta") or {}
    clips = payload.get("clips") or []
    provider = meta.get("provider") or fallback.get("provider") or settings.ai_default_provider
    model = meta.get("model") or fallback.get("model") or _ai_model_name(provider)
    return {
        "id": fallback.get("id") or "",
        "task_id": task_id,
        "run_number": int(fallback.get("run_number") or 0),
        "title": fallback.get("title") or "当前分析结果",
        "provider": provider,
        "provider_label": meta.get("provider_label") or fallback.get("provider_label") or _ai_provider_label(provider),
        "model": model,
        "ai_prompt_preset_id": fallback.get("ai_prompt_preset_id") or "",
        "ai_prompt_preset_name": fallback.get("ai_prompt_preset_name") or "",
        "requested_clip_count": int(fallback.get("requested_clip_count") or len(clips)),
        "clip_count": len(clips),
        "analysis_summary": payload.get("analysis_summary") or fallback.get("analysis_summary") or "",
        "fallback_notice": fallback.get("fallback_notice") or "",
        "created_at": _format_datetime(meta.get("generated_at") or fallback.get("created_at_raw")),
        "created_at_raw": meta.get("generated_at") or fallback.get("created_at_raw") or "",
        "review_url": f"/tasks/{task_id}/clips/review",
        "clips": clips,
        "clip_summaries": _summarize_analysis_clips(clips),
    }


def list_ai_analysis_runs(task_id: str) -> list[dict]:
    if not get_task(task_id, include_video_probe=False):
        raise ValueError("任务不存在")
    _ensure_ai_analysis_history_from_current_file(task_id)
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM ai_analysis_runs
            WHERE task_id = ?
            ORDER BY run_number DESC, created_at DESC
            """,
            (task_id,),
        ).fetchall()
    return [_analysis_run_row_to_dict(row, include_payload=True) for row in rows]


def get_latest_ai_analysis_run(task_id: str) -> dict | None:
    if not get_task(task_id, include_video_probe=False):
        raise ValueError("任务不存在")
    _ensure_ai_analysis_history_from_current_file(task_id)
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT *
            FROM ai_analysis_runs
            WHERE task_id = ?
            ORDER BY run_number DESC, created_at DESC
            LIMIT 1
            """,
            (task_id,),
        ).fetchone()
    if row:
        return _analysis_run_row_to_dict(row, include_payload=True)

    return None


def _next_ai_analysis_run_number(connection, task_id: str) -> int:
    row = connection.execute(
        "SELECT COALESCE(MAX(run_number), 0) AS max_run_number FROM ai_analysis_runs WHERE task_id = ?",
        (task_id,),
    ).fetchone()
    return int(row["max_run_number"] or 0) + 1


def _insert_ai_analysis_run(
    task_id: str,
    analysis_payload: dict,
    provider: str,
    provider_label: str,
    model: str,
    fallback_notice: str,
    prompt_preset: dict,
    requested_clip_count: int,
) -> dict:
    now = _now_iso()
    run_id = uuid4().hex[:12]
    clips = analysis_payload.get("clips") or []
    with get_connection() as connection:
        run_number = _next_ai_analysis_run_number(connection, task_id)
        connection.execute(
            """
            INSERT INTO ai_analysis_runs (
                id, task_id, run_number, provider, provider_label, model,
                ai_prompt_preset_id, ai_prompt_preset_name, requested_clip_count,
                clip_count, analysis_summary, fallback_notice, analysis_payload_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                task_id,
                run_number,
                provider,
                provider_label,
                model,
                prompt_preset.get("id") or "",
                prompt_preset.get("name") or "",
                requested_clip_count,
                len(clips),
                analysis_payload.get("analysis_summary") or "",
                fallback_notice,
                json.dumps(analysis_payload, ensure_ascii=False),
                now,
            ),
        )
        connection.commit()

    return get_ai_analysis_run(task_id, run_id)


def _ensure_ai_analysis_history_from_current_file(task_id: str) -> None:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS total FROM ai_analysis_runs WHERE task_id = ?",
            (task_id,),
        ).fetchone()
    if int(row["total"] or 0) > 0:
        return

    paths = get_artifact_paths(task_id)
    if not paths["analysis_path"].exists():
        return
    try:
        payload = json.loads(paths["analysis_path"].read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return

    task = get_task(task_id, include_video_probe=False)
    clips = payload.get("clips") or []
    meta = payload.get("analysis_meta") or {}
    provider = str(meta.get("provider") or settings.ai_default_provider).lower()
    prompt_preset = get_task_ai_prompt_preset(task_id)
    _insert_ai_analysis_run(
        task_id=task_id,
        analysis_payload=payload,
        provider=provider,
        provider_label=meta.get("provider_label") or _ai_provider_label(provider),
        model=meta.get("model") or _ai_model_name(provider),
        fallback_notice="",
        prompt_preset=prompt_preset,
        requested_clip_count=len(clips) or int(task.get("candidate_clip_count") or 5),
    )


def get_ai_analysis_run(task_id: str, run_id: str) -> dict:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT *
            FROM ai_analysis_runs
            WHERE task_id = ? AND id = ?
            """,
            (task_id, run_id),
        ).fetchone()
    if not row:
        raise ValueError("没有找到这条 AI 分析历史")
    return _analysis_run_row_to_dict(row, include_payload=True)


def _write_analysis_payload(task_id: str, payload: dict) -> None:
    paths = get_artifact_paths(task_id)
    paths["analysis_path"].parent.mkdir(parents=True, exist_ok=True)
    paths["analysis_path"].write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def restore_ai_analysis_run(task_id: str, run_id: str) -> dict:
    task = get_task(task_id, include_video_probe=False)
    if not task:
        raise ValueError("任务不存在")
    run = get_ai_analysis_run(task_id, run_id)
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT analysis_payload_json
            FROM ai_analysis_runs
            WHERE task_id = ? AND id = ?
            """,
            (task_id, run_id),
        ).fetchone()
    if not row:
        raise ValueError("没有找到这条 AI 分析历史")
    try:
        payload = json.loads(row["analysis_payload_json"])
    except json.JSONDecodeError as exc:
        raise ValueError("这条历史记录已损坏，无法恢复") from exc

    _write_analysis_payload(task_id, payload)
    _clear_clip_candidates(task_id)
    _insert_clip_candidates(task_id, payload.get("clips") or [])
    update_task_status(task_id, TaskStatus.pending_review)
    _append_task_log(task_id, f"已恢复 AI 分析历史：第 {run['run_number']} 次分析")

    return {
        "status": "ok",
        "message": f"已恢复第 {run['run_number']} 次 AI 分析结果。",
        "restored_run": _analysis_payload_to_preview(task_id, payload, run),
        "latest": get_latest_ai_analysis_run(task_id),
        "runs": list_ai_analysis_runs(task_id),
        "clips": list_clip_candidates(task_id),
        "task": get_task(task_id, include_video_probe=False),
    }


def _analyze_with_provider(task_id: str, task: dict, paths: dict[str, Path], provider_name: str):
    prompt_preset = get_task_ai_prompt_preset(task_id)
    prompt_template = (prompt_preset.get("prompt_text") or "").strip()
    if not prompt_template:
        raise AIAnalysisError(f"当前选择的 AI Prompt 方案“{prompt_preset.get('name')}”还没有填写 Prompt 内容")

    request = AnalysisRequest(
        task_id=task_id,
        transcript_path=paths["transcript_path"],
        max_clip_duration_minutes=int(task["max_clip_duration"]),
        target_clip_count=int(task["candidate_clip_count"]),
        ai_preference=task.get("ai_preference") or "",
        prompt_template=prompt_template,
        provider_name=provider_name,
    )
    _append_task_log(task_id, f"AI Prompt 方案：{prompt_preset.get('slot')}号 - {prompt_preset.get('name')}")
    if provider_name == "local":
        ensure_local_ai_ready()
    plan = inspect_local_analysis_plan(request)
    provider_label = _ai_provider_label(provider_name)
    _append_task_log(
        task_id,
        f"{provider_label} 将使用分段分析："
        f"{plan['chunk_count']} 段，单段约 {plan['chunk_seconds']} 秒，"
        f"最大 prompt 约 {plan['max_prompt_chars']} 字",
    )
    return analyze_task_transcript(request)


def _append_ai_clip_quality_warnings(task_id: str, clips: list[dict]) -> None:
    short_clips = []
    for clip in clips:
        try:
            duration_seconds = int(clip.get("duration_seconds") or 0)
        except (TypeError, ValueError):
            duration_seconds = 0
        if 0 < duration_seconds < AI_CLIP_MIN_RECOMMENDED_SECONDS:
            short_clips.append(
                f"{clip.get('title') or clip.get('clip_id') or '未命名片段'} {duration_seconds}秒"
            )

    if not short_clips:
        return

    preview = "、".join(short_clips[:5])
    if len(short_clips) > 5:
        preview += f" 等 {len(short_clips)} 条"
    _append_task_log(
        task_id,
        "AI 片段完整性提示："
        f"{preview} 短于建议的 {AI_CLIP_MIN_RECOMMENDED_SECONDS} 秒。"
        "这不影响切片，但如果成片仍有割裂感，建议用 2 号综艺访谈 Prompt 或把单条切片最长调到 4-6 分钟后重跑 AI。",
    )


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
        except Exception as provider_exc:
            provider_error = str(provider_exc)
            if provider_name == "remote":
                raise AIAnalysisError(
                    "远程 AI 分析接口不可用，已暂停 AI 分析："
                    f"{provider_error}。如需使用本地模型，请点击“本地 AI 分析”。"
                ) from provider_exc
            raise
        analysis_payload = result_to_jsonable(analysis)
        analysis_payload["analysis_meta"] = {
            "provider": used_provider,
            "provider_label": _ai_provider_label(used_provider),
            "model": _ai_model_name(used_provider),
            "generated_at": _now_iso(),
        }
        _write_analysis_payload(task_id, analysis_payload)
        prompt_preset = get_task_ai_prompt_preset(task_id)
        provider_label = _ai_provider_label(used_provider)
        model_name = _ai_model_name(used_provider)
        analysis_run = _insert_ai_analysis_run(
            task_id=task_id,
            analysis_payload=analysis_payload,
            provider=used_provider,
            provider_label=provider_label,
            model=model_name,
            fallback_notice=fallback_notice,
            prompt_preset=prompt_preset,
            requested_clip_count=int(task["candidate_clip_count"]),
        )
        _clear_clip_candidates(task_id)
        _insert_clip_candidates(task_id, analysis_payload["clips"])
        _append_ai_clip_quality_warnings(task_id, analysis_payload["clips"])
    except (AIAnalysisError, Exception) as exc:
        error = str(exc)
        user_error = _summarize_ai_error(error)
        update_task_status(task_id, TaskStatus.failed, user_error)
        _append_task_log(task_id, f"AI 分析失败：{error}")
        raise ValueError(user_error) from exc

    update_task_status(task_id, TaskStatus.pending_review)
    _append_task_log(task_id, f"AI 分析完成，Provider：{used_provider}，生成候选片段：{len(analysis_payload['clips'])} 条")
    message = f"AI 分析完成，已生成 {len(analysis_payload['clips'])} 条可直接切片的候选片段，可进入片段审核检查或直接生成切片。"
    if fallback_notice:
        message = f"{fallback_notice} {message}"
    return {
        "status": "ok",
        "message": message,
        "provider": used_provider,
        "provider_label": provider_label,
        "model": model_name,
        "fallback_notice": fallback_notice,
        "analysis_summary": analysis_payload.get("analysis_summary") or "",
        "clip_summaries": _summarize_analysis_clips(analysis_payload["clips"]),
        "analysis_run_id": analysis_run["id"],
        "analysis_run": analysis_run,
        "runs": list_ai_analysis_runs(task_id),
        "analysis_path": str(paths["analysis_path"]),
        "review_url": f"/tasks/{task_id}/clips/review",
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


