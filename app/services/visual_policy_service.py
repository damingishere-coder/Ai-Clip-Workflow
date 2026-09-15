"""显式选择的任务视觉策略；旧任务/Job 缺失快照时保持关闭。"""

import json

from app.services.ai.unit_checkpoint import build_unit_fingerprint


def visual_policy(enabled: bool) -> dict:
    return {"version": "visual-assist-v1", "enabled": bool(enabled), "max_candidates": 20,
            "frame_limit": 6, "max_bytes": 64 * 1024 * 1024, "round_seconds": 600,
            "call_seconds": 90, "max_score_fraction": 0.1, "max_score_points": 10,
            "preserve_text_tier": True, "judge_version": "visual-global-v1"}


def validate_policy(policy: dict) -> dict:
    if not isinstance(policy, dict) or type(policy.get("enabled")) is not bool or policy != visual_policy(policy["enabled"]):
        raise ValueError("视觉策略版本或参数不受支持，不能静默替换")
    return policy


def freeze_task_visual(connection, task_id: str, enabled: bool) -> None:
    policy = visual_policy(enabled)
    cursor = connection.execute("UPDATE task_generation_rules SET visual_policy_json=? WHERE task_id=?",
        (json.dumps({"policy": policy, "sha256": build_unit_fingerprint(policy)}, ensure_ascii=False), task_id))
    if cursor.rowcount != 1:
        raise ValueError("任务生成规则尚未冻结")


def task_visual_policy(connection, task_id: str) -> dict:
    row = connection.execute("""SELECT t.visual_enabled,r.visual_policy_json FROM tasks t
        LEFT JOIN task_generation_rules r ON r.task_id=t.id WHERE t.id=?""", (task_id,)).fetchone()
    if row is None:
        raise ValueError("任务不存在")
    if row[1] is None and not row[0]:
        return visual_policy(False)
    try:
        evidence = json.loads(row[1])
        policy = validate_policy(evidence["policy"])
        if evidence["sha256"] != build_unit_fingerprint(policy) or policy["enabled"] != bool(row[0]):
            raise ValueError("视觉策略快照不一致")
        return policy
    except (TypeError, KeyError, ValueError) as exc:
        raise ValueError("任务视觉策略证据损坏") from exc


def update_task_visual(task_id: str, enabled: bool) -> dict:
    from app.db.database import get_connection
    from app.services.weekly_review_service import freeze_task
    from app.services.task_service import get_task, _now_iso
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        from app.services.material_batch_service import require_editable_policy
        require_editable_policy(connection, task_id)
        if not connection.execute("SELECT 1 FROM tasks WHERE id=? AND COALESCE(is_deleted,0)=0", (task_id,)).fetchone():
            raise ValueError("任务不存在")
        if connection.execute("SELECT 1 FROM workflow_jobs WHERE task_id=? AND status IN ('queued','running')", (task_id,)).fetchone():
            raise ValueError("请先完成或取消后台作业，再更改视觉策略")
        freeze_task(connection, task_id)
        freeze_task_visual(connection, task_id, enabled)
        connection.execute("UPDATE tasks SET visual_enabled=?,updated_at=? WHERE id=?", (int(enabled), _now_iso(), task_id))
        connection.commit()
    return {"status": "ok", "task": get_task(task_id, include_video_probe=False), "visual_policy": visual_policy(enabled)}
