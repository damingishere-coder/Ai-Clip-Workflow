"""任务生命周期服务

从 task_service 中拆分出来的任务创建、状态更新、软删除和配置更新函数。
"""

import json
from sqlite3 import Connection
from uuid import uuid4

from app.db.database import get_connection
from app.models.task import TaskCreate, TaskStatus
from app.services.storage_service import (
    allocate_task_dir_name,
    build_task_media_cleanup_plan,
    create_task_directory,
    finalize_staged_task_media_cleanup,
    rollback_staged_task_media_cleanup,
    stage_task_media_cleanup_plan,
    validate_source_video_path,
)
from app.services.task_log_service import append_task_log
from app.services.media_preflight_service import preflight_media


class TaskDeletionConflictError(RuntimeError):
    """任务仍在执行，暂时不能删除其媒体文件。"""


class TaskStatusConflictError(RuntimeError):
    """任务状态已变化、已删除，或请求的状态转换不被允许。"""


PUBLIC_TASK_STATUS_TRANSITIONS: dict[str, set[str]] = {
    TaskStatus.pending_video.value: {TaskStatus.pending_processing.value},
    TaskStatus.pending_processing.value: {TaskStatus.audio_extracting.value, TaskStatus.failed.value},
    TaskStatus.audio_extracting.value: {TaskStatus.transcribing.value, TaskStatus.failed.value},
    TaskStatus.transcribing.value: {TaskStatus.pending_ai.value, TaskStatus.failed.value},
    TaskStatus.pending_ai.value: {TaskStatus.ai_analyzing.value, TaskStatus.failed.value},
    TaskStatus.ai_analyzing.value: {TaskStatus.pending_review.value, TaskStatus.failed.value},
    TaskStatus.pending_review.value: {
        TaskStatus.ai_analyzing.value,
        TaskStatus.cutting.value,
        TaskStatus.completed.value,
        TaskStatus.completed_with_errors.value,
    },
    TaskStatus.cutting.value: {
        TaskStatus.completed.value,
        TaskStatus.completed_with_errors.value,
        TaskStatus.failed.value,
    },
    TaskStatus.completed.value: {TaskStatus.cutting.value},
    TaskStatus.completed_with_errors.value: {TaskStatus.cutting.value},
    TaskStatus.failed.value: {
        TaskStatus.pending_processing.value,
        TaskStatus.audio_extracting.value,
        TaskStatus.transcribing.value,
        TaskStatus.pending_ai.value,
        TaskStatus.ai_analyzing.value,
        TaskStatus.cutting.value,
    },
    TaskStatus.CREATED.value: {TaskStatus.PREPARING_SOURCE.value},
    TaskStatus.PREPARING_SOURCE.value: {
        TaskStatus.TRANSCRIBING.value,
        TaskStatus.FAILED_PREPARING_SOURCE.value,
        TaskStatus.CANCELLED.value,
    },
    TaskStatus.TRANSCRIBING.value: {
        TaskStatus.AI_ANALYZING.value,
        TaskStatus.FAILED_TRANSCRIBING.value,
        TaskStatus.CANCELLED.value,
    },
    TaskStatus.AI_ANALYZING.value: {
        TaskStatus.CLIP_SELECTING.value,
        TaskStatus.FAILED_AI_ANALYZING.value,
        TaskStatus.CANCELLED.value,
    },
    TaskStatus.CLIP_SELECTING.value: {
        TaskStatus.VIDEO_CUTTING.value,
        TaskStatus.FAILED_CLIP_SELECTING.value,
        TaskStatus.CANCELLED.value,
    },
    TaskStatus.VIDEO_CUTTING.value: {
        TaskStatus.SUBTITLE_DRAFTING.value,
        TaskStatus.FAILED_VIDEO_CUTTING.value,
        TaskStatus.CANCELLED.value,
    },
    TaskStatus.SUBTITLE_DRAFTING.value: {
        TaskStatus.PENDING_SUBTITLE_REVIEW.value,
        TaskStatus.FAILED_SUBTITLE_DRAFTING.value,
        TaskStatus.CANCELLED.value,
    },
    TaskStatus.PENDING_SUBTITLE_REVIEW.value: {TaskStatus.METADATA_GENERATING.value},
    TaskStatus.METADATA_GENERATING.value: {
        TaskStatus.SCHEDULE_CREATING.value,
        TaskStatus.FAILED_METADATA_GENERATING.value,
        TaskStatus.CANCELLED.value,
    },
    TaskStatus.SCHEDULE_CREATING.value: {
        TaskStatus.PUBLISH_JOB_CREATING.value,
        TaskStatus.FAILED_SCHEDULE_CREATING.value,
        TaskStatus.CANCELLED.value,
    },
    TaskStatus.PUBLISH_JOB_CREATING.value: {
        TaskStatus.READY_TO_PUBLISH.value,
        TaskStatus.FAILED_PUBLISH_JOB_CREATING.value,
        TaskStatus.CANCELLED.value,
    },
    TaskStatus.FAILED_PREPARING_SOURCE.value: {TaskStatus.PREPARING_SOURCE.value},
    TaskStatus.FAILED_TRANSCRIBING.value: {TaskStatus.TRANSCRIBING.value},
    TaskStatus.FAILED_AI_ANALYZING.value: {TaskStatus.AI_ANALYZING.value},
    TaskStatus.FAILED_CLIP_SELECTING.value: {TaskStatus.CLIP_SELECTING.value},
    TaskStatus.FAILED_VIDEO_CUTTING.value: {TaskStatus.VIDEO_CUTTING.value},
    TaskStatus.FAILED_SUBTITLE_DRAFTING.value: {TaskStatus.SUBTITLE_DRAFTING.value},
    TaskStatus.FAILED_METADATA_GENERATING.value: {TaskStatus.METADATA_GENERATING.value},
    TaskStatus.FAILED_SCHEDULE_CREATING.value: {TaskStatus.SCHEDULE_CREATING.value},
    TaskStatus.FAILED_PUBLISH_JOB_CREATING.value: {TaskStatus.PUBLISH_JOB_CREATING.value},
    TaskStatus.CANCELLED.value: {TaskStatus.PREPARING_SOURCE.value},
}


ACTIVE_TASK_STATUSES = {
    TaskStatus.CREATED.value,
    TaskStatus.PREPARING_SOURCE.value,
    TaskStatus.TRANSCRIBING.value,
    TaskStatus.AI_ANALYZING.value,
    TaskStatus.CLIP_SELECTING.value,
    TaskStatus.VIDEO_CUTTING.value,
    TaskStatus.SUBTITLE_DRAFTING.value,
    TaskStatus.METADATA_GENERATING.value,
    TaskStatus.SCHEDULE_CREATING.value,
    TaskStatus.PUBLISH_JOB_CREATING.value,
    TaskStatus.audio_extracting.value,
    TaskStatus.transcribing.value,
    TaskStatus.ai_analyzing.value,
    TaskStatus.cutting.value,
}


def create_task_record(payload: TaskCreate, task_id: str | None = None, task_dir_name: str | None = None) -> dict:
    from app.services.task_service import _now_iso, get_status_label, STATUS_PROGRESS  # noqa: F811

    resolved_task_id = task_id or uuid4().hex[:12]
    resolved_task_dir_name = task_dir_name
    now = _now_iso()
    source_path = payload.original_video_path
    has_source_file = bool(source_path)
    media_preflight = None
    if source_path:
        valid, error_message = validate_source_video_path(source_path)
        if not valid:
            raise ValueError(error_message)
        output_limit = (
            payload.highlight_total_limit
            if payload.selection_profile == "long_live_talk"
            else payload.candidate_clip_count
        )
        media_preflight = preflight_media(source_path, total_output_limit=output_limit)

    if not resolved_task_dir_name:
        resolved_task_dir_name = allocate_task_dir_name(
            payload.task_name,
            exclude_task_id=resolved_task_id,
        )
    create_task_directory(resolved_task_id, resolved_task_dir_name)

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
            "source_type": "upload",
            "platform": payload.platform,
            "original_video_path": payload.original_video_path,
            "nas_file_path": None,
            "max_clip_duration": payload.max_clip_duration,
            "candidate_clip_count": payload.candidate_clip_count,
            "selection_profile": payload.selection_profile,
            "final_clip_target": payload.final_clip_target,
            "highlight_density_per_hour": payload.highlight_density_per_hour,
            "highlight_total_limit": payload.highlight_total_limit,
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
            insert_data["source_path"] = payload.original_video_path
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
    result = {
        "id": resolved_task_id,
        "task_name": payload.task_name,
        "task_dir_name": resolved_task_dir_name,
        "status": initial_status,
        "status_label": get_status_label(initial_status),
        "auto_mode": payload.auto_mode,
        "detail_url": f"/tasks/{resolved_task_id}",
        "message": "任务已创建并写入数据库。",
    }
    if media_preflight:
        result["media_preflight"] = media_preflight.to_dict()
    return result


def update_task_status(
    task_id: str,
    new_status: TaskStatus,
    error_message: str | None = None,
) -> dict | None:
    from app.services.task_service import _now_iso, get_task, STATUS_PROGRESS  # noqa: F811
    from app.services import job_service

    status_value = new_status.value
    progress = STATUS_PROGRESS.get(status_value, 0)
    active_lease = job_service.current_job_lease()

    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        now = _now_iso()
        current = connection.execute(
            "SELECT COALESCE(is_deleted, 0) AS is_deleted FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        if not current:
            connection.rollback()
            return None
        if int(current["is_deleted"] or 0):
            connection.rollback()
            raise TaskStatusConflictError("任务已永久删除，不能再更新处理状态")

        lease_condition = ""
        lease_params: tuple[str, ...] = ()
        if active_lease:
            active_job_id, lease_owner, lease_token = active_lease
            cancel_condition = "" if new_status == TaskStatus.CANCELLED else "AND cancel_requested = 0"
            lease_condition = f"""
                AND EXISTS (
                    SELECT 1 FROM workflow_jobs
                    WHERE id = ? AND task_id = ? AND status = ?
                      AND lease_owner = ? AND lease_token = ?
                      AND lease_expires_at > ? {cancel_condition}
                )
            """
            lease_params = (
                active_job_id,
                task_id,
                job_service.JOB_STATUS_RUNNING,
                lease_owner,
                lease_token,
                now,
            )
        cursor = connection.execute(
            f"""
            UPDATE tasks
            SET status = ?, progress = ?, error_message = ?, last_error = ?, updated_at = ?
            WHERE id = ? AND COALESCE(is_deleted, 0) = 0 {lease_condition}
            """,
            (status_value, progress, error_message, error_message, now, task_id, *lease_params),
        )
        if cursor.rowcount == 0:
            connection.rollback()
            if active_lease:
                raise job_service.JobLeaseLostError(
                    f"Workflow Job 租约已失效，拒绝覆盖 Task 状态：{active_lease[0]}"
                )
            raise TaskStatusConflictError("任务状态更新失败，请刷新后重试")
        connection.commit()

    return get_task(task_id, include_video_probe=False)


def transition_task_status(
    task_id: str,
    new_status: TaskStatus,
    error_message: str | None = None,
) -> dict | None:
    """执行对外可见的受控状态转换，并避免覆盖并发产生的新状态。"""
    from app.services.task_service import _now_iso, get_task, STATUS_PROGRESS  # noqa: F811

    status_value = new_status.value
    progress = STATUS_PROGRESS.get(status_value, 0)
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        now = _now_iso()
        current = connection.execute(
            "SELECT status, COALESCE(is_deleted, 0) AS is_deleted FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        if not current:
            connection.rollback()
            return None
        if int(current["is_deleted"] or 0):
            connection.rollback()
            raise TaskStatusConflictError("任务已永久删除，不能再更新处理状态")

        current_status = str(current["status"] or "")
        allowed = PUBLIC_TASK_STATUS_TRANSITIONS.get(current_status, set())
        if status_value != current_status and status_value not in allowed:
            connection.rollback()
            raise TaskStatusConflictError(
                f"不允许从 {current_status or '未知状态'} 跳转到 {status_value}"
            )
        _validate_public_transition_preconditions(connection, task_id, status_value)
        if status_value == TaskStatus.CANCELLED.value:
            from app.services import job_service

            job_service.cancel_active_auto_pipeline_jobs_for_task(
                connection,
                task_id,
                now=now,
            )
            error_message = error_message or "用户已取消全自动流水线"

        cursor = connection.execute(
            """
            UPDATE tasks
            SET status = ?, progress = ?, error_message = ?, last_error = ?, updated_at = ?
            WHERE id = ? AND status = ? AND COALESCE(is_deleted, 0) = 0
            """,
            (status_value, progress, error_message, error_message, now, task_id, current_status),
        )
        if cursor.rowcount != 1:
            connection.rollback()
            raise TaskStatusConflictError("任务状态已被其他流程更新，请刷新后重试")
        connection.commit()
    return get_task(task_id, include_video_probe=False)


def _validate_public_transition_preconditions(
    connection: Connection,
    task_id: str,
    status_value: str,
) -> None:
    if status_value == TaskStatus.pending_processing.value:
        source = connection.execute(
            "SELECT original_video_path FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        if not source or not str(source["original_video_path"] or "").strip():
            raise TaskStatusConflictError("任务尚未绑定源视频，不能进入待处理状态")
    elif status_value == TaskStatus.cutting.value:
        enabled = connection.execute(
            """
            SELECT 1 FROM clip_candidates
            WHERE task_id = ? AND enabled = 1 AND COALESCE(is_deleted, 0) = 0
            LIMIT 1
            """,
            (task_id,),
        ).fetchone()
        if not enabled:
            raise TaskStatusConflictError("任务没有已启用的候选片段，不能进入切割状态")
    elif status_value in {TaskStatus.completed.value, TaskStatus.completed_with_errors.value}:
        output = connection.execute(
            """
            SELECT 1 FROM output_clip
            WHERE task_id = ? AND status = 'completed' AND COALESCE(is_active, 1) = 1
            LIMIT 1
            """,
            (task_id,),
        ).fetchone()
        if not output:
            raise TaskStatusConflictError("任务没有成功的活跃切片，不能标记完成")


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
    highlight_density_per_hour: int = 4,
    highlight_total_limit: int = 30,
) -> dict:
    from app.services.task_service import _now_iso, get_task  # noqa: F811

    task = get_task(task_id, include_video_probe=False)
    if not task:
        raise ValueError("任务不存在")
    if selection_profile not in {"general", "variety_comedy", "long_live_talk"}:
        raise ValueError("选片模式只能是通用内容价值、康熙笑点选片模式或长直播高光")
    if final_clip_target < 1 or final_clip_target > 12:
        raise ValueError("最终启用目标必须在 1 到 12 条之间")
    if selection_profile != "long_live_talk":
        highlight_density_per_hour = 4
        highlight_total_limit = 30
    if not 1 <= highlight_density_per_hour <= 10:
        raise ValueError("每小时高光密度必须在 1 到 10 条之间")
    if not 1 <= highlight_total_limit <= 50:
        raise ValueError("高光总上限必须在 1 到 50 条之间")

    now = _now_iso()
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE tasks
            SET selection_profile = ?, final_clip_target = ?,
                highlight_density_per_hour = ?, highlight_total_limit = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                selection_profile,
                final_clip_target,
                highlight_density_per_hour,
                highlight_total_limit,
                now,
                task_id,
            ),
        )
        connection.commit()

    profile_label = {
        "general": "通用内容价值",
        "variety_comedy": "康熙笑点选片模式",
        "long_live_talk": "长直播高光（语言类）",
    }[selection_profile]
    append_task_log(task_id, f"已更新选片模式：{profile_label}，最终启用目标：{final_clip_target} 条")
    return {
        "status": "ok",
        "message": f"已保存{profile_label}，最终启用目标为 {final_clip_target} 条。",
        "task": get_task(task_id, include_video_probe=False),
    }


def delete_task_permanently(task_id: str) -> dict:
    from app.services.task_service import _now_iso, get_task  # noqa: F811

    task = get_task(task_id, include_video_probe=False, include_deleted=True)
    if not task:
        raise ValueError("任务不存在")
    cleanup_plan = build_task_media_cleanup_plan(task)
    existing_target_count = len(cleanup_plan.existing_targets)
    now = _now_iso()
    staged_cleanup = None
    with get_connection() as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT status, COALESCE(is_deleted, 0) AS is_deleted FROM tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
            if not current:
                raise ValueError("任务不存在")

            if not current["is_deleted"] and str(current["status"] or "") in ACTIVE_TASK_STATUSES:
                raise TaskDeletionConflictError("任务正在处理，请等待处理结束后再永久删除。")

            conflicting_task = connection.execute(
                """
                SELECT id
                FROM tasks
                WHERE id != ? AND COALESCE(is_deleted, 0) = 0
                  AND LOWER(COALESCE(task_dir_name, id)) = LOWER(?)
                LIMIT 1
                """,
                (task_id, str(task.get("task_dir_name") or task_id)),
            ).fetchone()
            if conflicting_task:
                raise TaskDeletionConflictError(
                    "该目录仍被另一条有效任务使用，已拒绝删除以避免误删。"
                )

            running_job = connection.execute(
                "SELECT id FROM workflow_jobs WHERE task_id = ? AND status = 'running' LIMIT 1",
                (task_id,),
            ).fetchone()
            if running_job:
                raise TaskDeletionConflictError("任务仍有后台切片工作正在运行，请等待结束后再删除。")

            publishing_job = connection.execute(
                "SELECT id FROM publish_jobs WHERE task_id = ? AND status = 'PUBLISHING' LIMIT 1",
                (task_id,),
            ).fetchone()
            if publishing_job:
                raise TaskDeletionConflictError("任务正在向平台发送视频，请等待发送结束后再删除。")

            staged_cleanup = stage_task_media_cleanup_plan(cleanup_plan)
            connection.execute(
                """
                UPDATE workflow_jobs
                SET status = 'cancelled', progress = 100,
                    message = '任务已永久删除，排队任务已取消',
                    error_message = '任务已永久删除', finished_at = ?, updated_at = ?
                WHERE task_id = ? AND status = 'queued'
                """,
                (now, now, task_id),
            )
            connection.execute(
                """
                UPDATE publish_jobs
                SET status = 'CANCELLED', scheduled_at = '', next_attempt_at = NULL,
                    error_code = 'task_deleted', error_message = '任务已永久删除',
                    last_error = '任务已永久删除', history_hidden = 1,
                    finished_at = ?, updated_at = ?
                WHERE task_id = ?
                  AND status NOT IN ('PUBLISHED', 'EXPORTED', 'NEED_REVIEW', 'CANCELLED')
                """,
                (now, now, task_id),
            )
            connection.execute(
                """
                UPDATE tasks
                SET is_deleted = 1, deleted_at = COALESCE(deleted_at, ?), updated_at = ?
                WHERE id = ?
                """,
                (now, now, task_id),
            )
            connection.commit()
        except Exception as exc:
            try:
                connection.rollback()
            finally:
                if staged_cleanup is not None:
                    try:
                        rollback_staged_task_media_cleanup(staged_cleanup)
                    except Exception as rollback_exc:
                        raise RuntimeError(
                            "数据库删除状态提交失败，且媒体自动恢复未完成；"
                            f"请保留隔离清单并人工恢复。原错误：{exc}；恢复错误：{rollback_exc}"
                        ) from exc
            raise

    cleanup_result = finalize_staged_task_media_cleanup(staged_cleanup)
    if cleanup_result.cleanup_pending:
        status = "cleanup_pending"
    else:
        status = "already_deleted" if task.get("is_deleted") and existing_target_count == 0 else "deleted"
    freed_mb = cleanup_result.freed_bytes / (1024 * 1024)
    if status == "cleanup_pending":
        message = (
            "任务已从系统中永久隐藏，但隔离区文件暂时无法清除；"
            "清单已保留，可安全重试清理。"
        )
    elif status == "already_deleted":
        message = "任务已经永久删除，当前没有残留的任务视频文件。"
    else:
        message = f"任务已永久删除，共释放约 {freed_mb:.1f} MB；数据库历史记录已隐藏保留。"
    return {
        "status": status,
        "task_id": task_id,
        "freed_bytes": cleanup_result.freed_bytes,
        "external_source_preserved": cleanup_result.external_source_preserved,
        "deleted_paths": list(cleanup_result.deleted_paths),
        "cleanup_pending": cleanup_result.cleanup_pending,
        "staged_paths": list(cleanup_result.staged_paths),
        "message": message,
    }


def soft_delete_task(task_id: str) -> dict:
    """兼容旧调用名称；实际执行永久媒体删除并保留隐藏数据库记录。"""
    return delete_task_permanently(task_id)
