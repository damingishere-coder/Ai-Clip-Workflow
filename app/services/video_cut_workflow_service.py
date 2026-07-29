"""视频切割工作流服务

从 task_service 中拆分出来的视频切片流程函数。
支持 cut_run 版本化：每次切片创建新的 run，成功后才切换 active，失败保留旧结果。
"""

from uuid import uuid4

from app.core.config import settings
from app.models.task import TaskStatus
from app.services.storage_service import get_artifact_paths, get_source_video_path, validate_source_video_path
from app.services.task_log_service import append_task_log
from app.services.video_cut_service import CutResult, cut_clips


# ---------- Cut Run 数据库操作 ----------

def _next_cut_run_number(task_id: str) -> int:
    from app.db.database import get_connection

    with get_connection() as connection:
        row = connection.execute(
            "SELECT COALESCE(MAX(run_number), 0) AS max_run_number FROM cut_runs WHERE task_id = ?",
            (task_id,),
        ).fetchone()
    return int(row["max_run_number"] or 0) + 1


def _create_cut_run(task_id: str) -> dict:
    """创建新的切割 run，状态为 processing，is_active=0"""
    from app.db.database import get_connection
    from app.services.task_service import _now_iso

    now = _now_iso()
    run_id = uuid4().hex[:12]
    run_number = _next_cut_run_number(task_id)
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO cut_runs (id, task_id, run_number, status, is_active, created_at, updated_at)
            VALUES (?, ?, ?, 'processing', 0, ?, ?)
            """,
            (run_id, task_id, run_number, now, now),
        )
        connection.commit()
    return {"id": run_id, "run_number": run_number, "task_id": task_id}


def _activate_cut_run(task_id: str, run_id: str) -> None:
    """激活指定的 cut_run，同时将该 task 下其他 run 和旧 output_clip 标记为非活跃"""
    from app.db.database import get_connection
    from app.services.task_service import _now_iso

    now = _now_iso()
    with get_connection() as connection:
        # 将同 task 下所有其他 cut_run 设为非活跃
        connection.execute(
            "UPDATE cut_runs SET is_active = 0, updated_at = ? WHERE task_id = ? AND id != ?",
            (now, task_id, run_id),
        )
        # 激活当前 run
        connection.execute(
            "UPDATE cut_runs SET is_active = 1, status = 'completed', updated_at = ? WHERE id = ?",
            (now, run_id),
        )
        # 将同 task 下旧的 output_clip 设为非活跃
        connection.execute(
            "UPDATE output_clip SET is_active = 0 WHERE task_id = ? AND (cut_run_id IS NULL OR cut_run_id != ?)",
            (task_id, run_id),
        )
        connection.commit()


def _fail_cut_run(run_id: str, error_message: str = "") -> None:
    """将 cut_run 标记为失败"""
    from app.db.database import get_connection
    from app.services.task_service import _now_iso

    now = _now_iso()
    with get_connection() as connection:
        connection.execute(
            "UPDATE cut_runs SET status = 'failed', error_message = ?, updated_at = ? WHERE id = ?",
            (error_message, now, run_id),
        )
        connection.commit()


# ---------- Output Clip 数据库操作 ----------

def _insert_output_clip_record(task_id: str, cut_run_id: str, result: CutResult) -> None:
    from app.db.database import get_connection
    from app.services.task_service import _now_iso

    now = _now_iso()
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO output_clip (
                id, task_id, clip_candidate_id, output_file_path, output_file_name,
                status, error_message, cut_run_id, is_active, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                uuid4().hex[:12],
                task_id,
                result.clip_candidate_id,
                result.output_file_path,
                result.output_file_name,
                result.status,
                result.error_message,
                cut_run_id,
                now,
                now,
            ),
        )
        connection.commit()


def _deactivate_output_clips(task_id: str, except_run_id: str | None = None) -> None:
    """将指定 task 下的 output_clip 标记为非活跃，可选择保留某个 run 的"""
    from app.db.database import get_connection

    with get_connection() as connection:
        if except_run_id:
            connection.execute(
                "UPDATE output_clip SET is_active = 0 WHERE task_id = ? AND (cut_run_id IS NULL OR cut_run_id != ?)",
                (task_id, except_run_id),
            )
        else:
            connection.execute(
                "UPDATE output_clip SET is_active = 0 WHERE task_id = ?",
                (task_id,),
            )
        connection.commit()


# ---------- 状态解析 ----------

def _resolve_final_cut_status(results: list[CutResult]) -> tuple[TaskStatus, str | None]:
    success_count = sum(1 for result in results if result.status == "completed")
    failed_results = [result for result in results if result.status == "failed"]

    if not failed_results:
        return TaskStatus.completed, None

    error_summary = "；".join(
        result.error_message or f"{result.clip_candidate_id} 切割失败"
        for result in failed_results[:3]
    )
    if success_count > 0:
        return TaskStatus.completed_with_errors, f"部分切片失败：{error_summary}"
    return TaskStatus.failed, f"全部切片失败：{error_summary}"


# ---------- 切片主流程 ----------

def process_task_video_cuts(task_id: str, *, sync_publish_jobs: bool = True) -> dict:
    from app.services.task_service import (
        get_status_label,
        get_task,
        list_enabled_clip_candidates,
        update_task_status,
    )

    task = get_task(task_id)
    if not task:
        raise ValueError("任务不存在")

    source_path = get_source_video_path(task)
    valid, error_message = validate_source_video_path(str(source_path) if source_path else None)
    if not valid:
        update_task_status(task_id, TaskStatus.failed, error_message)
        append_task_log(task_id, f"视频切割失败：{error_message}")
        raise ValueError(error_message)

    enabled_clips = list_enabled_clip_candidates(task_id)
    if not enabled_clips:
        error = "没有任何启用片段，请先在片段审核页启用至少一条候选片段"
        update_task_status(task_id, TaskStatus.failed, error)
        append_task_log(task_id, f"视频切割失败：{error}")
        raise ValueError(error)

    paths = get_artifact_paths(task_id)
    update_task_status(task_id, TaskStatus.cutting)
    append_task_log(task_id, f"开始自动切割视频，启用片段数：{len(enabled_clips)}")

    # === 版本化：创建新的 cut_run，不删除旧记录 ===
    cut_run = _create_cut_run(task_id)
    cut_run_id = cut_run["id"]
    append_task_log(task_id, f"创建切割批次：第 {cut_run['run_number']} 次切割")

    try:
        results = cut_clips(
            source_video=source_path,
            clips=enabled_clips,
            output_dir=paths["clips_dir"],
            strategy=settings.default_cut_strategy,
        )
    except Exception as exc:
        error = str(exc)
        # 失败时：标记 cut_run 为 failed，保留旧的 active output_clip
        _fail_cut_run(cut_run_id, error)
        update_task_status(task_id, TaskStatus.failed, error)
        append_task_log(task_id, f"视频切割失败：{error}")
        raise

    # 插入新 output_clip 记录，关联到当前 cut_run
    for result in results:
        _insert_output_clip_record(task_id, cut_run_id, result)
        if result.status == "completed":
            append_task_log(task_id, f"切片完成：{result.output_file_name}")
        else:
            append_task_log(task_id, f"切片失败：{result.clip_candidate_id}，原因：{result.error_message}")

    final_status, final_error = _resolve_final_cut_status(results)

    publish_sync = None
    if final_status == TaskStatus.failed:
        # 全部失败：不激活新 run，旧 active 保持不变
        _fail_cut_run(cut_run_id, final_error or "全部切片失败")
        update_task_status(task_id, final_status, final_error)
        append_task_log(task_id, "切割批次失败，旧切片结果保留不变")
    else:
        # 成功或部分成功：激活新 run，旧 output_clip 标记为非活跃
        _activate_cut_run(task_id, cut_run_id)
        update_task_status(task_id, final_status, final_error)
        append_task_log(task_id, f"自动切割结束：{get_status_label(final_status.value)}")
        if sync_publish_jobs:
            try:
                from app.services.publish_service import sync_task_publish_jobs as sync_publish

                publish_sync = sync_publish(
                    task_id,
                    prefer_subtitled=False,
                    restore_removed=False,
                )
            except Exception as exc:
                publish_sync = {
                    "status": "partial",
                    "message": f"切片已生成，但发送中心自动同步失败：{exc}",
                    "errors": [str(exc)],
                }
                append_task_log(task_id, f"切片完成后的发送中心同步失败：{exc}")

    return {
        "status": final_status.value,
        "status_label": get_status_label(final_status.value),
        "message": "视频切割流程已完成",
        "output_dir": str(paths["clips_dir"]),
        "cut_run_id": cut_run_id,
        "cut_run_number": cut_run["run_number"],
        "results": [result.__dict__ for result in results],
        "publish_sync": publish_sync,
        "task": get_task(task_id),
    }
