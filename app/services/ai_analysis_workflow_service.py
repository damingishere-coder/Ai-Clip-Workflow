"""AI 分析工作流服务

从 task_service 中拆分出来的 AI 片段分析、历史记录管理和结果回滚函数。
"""

import json
from pathlib import Path
from sqlite3 import Row
from uuid import uuid4

from app.core.config import settings
from app.db.database import get_connection
from app.models.task import TaskStatus
from app.services.ai.ai_clip_analyzer import (
    AIAnalysisError,
    AnalysisRequest,
    analyze_task_transcript,
    inspect_local_analysis_plan,
    result_to_jsonable,
)
from app.services.ai.variety_comedy_analyzer import ComedyAnalysisRequest, analyze_variety_comedy
from app.services.ai.long_live_talk_analyzer import (
    LongLiveAnalysisOutcome,
    LongLiveAnalysisRequest,
    analyze_long_live_talk,
    get_latest_long_live_window_status,
)
from app.services.ai.diagnostics import ensure_local_ai_ready
from app.services.ai.base import AIProviderError
from app.services.ai_prompt_preset_service import get_task_ai_prompt_preset
from app.services.storage_service import get_artifact_paths
from app.services.task_log_service import append_task_log, read_task_log_tail

AI_CLIP_MIN_RECOMMENDED_SECONDS = 45


class AIAnalysisConflictError(ValueError):
    """当前任务已有执行中或下游结果，拒绝破坏性 AI 重入。"""


# ---------- AI Provider 辅助 ----------

def _ai_model_name(provider_name: str) -> str:
    if provider_name == "codex":
        return settings.ai_codex_model
    if provider_name == "local":
        return settings.ai_local_model
    if provider_name == "remote":
        return settings.ai_analysis_remote_model
    return settings.ai_analysis_remote_model


def _ai_provider_label(provider_name: str) -> str:
    if provider_name == "codex":
        return "Codex CLI"
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


def get_task_ai_analysis_meta(task_id: str) -> dict:
    """读取当前生效分析的元数据，供长直播覆盖率门禁复用。"""
    return dict(_read_analysis_meta(task_id))


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


# ---------- AI 分析状态 ----------

def get_task_ai_analysis_status(task_id: str) -> dict:
    from app.services.task_service import get_task  # noqa: F811

    task = get_task(task_id, include_video_probe=False)
    if not task:
        raise ValueError("任务不存在")

    paths = get_artifact_paths(task_id)
    log_lines = read_task_log_tail(task_id)
    is_running = task.get("status") == TaskStatus.ai_analyzing.value
    has_analysis = paths["analysis_path"].exists()
    window_status = (
        get_latest_long_live_window_status(task_id)
        if task.get("selection_profile") == "long_live_talk"
        else {}
    )

    percent = 0
    message = "等待开始 AI 分析"
    status = "idle"
    if is_running:
        status = "running"
        percent = int(window_status.get("percent") or 0) or 1
        message = (
            f"长直播 AI 正在处理窗口：已完成 {window_status.get('completed_window_count', 0)}"
            f"/{window_status.get('window_count', 0)}。"
            if window_status
            else "AI 正在分析转写文本，请保持页面打开。"
        )
        if any("将使用分段分析" in line for line in log_lines):
            percent = 62
            message = "AI 已读取 Prompt 和转写文本，正在分段生成候选片段。"
        if any("远程 AI 分析接口不可用" in line for line in log_lines):
            percent = 72
            message = "远程 AI 分析接口暂不可用，已暂停等待你确认下一步。"
    elif task.get("status") == TaskStatus.pending_review.value and has_analysis:
        meta = _read_analysis_meta(task_id)
        status = "incomplete" if meta.get("analysis_incomplete") else "completed"
        percent = int(float(meta.get("coverage_percent") or 100))
        message = (
            f"长直播分析覆盖 {float(meta.get('coverage_percent') or 0):.2f}%，"
            "仍有窗口失败；请重试 AI 分析补齐窗口。"
            if meta.get("analysis_incomplete")
            else "AI 分析完成，候选片段已生成，可检查后直接生成切片。"
        )
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
        "window_status": window_status,
    }


# ---------- 候选片段数据库操作 ----------

def _clear_clip_candidates(task_id: str) -> None:
    from app.db.database import get_connection

    with get_connection() as connection:
        connection.execute("DELETE FROM clip_candidates WHERE task_id = ?", (task_id,))
        connection.commit()


def _insert_clip_candidates(task_id: str, clips: list[dict]) -> None:
    from app.services.task_service import _now_iso

    now = _now_iso()
    with get_connection() as connection:
        _insert_clip_candidates_with_connection(connection, task_id, clips, now)
        connection.commit()


def _insert_clip_candidates_with_connection(connection, task_id: str, clips: list[dict], now: str) -> None:
    for index, clip in enumerate(clips, start=1):
        clip_key = str(clip["clip_id"])
        database_id = f"{task_id}_{clip_key}"[:120]
        selected_by_default = bool(clip.get("selected_by_default", True))
        connection.execute(
            """
            INSERT INTO clip_candidates (
                id, task_id, clip_key, title, start_time, end_time, duration_seconds,
                cover_time_seconds, summary, reason, highlight_reason, spread_value, suggested_editing,
                confidence_score, quality_tier, quality_score, text_quality_score, humor_score,
                completeness_score, audio_reaction_score, topic_key, key_moment_time,
                quality_evidence_json, rejection_reason,
                selected_by_default, enabled, reviewed, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                database_id or f"{task_id}_clip_{index:03d}",
                task_id,
                clip_key,
                clip["title"],
                clip["start_time"],
                clip["end_time"],
                clip["duration_seconds"],
                clip.get("cover_time_seconds"),
                clip["summary"],
                clip["highlight_reason"],
                clip["highlight_reason"],
                clip["spread_value"],
                clip["suggested_editing"],
                clip["confidence_score"],
                clip.get("quality_tier") or "",
                float(clip.get("quality_score") or 0),
                float(clip.get("text_quality_score") or 0),
                float(clip.get("humor_score") or 0),
                float(clip.get("completeness_score") or 0),
                float(clip.get("audio_reaction_score") or 0),
                clip.get("topic_key") or "",
                clip.get("key_moment_time") or "",
                json.dumps(clip.get("quality_evidence") or {}, ensure_ascii=False),
                clip.get("rejection_reason") or "",
                1 if selected_by_default else 0,
                1 if selected_by_default else 0,
                0,
                now,
                now,
            ),
        )


def _replace_clip_candidates(task_id: str, clips: list[dict]) -> None:
    """在同一个事务里替换候选片段，失败时保留原结果。"""
    from app.services.task_service import _now_iso

    now = _now_iso()
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        referenced = connection.execute(
            """
            SELECT 1
            FROM output_clip
            WHERE task_id = ? AND clip_candidate_id IS NOT NULL
            LIMIT 1
            """,
            (task_id,),
        ).fetchone()
        if referenced:
            connection.rollback()
            raise AIAnalysisConflictError(
                "任务已有切片引用当前候选结果，不能直接覆盖 AI 分析；"
                "请在片段审核页修改现有候选并重新切片。"
            )
        connection.execute("DELETE FROM clip_candidates WHERE task_id = ?", (task_id,))
        _insert_clip_candidates_with_connection(connection, task_id, clips, now)
        connection.commit()


def _pipeline_lease_belongs_to_task(task_id: str) -> bool:
    from app.services import job_service

    active_lease = job_service.current_job_lease()
    if not active_lease:
        return False
    if not job_service.validate_job_lease(active_lease[0], active_lease[1], active_lease[2]):
        return False
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT 1
            FROM workflow_jobs
            WHERE id = ? AND task_id = ? AND job_type = ? AND status = ?
              AND lease_owner = ? AND lease_token = ?
            """,
            (
                active_lease[0],
                task_id,
                job_service.JOB_TYPE_AUTO_PIPELINE,
                job_service.JOB_STATUS_RUNNING,
                active_lease[1],
                active_lease[2],
            ),
        ).fetchone()
    return row is not None


def _claim_ai_analysis(task_id: str, task: dict) -> bool:
    """自动流水线沿用租约；手动请求则原子校验并占用 ai_analyzing 状态。"""
    from app.services import job_service
    from app.services.task_service import STATUS_PROGRESS, _now_iso

    if _pipeline_lease_belongs_to_task(task_id):
        return False
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        current = connection.execute(
            "SELECT status FROM tasks WHERE id = ? AND COALESCE(is_deleted, 0) = 0",
            (task_id,),
        ).fetchone()
        if not current:
            connection.rollback()
            raise AIAnalysisConflictError("任务不存在或已被删除，不能启动 AI 分析。")
        active_pipeline = connection.execute(
            """
            SELECT 1
            FROM workflow_jobs
            WHERE task_id = ? AND job_type = ? AND status IN (?, ?)
            LIMIT 1
            """,
            (
                task_id,
                job_service.JOB_TYPE_AUTO_PIPELINE,
                job_service.JOB_STATUS_QUEUED,
                job_service.JOB_STATUS_RUNNING,
            ),
        ).fetchone()
        materialized = connection.execute(
            """
            SELECT 1
            WHERE EXISTS (
                SELECT 1 FROM output_clip
                WHERE task_id = ? AND COALESCE(is_active, 1) = 1
            ) OR EXISTS (
                SELECT 1 FROM publish_jobs WHERE task_id = ?
            )
            """,
            (task_id, task_id),
        ).fetchone()
        current_status = str(current["status"] or "")
        if active_pipeline:
            connection.rollback()
            raise AIAnalysisConflictError("全自动流水线正在处理此任务，请等待当前流程完成，不要重复启动 AI 分析。")
        if materialized:
            connection.rollback()
            raise AIAnalysisConflictError(
                "任务已经生成切片或进入发送中心，不能覆盖 AI 候选；请在片段审核页修改后重新切片。"
            )
        if current_status in {TaskStatus.ai_analyzing.value, TaskStatus.AI_ANALYZING.value}:
            connection.rollback()
            raise AIAnalysisConflictError("AI 分析已经在运行，请等待当前分析完成。")
        if current_status != str(task.get("status") or ""):
            connection.rollback()
            raise AIAnalysisConflictError("任务状态刚刚发生变化，请刷新页面后再决定是否重新分析。")
        if current_status not in {
            TaskStatus.pending_ai.value,
            TaskStatus.pending_review.value,
            TaskStatus.failed.value,
        }:
            connection.rollback()
            raise AIAnalysisConflictError("当前任务阶段不允许手动启动 AI 分析，请从任务详情继续正确流程。")
        connection.execute(
            """
            UPDATE tasks
            SET status = ?, progress = ?, error_message = NULL, last_error = NULL, updated_at = ?
            WHERE id = ? AND status = ? AND COALESCE(is_deleted, 0) = 0
            """,
            (
                TaskStatus.ai_analyzing.value,
                STATUS_PROGRESS[TaskStatus.ai_analyzing.value],
                _now_iso(),
                task_id,
                current_status,
            ),
        )
        connection.commit()
    return True


def _restore_task_after_ai_conflict(task_id: str, previous_task: dict) -> None:
    """只回滚仍停留在本次 ai_analyzing 的状态，避免覆盖其他流程的新状态。"""
    from app.services.task_service import _now_iso

    with get_connection() as connection:
        connection.execute(
            """
            UPDATE tasks
            SET status = ?, progress = ?, error_message = ?, last_error = ?, updated_at = ?
            WHERE id = ? AND status = ? AND COALESCE(is_deleted, 0) = 0
            """,
            (
                previous_task.get("status") or TaskStatus.pending_review.value,
                int(previous_task.get("progress") or 0),
                previous_task.get("error_message") or None,
                previous_task.get("last_error") or None,
                _now_iso(),
                task_id,
                TaskStatus.ai_analyzing.value,
            ),
        )
        connection.commit()


def _mark_ai_failed_if_still_running(task_id: str, error_message: str) -> None:
    """AI 失败只能结束自己仍持有的可见状态，不能降级已完成任务。"""
    from app.services.task_service import STATUS_PROGRESS, _now_iso

    with get_connection() as connection:
        connection.execute(
            """
            UPDATE tasks
            SET status = ?, progress = ?, error_message = ?, last_error = ?, updated_at = ?
            WHERE id = ? AND status = ? AND COALESCE(is_deleted, 0) = 0
            """,
            (
                TaskStatus.failed.value,
                STATUS_PROGRESS[TaskStatus.failed.value],
                error_message,
                error_message,
                _now_iso(),
                task_id,
                TaskStatus.ai_analyzing.value,
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
                "cover_time_seconds": clip.get("cover_time_seconds"),
            }
        )
    return summaries


# ---------- AI 分析历史记录 ----------

def _analysis_run_row_to_dict(row: Row, include_payload: bool = False) -> dict:
    from app.services.task_service import _format_datetime

    run = dict(row)
    payload = {}
    if include_payload:
        try:
            payload = json.loads(run.get("analysis_payload_json") or "{}")
        except json.JSONDecodeError:
            payload = {}

    clips = payload.get("clips") or []
    analysis_meta = payload.get("analysis_meta") or {}
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
        "analysis_meta": analysis_meta if include_payload else {},
        "analysis_incomplete": bool(analysis_meta.get("analysis_incomplete")),
        "coverage_ratio": float(analysis_meta.get("coverage_ratio") or 0),
    }


def _analysis_payload_to_preview(task_id: str, payload: dict, fallback: dict | None = None) -> dict:
    from app.services.task_service import _format_datetime

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
    from app.services.task_service import get_task  # noqa: F811

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
    from app.services.task_service import get_task  # noqa: F811

    if not get_task(task_id, include_video_probe=False):
        raise ValueError("任务不存在")
    _ensure_ai_analysis_history_from_current_file(task_id)
    with get_connection() as connection:
        # 优先查找 active run，其次最新 run_number
        row = connection.execute(
            """
            SELECT *
            FROM ai_analysis_runs
            WHERE task_id = ?
            ORDER BY is_active DESC, run_number DESC, created_at DESC
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
    from app.services.task_service import _now_iso

    now = _now_iso()
    run_id = uuid4().hex[:12]
    clips = analysis_payload.get("clips") or []
    with get_connection() as connection:
        run_number = _next_ai_analysis_run_number(connection, task_id)
        # 将同 task 下所有旧 run 标记为非活跃
        connection.execute(
            "UPDATE ai_analysis_runs SET is_active = 0 WHERE task_id = ?",
            (task_id,),
        )
        connection.execute(
            """
            INSERT INTO ai_analysis_runs (
                id, task_id, run_number, provider, provider_label, model,
                ai_prompt_preset_id, ai_prompt_preset_name, requested_clip_count,
                clip_count, analysis_summary, fallback_notice, analysis_payload_json,
                is_active, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                1,  # is_active
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

    from app.services.task_service import get_task  # noqa: F811

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
        requested_clip_count=len(clips) or int(task.get("candidate_clip_count") or 12),
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
    from app.services.task_service import (
        get_task,  # noqa: F811
        list_clip_candidates,
        update_task_status,
    )

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

    _replace_clip_candidates(task_id, payload.get("clips") or [])
    _write_analysis_payload(task_id, payload)
    # 切换 active 到被恢复的 run
    with get_connection() as connection:
        connection.execute(
            "UPDATE ai_analysis_runs SET is_active = 0 WHERE task_id = ?",
            (task_id,),
        )
        connection.execute(
            "UPDATE ai_analysis_runs SET is_active = 1 WHERE id = ?",
            (run_id,),
        )
        connection.commit()
    update_task_status(task_id, TaskStatus.pending_review)
    append_task_log(task_id, f"已恢复 AI 分析历史：第 {run['run_number']} 次分析")

    return {
        "status": "ok",
        "message": f"已恢复第 {run['run_number']} 次 AI 分析结果。",
        "restored_run": _analysis_payload_to_preview(task_id, payload, run),
        "latest": get_latest_ai_analysis_run(task_id),
        "runs": list_ai_analysis_runs(task_id),
        "clips": list_clip_candidates(task_id),
        "task": get_task(task_id, include_video_probe=False),
    }


# ---------- AI 分析核心流程 ----------

def _analyze_with_provider(task_id: str, task: dict, paths: dict[str, Path], provider_name: str):
    prompt_preset = get_task_ai_prompt_preset(task_id)
    prompt_template = (prompt_preset.get("prompt_text") or "").strip()
    if not prompt_template:
        raise AIAnalysisError(f"当前选择的 AI Prompt 方案\"{prompt_preset.get('name')}\"还没有填写 Prompt 内容")

    append_task_log(task_id, f"AI Prompt 方案：{prompt_preset.get('slot')}号 - {prompt_preset.get('name')}")
    if provider_name == "local":
        ensure_local_ai_ready()

    if task.get("selection_profile") == "variety_comedy":
        window_seconds = 180 if provider_name == "local" else 300
        overlap_seconds = 45 if provider_name == "local" else 60
        append_task_log(
            task_id,
            "综艺笑点优先 V2："
            f"{window_seconds // 60} 分钟重叠召回窗口，重叠 {overlap_seconds} 秒；"
            f"候选池最多 {min(12, int(task['candidate_clip_count']))} 条，"
            f"最终最多启用 {int(task.get('final_clip_target') or 5)} 条 A 级片段",
        )
        return analyze_variety_comedy(
            ComedyAnalysisRequest(
                task_id=task_id,
                transcript_path=paths["transcript_path"],
                audio_path=paths["audio_path"],
                candidate_pool_limit=int(task["candidate_clip_count"]),
                final_clip_target=int(task.get("final_clip_target") or 5),
                ai_preference=task.get("ai_preference") or "",
                prompt_template=prompt_template,
                provider_name=provider_name,
            )
        )

    if task.get("selection_profile") == "long_live_talk":
        density = max(1, min(10, int(task.get("highlight_density_per_hour") or 4)))
        total_limit = max(1, min(50, int(task.get("highlight_total_limit") or 30)))
        append_task_log(
            task_id,
            "长直播高光：使用约 5 分钟、重叠 60 秒的可恢复窗口；"
            f"每小时最多 {density} 条，总计最多 {total_limit} 条。",
        )

        def report_progress(progress: dict) -> None:
            window_index = int(progress.get("window_index") or 0)
            window_count = int(progress.get("window_count") or 0)
            status = str(progress.get("status") or "")
            if status in {"failed", "reused"} or window_index in {1, window_count} or window_index % 10 == 0:
                label = {"failed": "失败", "reused": "复用", "completed": "完成"}.get(status, status)
                append_task_log(task_id, f"长直播 AI 窗口 {window_index}/{window_count}：{label}")

        return analyze_long_live_talk(
            LongLiveAnalysisRequest(
                task_id=task_id,
                transcript_path=paths["transcript_path"],
                provider_name=provider_name,
                model_name=_ai_model_name(provider_name),
                density_per_hour=density,
                total_limit=total_limit,
                ai_preference=task.get("ai_preference") or "",
                prompt_template=prompt_template,
            ),
            progress_callback=report_progress,
        )

    request = AnalysisRequest(
        task_id=task_id,
        transcript_path=paths["transcript_path"],
        max_clip_duration_minutes=int(task["max_clip_duration"]),
        target_clip_count=int(task["candidate_clip_count"]),
        ai_preference=task.get("ai_preference") or "",
        prompt_template=prompt_template,
        provider_name=provider_name,
    )
    plan = inspect_local_analysis_plan(request)
    provider_label = _ai_provider_label(provider_name)
    append_task_log(
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
    append_task_log(
        task_id,
        "AI 片段完整性提示："
        f"{preview} 短于建议的 {AI_CLIP_MIN_RECOMMENDED_SECONDS} 秒。"
        "这不影响切片，但如果成片仍有割裂感，建议用 2 号综艺访谈 Prompt 或把单条切片最长调到 4-6 分钟后重跑 AI。",
    )


def process_task_ai_analysis(task_id: str, provider: str | None = None) -> dict:
    from app.services.task_service import (
        _now_iso,
        get_task,
        list_clip_candidates,
        update_task_status,
    )

    task = get_task(task_id, include_video_probe=False)
    if not task:
        raise ValueError("任务不存在")
    manual_claimed = _claim_ai_analysis(task_id, task)

    paths = get_artifact_paths(task_id)
    if not paths["transcript_path"].exists():
        error = "请先生成带时间戳的转写 Markdown，再开始 AI 分析"
        update_task_status(task_id, TaskStatus.failed, error)
        append_task_log(task_id, f"AI 分析失败：{error}")
        raise ValueError(error)

    provider_name = (provider or settings.ai_default_provider).lower()
    if not manual_claimed:
        update_task_status(task_id, TaskStatus.ai_analyzing)
    append_task_log(task_id, f"开始 AI 片段分析，Provider：{provider_name}")

    used_provider = provider_name
    fallback_notice = ""
    try:
        try:
            analysis = _analyze_with_provider(task_id, task, paths, provider_name)
        except Exception as provider_exc:
            provider_error = (
                provider_exc.checkpoint_message()
                if isinstance(provider_exc, AIProviderError)
                else str(provider_exc)
            )
            if provider_name == "remote":
                raise AIAnalysisError(
                    "远程 AI 分析接口不可用，已暂停 AI 分析："
                    f"{provider_error}。如需使用本地模型，请点击\"本地 AI 分析\"。"
                ) from provider_exc
            raise
        long_live_meta = {}
        if isinstance(analysis, LongLiveAnalysisOutcome):
            long_live_meta = analysis.meta
            analysis = analysis.result
        analysis_payload = result_to_jsonable(analysis)
        analysis_payload["analysis_meta"] = {
            "provider": used_provider,
            "provider_label": _ai_provider_label(used_provider),
            "model": _ai_model_name(used_provider),
            "selection_profile": task.get("selection_profile") or "general",
            "final_clip_target": int(task.get("final_clip_target") or 5),
            "generated_at": _now_iso(),
            **long_live_meta,
        }
        prompt_preset = get_task_ai_prompt_preset(task_id)
        provider_label = _ai_provider_label(used_provider)
        model_name = _ai_model_name(used_provider)
        _replace_clip_candidates(task_id, analysis_payload["clips"])
        _write_analysis_payload(task_id, analysis_payload)
        # 插入新的 AI 分析历史 run（自动标记 is_active=1，旧 run 取消激活）
        analysis_run = _insert_ai_analysis_run(
            task_id=task_id,
            analysis_payload=analysis_payload,
            provider=used_provider,
            provider_label=provider_label,
            model=model_name,
            fallback_notice=fallback_notice,
            prompt_preset=prompt_preset,
            requested_clip_count=(
                int(task.get("highlight_total_limit") or 30)
                if task.get("selection_profile") == "long_live_talk"
                else int(task["candidate_clip_count"])
            ),
        )
        _append_ai_clip_quality_warnings(task_id, analysis_payload["clips"])
    except AIAnalysisConflictError:
        _restore_task_after_ai_conflict(task_id, task)
        append_task_log(task_id, "AI 分析已停止：任务已有下游切片引用，原任务状态和数据保持不变")
        raise
    except Exception as exc:
        error = str(exc)
        user_error = _summarize_ai_error(error)
        _mark_ai_failed_if_still_running(task_id, user_error)
        append_task_log(task_id, f"AI 分析失败：{error}")
        raise ValueError(user_error) from exc

    update_task_status(task_id, TaskStatus.pending_review)
    incomplete = bool(analysis_payload.get("analysis_meta", {}).get("analysis_incomplete"))
    if incomplete:
        coverage = float(analysis_payload["analysis_meta"].get("coverage_percent") or 0)
        append_task_log(
            task_id,
            f"长直播 AI 分析不完整：覆盖率 {coverage:.2f}%，已保留成功窗口，自动切片已锁定。",
        )
        message = (
            f"长直播分析覆盖率为 {coverage:.2f}%，低于 90%。"
            "成功窗口已保存，请重试 AI 分析补齐缺失窗口；补齐前不能进入自动切片。"
        )
    else:
        append_task_log(task_id, f"AI 分析完成，Provider：{used_provider}，生成候选片段：{len(analysis_payload['clips'])} 条")
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
