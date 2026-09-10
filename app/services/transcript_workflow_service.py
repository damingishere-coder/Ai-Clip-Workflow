"""转写工作流服务

从 task_service 中拆分出来的语音转写、音频提取、转写进度管理函数。
"""

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.models.task import TaskStatus
from app.services import job_service
from app.services.local_transcription_runtime import (
    ensure_transcription_provider_allowed,
    get_local_transcription_runtime_status,
)
from app.services.storage_service import get_artifact_paths, get_source_video_path, validate_source_video_path
from app.services.task_log_service import append_task_log
from app.services.transcript_service import (
    TranscriptCancelledError,
    cleanup_transcript_chunk_dirs,
    read_transcript_preview,
    read_transcript_progress,
    run_ffmpeg_audio_extract,
    write_transcript_markdown,
    write_transcript_progress,
)

# ---------- 模块级状态 ----------

_RUNNING_TRANSCRIPT_TASKS: set[str] = set()
_CANCEL_TRANSCRIPT_TASKS: set[str] = set()


_TRANSCRIPT_STALE_AFTER = timedelta(minutes=10)
# ---------- 转写进度辅助 ----------

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
    configured_provider = (settings.transcription_provider or "").strip().lower()
    choice = (provider or configured_provider or "local").strip().lower()
    if choice == "remote":
        choice = configured_provider if configured_provider and configured_provider != "local" else "volcengine"
    if choice not in {"local", "volcengine", "aliyun", "tencent", "xunfei"}:
        raise ValueError(f"未知转写服务商：{choice or '空'}")
    return ensure_transcription_provider_allowed(choice)


def validate_transcription_provider_choice(provider: str | None = None) -> str:
    """在创建持久化 Job 前验证 Provider 和完全离线边界。"""
    return _resolve_transcription_provider_choice(provider)


def _transcription_choice_label(provider: str) -> str:
    if provider == "local":
        return "本地 faster-whisper"
    if provider == "volcengine":
        return "火山引擎远程转写"
    return provider or "远程转写"


def _can_retry_transcript_with_local(progress: dict, transcript_exists: bool) -> bool:
    if settings.transcription_offline_only:
        return False
    provider = str(progress.get("provider") or "").strip().lower()
    return not transcript_exists and progress.get("status") == "failed" and provider != "local"


def _finalize_cancelled_transcript_task(task_id: str, paths: dict[str, Path], progress: dict) -> dict:
    from app.services.task_service import update_task_status  # noqa: F811

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
    append_task_log(task_id, "已自动收尾停止转写请求，任务可重新生成转写")
    return cancelled_progress


# ---------- 转写查询 ----------

def get_transcript_preview(task_id: str) -> list[dict[str, str]]:
    paths = get_artifact_paths(task_id)
    return read_transcript_preview(paths["transcript_path"])


def get_task_transcript_status(task_id: str) -> dict:
    from app.services.task_service import get_task  # noqa: F811

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
            "message": "转写进度长时间没有更新，可能已经卡住。请重新点击\"生成转写 MD\"。",
        }

    transcript_exists = paths["transcript_path"].exists()
    runtime = get_local_transcription_runtime_status()
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
        "offline_only": runtime["offline_only"],
        "model_ready": runtime["model_ready"],
        "gpu_ready": runtime["gpu_ready"],
        "model_revision": runtime["model_revision"],
    }


# ---------- 音频提取 ----------

def process_task_audio(task_id: str, job_id: str | None = None) -> dict:
    from app.services.task_service import get_task, update_task_status  # noqa: F811

    task = get_task(task_id)
    if not task:
        raise ValueError("任务不存在")
    source_path = get_source_video_path(task)
    valid, error_message = validate_source_video_path(str(source_path) if source_path else None)
    if not valid:
        update_task_status(task_id, TaskStatus.failed, error_message)
        append_task_log(task_id, f"音频提取失败：{error_message}")
        raise ValueError(error_message)

    paths = get_artifact_paths(task_id)
    update_task_status(task_id, TaskStatus.audio_extracting)
    append_task_log(task_id, "开始使用 FFmpeg 提取音频")
    try:
        cancel_check = None
        audio_progress_callback = None
        if job_id:
            def cancel_check() -> bool:
                return job_service.is_cancel_requested(job_id)

            def audio_progress_callback(percent: int) -> None:
                job_service.update_job_progress(
                    job_id, max(1, min(20, round(percent * 0.2))), f"正在提取音频：{percent}%"
                )
                job_service.heartbeat_job(job_id)
        result = run_ffmpeg_audio_extract(
            source_path,
            paths["audio_path"],
            cancel_check=cancel_check,
            progress_callback=audio_progress_callback,
        )
    except job_service.JobLeaseLostError:
        raise
    except Exception as exc:
        error = str(exc)
        update_task_status(task_id, TaskStatus.failed, error)
        append_task_log(task_id, f"音频提取失败：{error}")
        raise

    update_task_status(task_id, TaskStatus.transcribing)
    append_task_log(task_id, f"音频提取完成：{paths['audio_path']}")
    return {**result, "task": get_task(task_id)}


# ---------- 转写流程 ----------

def process_task_transcript(
    task_id: str,
    background_tasks: Any | None = None,
    provider: str | None = None,
    job_id: str | None = None,
    allow_uncertain_retry: bool = False,
) -> dict:
    from app.services.task_service import get_task, update_task_status  # noqa: F811

    task = get_task(task_id)
    if not task:
        raise ValueError("任务不存在")
    paths = get_artifact_paths(task_id)
    if not paths["audio_path"].exists():
        error = "请先完成音频提取，再生成转写 Markdown"
        update_task_status(task_id, TaskStatus.failed, error)
        append_task_log(task_id, f"转写失败：{error}")
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
        append_task_log(task_id, "发现过期的后台转写状态，准备重新开始转写")

    provider_name = _resolve_transcription_provider_choice(provider)
    provider_label = _transcription_choice_label(provider_name)
    removed_dirs = cleanup_transcript_chunk_dirs(paths["transcript_path"])
    if removed_dirs:
        append_task_log(task_id, f"已清理旧的转写临时目录：{removed_dirs} 个")
    update_task_status(task_id, TaskStatus.transcribing)
    write_transcript_progress(
        paths["transcript_path"],
        status="running",
        current_chunk=0,
        total_chunks=0,
        percent=1,
        message=f"后台分段转写已启动，正在准备{provider_label}环境",
    )
    append_task_log(task_id, f"开始后台分段语音转写，Provider：{provider_name}")
    _CANCEL_TRANSCRIPT_TASKS.discard(task_id)
    _RUNNING_TRANSCRIPT_TASKS.add(task_id)
    if background_tasks is not None:
        background_tasks.add_task(
            _run_task_transcript_background,
            task_id,
            provider_name,
            job_id,
            allow_uncertain_retry,
        )
    else:
        _run_task_transcript_background(task_id, provider_name, job_id, allow_uncertain_retry)
    return {
        "status": "started",
        "message": f"已开始{provider_label}分段转写，请稍后刷新查看进度。",
        "provider": provider_name,
        "provider_label": provider_label,
        "task": get_task(task_id),
    }


def cancel_task_transcript(task_id: str) -> dict:
    from app.services.task_service import get_task  # noqa: F811

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
    append_task_log(task_id, "用户请求停止当前转写任务")
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
    job_id: str | None = None,
) -> dict:
    from app.services.task_service import get_task  # noqa: F811

    task = get_task(task_id)
    if not task:
        raise ValueError("任务不存在")
    paths = get_artifact_paths(task_id)

    if paths["transcript_path"].exists() and not force:
        if read_transcript_preview(paths["transcript_path"], max_lines=1):
            return {
                "status": "completed",
                "message": "转写 Markdown 已经生成，无需重复处理。如需重做，请点击\"重新生成转写\"。",
                "task": get_task(task_id),
            }
        append_task_log(task_id, "检测到空白、截断或不可解析的转写 Markdown，本次不会误判为已完成")

    if not paths["audio_path"].exists():
        append_task_log(task_id, "一键处理：未发现音频文件，先自动提取音频")
        process_task_audio(task_id, job_id=job_id)

    if force:
        append_task_log(task_id, "用户明确要求重新生成转写 Markdown")

    return process_task_transcript(
        task_id,
        background_tasks=background_tasks,
        provider=provider,
        job_id=job_id,
        allow_uncertain_retry=force,
    )


def _run_task_transcript_background(
    task_id: str,
    provider: str | None = None,
    job_id: str | None = None,
    allow_uncertain_retry: bool = False,
) -> None:
    from app.services.task_service import get_task, update_task_status  # noqa: F811

    task = get_task(task_id)
    if not task:
        _RUNNING_TRANSCRIPT_TASKS.discard(task_id)
        return
    paths = get_artifact_paths(task_id)
    provider_name = _resolve_transcription_provider_choice(provider)
    provider_label = _transcription_choice_label(provider_name)

    def progress_callback(progress: dict) -> None:
        if job_id:
            if job_service.is_cancel_requested(job_id):
                raise TranscriptCancelledError("用户已停止当前转写任务")
            percent = int(progress.get("percent") or 0)
            job_percent = 20 + round(max(0, min(100, percent)) * 0.79)
            job_service.update_job_progress(job_id, job_percent, str(progress.get("message") or "转写中"))
            job_service.heartbeat_job(job_id)
        if task_id in _CANCEL_TRANSCRIPT_TASKS:
            raise TranscriptCancelledError("用户已停止当前转写任务")
        message = progress.get("message") or "转写进度已更新"
        current_chunk = int(progress.get("current_chunk") or 0)
        total_chunks = int(progress.get("total_chunks") or 0)
        percent = int(progress.get("percent") or 0)
        if total_chunks:
            append_task_log(task_id, f"{message}（{current_chunk}/{total_chunks}，{percent}%）")
        else:
            append_task_log(task_id, message)

    try:
        if task_id in _CANCEL_TRANSCRIPT_TASKS:
            raise TranscriptCancelledError("用户已停止当前转写任务")
        write_transcript_markdown(
            task,
            paths["audio_path"],
            paths["transcript_path"],
            progress_callback=progress_callback,
            provider=provider_name,
            allow_uncertain_retry=allow_uncertain_retry,
        )
    except job_service.JobLeaseLostError:
        raise
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
        append_task_log(task_id, str(exc))
        return
    except Exception as exc:
        error = str(exc)
        last_progress = read_transcript_progress(paths["transcript_path"])
        if provider_name == "local":
            user_message = f"本地转写失败：{error}"
        else:
            user_message = f"{provider_label}不可用，已暂停转写：{error}。如需改用本地模型，请点击\"改用本地模型转写\"。"
        write_transcript_progress(
            paths["transcript_path"],
            status="failed",
            current_chunk=int(last_progress.get("current_chunk") or 0),
            total_chunks=int(last_progress.get("total_chunks") or 0),
            percent=int(last_progress.get("percent") or 0),
            message=user_message,
        )
        update_task_status(task_id, TaskStatus.failed, user_message)
        append_task_log(task_id, f"转写失败：{user_message}")
        return
    finally:
        _RUNNING_TRANSCRIPT_TASKS.discard(task_id)
        _CANCEL_TRANSCRIPT_TASKS.discard(task_id)

    update_task_status(task_id, TaskStatus.pending_ai)
    append_task_log(task_id, f"真实转写 Markdown 已生成：{paths['transcript_path']}")
