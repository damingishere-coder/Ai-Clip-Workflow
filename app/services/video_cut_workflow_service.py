"""视频切割工作流服务

从 task_service 中拆分出来的视频切片流程函数。
"""

from uuid import uuid4

from app.core.config import settings
from app.models.task import TaskStatus
from app.services.storage_service import get_artifact_paths, get_source_video_path, validate_source_video_path
from app.services.task_log_service import append_task_log
from app.services.video_cut_service import CutResult, cut_clips


def _insert_output_clip_record(task_id: str, result: CutResult) -> None:
    from app.db.database import get_connection
    from app.services.task_service import _now_iso

    now = _now_iso()
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO output_clip (
                id, task_id, clip_candidate_id, output_file_path, output_file_name,
                status, error_message, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid4().hex[:12],
                task_id,
                result.clip_candidate_id,
                result.output_file_path,
                result.output_file_name,
                result.status,
                result.error_message,
                now,
                now,
            ),
        )
        connection.commit()


def _clear_previous_output_clip_records(task_id: str) -> None:
    from app.db.database import get_connection

    with get_connection() as connection:
        connection.execute("DELETE FROM output_clip WHERE task_id = ?", (task_id,))
        connection.commit()


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


def process_task_video_cuts(task_id: str) -> dict:
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
    _clear_previous_output_clip_records(task_id)

    try:
        results = cut_clips(
            source_video=source_path,
            clips=enabled_clips,
            output_dir=paths["clips_dir"],
            strategy=settings.default_cut_strategy,
        )
    except Exception as exc:
        error = str(exc)
        update_task_status(task_id, TaskStatus.failed, error)
        append_task_log(task_id, f"视频切割失败：{error}")
        raise

    for result in results:
        _insert_output_clip_record(task_id, result)
        if result.status == "completed":
            append_task_log(task_id, f"切片完成：{result.output_file_name}")
        else:
            append_task_log(task_id, f"切片失败：{result.clip_candidate_id}，原因：{result.error_message}")

    final_status, final_error = _resolve_final_cut_status(results)
    update_task_status(task_id, final_status, final_error)
    append_task_log(task_id, f"自动切割结束：{get_status_label(final_status.value)}")

    return {
        "status": final_status.value,
        "status_label": get_status_label(final_status.value),
        "message": "视频切割流程已完成",
        "output_dir": str(paths["clips_dir"]),
        "results": [result.__dict__ for result in results],
        "task": get_task(task_id),
    }
