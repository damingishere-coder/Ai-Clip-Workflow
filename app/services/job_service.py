"""轻量本地工作流任务队列 —— Job Service

为转写、AI 分析、切片、字幕、发布等长任务提供统一的 job 记录模型。
第一轮仅接入自动切片（video_cut），后续再逐步迁移其他流程。
"""

import json
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from app.db.database import get_connection

# ── 支持的 job 类型 ──────────────────────────────────────────────
JOB_TYPE_VIDEO_CUT = "video_cut"
JOB_TYPE_AI_ANALYSIS = "ai_analysis"
JOB_TYPE_TRANSCRIPT = "transcript"
JOB_TYPE_SUBTITLE = "subtitle"
JOB_TYPE_PUBLISH = "publish"

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
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row_to_dict(row) -> dict:
    """将 sqlite3.Row 转为普通字典，并解析 JSON 字段"""
    job = dict(row)
    for field in ("payload_json", "result_json"):
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
                finished_at = ?, updated_at = ?
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
                finished_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (JOB_STATUS_FAILED, error_message, f"任务失败：{error_message}", now, now, job_id),
        )
        connection.commit()

    if cursor.rowcount == 0:
        return None
    return get_job(job_id)
