"""轻量本地工作流任务队列 —— Job Service

为转写、AI 分析、切片、字幕、发布等长任务提供统一的 job 记录模型。
第一轮仅接入自动切片（video_cut），后续再逐步迁移其他流程。
"""

import json
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
    """将 job 标记为 running"""
    now = _now_iso()
    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE workflow_jobs
            SET status = ?, progress = 10, message = '任务已开始执行',
                started_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (JOB_STATUS_RUNNING, now, now, job_id),
        )
        connection.commit()

    if cursor.rowcount == 0:
        return None
    return get_job(job_id)


def claim_job(job_id: str, lease_owner: str, lease_seconds: int = 120) -> dict | None:
    """原子领取一个排队任务，或接管 lease 已过期的运行任务。"""
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat(timespec="seconds")
    lease_expires_at = (now + timedelta(seconds=max(30, lease_seconds))).isoformat(timespec="seconds")
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
                updated_at = ?, heartbeat_at = ?, lease_owner = ?, lease_expires_at = ?,
                attempt_count = attempt_count + 1
            WHERE id = ?
            """,
            (JOB_STATUS_RUNNING, now_iso, now_iso, now_iso, lease_owner, lease_expires_at, job_id),
        )
        connection.commit()
    return get_job(job_id)


def claim_next_job(lease_owner: str, lease_seconds: int = 120) -> dict | None:
    """按创建时间领取一个重型任务，保证本地默认串行。"""
    now = _now_iso()
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE workflow_jobs SET status = ?, error_message = '已达到最大尝试次数',
                message = '任务失败：已达到最大尝试次数', finished_at = ?, updated_at = ?,
                lease_owner = NULL, lease_expires_at = NULL
            WHERE status IN (?, ?) AND attempt_count >= max_attempts
            """,
            (JOB_STATUS_FAILED, now, now, JOB_STATUS_QUEUED, JOB_STATUS_RUNNING),
        )
        connection.commit()
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
    return claim_job(row["id"], lease_owner, lease_seconds) if row else None


def heartbeat_job(job_id: str, lease_owner: str, lease_seconds: int = 120) -> bool:
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat(timespec="seconds")
    expires = (now + timedelta(seconds=max(30, lease_seconds))).isoformat(timespec="seconds")
    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE workflow_jobs SET heartbeat_at = ?, lease_expires_at = ?, updated_at = ?
            WHERE id = ? AND status = ? AND lease_owner = ?
            """,
            (now_iso, expires, now_iso, job_id, JOB_STATUS_RUNNING, lease_owner),
        )
        connection.commit()
    return cursor.rowcount == 1


def update_job_checkpoint(job_id: str, checkpoint: dict) -> dict | None:
    now = _now_iso()
    with get_connection() as connection:
        connection.execute(
            "UPDATE workflow_jobs SET checkpoint_json = ?, checkpoint_updated_at = ?, updated_at = ? WHERE id = ?",
            (json.dumps(checkpoint, ensure_ascii=False), now, now, job_id),
        )
        connection.commit()
    return get_job(job_id)


def request_job_cancel(job_id: str) -> dict | None:
    now = _now_iso()
    with get_connection() as connection:
        row = connection.execute("SELECT status FROM workflow_jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            return None
        if row["status"] == JOB_STATUS_QUEUED:
            connection.execute(
                """UPDATE workflow_jobs SET status = ?, cancel_requested = 1, message = '任务已取消',
                   finished_at = ?, updated_at = ?, lease_owner = NULL, lease_expires_at = NULL WHERE id = ?""",
                (JOB_STATUS_CANCELLED, now, now, job_id),
            )
        elif row["status"] == JOB_STATUS_RUNNING:
            connection.execute(
                "UPDATE workflow_jobs SET cancel_requested = 1, message = '正在停止任务', updated_at = ? WHERE id = ?",
                (now, job_id),
            )
        connection.commit()
    return get_job(job_id)


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
                lease_owner = NULL, lease_expires_at = NULL, heartbeat_at = NULL,
                attempt_count = 0, updated_at = ?
            WHERE id = ? AND status IN (?, ?)
            """,
            (JOB_STATUS_QUEUED, now, now, job_id, JOB_STATUS_FAILED, JOB_STATUS_CANCELLED),
        )
        connection.commit()
    return get_job(job_id) if cursor.rowcount else None


def release_job_lease(job_id: str, lease_owner: str) -> bool:
    """Web 正常停止时释放子进程 Job，供新进程立即恢复。"""
    now = _now_iso()
    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE workflow_jobs SET status = ?, message = '应用停止，等待新 worker 恢复',
                lease_owner = NULL, lease_expires_at = NULL, heartbeat_at = NULL, updated_at = ?
            WHERE id = ? AND status = ? AND lease_owner = ?
            """,
            (JOB_STATUS_QUEUED, now, job_id, JOB_STATUS_RUNNING, lease_owner),
        )
        connection.commit()
    return cursor.rowcount == 1


def update_job_progress(job_id: str, progress: int, message: Optional[str] = None) -> dict | None:
    """更新 job 进度（0-100）和消息"""
    now = _now_iso()
    clamped_progress = max(0, min(100, progress))
    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE workflow_jobs
            SET progress = ?, message = ?, updated_at = ?
            WHERE id = ?
            """,
            (clamped_progress, message or "", now, job_id),
        )
        connection.commit()

    if cursor.rowcount == 0:
        return None
    return get_job(job_id)


def mark_job_completed(job_id: str, result: Optional[dict] = None) -> dict | None:
    """将 job 标记为 completed，附带结果"""
    now = _now_iso()
    result_json = json.dumps(result or {}, ensure_ascii=False)
    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE workflow_jobs
            SET status = ?, progress = 100,
                message = '任务已完成', result_json = ?,
                finished_at = ?, updated_at = ?, lease_owner = NULL, lease_expires_at = NULL
            WHERE id = ?
            """,
            (JOB_STATUS_COMPLETED, result_json, now, now, job_id),
        )
        connection.commit()

    if cursor.rowcount == 0:
        return None
    return get_job(job_id)


def mark_job_failed(job_id: str, error_message: str) -> dict | None:
    """将 job 标记为 failed，记录错误信息"""
    now = _now_iso()
    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE workflow_jobs
            SET status = ?, error_message = ?, message = ?,
                finished_at = ?, updated_at = ?, lease_owner = NULL, lease_expires_at = NULL
            WHERE id = ?
            """,
            (JOB_STATUS_FAILED, error_message, f"任务失败：{error_message}", now, now, job_id),
        )
        connection.commit()

    if cursor.rowcount == 0:
        return None
    return get_job(job_id)


def mark_job_cancelled(job_id: str, message: str = "任务已取消") -> dict | None:
    now = _now_iso()
    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE workflow_jobs SET status = ?, message = ?, finished_at = ?, updated_at = ?,
                lease_owner = NULL, lease_expires_at = NULL
            WHERE id = ?
            """,
            (JOB_STATUS_CANCELLED, message, now, now, job_id),
        )
        connection.commit()
    return get_job(job_id) if cursor.rowcount else None
