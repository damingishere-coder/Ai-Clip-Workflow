"""冻结候选附件，复用 AI unit checkpoint；本模块尚无生产入口。"""

from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import time
from uuid import uuid4

from app.db.database import get_connection
from app.services import job_service
from app.services.ai.base import AIProviderError
from app.services.ai.unit_checkpoint import build_unit_fingerprint, execute_checkpointed_ai_unit, provider_fingerprint_fields
from app.services.ai.visual_provider import VISUAL_PROMPT, VISUAL_PROMPT_VERSION, VisualImage, VisualResponse, validate_visual_response
from app.services.ai.visual_cli_policy import VISUAL_TOOL_POLICY_VERSION
from app.services.storage_service import get_task_directory


NAMESPACE = "optional-visual-v1"
ALLOWED_JOBS = {job_service.JOB_TYPE_AI_ANALYSIS, job_service.JOB_TYPE_AUTO_PIPELINE}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _require_job(connection, task_id):
    return job_service.require_job_lease_with_connection(connection, task_id=task_id, allowed_job_types=ALLOWED_JOBS)


def _task_root(connection, task_id):
    task = connection.execute("SELECT task_dir_name FROM tasks WHERE id=? AND COALESCE(is_deleted,0)=0", (task_id,)).fetchone()
    if task is None:
        raise ValueError("视觉证据的任务不存在")
    return get_task_directory(task_id, task["task_dir_name"] or task_id).resolve()


def get_candidate_visual(task_id: str, candidate_key: str) -> dict | None:
    """恢复时先查此记录，避免重新采样、改附件后再次调用模型。"""
    with get_connection() as connection:
        job = _require_job(connection, task_id)
        row = connection.execute("SELECT * FROM candidate_visual_evidence WHERE workflow_job_id=? AND candidate_key=?", (job["id"], candidate_key)).fetchone()
        return dict(row) if row else None


def _validate_sampling(sampling):
    if sampling.get("status") not in {"completed", "partial", "unavailable"}:
        raise ValueError("视觉采样状态无效")
    if not re.fullmatch(r"[a-f0-9]{64}", sampling.get("source_sha256", "")):
        raise ValueError("视觉采样缺少完整原片哈希")
    plan = sampling["plan"]
    start, end = plan["start_seconds"], plan["end_seconds"]
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (start, end)) or not 0 <= start < end:
        raise ValueError("视觉候选边界无效")
    frames = sampling["frames"]
    if not isinstance(frames, list) or len(frames) > 8:
        raise ValueError("视觉帧数超过上限")
    if sampling["status"] in {"completed", "partial"} and not frames or sampling["status"] == "unavailable" and frames:
        raise ValueError("视觉状态与采样结果不一致")
    names, hashes = set(), set()
    for frame in frames:
        if not re.fullmatch(r"[a-f0-9]{32}\.jpg", frame["file_name"]) or frame["file_name"] in names:
            raise ValueError("视觉附件名称无效或重复")
        if not re.fullmatch(r"[a-f0-9]{64}", frame["sha256"]) or frame["sha256"] in hashes:
            raise ValueError("视觉附件哈希无效或重复")
        names.add(frame["file_name"])
        hashes.add(frame["sha256"])
        for value in (frame["timestamp_seconds"], frame["actual_timestamp_seconds"]):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not start <= value < end:
                raise ValueError("视觉附件时间越出候选")
        if not isinstance(frame["size_bytes"], int) or isinstance(frame["size_bytes"], bool) or not 1 <= frame["size_bytes"] <= 2 * 1024 * 1024:
            raise ValueError("视觉附件大小无效")


def _request_identity(request):
    sampling = request["sampling"]
    # 路径和随机文件名不改变模型请求身份，附件内容、顺序和时间才改变。
    return {"version": request["version"], "tool_policy": request["tool_policy"], "prompt": request["prompt"], "schema": request["schema"],
        "provider_sha256": request["provider_sha256"], "source_sha256": sampling["source_sha256"],
        "sampler_version": sampling["sampler_version"], "plan": sampling["plan"],
        "frames": [{k: v for k, v in frame.items() if k != "file_name"} for frame in sampling["frames"]]}


def prepare_candidate_visual(*, task_id: str, candidate_key: str, input_fingerprint: str,
                             sampling: dict, cache_directory: Path, provider) -> dict:
    if not candidate_key or len(candidate_key) > 200 or not re.fullmatch(r"[a-f0-9]{64}", input_fingerprint):
        raise ValueError("视觉候选或冻结输入指纹无效")
    _validate_sampling(sampling)
    mapping = [{"image_index": i, "timestamp_seconds": f["actual_timestamp_seconds"], "role": f["role"], "source": f["source"]} for i, f in enumerate(sampling["frames"])]
    identity = provider_fingerprint_fields(provider)
    request = {"version": VISUAL_PROMPT_VERSION, "tool_policy": VISUAL_TOOL_POLICY_VERSION, "sampling": sampling,
        "provider_sha256": build_unit_fingerprint(identity), "schema": VisualResponse.model_json_schema(),
        "prompt": VISUAL_PROMPT + "\n附件映射：" + _json(mapping)}
    request_json = _json(request)
    if len(request_json.encode("utf-8")) > 128 * 1024:
        raise ValueError("视觉请求证据过大")
    fingerprint = build_unit_fingerprint(_request_identity(request))
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        job = _require_job(connection, task_id)
        root = _task_root(connection, task_id)
        directory = cache_directory.resolve()
        if not directory.is_relative_to(root):
            raise ValueError("视觉缓存逃逸任务根目录")
        relative = directory.relative_to(root).as_posix()
        if not re.fullmatch(r"analysis/visual/[a-f0-9]{32}", relative):
            raise ValueError("视觉缓存不是托管的采样目录")
        previous = connection.execute("SELECT * FROM candidate_visual_evidence WHERE workflow_job_id=? AND candidate_key=?", (job["id"], candidate_key)).fetchone()
        if previous:
            if previous["input_fingerprint"] != input_fingerprint or previous["request_fingerprint"] != fingerprint:
                raise ValueError("已冻结视觉请求发生变化，旧证据保留且未重新调用")
            connection.commit()
            return dict(previous)
        reason = "provider_visual_unsupported" if not callable(getattr(provider, "generate_visual_json", None)) else "sampling_unavailable" if not sampling["frames"] else ""
        evidence_id, now = uuid4().hex, _now()
        connection.execute("""INSERT INTO candidate_visual_evidence
            (id,task_id,workflow_job_id,candidate_key,input_fingerprint,request_fingerprint,request_json,
             cache_relative_dir,provider,model,status,failure_reason,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (evidence_id,task_id,job["id"],candidate_key,input_fingerprint,
            fingerprint,request_json,relative,identity["provider_name"],identity["model"],
            "unavailable" if reason else "pending",reason,now,now))
        result = dict(connection.execute("SELECT * FROM candidate_visual_evidence WHERE id=?", (evidence_id,)).fetchone())
        connection.commit()
        return result


def _read_active(task_id, evidence_id):
    with get_connection() as connection:
        job = _require_job(connection, task_id)
        row = connection.execute("SELECT * FROM candidate_visual_evidence WHERE id=? AND task_id=? AND workflow_job_id=?", (evidence_id, task_id, job["id"])).fetchone()
        if row is None:
            raise ValueError("视觉证据不属于当前任务和 Job")
        return dict(row), _task_root(connection, task_id), job


def _update_result(task_id, row, *, status, call_status, failure="", payload=None):
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        job = _require_job(connection, task_id)
        serialized = _json(payload) if payload is not None else None
        connection.execute("""UPDATE candidate_visual_evidence SET status=?,call_status=?,failure_reason=?,
            evidence_json=COALESCE(evidence_json,?),evidence_sha256=COALESCE(evidence_sha256,?),updated_at=?
            WHERE id=? AND task_id=? AND workflow_job_id=?""",
            (status,call_status,failure,serialized,_sha(serialized) if serialized else None,_now(),row["id"],task_id,job["id"]))
        result = dict(connection.execute("SELECT * FROM candidate_visual_evidence WHERE id=?", (row["id"],)).fetchone())
        connection.commit()
        return result


def analyze_candidate_visual(*, task_id: str, evidence_id: str, provider, timeout_seconds: float = 90, retry_unbilled: bool = False, deadline_epoch: float | None = None) -> dict:
    """只允许有租约的 Job 调用；可选失败留证，不修改 Task 或文字 coverage。"""
    row, root, job = _read_active(task_id, evidence_id)
    if row["status"] in {"unavailable", "disabled"} and not (retry_unbilled and row["call_status"] == "retryable_failed"):
        return row
    try:
        request = json.loads(row["request_json"])
        _validate_sampling(request["sampling"])
        if build_unit_fingerprint(_request_identity(request)) != row["request_fingerprint"]:
            raise ValueError("视觉请求指纹不一致")
        if build_unit_fingerprint(provider_fingerprint_fields(provider)) != request["provider_sha256"]:
            raise ValueError("视觉 Provider 或模型已改变")
        if row["evidence_json"] and _sha(row["evidence_json"]) != row["evidence_sha256"]:
            raise ValueError("视觉结果校验和不一致")
    except (ValueError, KeyError, TypeError):
        return _update_result(task_id, row, status="unavailable", call_status=row["call_status"], failure="visual_evidence_mismatch")
    frames = request["sampling"]["frames"]

    # 证据表曾标记调用后，丢失 checkpoint 不能被解释为“第一次调用”。
    if row["call_status"] != "not_called":
        checkpoint = job.get("checkpoint_json")
        try:
            prior = checkpoint["_ai_analysis_units_v1"]["namespaces"][NAMESPACE]["units"][row["candidate_key"]]
        except (KeyError, TypeError):
            prior = None
        if not prior:
            return _update_result(task_id, row, status="unavailable", call_status="uncertain", failure="visual_checkpoint_missing_after_call")
        if row["call_status"] == "completed" and (not isinstance(prior, dict) or prior.get("status") != "completed"):
            return _update_result(task_id, row, status="unavailable", call_status="uncertain", failure="visual_checkpoint_conflicts_with_result")

    def validate(payload):
        if set(payload) != {"response", "raw_response_sha256"} or not re.fullmatch(r"[a-f0-9]{64}", payload["raw_response_sha256"]):
            raise ValueError("视觉响应哈希或结构无效")
        validate_visual_response(payload["response"], len(frames))

    diagnostic = {}

    def operation():
        try:
            directory = (root / row["cache_relative_dir"]).resolve()
            if not directory.is_relative_to(root) or not re.fullmatch(r"analysis/visual/[a-f0-9]{32}", directory.relative_to(root).as_posix()):
                raise ValueError("视觉缓存路径已经改变")
            images = []
            for frame in frames:
                path = directory / frame["file_name"]
                if path.resolve().parent != directory or path.is_symlink() or not path.is_file():
                    raise ValueError("视觉缓存文件缺失或路径异常")
                if path.stat().st_size != frame["size_bytes"] or hashlib.sha256(path.read_bytes()).hexdigest() != frame["sha256"]:
                    raise ValueError("视觉缓存内容已改变")
                images.append(VisualImage(path, frame["sha256"]))
        except (ValueError, OSError) as exc:
            raise AIProviderError("视觉附件未通过调用前校验，尚未请求模型", category="visual_attachment_unavailable", safe_to_retry=True) from exc
        # 即便等待本地校验耗时，发请求前也必须仍持有有效 Job。
        job_service.require_active_job_lease()
        if deadline_epoch is not None and time.time() >= deadline_epoch:
            raise AIProviderError("视觉整轮预算已耗尽，尚未调用", category="visual_round_budget_exhausted", safe_to_retry=True)
        from app.services.ai.visual_provider import visual_call_deadline
        with visual_call_deadline(deadline_epoch):
            raw = provider.generate_visual_json(request["prompt"], request["schema"], tuple(images), timeout_seconds=timeout_seconds)
        diagnostic["raw_response_sha256"] = _sha(raw)
        if len(raw.encode("utf-8")) > 128 * 1024:
            raise ValueError("视觉响应超过证据预算")
        return {"response": json.loads(raw), "raw_response_sha256": diagnostic["raw_response_sha256"]}

    # 从已完成 checkpoint 恢复时 operation 不会执行，图片已清理也无需再次请求。
    if row["call_status"] == "not_called":
        _update_result(task_id, row, status="pending", call_status="pending")
    try:
        execution = execute_checkpointed_ai_unit(task_id=task_id, namespace=NAMESPACE,
            input_fingerprint=row["input_fingerprint"], unit_id=row["candidate_key"],
            request_fingerprint=row["request_fingerprint"], operation=operation, validate_payload=validate)
    except job_service.JobLeaseLostError:
        raise
    except ValueError:
        return _update_result(task_id, row, status="unavailable", call_status="uncertain", failure="visual_checkpoint_mismatch")
    if execution.status == "completed":
        if row["evidence_json"] and json.loads(row["evidence_json"]) != execution.payload:
            return _update_result(task_id, row, status="unavailable", call_status="uncertain", failure="visual_result_mismatch")
        return _update_result(task_id, row, status=request["sampling"]["status"], call_status="completed", payload=execution.payload)
    return _update_result(task_id, row, status="unavailable", call_status=execution.status,
        failure=execution.error, payload={**diagnostic, "invalid_response": True} if diagnostic else None)


def attach_visual_evidence_with_connection(connection, *, task_id: str, job_id: str, run_id: str) -> None:
    """供既有 AI 原子提交事务调用；本 PR 尚不接入该事务。"""
    if not connection.in_transaction:
        raise ValueError("视觉 Run 关联必须位于既有原子提交事务内")
    job = _require_job(connection, task_id)
    if job["id"] != job_id or not connection.execute("SELECT 1 FROM ai_analysis_runs WHERE id=? AND task_id=?", (run_id, task_id)).fetchone():
        raise ValueError("视觉证据与分析 Run 归属不一致")
    if connection.execute("SELECT 1 FROM candidate_visual_evidence WHERE workflow_job_id=? AND analysis_run_id IS NOT NULL AND analysis_run_id!=?", (job_id, run_id)).fetchone():
        raise ValueError("视觉证据已经归属于其他分析 Run")
    connection.execute("UPDATE candidate_visual_evidence SET analysis_run_id=? WHERE task_id=? AND workflow_job_id=? AND analysis_run_id IS NULL", (run_id,task_id,job_id))
