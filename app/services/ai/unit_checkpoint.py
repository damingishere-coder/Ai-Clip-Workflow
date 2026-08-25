"""Workflow Job 内的 AI 单元调用 checkpoint。

在调用 Provider 前先记录 ``running``，成功解析后再记录经过校验的结果。
如果进程在两者之间退出，下一代 lease 会把该单元视为计费结果不确定，
不会自动再次请求。这样不能消除第三方已经受理但本地尚未落账的物理窗口，
但可以把这个窗口显式化并阻止无证据的重复计费。
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Callable

from app.db.database import get_connection
from app.services import job_service
from app.services.ai.base import AIProviderError


_CHECKPOINT_KEY = "_ai_analysis_units_v1"
_ALLOWED_JOB_TYPES = {
    job_service.JOB_TYPE_AI_ANALYSIS,
    job_service.JOB_TYPE_AUTO_PIPELINE,
}


@dataclass(frozen=True)
class AIUnitExecution:
    status: str
    payload: dict[str, Any] | None = None
    error: str = ""
    reused: bool = False


def build_unit_fingerprint(payload: dict[str, Any]) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def provider_fingerprint_fields(provider: Any) -> dict[str, str]:
    """把真正影响结果来源的 Provider 配置纳入不可逆输入指纹。"""
    config = getattr(provider, "config", None)
    return {
        "provider_class": f"{type(provider).__module__}.{type(provider).__qualname__}",
        "provider_name": str(getattr(provider, "name", "") or ""),
        "model": str(getattr(config, "model", getattr(provider, "model", "")) or ""),
        "base_url": str(getattr(config, "base_url", "") or "").rstrip("/"),
        "protocol": str(getattr(config, "protocol", "") or ""),
        "fallback_protocol": str(getattr(config, "fallback_protocol", "") or ""),
        "responses_path": str(getattr(config, "responses_path", "") or ""),
        "reasoning_effort": str(getattr(config, "reasoning_effort", "") or ""),
        "executable": str(getattr(config, "executable", "") or ""),
        "codex_home": str(getattr(config, "codex_home", "") or ""),
    }


def execute_checkpointed_ai_unit(
    *,
    task_id: str,
    namespace: str,
    input_fingerprint: str,
    unit_id: str,
    operation: Callable[[], dict[str, Any]],
) -> AIUnitExecution:
    """执行一个可恢复 AI 单元；无 Job context 时保持旧的库内调用兼容。"""
    active = job_service.current_job_lease()
    if active is None:
        return _execute_untracked(operation)

    state = _begin_unit(
        task_id=task_id,
        namespace=namespace,
        input_fingerprint=input_fingerprint,
        unit_id=unit_id,
    )
    if state.status != "call_provider":
        return state

    try:
        payload = operation()
        if not isinstance(payload, dict):
            raise ValueError("AI 单元结果不是 JSON 对象")
    except job_service.JobLeaseLostError:
        raise
    except Exception as exc:
        retryable = bool(
            isinstance(exc, AIProviderError)
            and exc.safe_to_retry
            and not exc.billing_uncertain
        )
        error = _safe_error(exc)
        _finish_unit_failure(
            task_id=task_id,
            namespace=namespace,
            input_fingerprint=input_fingerprint,
            unit_id=unit_id,
            error=error,
            retryable=retryable,
        )
        return AIUnitExecution(
            status="retryable_failed" if retryable else "uncertain",
            error=error,
        )

    _finish_unit_success(
        task_id=task_id,
        namespace=namespace,
        input_fingerprint=input_fingerprint,
        unit_id=unit_id,
        payload=payload,
    )
    return AIUnitExecution(status="completed", payload=payload)


def _execute_untracked(operation: Callable[[], dict[str, Any]]) -> AIUnitExecution:
    try:
        payload = operation()
        if not isinstance(payload, dict):
            raise ValueError("AI 单元结果不是 JSON 对象")
        return AIUnitExecution(status="completed", payload=payload)
    except Exception as exc:
        retryable = bool(
            isinstance(exc, AIProviderError)
            and exc.safe_to_retry
            and not exc.billing_uncertain
        )
        return AIUnitExecution(
            status="retryable_failed" if retryable else "uncertain",
            error=_safe_error(exc),
        )


def _begin_unit(
    *,
    task_id: str,
    namespace: str,
    input_fingerprint: str,
    unit_id: str,
) -> AIUnitExecution:
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        job = job_service.require_job_lease_with_connection(
            connection,
            task_id=task_id,
            allowed_job_types=_ALLOWED_JOB_TYPES,
        )
        checkpoint = _checkpoint_dict(job.get("checkpoint_json"))
        namespace_state = _namespace_state(checkpoint, namespace, input_fingerprint)
        units = namespace_state.setdefault("units", {})
        previous = units.get(unit_id)
        if isinstance(previous, dict) and previous.get("status") == "completed":
            payload = _verified_payload(previous)
            if payload is not None:
                connection.commit()
                return AIUnitExecution(status="completed", payload=payload, reused=True)
            previous = {
                "status": "uncertain",
                "error": "AI 单元 checkpoint 校验失败，未自动重复请求",
            }
            units[unit_id] = previous
            _write_checkpoint(connection, str(job["id"]), checkpoint)
            connection.commit()
            return AIUnitExecution(status="uncertain", error=str(previous["error"]), reused=True)
        if isinstance(previous, dict) and previous.get("status") in {"running", "uncertain"}:
            error = str(previous.get("error") or "上一次 AI 请求已开始但结果未确认，未自动重复请求")
            units[unit_id] = {
                **previous,
                "status": "uncertain",
                "error": error,
            }
            _write_checkpoint(connection, str(job["id"]), checkpoint)
            connection.commit()
            return AIUnitExecution(status="uncertain", error=error, reused=True)

        if previous is not None and not (
            isinstance(previous, dict) and previous.get("status") == "retryable_failed"
        ):
            error = "AI 单元 checkpoint 状态未知或结构损坏，未自动重复请求"
            units[unit_id] = {"status": "uncertain", "error": error}
            _write_checkpoint(connection, str(job["id"]), checkpoint)
            connection.commit()
            return AIUnitExecution(status="uncertain", error=error, reused=True)

        active = job_service.current_job_lease()
        if active is None:
            connection.rollback()
            raise job_service.JobLeaseLostError("AI 单元缺少 Workflow Job 租约")
        units[unit_id] = {
            "status": "running",
            "workflow_job_id": str(job["id"]),
            "lease_generation": hashlib.sha256(active[2].encode("utf-8")).hexdigest(),
        }
        _write_checkpoint(connection, str(job["id"]), checkpoint)
        connection.commit()
    return AIUnitExecution(status="call_provider")


def _finish_unit_success(
    *,
    task_id: str,
    namespace: str,
    input_fingerprint: str,
    unit_id: str,
    payload: dict[str, Any],
) -> None:
    result_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    result_checksum = hashlib.sha256(result_json.encode("utf-8")).hexdigest()
    _finish_unit(
        task_id=task_id,
        namespace=namespace,
        input_fingerprint=input_fingerprint,
        unit_id=unit_id,
        state={
            "status": "completed",
            "result_json": result_json,
            "result_checksum": result_checksum,
        },
    )


def _finish_unit_failure(
    *,
    task_id: str,
    namespace: str,
    input_fingerprint: str,
    unit_id: str,
    error: str,
    retryable: bool,
) -> None:
    _finish_unit(
        task_id=task_id,
        namespace=namespace,
        input_fingerprint=input_fingerprint,
        unit_id=unit_id,
        state={
            "status": "retryable_failed" if retryable else "uncertain",
            "error": error,
        },
    )


def _finish_unit(
    *,
    task_id: str,
    namespace: str,
    input_fingerprint: str,
    unit_id: str,
    state: dict[str, Any],
) -> None:
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        job = job_service.require_job_lease_with_connection(
            connection,
            task_id=task_id,
            allowed_job_types=_ALLOWED_JOB_TYPES,
        )
        checkpoint = _checkpoint_dict(job.get("checkpoint_json"))
        namespace_state = _namespace_state(checkpoint, namespace, input_fingerprint)
        units = namespace_state.setdefault("units", {})
        previous = units.get(unit_id)
        if not isinstance(previous, dict) or previous.get("status") != "running":
            connection.rollback()
            raise job_service.JobLeaseLostError(f"AI 单元执行代际已改变：{unit_id}")
        active = job_service.current_job_lease()
        generation = hashlib.sha256(active[2].encode("utf-8")).hexdigest() if active else ""
        if str(previous.get("lease_generation") or "") != generation:
            connection.rollback()
            raise job_service.JobLeaseLostError(f"AI 单元 lease 代际已改变：{unit_id}")
        units[unit_id] = state
        _write_checkpoint(connection, str(job["id"]), checkpoint)
        connection.commit()


def _checkpoint_dict(value: Any) -> dict[str, Any]:
    if value in (None, "", {}):
        return {}
    if not isinstance(value, dict):
        raise ValueError("Workflow Job checkpoint_json 已损坏，拒绝重复调用 AI")
    return value


def _namespace_state(
    checkpoint: dict[str, Any],
    namespace: str,
    input_fingerprint: str,
) -> dict[str, Any]:
    root = checkpoint.setdefault(_CHECKPOINT_KEY, {})
    if not isinstance(root, dict):
        raise ValueError("AI 单元 checkpoint 根节点已损坏，拒绝重复调用 AI")
    namespaces = root.setdefault("namespaces", {})
    if not isinstance(namespaces, dict):
        raise ValueError("AI 单元 checkpoint namespaces 已损坏，拒绝重复调用 AI")
    state = namespaces.get(namespace)
    if state is None:
        state = {"input_fingerprint": input_fingerprint, "units": {}}
        namespaces[namespace] = state
    elif not isinstance(state, dict):
        raise ValueError(f"AI 单元 checkpoint namespace 已损坏：{namespace}")
    elif not isinstance(state.get("input_fingerprint"), str):
        raise ValueError(f"AI 单元 checkpoint 缺少输入指纹：{namespace}")
    elif state.get("input_fingerprint") != input_fingerprint:
        raise ValueError(
            f"AI 单元输入指纹已变化，旧恢复证据不会被覆盖：{namespace}；"
            "请保留当前 Job 并人工确认后再创建新的分析批次"
        )
    if not isinstance(state.get("units"), dict):
        raise ValueError(f"AI 单元 checkpoint 已损坏：{namespace}")
    return state


def _write_checkpoint(connection, job_id: str, checkpoint: dict[str, Any]) -> None:
    cursor = connection.execute(
        "UPDATE workflow_jobs SET checkpoint_json = ?, checkpoint_updated_at = CURRENT_TIMESTAMP, "
        "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (json.dumps(checkpoint, ensure_ascii=False), job_id),
    )
    if cursor.rowcount != 1:
        raise job_service.JobLeaseLostError(f"Workflow Job checkpoint 写入失败：{job_id}")


def _verified_payload(state: dict[str, Any]) -> dict[str, Any] | None:
    result_json = str(state.get("result_json") or "")
    checksum = str(state.get("result_checksum") or "")
    if not result_json or not checksum:
        return None
    if hashlib.sha256(result_json.encode("utf-8")).hexdigest() != checksum:
        return None
    try:
        payload = json.loads(result_json)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _safe_error(exc: Exception) -> str:
    if isinstance(exc, AIProviderError):
        text = exc.checkpoint_message()
    else:
        text = str(exc)
    return " ".join(str(text or "AI 单元执行失败").split())[:1000]
