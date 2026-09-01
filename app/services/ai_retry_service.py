"""不完整 AI 分析的显式确认与安全恢复。"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from app.db.database import get_connection
from app.models.task import TaskStatus
from app.services import job_service
from app.services.storage_service import get_artifact_paths


_AI_UNIT_CHECKPOINT_KEY = "_ai_analysis_units_v1"
_SAFE_RETAINED_STEPS = (
    TaskStatus.PREPARING_SOURCE.value,
    TaskStatus.TRANSCRIBING.value,
)


class AIAnalysisRetryConfirmationRequired(ValueError):
    """AI 单元可能已计费，必须由用户显式确认。"""

    def __init__(self, detail: dict[str, Any]) -> None:
        super().__init__(str(detail.get("message") or "AI 重试需要确认"))
        self.detail = detail


class AutoPipelineRetryConflictError(ValueError):
    """当前任务不满足安全重试前提。"""

    def __init__(self, message: str, *, code: str = "auto_retry_conflict") -> None:
        super().__init__(message)
        self.detail = {"code": code, "message": message}


def get_ai_retry_confirmation(task_id: str) -> dict[str, Any] | None:
    """返回待确认信息；没有计费不确定单元时返回 ``None``。"""
    with get_connection() as connection:
        active = _active_auto_job(connection, task_id)
        if active:
            return None
        row = _latest_failed_auto_job(connection, task_id)
        if not row:
            return None
        checkpoint = _decode_checkpoint(row["checkpoint_json"])
        uncertain_units = _uncertain_units(checkpoint)
        if not uncertain_units:
            return None
        _require_no_downstream_records(connection, task_id)
        retry_mode = _resolve_retry_mode(task_id, checkpoint)
        return _confirmation_detail(
            task_id=task_id,
            job_id=str(row["id"]),
            uncertain_count=len(uncertain_units),
            retry_mode=retry_mode,
        )


def prepare_confirmed_ai_retry(task_id: str) -> tuple[dict, bool, dict[str, Any]]:
    """在同一事务中重新验证前提并准备已确认的 AI 重试。"""
    now = _now_iso()
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        active = _active_auto_job(connection, task_id)
        if active:
            connection.commit()
            job = job_service.get_job(str(active["id"]))
            return job, False, {
                "retry_mode": "active",
                "uncertain_unit_count": 0,
                "restart_step": TaskStatus.AI_ANALYZING.value,
            }

        row = _latest_failed_auto_job(connection, task_id)
        if not row:
            connection.rollback()
            raise AutoPipelineRetryConflictError("没有可恢复的失败全自动 Job")
        checkpoint = _decode_checkpoint(row["checkpoint_json"])
        uncertain_units = _uncertain_units(checkpoint)
        if not uncertain_units:
            connection.rollback()
            raise AutoPipelineRetryConflictError("失败 Job 不包含需要确认的 AI 单元")
        _require_no_downstream_records(connection, task_id)
        retry_mode = _resolve_retry_mode(task_id, checkpoint)
        _prepare_task_for_ai_retry(connection, task_id, now=now)

        if retry_mode == "fresh_ai":
            new_job_id, created = job_service.create_or_get_active_job_with_connection(
                connection,
                task_id=task_id,
                job_type=job_service.JOB_TYPE_AUTO_PIPELINE,
                payload={
                    "retry": True,
                    "start_step": TaskStatus.AI_ANALYZING.value,
                    "confirmed_uncertain_ai": True,
                    "retry_mode": retry_mode,
                    "retry_of_job_id": str(row["id"]),
                },
            )
            connection.commit()
            job = job_service.get_job(new_job_id)
            return job, created, {
                "retry_mode": retry_mode,
                "uncertain_unit_count": len(uncertain_units),
                "restart_step": TaskStatus.AI_ANALYZING.value,
                "previous_job_id": str(row["id"]),
            }

        updated_checkpoint = _checkpoint_for_uncertain_resume(
            checkpoint,
            authorized_at=now,
        )
        payload = _decode_json_object(row["payload_json"], field="payload_json")
        payload.update(
            {
                "retry": True,
                "start_step": TaskStatus.PREPARING_SOURCE.value,
                "confirmed_uncertain_ai": True,
                "retry_mode": retry_mode,
            }
        )
        cursor = connection.execute(
            """
            UPDATE workflow_jobs
            SET status = ?, progress = 0, message = '已确认 AI 重试，任务已重新加入队列',
                payload_json = ?, result_json = '{}', checkpoint_json = ?,
                checkpoint_updated_at = ?, error_message = NULL, finished_at = NULL,
                next_attempt_at = ?, cancel_requested = 0, lease_owner = NULL,
                lease_token = NULL, lease_expires_at = NULL, heartbeat_at = NULL,
                attempt_count = 0, updated_at = ?
            WHERE id = ? AND status IN (?, ?)
            """,
            (
                job_service.JOB_STATUS_QUEUED,
                json.dumps(payload, ensure_ascii=False),
                json.dumps(updated_checkpoint, ensure_ascii=False),
                now,
                now,
                now,
                str(row["id"]),
                job_service.JOB_STATUS_FAILED,
                job_service.JOB_STATUS_CANCELLED,
            ),
        )
        if cursor.rowcount != 1:
            connection.rollback()
            raise AutoPipelineRetryConflictError("失败 Job 状态已经变化，请刷新页面后重试")
        connection.commit()
    job = job_service.get_job(str(row["id"]))
    return job, True, {
        "retry_mode": retry_mode,
        "uncertain_unit_count": len(uncertain_units),
        "restart_step": TaskStatus.AI_ANALYZING.value,
        "previous_job_id": str(row["id"]),
    }


def _active_auto_job(connection, task_id: str):
    return connection.execute(
        """
        SELECT id FROM workflow_jobs
        WHERE task_id = ? AND job_type = ? AND status IN (?, ?)
        ORDER BY created_at DESC LIMIT 1
        """,
        (
            task_id,
            job_service.JOB_TYPE_AUTO_PIPELINE,
            job_service.JOB_STATUS_QUEUED,
            job_service.JOB_STATUS_RUNNING,
        ),
    ).fetchone()


def _latest_failed_auto_job(connection, task_id: str):
    return connection.execute(
        """
        SELECT id, payload_json, checkpoint_json
        FROM workflow_jobs
        WHERE task_id = ? AND job_type = ? AND status IN (?, ?)
        ORDER BY created_at DESC LIMIT 1
        """,
        (
            task_id,
            job_service.JOB_TYPE_AUTO_PIPELINE,
            job_service.JOB_STATUS_FAILED,
            job_service.JOB_STATUS_CANCELLED,
        ),
    ).fetchone()


def _require_no_downstream_records(connection, task_id: str) -> None:
    output_count = int(
        connection.execute(
            "SELECT COUNT(*) AS value FROM output_clip WHERE task_id = ?",
            (task_id,),
        ).fetchone()["value"]
        or 0
    )
    publish_count = int(
        connection.execute(
            "SELECT COUNT(*) AS value FROM publish_jobs WHERE task_id = ?",
            (task_id,),
        ).fetchone()["value"]
        or 0
    )
    if output_count or publish_count:
        raise AutoPipelineRetryConflictError(
            "任务已经存在下游切片或发布记录，为避免覆盖现有结果，已拒绝 AI 覆盖式重试。",
            code="ai_retry_downstream_conflict",
        )


def _prepare_task_for_ai_retry(connection, task_id: str, *, now: str) -> None:
    """把旧的下游失败态收敛到允许进入 AI_ANALYZING 的失败态。"""
    cursor = connection.execute(
        """
        UPDATE tasks
        SET status = ?, progress = 45,
            error_message = '等待执行已确认的 AI 分析重试',
            last_error = '等待执行已确认的 AI 分析重试', updated_at = ?
        WHERE id = ? AND COALESCE(is_deleted, 0) = 0 AND auto_mode = 1
          AND status LIKE 'FAILED_%'
        """,
        (TaskStatus.FAILED_AI_ANALYZING.value, now, task_id),
    )
    if cursor.rowcount != 1:
        raise AutoPipelineRetryConflictError(
            "任务已不处于可恢复的全自动失败状态，请刷新页面后重试。",
            code="ai_retry_task_state_conflict",
        )


def _resolve_retry_mode(task_id: str, checkpoint: dict[str, Any]) -> str:
    steps = checkpoint.get("steps") or {}
    transcription = steps.get(TaskStatus.TRANSCRIBING.value) or {}
    outputs = transcription.get("outputs") or {}
    transcript_evidence = outputs.get("transcript") or {}
    stored_sha256 = str(transcript_evidence.get("sha256") or "")
    transcript_path = get_artifact_paths(task_id)["transcript_path"]
    if not stored_sha256 or not transcript_path.is_file():
        raise AutoPipelineRetryConflictError(
            "旧 Job 缺少可信的转写输入校验值，无法判断是否可安全复用 AI checkpoint。",
            code="ai_retry_input_evidence_missing",
        )
    return "resume_uncertain" if _sha256_file(transcript_path) == stored_sha256 else "fresh_ai"


def _checkpoint_for_uncertain_resume(
    checkpoint: dict[str, Any],
    *,
    authorized_at: str,
) -> dict[str, Any]:
    updated = deepcopy(checkpoint)
    steps = updated.get("steps")
    completed = updated.get("completed_steps")
    if not isinstance(steps, dict) or not isinstance(completed, list):
        raise AutoPipelineRetryConflictError(
            "自动流水线 checkpoint 结构损坏，已拒绝重复调用 AI。",
            code="ai_retry_checkpoint_invalid",
        )
    if updated.get("start_step") != TaskStatus.PREPARING_SOURCE.value:
        raise AutoPipelineRetryConflictError(
            "旧 Job 不是从完整全自动流程启动，无法原位复用 AI 单元。",
            code="ai_retry_checkpoint_invalid",
        )
    retained_steps: dict[str, Any] = {}
    for step in _SAFE_RETAINED_STEPS:
        record = steps.get(step)
        if not isinstance(record, dict) or record.get("state") != "succeeded":
            raise AutoPipelineRetryConflictError(
                "准备素材或转写 checkpoint 缺少成功证据，已拒绝原位重试。",
                code="ai_retry_checkpoint_invalid",
            )
        retained_steps[step] = deepcopy(record)

    ai_record = steps.get(TaskStatus.AI_ANALYZING.value)
    attempts = int(ai_record.get("attempts") or 1) if isinstance(ai_record, dict) else 1
    baseline = deepcopy(ai_record.get("baseline") or {}) if isinstance(ai_record, dict) else {}
    retained_steps[TaskStatus.AI_ANALYZING.value] = {
        "state": "failed",
        "attempts": max(1, attempts),
        "started_at": str((ai_record or {}).get("started_at") or authorized_at),
        "failed_at": authorized_at,
        "baseline": baseline,
        "outputs": {},
        "error": "用户已确认重试计费结果不确定的 AI 单元",
    }
    updated["completed_steps"] = list(_SAFE_RETAINED_STEPS)
    updated["current_step"] = TaskStatus.AI_ANALYZING.value
    updated["steps"] = retained_steps
    updated["last_error"] = "用户已确认重试计费结果不确定的 AI 单元"
    updated["updated_at"] = authorized_at

    reset_count = 0
    for _, unit in _uncertain_units(updated):
        previous_error = str(unit.get("error") or "AI 请求结果不确定")
        unit.clear()
        unit.update(
            {
                "status": "retryable_failed",
                "error": previous_error,
                "retry_authorized_at": authorized_at,
                "retry_authorized_reason": "explicit_user_confirmation",
            }
        )
        reset_count += 1
    if not reset_count:
        raise AutoPipelineRetryConflictError("没有可重置的计费不确定 AI 单元")
    return updated


def _uncertain_units(checkpoint: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    root = checkpoint.get(_AI_UNIT_CHECKPOINT_KEY)
    if not isinstance(root, dict):
        return []
    namespaces = root.get("namespaces")
    if not isinstance(namespaces, dict):
        return []
    result: list[tuple[str, dict[str, Any]]] = []
    for namespace, state in namespaces.items():
        units = state.get("units") if isinstance(state, dict) else None
        if not isinstance(units, dict):
            continue
        for unit_id, unit in units.items():
            if isinstance(unit, dict) and unit.get("status") in {"running", "uncertain"}:
                result.append((f"{namespace}/{unit_id}", unit))
    return result


def _confirmation_detail(
    *,
    task_id: str,
    job_id: str,
    uncertain_count: int,
    retry_mode: str,
) -> dict[str, Any]:
    action = (
        "只重新请求结果不确定的 AI 单元，并复用其余成功 checkpoint"
        if retry_mode == "resume_uncertain"
        else "转写输入已变化，将从 AI 分析阶段创建全新 Job，不复用旧 AI 单元"
    )
    return {
        "code": "ai_retry_confirmation_required",
        "message": (
            f"检测到 {uncertain_count} 个 AI 单元的请求可能已经产生费用，但结果未确认。"
            f"确认后将{action}。"
        ),
        "task_id": task_id,
        "previous_job_id": job_id,
        "uncertain_unit_count": uncertain_count,
        "retry_mode": retry_mode,
        "restart_step": TaskStatus.AI_ANALYZING.value,
    }


def _decode_checkpoint(value: Any) -> dict[str, Any]:
    return _decode_json_object(value, field="checkpoint_json")


def _decode_json_object(value: Any, *, field: str) -> dict[str, Any]:
    if isinstance(value, dict):
        return deepcopy(value)
    try:
        decoded = json.loads(str(value or "{}"))
    except json.JSONDecodeError as exc:
        raise AutoPipelineRetryConflictError(
            f"Workflow Job {field} 已损坏，已拒绝重试。",
            code="ai_retry_checkpoint_invalid",
        ) from exc
    if not isinstance(decoded, dict):
        raise AutoPipelineRetryConflictError(
            f"Workflow Job {field} 不是对象，已拒绝重试。",
            code="ai_retry_checkpoint_invalid",
        )
    return decoded


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
