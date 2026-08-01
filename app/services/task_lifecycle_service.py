"""任务生命周期服务

从 task_service 中拆分出来的任务创建、状态更新、软删除和配置更新函数。
"""

import json
from uuid import uuid4

from app.db.database import get_connection
from app.models.task import TaskCreate, TaskStatus
from app.services.storage_service import allocate_task_dir_name, create_task_directory, validate_source_video_path
from app.services.task_log_service import append_task_log


def create_task_record(payload: TaskCreate, task_id: str | None = None, task_dir_name: str | None = None) -> dict:
    from app.services.task_service import _now_iso, get_status_label, STATUS_PROGRESS  # noqa: F811

    resolved_task_id = task_id or uuid4().hex[:12]
    resolved_task_dir_name = task_dir_name or allocate_task_dir_name(
        payload.task_name,
        exclude_task_id=resolved_task_id,
    )
    now = _now_iso()
    create_task_directory(resolved_task_id, resolved_task_dir_name)

    source_path = payload.nas_file_path if payload.source_type == "nas" else payload.original_video_path
    has_source_file = bool(source_path)
    if source_path:
        valid, error_message = validate_source_video_path(source_path)
        if not valid:
            raise ValueError(error_message)

    initial_status = TaskStatus.CREATED.value if payload.auto_mode else (
        TaskStatus.pending_processing.value if has_source_file else TaskStatus.pending_video.value
    )
    progress = STATUS_PROGRESS[initial_status]
    auto_config = {
        "auto_clip_count": payload.auto_clip_count,
        "auto_min_clip_seconds": payload.auto_min_clip_seconds,
        "auto_max_clip_seconds": payload.auto_max_clip_seconds,
        "auto_schedule_mode": payload.auto_schedule_mode,
        "auto_schedule_start_at": payload.auto_schedule_start_at or "",
        "auto_schedule_interval_hours": payload.auto_schedule_interval_hours,
        "auto_schedule_daily_start_time": payload.auto_schedule_daily_start_time,
        "auto_schedule_daily_end_time": payload.auto_schedule_daily_end_time,
        "auto_metadata_use_ai": payload.auto_metadata_use_ai,
    }

    with get_connection() as connection:
        existing_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(tasks)").fetchall()
        }
        insert_data = {
            "id": resolved_task_id,
            "task_name": payload.task_name,
            "task_dir_name": resolved_task_dir_name,
            "source_type": payload.source_type,
            "platform": payload.platform,
            "original_video_path": payload.original_video_path,
            "nas_file_path": payload.nas_file_path,
            "max_clip_duration": payload.max_clip_duration,
            "candidate_clip_count": payload.candidate_clip_count,
            "selection_profile": payload.selection_profile,
            "final_clip_target": payload.final_clip_target,
            "ai_preference": payload.ai_preference,
            "ai_prompt_preset_id": "preset_001",
            "auto_mode": 1 if payload.auto_mode else 0,
            "auto_config_json": json.dumps(auto_config, ensure_ascii=False),
            "status": initial_status,
            "progress": progress,
            "error_message": None,
            "last_error": None,
            "is_deleted": 0,
            "deleted_at": None,
            "created_at": now,
            "updated_at": now,
        }

        if "title" in existing_columns:
            insert_data["title"] = payload.task_name
        if "source_path" in existing_columns:
            insert_data["source_path"] = payload.nas_file_path or payload.original_video_path
        if "max_clip_minutes" in existing_columns:
            insert_data["max_clip_minutes"] = payload.max_clip_duration
        if "target_clip_count" in existing_columns:
            insert_data["target_clip_count"] = payload.candidate_clip_count

        columns = [column for column in insert_data if column in existing_columns]
        placeholders = ", ".join("?" for _ in columns)
        connection.execute(
            f"INSERT INTO tasks ({', '.join(columns)}) VALUES ({placeholders})",
            tuple(insert_data[column] for column in columns),
        )
        connection.commit()

    append_task_log(resolved_task_id, "任务已创建")
    if payload.auto_mode:
        append_task_log(resolved_task_id, "已开启全自动模式，等待流水线启动")
    return {
        "id": resolved_task_id,
        "task_name": payload.task_name,
        "task_dir_name": resolved_task_dir_name,
        "status": initial_status,
        "status_label": get_status_label(initial_status),
        "auto_mode": payload.auto_mode,
        "detail_url": f"/tasks/{resolved_task_id}",
        "message": "任务已创建并写入数据库。",
    }


def update_task_status(
    task_id: str,
    new_status: TaskStatus,
    error_message: str | None = None,
) -> dict | None:
    from app.services.task_service import _now_iso, get_task, STATUS_PROGRESS  # noqa: F811

    now = _now_iso()
    status_value = new_status.value
    progress = STATUS_PROGRESS.get(status_value, 0)

    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE tasks
            SET status = ?, progress = ?, error_message = ?, last_error = ?, updated_at = ?
            WHERE id = ?
            """,
            (status_value, progress, error_message, error_message, now, task_id),
        )
        connection.commit()

    if cursor.rowcount == 0:
        return None
    return get_task(task_id)


def update_task_ai_preference(task_id: str, ai_preference: str | None) -> dict:
    from app.services.task_service import _now_iso, get_task  # noqa: F811

    task = get_task(task_id, include_video_probe=False)
    if not task:
        raise ValueError("任务不存在")

    now = _now_iso()
    normalized_preference = (ai_preference or "").strip()
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE tasks
            SET ai_preference = ?, updated_at = ?
            WHERE id = ?
            """,
            (normalized_preference, now, task_id),
        )
        connection.commit()

    append_task_log(task_id, "已保存 AI 分析偏好")
    return {
        "status": "ok",
        "message": "AI 偏好已保存。",
        "task": get_task(task_id, include_video_probe=False),
    }


def update_task_candidate_clip_count(task_id: str, candidate_clip_count: int) -> dict:
    from app.services.task_service import _now_iso, get_task  # noqa: F811

    task = get_task(task_id, include_video_probe=False)
    if not task:
        raise ValueError("任务不存在")
    if candidate_clip_count < 1 or candidate_clip_count > 50:
        raise ValueError("候选片段数量必须在 1 到 50 条之间")

    now = _now_iso()
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE tasks
            SET candidate_clip_count = ?, updated_at = ?
            WHERE id = ?
            """,
            (candidate_clip_count, now, task_id),
        )
        connection.commit()

    append_task_log(task_id, f"已更新 AI 候选片段数量：{candidate_clip_count} 条")
    return {
        "status": "ok",
        "message": f"候选片段数量已更新为 {candidate_clip_count} 条。",
        "task": get_task(task_id, include_video_probe=False),
    }


def update_task_selection_settings(
    task_id: str,
    selection_profile: str,
    final_clip_target: int,
) -> dict:
    from app.services.task_service import _now_iso, get_task  # noqa: F811

    task = get_task(task_id, include_video_probe=False)
    if not task:
        raise ValueError("任务不存在")
    if selection_profile not in {"general", "variety_comedy"}:
        raise ValueError("选片模式只能是通用模式或综艺笑点优先")
    if final_clip_target < 1 or final_clip_target > 12:
        raise ValueError("最终启用目标必须在 1 到 12 条之间")

    now = _now_iso()
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE tasks
            SET selection_profile = ?, final_clip_target = ?, updated_at = ?
            WHERE id = ?
            """,
            (selection_profile, final_clip_target, now, task_id),
        )
        connection.commit()

    profile_label = "综艺笑点优先" if selection_profile == "variety_comedy" else "通用模式"
    append_task_log(task_id, f"已更新选片模式：{profile_label}，最终启用目标：{final_clip_target} 条")
    return {
        "status": "ok",
        "message": f"已保存{profile_label}，最终启用目标为 {final_clip_target} 条。",
        "task": get_task(task_id, include_video_probe=False),
    }


def soft_delete_task(task_id: str) -> dict:
    from app.services.task_service import _now_iso, get_task  # noqa: F811

    task = get_task(task_id, include_video_probe=False)
    if not task:
        raise ValueError("任务不存在")
    if task.get("is_deleted"):
        return {
            "message": "任务已隐藏，无需重复操作。",
            "task_id": task_id,
            "task_dir": task["task_dir"],
        }

    now = _now_iso()
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE tasks
            SET is_deleted = 1, deleted_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (now, now, task_id),
        )
        connection.commit()

    append_task_log(task_id, "任务已从列表隐藏，文件未删除")
    return {
        "message": "任务已隐藏，原视频、切片和任务目录都已保留。",
        "task_id": task_id,
        "task_dir": task["task_dir"],
    }
