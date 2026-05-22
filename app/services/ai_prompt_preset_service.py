from datetime import datetime

from app.db.database import DEFAULT_AI_PROMPT_PRESET_ID, get_connection
from app.models.task import AIPromptPresetUpdate


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def list_ai_prompt_presets() -> list[dict]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT id, slot, name, prompt_text, is_default, created_at, updated_at
            FROM ai_prompt_presets
            ORDER BY slot ASC
            """
        ).fetchall()
    return [
        {
            **dict(row),
            "is_default": bool(row["is_default"]),
            "prompt_preview": _prompt_preview(row["prompt_text"]),
        }
        for row in rows
    ]


def get_ai_prompt_preset(preset_id: str) -> dict | None:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT id, slot, name, prompt_text, is_default, created_at, updated_at
            FROM ai_prompt_presets
            WHERE id = ?
            """,
            (preset_id,),
        ).fetchone()
    if not row:
        return None
    preset = dict(row)
    preset["is_default"] = bool(preset["is_default"])
    preset["prompt_preview"] = _prompt_preview(preset["prompt_text"])
    return preset


def update_ai_prompt_preset(preset_id: str, payload: AIPromptPresetUpdate) -> dict:
    now = _now_iso()
    normalized_name = payload.name.strip() or "未命名方案"
    prompt_text = payload.prompt_text.strip()
    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE ai_prompt_presets
            SET name = ?, prompt_text = ?, updated_at = ?
            WHERE id = ?
            """,
            (normalized_name, prompt_text, now, preset_id),
        )
        connection.commit()
    if cursor.rowcount == 0:
        raise ValueError("AI Prompt 方案不存在")
    return {
        "status": "ok",
        "message": "AI Prompt 方案已保存。",
        "preset": get_ai_prompt_preset(preset_id),
    }


def update_task_ai_prompt_preset(task_id: str, preset_id: str) -> dict:
    preset = get_ai_prompt_preset(preset_id)
    if not preset:
        raise ValueError("AI Prompt 方案不存在")

    now = _now_iso()
    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE tasks
            SET ai_prompt_preset_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (preset_id, now, task_id),
        )
        connection.commit()
    if cursor.rowcount == 0:
        raise ValueError("任务不存在")
    return {
        "status": "ok",
        "message": f"当前任务已选择：{preset['name']}。",
        "ai_prompt_preset_id": preset_id,
        "preset": preset,
    }


def get_task_ai_prompt_preset(task_id: str) -> dict:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT p.id, p.slot, p.name, p.prompt_text, p.is_default, p.created_at, p.updated_at
            FROM tasks t
            LEFT JOIN ai_prompt_presets p ON p.id = t.ai_prompt_preset_id
            WHERE t.id = ?
            """,
            (task_id,),
        ).fetchone()
    if row and row["id"]:
        preset = dict(row)
        preset["is_default"] = bool(preset["is_default"])
        preset["prompt_preview"] = _prompt_preview(preset["prompt_text"])
        return preset

    fallback = get_ai_prompt_preset(DEFAULT_AI_PROMPT_PRESET_ID)
    if not fallback:
        raise ValueError("默认 AI Prompt 方案不存在")
    return fallback


def _prompt_preview(prompt_text: str) -> str:
    compact = " ".join((prompt_text or "").split())
    if len(compact) <= 80:
        return compact
    return f"{compact[:80]}..."
