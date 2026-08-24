"""轻量本地工作流任务队列 —— Job Service

为转写、AI 分析、切片、字幕、发布等长任务提供统一的 job 记录模型。
第一轮仅接入自动切片（video_cut），后续再逐步迁移其他流程。
"""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import uuid4

from app.db.database import get_connection

# ── 支持的 job 类型 ──────────────────────────────────────────────
JOB_TYPE_VIDEO_CUT = "video_cut"
JOB_TYPE_AI_ANALYSIS = "ai_analysis"
JOB_TYPE_TRANSCRIPT = "transcript"
JOB_TYPE_SUBTITLE = "subtitle"
JOB_TYPE_PUBLISH = "publish"
JOB_TYPE_AUTO_PIPELINE = "auto_pipeline"

# ── job 状态枚举 ─────────────────────────────────────────────────
JOB_STATUS_QUEUED = "queued"
JOB_STATUS_RUNNING = "running"
JOB_STATUS_COMPLETED = "completed"
JOB_STATUS_FAILED = "failed"
JOB_STATUS_CANCELLED = "cancelled"

JOB_STATUS_LABELS = {
    JOB_STATUS_QUEUED: "排队中",
    JOB_STATUS_RUNNING: "运行中",
    JOB_STATUS_COMPLETED: "已完成",
    JOB_STATUS_FAILED: "失败",
    JOB_STATUS_CANCELLED: "已取消",
}

JOB_TYPE_LABELS = {
    JOB_TYPE_VIDEO_CUT: "自动切片",
    JOB_TYPE_AI_ANALYSIS: "AI 分析",
    JOB_TYPE_TRANSCRIPT: "转写",
    JOB_TYPE_SUBTITLE: "字幕",
    JOB_TYPE_PUBLISH: "发布",
    JOB_TYPE_AUTO_PIPELINE: "全自动流水线",
}


class JobLeaseLostError(RuntimeError):
    """当前执行已失去 Workflow Job 的 claim 代际。"""


_active_job_lease: ContextVar[tuple[str, str, str] | None] = ContextVar(
    "active_workflow_job_lease",
    default=None,
)


@contextmanager
def job_lease_context(job_id: str, lease_owner: str, lease_token: str) -> Iterator[None]:
    """让同一执行链中的深层进度/checkpoint 写回自动携带租约代际。"""
    if not job_id or not lease_owner or not lease_token:
        raise ValueError("Workflow Job 租约缺少 job_id、owner 或 token")
    token = _active_job_lease.set((job_id, lease_owner, lease_token))
    try:
        yield
    finally:
        _active_job_lease.reset(token)


def _resolve_job_lease(
    lease_owner: str | None = None,
    lease_token: str | None = None,
) -> tuple[str, str] | None:
    if lease_owner is not None or lease_token is not None:
        if not lease_owner or not lease_token:
            raise ValueError("Workflow Job 租约必须同时提供 owner 和 token")
        return lease_owner, lease_token
    active = _active_job_lease.get()
    return (active[1], active[2]) if active else None


def require_active_job_lease() -> tuple[str, str, str] | None:
    """深层持久化副作用执行前确认当前 ContextVar 仍属于有效 claim。"""
    active = _active_job_lease.get()
    if active is None:
        return None
    job_id, lease_owner, lease_token = active
    if not validate_job_lease(job_id, lease_owner, lease_token):
        raise JobLeaseLostError(f"Workflow Job 租约已失效：{job_id}")
    return active


def current_job_lease() -> tuple[str, str, str] | None:
    """供同一 SQLite 事务把业务写入与 Job claim 条件绑定。"""
    return _active_job_lease.get()


def _lease_write_condition(
    lease_owner: str | None = None,
    lease_token: str | None = None,
) -> tuple[str, tuple[str, ...], bool]:
    lease = _resolve_job_lease(lease_owner, lease_token)
    if lease:
        return (
            "id = ? AND status = ? AND lease_owner = ? AND lease_token = ?",
            (JOB_STATUS_RUNNING, lease[0], lease[1]),
            True,
        )
    return (
        "id = ? AND status = ? AND lease_owner IS NULL AND lease_token IS NULL",
        (JOB_STATUS_RUNNING,),
        False,
    )


def _raise_if_lease_lost(job_id: str, rowcount: int, fenced: bool) -> None:
    if fenced and rowcount == 0:
        raise JobLeaseLostError(f"Workflow Job 租约已失效：{job_id}")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row_to_dict(row) -> dict:
    """将 sqlite3.Row 转为普通字典，并解析 JSON 字段"""
    job = dict(row)
    for field in ("payload_json", "result_json", "checkpoint_json"):
        raw = job.get(field)
        if isinstance(raw, str) and raw:
            try:
                job[field] = json.loads(raw)
            except json.JSONDecodeError:
                pass
        else:
            job[field] = raw or {}
    return job


# ── 创建 job ─────────────────────────────────────────────────────

def create_job(
    task_id: str,
    job_type: str,
    payload: Optional[dict] = None,
    job_id: Optional[str] = None,
) -> dict:
    """创建一个新的 workflow job 记录，初始状态 queued"""
    resolved_job_id = job_id or uuid4().hex[:12]
    now = _now_iso()
    payload_json = json.dumps(payload or {}, ensure_ascii=False)

    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO workflow_jobs (
                id, task_id, job_type, status, progress, message,
                payload_json, result_json, error_message,
                created_at, updated_at, started_at, finished_at
            )
            VALUES (?, ?, ?, ?, 0, ?, ?, '{}', NULL, ?, ?, NULL, NULL)
            """,
            (
                resolved_job_id,
                task_id,
                job_type,
                JOB_STATUS_QUEUED,
                f"{JOB_TYPE_LABELS.get(job_type, job_type)}任务已加入队列",
                payload_json,
                now,
                now,
            ),
        )
        connection.commit()

    return get_job(resolved_job_id)


def create_or_get_active_job(
    task_id: str,
    job_type: str,
    payload: Optional[dict] = None,
) -> tuple[dict, bool]:
    """原子复用进行中的 job，或创建新的 queued job。

    返回值中的布尔值表示是否新建。`BEGIN IMMEDIATE` 将“查询 + 新建”
    串行化，避免连续点击或并发请求为同一任务创建重复的切片作业。
    """
    resolved_job_id = uuid4().hex[:12]
    now = _now_iso()
    payload_json = json.dumps(payload or {}, ensure_ascii=False)

    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            """
            SELECT id
            FROM workflow_jobs
            WHERE task_id = ?
              AND job_type = ?
              AND status IN (?, ?)
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (
                task_id,
                job_type,
                JOB_STATUS_QUEUED,
                JOB_STATUS_RUNNING,
            ),
        ).fetchone()
        if existing:
            connection.commit()
            return get_job(existing["id"]), False

        connection.execute(
            """
            INSERT INTO workflow_jobs (
                id, task_id, job_type, status, progress, message,
                payload_json, result_json, error_message,
                created_at, updated_at, started_at, finished_at
            )
            VALUES (?, ?, ?, ?, 0, ?, ?, '{}', NULL, ?, ?, NULL, NULL)
            """,
            (
                resolved_job_id,
                task_id,
                job_type,
                JOB_STATUS_QUEUED,
                f"{JOB_TYPE_LABELS.get(job_type, job_type)}任务已加入队列",
                payload_json,
                now,
                now,
            ),
        )
        connection.commit()

    return get_job(resolved_job_id), True


# ── 查询 job ─────────────────────────────────────────────────────

def get_job(job_id: str) -> dict | None:
    """根据 job_id 获取单条 job 记录"""
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT *
            FROM workflow_jobs
            WHERE id = ?
            """,
            (job_id,),
        ).fetchone()
    if not row:
        return None
    result = _row_to_dict(row)
    result["status_label"] = JOB_STATUS_LABELS.get(result.get("status"), result.get("status"))
    result["job_type_label"] = JOB_TYPE_LABELS.get(result.get("job_type"), result.get("job_type"))
    return result


def list_jobs(task_id: Optional[str] = None, status: Optional[str] = None) -> list[dict]:
    """列出 job 记录，可按 task_id 和/或 status 过滤"""
    conditions = []
    params = []

    if task_id:
        conditions.append("task_id = ?")
        params.append(task_id)
    if status:
        conditions.append("status = ?")
        params.append(status)

    where_clause = ""
    if conditions:
        where_clause = "WHERE " + " AND ".join(conditions)

    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT *
            FROM workflow_jobs
            {where_clause}
            ORDER BY created_at DESC
            """,
            tuple(params),
        ).fetchall()

    results = []
    for row in rows:
        job = _row_to_dict(row)
        job["status_label"] = JOB_STATUS_LABELS.get(job.get("status"), job.get("status"))
        job["job_type_label"] = JOB_TYPE_LABELS.get(job.get("job_type"), job.get("job_type"))
        results.append(job)
    return results


# ── 状态流转 ─────────────────────────────────────────────────────

def mark_job_running(job_id: str) -> dict | None:
    """兼容入口：仍通过正式 claim 生成 token，不再制造无代际 running job。"""
    return claim_job(job_id, f"legacy:{uuid4().hex}")


def claim_job(job_id: str, lease_owner: str, lease_seconds: int = 120) -> dict | None:
    """原子领取一个排队任务，或接管 lease 已过期的运行任务。"""
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat(timespec="seconds")
    lease_expires_at = (now + timedelta(seconds=max(30, lease_seconds))).isoformat(timespec="seconds")
    lease_token = uuid4().hex
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT status, lease_expires_at, cancel_requested, attempt_count, max_attempts FROM workflow_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        if not row:
            connection.commit()
            return None
        expired = row["status"] == JOB_STATUS_RUNNING and (
            not row["lease_expires_at"] or str(row["lease_expires_at"]) <= now_iso
        )
        claimable = row["status"] == JOB_STATUS_QUEUED or expired
        if not claimable or int(row["cancel_requested"] or 0) or int(row["attempt_count"] or 0) >= int(row["max_attempts"] or 3):
            connection.commit()
            return None
        connection.execute(
            """
            UPDATE workflow_jobs
            SET status = ?, progress = CASE WHEN progress < 10 THEN 10 ELSE progress END,
                message = '任务已开始执行', started_at = COALESCE(started_at, ?),
                updated_at = ?, heartbeat_at = ?, lease_owner = ?, lease_token = ?, lease_expires_at = ?,
                attempt_count = attempt_count + 1
            WHERE id = ?
            """,
            (
                JOB_STATUS_RUNNING,
                now_iso,
                now_iso,
                now_iso,
                lease_owner,
                lease_token,
                lease_expires_at,
                job_id,
            ),
        )
        connection.commit()
    return get_job(job_id)


def claim_next_job(lease_owner: str, lease_seconds: int = 120) -> dict | None:
    """按创建时间领取一个重型任务，保证本地默认串行。"""
    now_value = datetime.now(timezone.utc)
    now = now_value.isoformat(timespec="seconds")
    lease_expires_at = (now_value + timedelta(seconds=max(30, lease_seconds))).isoformat(timespec="seconds")
    lease_token = uuid4().hex
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            UPDATE workflow_jobs
            SET status = ?, message = '任务已取消，过期执行已回收',
                finished_at = ?, updated_at = ?, lease_owner = NULL,
                lease_token = NULL, lease_expires_at = NULL, heartbeat_at = NULL
            WHERE status = ? AND cancel_requested = 1
              AND (lease_expires_at IS NULL OR lease_expires_at <= ?)
            """,
            (JOB_STATUS_CANCELLED, now, now, JOB_STATUS_RUNNING, now),
        )
        connection.execute(
            """
            UPDATE workflow_jobs SET status = ?, error_message = '已达到最大尝试次数',
                message = '任务失败：已达到最大尝试次数', finished_at = ?, updated_at = ?,
                lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL
            WHERE attempt_count >= max_attempts
              AND (
                status = ?
                OR (status = ? AND (lease_expires_at IS NULL OR lease_expires_at <= ?))
              )
            """,
            (JOB_STATUS_FAILED, now, now, JOB_STATUS_QUEUED, JOB_STATUS_RUNNING, now),
        )
        row = connection.execute(
            """
            SELECT id FROM workflow_jobs
            WHERE cancel_requested = 0
              AND attempt_count < max_attempts
              AND (
                (status = ? AND (next_attempt_at IS NULL OR next_attempt_at <= ?))
                OR (status = ? AND (lease_expires_at IS NULL OR lease_expires_at <= ?))
              )
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (JOB_STATUS_QUEUED, now, JOB_STATUS_RUNNING, now),
        ).fetchone()
        if row:
            cursor = connection.execute(
                """
                UPDATE workflow_jobs
                SET status = ?, progress = CASE WHEN progress < 10 THEN 10 ELSE progress END,
                    message = '任务已开始执行', started_at = COALESCE(started_at, ?),
                    updated_at = ?, heartbeat_at = ?, lease_owner = ?, lease_token = ?,
                    lease_expires_at = ?, attempt_count = attempt_count + 1
                WHERE id = ? AND attempt_count < max_attempts AND cancel_requested = 0
                  AND (
                    status = ?
                    OR (status = ? AND (lease_expires_at IS NULL OR lease_expires_at <= ?))
                  )
                """,
                (
                    JOB_STATUS_RUNNING,
                    now,
                    now,
                    now,
                    lease_owner,
                    lease_token,
                    lease_expires_at,
                    row["id"],
                    JOB_STATUS_QUEUED,
                    JOB_STATUS_RUNNING,
                    now,
                ),
            )
            if cursor.rowcount != 1:
                row = None
        connection.commit()
    return get_job(row["id"]) if row else None


def validate_job_lease(
    job_id: str,
    lease_owner: str,
    lease_token: str,
    *,
    require_unexpired: bool = True,
) -> dict | None:
    now = _now_iso()
    expiry_clause = "AND lease_expires_at > ?" if require_unexpired else ""
    params: tuple[str, ...] = (
        job_id,
        JOB_STATUS_RUNNING,
        lease_owner,
        lease_token,
        *((now,) if require_unexpired else ()),
    )
    with get_connection() as connection:
        row = connection.execute(
            f"""
            SELECT * FROM workflow_jobs
            WHERE id = ? AND status = ? AND lease_owner = ? AND lease_token = ?
              {expiry_clause}
            """,
            params,
        ).fetchone()
    return _row_to_dict(row) if row else None


def heartbeat_job(
    job_id: str,
    lease_owner: str | None = None,
    lease_token: str | None = None,
    lease_seconds: int = 120,
) -> bool:
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat(timespec="seconds")
    expires = (now + timedelta(seconds=max(30, lease_seconds))).isoformat(timespec="seconds")
    lease = _resolve_job_lease(lease_owner, lease_token)
    if lease is None:
        raise ValueError("heartbeat_job 需要有效的 Workflow Job 租约")
    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE workflow_jobs SET heartbeat_at = ?, lease_expires_at = ?, updated_at = ?
            WHERE id = ? AND status = ? AND lease_owner = ? AND lease_token = ?
              AND lease_expires_at > ?
            """,
            (now_iso, expires, now_iso, job_id, JOB_STATUS_RUNNING, lease[0], lease[1], now_iso),
        )
        connection.commit()
    if cursor.rowcount == 0:
        raise JobLeaseLostError(f"Workflow Job 租约已失效：{job_id}")
    return True


def update_job_checkpoint(
    job_id: str,
    checkpoint: dict,
    *,
    lease_owner: str | None = None,
    lease_token: str | None = None,
) -> dict | None:
    now = _now_iso()
    condition, lease_params, fenced = _lease_write_condition(lease_owner, lease_token)
    with get_connection() as connection:
        cursor = connection.execute(
            f"UPDATE workflow_jobs SET checkpoint_json = ?, checkpoint_updated_at = ?, updated_at = ? WHERE {condition}",
            (json.dumps(checkpoint, ensure_ascii=False), now, now, job_id, *lease_params),
        )
        connection.commit()
    _raise_if_lease_lost(job_id, cursor.rowcount, fenced)
    return get_job(job_id) if cursor.rowcount else None


def request_job_cancel(job_id: str) -> dict | None:
    now = _now_iso()
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT status, job_type, task_id FROM workflow_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        if not row:
            connection.rollback()
            return None
        if row["status"] == JOB_STATUS_QUEUED:
            cursor = connection.execute(
                """UPDATE workflow_jobs SET status = ?, cancel_requested = 1, message = '任务已取消',
                   finished_at = ?, updated_at = ?, lease_owner = NULL, lease_token = NULL,
                   lease_expires_at = NULL WHERE id = ? AND status = ?""",
                (JOB_STATUS_CANCELLED, now, now, job_id, JOB_STATUS_QUEUED),
            )
        elif row["status"] == JOB_STATUS_RUNNING:
            cursor = connection.execute(
                """
                UPDATE workflow_jobs
                SET cancel_requested = 1, message = '正在停止任务', updated_at = ?
                WHERE id = ? AND status = ?
                """,
                (now, job_id, JOB_STATUS_RUNNING),
            )
        else:
            cursor = None
        if cursor and cursor.rowcount == 1 and row["job_type"] == JOB_TYPE_AUTO_PIPELINE:
            _cancel_linked_auto_publish_jobs(
                connection,
                task_id=str(row["task_id"] or ""),
                workflow_job_ids=[job_id],
                now=now,
            )
            connection.execute(
                """
                UPDATE tasks
                SET status = 'CANCELLED', progress = 0,
                    error_message = '用户已取消全自动流水线',
                    last_error = '用户已取消全自动流水线', updated_at = ?
                WHERE id = ? AND COALESCE(is_deleted, 0) = 0
                """,
                (now, str(row["task_id"] or "")),
            )
        connection.commit()
    return get_job(job_id)


def cancel_active_auto_pipeline_jobs_for_task(
    connection,
    task_id: str,
    *,
    now: str,
) -> int:
    """在调用方事务中取消一个 Task 的活跃自动流水线及其未发布结果。"""
    rows = connection.execute(
        """
        SELECT id, status
        FROM workflow_jobs
        WHERE task_id = ? AND job_type = ? AND status IN (?, ?)
        """,
        (task_id, JOB_TYPE_AUTO_PIPELINE, JOB_STATUS_QUEUED, JOB_STATUS_RUNNING),
    ).fetchall()
    if not rows:
        return 0

    queued_ids = [str(row["id"]) for row in rows if row["status"] == JOB_STATUS_QUEUED]
    running_ids = [str(row["id"]) for row in rows if row["status"] == JOB_STATUS_RUNNING]
    if queued_ids:
        placeholders = ", ".join("?" for _ in queued_ids)
        connection.execute(
            f"""
            UPDATE workflow_jobs
            SET status = ?, cancel_requested = 1, message = '任务已取消',
                finished_at = ?, updated_at = ?, lease_owner = NULL,
                lease_token = NULL, lease_expires_at = NULL, heartbeat_at = NULL
            WHERE id IN ({placeholders}) AND status = ?
            """,
            (JOB_STATUS_CANCELLED, now, now, *queued_ids, JOB_STATUS_QUEUED),
        )
    if running_ids:
        placeholders = ", ".join("?" for _ in running_ids)
        connection.execute(
            f"""
            UPDATE workflow_jobs
            SET cancel_requested = 1, message = '正在停止任务', updated_at = ?
            WHERE id IN ({placeholders}) AND status = ?
            """,
            (now, *running_ids, JOB_STATUS_RUNNING),
        )
    _cancel_linked_auto_publish_jobs(
        connection,
        task_id=task_id,
        workflow_job_ids=[str(row["id"]) for row in rows],
        now=now,
    )
    return len(rows)


def _cancel_linked_auto_publish_jobs(
    connection,
    *,
    task_id: str,
    workflow_job_ids: list[str],
    now: str,
) -> int:
    """只取消 provider_response 明确归属于指定流水线的未发布任务。"""
    resolved_ids = [job_id for job_id in workflow_job_ids if job_id]
    if not task_id or not resolved_ids:
        return 0
    candidates = connection.execute(
        """
        SELECT id, provider_response
        FROM publish_jobs
        WHERE task_id = ? AND status IN ('DRAFT', 'WAITING', 'SCHEDULED', 'NEED_REVIEW')
        """,
        (task_id,),
    ).fetchall()
    linked_ids: list[str] = []
    allowed_workflow_ids = set(resolved_ids)
    for row in candidates:
        try:
            provider_response = json.loads(str(row["provider_response"] or "{}"))
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(provider_response, dict):
            continue
        if (
            provider_response.get("source") == "auto_pipeline"
            and str(provider_response.get("workflow_job_id") or "") in allowed_workflow_ids
        ):
            linked_ids.append(str(row["id"]))
    if not linked_ids:
        return 0
    placeholders = ", ".join("?" for _ in linked_ids)
    cursor = connection.execute(
        f"""
        UPDATE publish_jobs
        SET status = 'CANCELLED', error_code = 'pipeline_cancelled',
            error_message = '全自动流水线已取消', last_error = '全自动流水线已取消',
            finished_at = ?, updated_at = ?
        WHERE task_id = ?
          AND status IN ('DRAFT', 'WAITING', 'SCHEDULED', 'NEED_REVIEW')
          AND id IN ({placeholders})
        """,
        (now, now, task_id, *linked_ids),
    )
    return cursor.rowcount


def is_cancel_requested(job_id: str) -> bool:
    job = get_job(job_id)
    return bool(job and int(job.get("cancel_requested") or 0))


def retry_job(job_id: str) -> dict | None:
    now = _now_iso()
    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE workflow_jobs
            SET status = ?, progress = 0, message = '任务已重新加入队列', error_message = NULL,
                finished_at = NULL, next_attempt_at = ?, cancel_requested = 0,
                lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL, heartbeat_at = NULL,
                attempt_count = 0, updated_at = ?
            WHERE id = ? AND status IN (?, ?)
            """,
            (JOB_STATUS_QUEUED, now, now, job_id, JOB_STATUS_FAILED, JOB_STATUS_CANCELLED),
        )
        connection.commit()
    return get_job(job_id) if cursor.rowcount else None


def retry_latest_or_get_active_job(task_id: str, job_type: str) -> tuple[dict | None, bool]:
    """Return the active job, or atomically requeue the latest retryable job.

    Reusing the same row is important for auto-pipeline jobs because their
    fenced step checkpoint belongs to that workflow_jobs.id.
    """

    now = _now_iso()
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        active = connection.execute(
            """
            SELECT id FROM workflow_jobs
            WHERE task_id = ? AND job_type = ? AND status IN (?, ?)
            ORDER BY created_at DESC LIMIT 1
            """,
            (task_id, job_type, JOB_STATUS_QUEUED, JOB_STATUS_RUNNING),
        ).fetchone()
        if active:
            connection.commit()
            return get_job(str(active["id"])), False

        retryable = connection.execute(
            """
            SELECT id FROM workflow_jobs
            WHERE task_id = ? AND job_type = ? AND status IN (?, ?)
            ORDER BY created_at DESC LIMIT 1
            """,
            (task_id, job_type, JOB_STATUS_FAILED, JOB_STATUS_CANCELLED),
        ).fetchone()
        if not retryable:
            connection.commit()
            return None, False

        cursor = connection.execute(
            """
            UPDATE workflow_jobs
            SET status = ?, progress = 0, message = '任务已重新加入队列',
                result_json = '{}', error_message = NULL, finished_at = NULL,
                next_attempt_at = ?, cancel_requested = 0,
                lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL,
                heartbeat_at = NULL, attempt_count = 0, updated_at = ?
            WHERE id = ? AND status IN (?, ?)
            """,
            (
                JOB_STATUS_QUEUED,
                now,
                now,
                retryable["id"],
                JOB_STATUS_FAILED,
                JOB_STATUS_CANCELLED,
            ),
        )
        connection.commit()
    if cursor.rowcount != 1:
        return None, False
    return get_job(str(retryable["id"])), True


def release_job_lease(job_id: str, lease_owner: str, lease_token: str) -> bool:
    """Web 正常停止时释放子进程 Job，供新进程立即恢复。"""
    now = _now_iso()
    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE workflow_jobs SET status = ?, message = '应用停止，等待新 worker 恢复',
                lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL,
                heartbeat_at = NULL, updated_at = ?
            WHERE id = ? AND status = ? AND lease_owner = ? AND lease_token = ?
            """,
            (JOB_STATUS_QUEUED, now, job_id, JOB_STATUS_RUNNING, lease_owner, lease_token),
        )
        connection.commit()
    return cursor.rowcount == 1


def update_job_progress(
    job_id: str,
    progress: int,
    message: Optional[str] = None,
    *,
    lease_owner: str | None = None,
    lease_token: str | None = None,
) -> dict | None:
    """更新 job 进度（0-100）和消息"""
    now = _now_iso()
    clamped_progress = max(0, min(100, progress))
    condition, lease_params, fenced = _lease_write_condition(lease_owner, lease_token)
    with get_connection() as connection:
        cursor = connection.execute(
            f"""
            UPDATE workflow_jobs
            SET progress = ?, message = ?, updated_at = ?
            WHERE {condition}
            """,
            (clamped_progress, message or "", now, job_id, *lease_params),
        )
        connection.commit()

    _raise_if_lease_lost(job_id, cursor.rowcount, fenced)
    if cursor.rowcount == 0:
        return None
    return get_job(job_id)


def mark_job_completed(
    job_id: str,
    result: Optional[dict] = None,
    *,
    lease_owner: str | None = None,
    lease_token: str | None = None,
) -> dict | None:
    """将 job 标记为 completed，附带结果"""
    now = _now_iso()
    result_json = json.dumps(result or {}, ensure_ascii=False)
    condition, lease_params, fenced = _lease_write_condition(lease_owner, lease_token)
    with get_connection() as connection:
        cursor = connection.execute(
            f"""
            UPDATE workflow_jobs
            SET status = ?, progress = 100,
                message = '任务已完成', result_json = ?,
                finished_at = ?, updated_at = ?, lease_owner = NULL,
                lease_token = NULL, lease_expires_at = NULL, heartbeat_at = NULL
            WHERE {condition} AND cancel_requested = 0
            """,
            (JOB_STATUS_COMPLETED, result_json, now, now, job_id, *lease_params),
        )
        connection.commit()

    _raise_if_lease_lost(job_id, cursor.rowcount, fenced)
    if cursor.rowcount == 0:
        return None
    return get_job(job_id)


def mark_job_failed(
    job_id: str,
    error_message: str,
    *,
    lease_owner: str | None = None,
    lease_token: str | None = None,
) -> dict | None:
    """将 job 标记为 failed，记录错误信息"""
    now = _now_iso()
    condition, lease_params, fenced = _lease_write_condition(lease_owner, lease_token)
    with get_connection() as connection:
        cursor = connection.execute(
            f"""
            UPDATE workflow_jobs
            SET status = ?, error_message = ?, message = ?,
                finished_at = ?, updated_at = ?, lease_owner = NULL,
                lease_token = NULL, lease_expires_at = NULL, heartbeat_at = NULL
            WHERE {condition} AND cancel_requested = 0
            """,
            (
                JOB_STATUS_FAILED,
                error_message,
                f"任务失败：{error_message}",
                now,
                now,
                job_id,
                *lease_params,
            ),
        )
        connection.commit()

    _raise_if_lease_lost(job_id, cursor.rowcount, fenced)
    if cursor.rowcount == 0:
        return None
    return get_job(job_id)


def mark_job_cancelled(
    job_id: str,
    message: str = "任务已取消",
    *,
    lease_owner: str | None = None,
    lease_token: str | None = None,
) -> dict | None:
    now = _now_iso()
    condition, lease_params, fenced = _lease_write_condition(lease_owner, lease_token)
    with get_connection() as connection:
        cursor = connection.execute(
            f"""
            UPDATE workflow_jobs SET status = ?, message = ?, finished_at = ?, updated_at = ?,
                lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL, heartbeat_at = NULL
            WHERE {condition}
            """,
            (JOB_STATUS_CANCELLED, message, now, now, job_id, *lease_params),
        )
        connection.commit()
    _raise_if_lease_lost(job_id, cursor.rowcount, fenced)
    return get_job(job_id) if cursor.rowcount else None
