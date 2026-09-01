"""候选片段人工反馈。

反馈作为独立审片记录保存，不会删除候选片段或历史 AI 分析结果。
"""

from __future__ import annotations

from uuid import uuid4

from app.db.database import get_connection
from app.models.task import ClipFeedbackCreate
from app.services.task_log_service import append_task_log


FEEDBACK_REASON_LABELS = {
    "worth_publishing": "值得发",
    "not_funny": "不好笑",
    "fragmented": "片段不完整",
    "missing_setup": "铺垫缺失",
    "duplicate": "内容重复",
    "dragging": "节奏拖沓",
    "other": "其他",
}


def record_review_toggle_feedback_with_connection(
    connection,
    *,
    task_id: str,
    clip: dict,
    selection_profile: str,
    enabled: bool,
    reason_code: str | None,
    now: str,
) -> bool:
    """在候选保存事务中记录开关反馈；相同状态和原因不会重复写入。"""
    decision = "keep" if enabled else "reject"
    normalized_reason = "worth_publishing" if enabled else (reason_code or "other")
    latest = connection.execute(
        """
        SELECT decision, reason_code
        FROM clip_feedback
        WHERE task_id = ? AND clip_candidate_id = ?
        ORDER BY created_at DESC, rowid DESC
        LIMIT 1
        """,
        (task_id, clip["id"]),
    ).fetchone()
    if latest is not None and (
        str(latest["decision"] or "") == decision
        and str(latest["reason_code"] or "") == normalized_reason
    ):
        return False

    connection.execute(
        """
        INSERT INTO clip_feedback (
            id, task_id, clip_candidate_id, analysis_run_id, selection_profile,
            decision, reason_code, decision_source, note, title_snapshot,
            summary_snapshot, start_time, end_time, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'review_toggle', '', ?, ?, ?, ?, ?)
        """,
        (
            uuid4().hex[:12],
            task_id,
            clip["id"],
            clip.get("source_analysis_run_id"),
            selection_profile or "general",
            decision,
            normalized_reason,
            clip.get("title") or "",
            clip.get("summary") or "",
            clip.get("start_time") or "",
            clip.get("end_time") or "",
            now,
        ),
    )
    return True


def save_clip_feedback(task_id: str, clip_id: str, payload: ClipFeedbackCreate) -> dict:
    from app.services.task_service import _now_iso, get_clip_candidate, get_task  # noqa: F811

    task = get_task(task_id, include_video_probe=False)
    if not task:
        raise ValueError("任务不存在")
    clip = get_clip_candidate(task_id, clip_id)
    now = _now_iso()

    with get_connection() as connection:
        active_run = connection.execute(
            """
            SELECT id FROM ai_analysis_runs
            WHERE task_id = ? AND is_active = 1
            ORDER BY run_number DESC LIMIT 1
            """,
            (task_id,),
        ).fetchone()
        connection.execute(
            """
            INSERT INTO clip_feedback (
                id, task_id, clip_candidate_id, analysis_run_id, selection_profile,
                decision, reason_code, decision_source, note, title_snapshot, summary_snapshot,
                start_time, end_time, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'explicit_feedback', ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid4().hex[:12],
                task_id,
                clip_id,
                active_run["id"] if active_run else None,
                task.get("selection_profile") or "general",
                payload.decision,
                payload.reason_code,
                (payload.note or "").strip(),
                clip.get("title") or "",
                clip.get("summary") or "",
                clip.get("start_time") or "",
                clip.get("end_time") or "",
                now,
            ),
        )
        connection.execute(
            """
            UPDATE clip_candidates
            SET enabled = ?, reviewed = 1, updated_at = ?
            WHERE task_id = ? AND id = ? AND is_deleted = 0
            """,
            (1 if payload.decision == "keep" else 0, now, task_id, clip_id),
        )
        connection.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", (now, task_id))
        connection.commit()

    label = FEEDBACK_REASON_LABELS.get(payload.reason_code, payload.reason_code)
    append_task_log(task_id, f"已记录片段反馈：{clip.get('title') or clip_id} · {label}")
    return {
        "status": "ok",
        "message": f"已记录反馈：{label}。",
        "decision": payload.decision,
        "reason_code": payload.reason_code,
        "enabled": payload.decision == "keep",
    }


def list_recent_feedback_context(selection_profile: str, limit: int = 20) -> list[dict]:
    safe_limit = max(1, min(50, int(limit)))
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT decision, reason_code, note, title_snapshot, summary_snapshot,
                   start_time, end_time, created_at, decision_source
            FROM (
                SELECT f.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY f.task_id, f.clip_candidate_id
                           ORDER BY f.created_at DESC, f.rowid DESC
                       ) AS feedback_rank
                FROM clip_feedback f
                WHERE f.selection_profile = ?
            )
            WHERE feedback_rank = 1
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (selection_profile, safe_limit),
        ).fetchall()
    return [dict(row) for row in rows]


__all__ = [
    "FEEDBACK_REASON_LABELS",
    "list_recent_feedback_context",
    "record_review_toggle_feedback_with_connection",
    "save_clip_feedback",
]
