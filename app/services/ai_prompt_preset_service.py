import hashlib
from datetime import datetime

from app.db.database import DEFAULT_AI_PROMPT_PRESET_ID, get_connection
from app.models.task import AIPromptPresetUpdate


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _prompt_sha256(prompt_text: str) -> str:
    return hashlib.sha256((prompt_text or "").strip().encode("utf-8")).hexdigest()


def ensure_ai_prompt_version_with_connection(
    connection,
    *,
    preset_id: str,
    preset_name: str,
    prompt_text: str,
    now: str | None = None,
) -> dict:
    """按 Prompt 实际内容复用或创建不可变版本；仅改名称不会增加版本号。"""
    normalized_prompt = (prompt_text or "").strip()
    prompt_sha256 = _prompt_sha256(normalized_prompt)
    existing = connection.execute(
        """
        SELECT id, preset_id, version_number, preset_name_snapshot,
               prompt_text, prompt_sha256, created_at
        FROM ai_prompt_versions
        WHERE preset_id = ? AND prompt_sha256 = ?
        """,
        (preset_id, prompt_sha256),
    ).fetchone()
    if existing is not None:
        return dict(existing)

    version_number = int(
        connection.execute(
            "SELECT COALESCE(MAX(version_number), 0) + 1 FROM ai_prompt_versions WHERE preset_id = ?",
            (preset_id,),
        ).fetchone()[0]
    )
    version_id = f"promptv_{preset_id}_{version_number:03d}"
    created_at = now or _now_iso()
    connection.execute(
        """
        INSERT INTO ai_prompt_versions (
            id, preset_id, version_number, preset_name_snapshot,
            prompt_text, prompt_sha256, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            version_id,
            preset_id,
            version_number,
            (preset_name or "未命名方案").strip() or "未命名方案",
            normalized_prompt,
            prompt_sha256,
            created_at,
        ),
    )
    return {
        "id": version_id,
        "preset_id": preset_id,
        "version_number": version_number,
        "preset_name_snapshot": (preset_name or "未命名方案").strip() or "未命名方案",
        "prompt_text": normalized_prompt,
        "prompt_sha256": prompt_sha256,
        "created_at": created_at,
    }


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
        connection.execute("BEGIN IMMEDIATE")
        cursor = connection.execute(
            """
            UPDATE ai_prompt_presets
            SET name = ?, prompt_text = ?, updated_at = ?
            WHERE id = ?
            """,
            (normalized_name, prompt_text, now, preset_id),
        )
        if cursor.rowcount == 0:
            connection.rollback()
            raise ValueError("AI Prompt 方案不存在")
        ensure_ai_prompt_version_with_connection(
            connection,
            preset_id=preset_id,
            preset_name=normalized_name,
            prompt_text=prompt_text,
            now=now,
        )
        connection.commit()
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


def get_task_ai_prompt_snapshot(task_id: str) -> dict:
    """一次读取任务 Prompt 并绑定不可变版本，供整次 AI 分析复用。"""
    now = _now_iso()
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT p.id, p.slot, p.name, p.prompt_text, p.is_default,
                   p.created_at, p.updated_at
            FROM tasks t
            LEFT JOIN ai_prompt_presets p ON p.id = t.ai_prompt_preset_id
            WHERE t.id = ?
            """,
            (task_id,),
        ).fetchone()
        if row is None:
            connection.rollback()
            raise ValueError("任务不存在")
        if not row["id"]:
            row = connection.execute(
                """
                SELECT id, slot, name, prompt_text, is_default, created_at, updated_at
                FROM ai_prompt_presets WHERE id = ?
                """,
                (DEFAULT_AI_PROMPT_PRESET_ID,),
            ).fetchone()
        if row is None:
            connection.rollback()
            raise ValueError("默认 AI Prompt 方案不存在")

        preset = dict(row)
        version = ensure_ai_prompt_version_with_connection(
            connection,
            preset_id=str(preset["id"]),
            preset_name=str(preset["name"] or ""),
            prompt_text=str(preset["prompt_text"] or ""),
            now=now,
        )
        connection.commit()
    preset["is_default"] = bool(preset["is_default"])
    preset["prompt_preview"] = _prompt_preview(preset["prompt_text"])
    preset["prompt_version_id"] = version["id"]
    preset["prompt_version_number"] = version["version_number"]
    preset["prompt_sha256"] = version["prompt_sha256"]
    return preset


def list_ai_prompt_versions() -> list[dict]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT id, preset_id, version_number, preset_name_snapshot,
                   prompt_sha256, created_at
            FROM ai_prompt_versions
            ORDER BY preset_id, version_number DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def _prompt_preview(prompt_text: str) -> str:
    compact = " ".join((prompt_text or "").split())
    if len(compact) <= 80:
        return compact
    return f"{compact[:80]}..."
