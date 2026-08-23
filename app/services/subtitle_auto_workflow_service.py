"""字幕审核暂停、批量烧录与自动流水线恢复。"""

from __future__ import annotations

import json
from typing import Any

from app.db.database import get_connection
from app.models.task import TaskStatus
from app.services import job_service
from app.services.storage_service import get_artifact_paths, resolve_video_file_path
from app.services.subtitle_data_service import (
    approve_revision,
    ensure_clip_track,
    ensure_source_track,
    get_revision,
)
from app.services.subtitle_workflow_service import (
    SubtitleRenderCancelled,
    render_subtitles_for_output_clip,
)
from app.services.task_log_service import append_task_log


def prepare_task_subtitle_review(task_id: str) -> dict[str, Any]:
    """从原片主时间轴生成所有成功切片的字幕草稿。"""
    from app.services import task_service

    task = task_service.get_task(task_id, include_video_probe=False)
    if not task:
        raise ValueError("任务不存在")
    source_track = ensure_source_track(task_id)
    output_clips = [
        item
        for item in task_service.list_output_clips(task_id)
        if item.get("status") == "completed" and item.get("file_exists")
    ]
    if not output_clips:
        raise ValueError("没有可生成字幕草稿的成功切片")
    tracks = [ensure_clip_track(task_id, item["id"]) for item in output_clips]
    task_service.update_task_status(task_id, TaskStatus.PENDING_SUBTITLE_REVIEW)
    append_task_log(task_id, f"字幕草稿已生成：{len(tracks)} 条，流水线暂停等待人工审核")
    return {
        "status": "pending_subtitle_review",
        "source_track_id": source_track["id"],
        "track_count": len(tracks),
        "tracks": tracks,
    }


def enqueue_task_subtitle_render(
    task_id: str,
    *,
    output_clip_ids: list[str] | None = None,
    approve_active_revisions: bool = False,
    continue_pipeline: bool = False,
) -> dict[str, Any]:
    from app.services import task_service

    task = task_service.get_task(task_id, include_video_probe=False)
    if not task:
        raise ValueError("任务不存在")
    if continue_pipeline and task.get("status") != TaskStatus.PENDING_SUBTITLE_REVIEW.value:
        raise ValueError("当前任务不在字幕审核暂停状态")
    requested = set(output_clip_ids or [])
    output_clips = [
        item
        for item in task_service.list_output_clips(task_id)
        if item.get("status") == "completed"
        and item.get("file_exists")
        and (not requested or item["id"] in requested)
    ]
    if requested and {item["id"] for item in output_clips} != requested:
        raise ValueError("部分切片不存在或尚未生成完成")
    if not output_clips:
        raise ValueError("没有可烧录字幕的成功切片")

    items = []
    for output_clip in output_clips:
        track = ensure_clip_track(task_id, output_clip["id"])
        revision_id = str(track.get("active_revision_id") or "")
        if not revision_id:
            raise ValueError(f"{output_clip.get('output_file_name') or output_clip['id']} 没有字幕 revision")
        revision = get_revision(revision_id, include_cues=True)
        if int(revision.get("cue_count") or 0) <= 0:
            raise ValueError(f"{output_clip.get('output_file_name') or output_clip['id']} 没有可烧录的字幕内容")
        if approve_active_revisions:
            revision = approve_revision(track["id"], revision_id)
        if revision.get("status") != "approved":
            raise ValueError(f"{output_clip.get('output_file_name') or output_clip['id']} 的字幕尚未审核")
        items.append(
            {
                "output_clip_id": output_clip["id"],
                "revision_id": revision_id,
                "output_file_name": output_clip.get("output_file_name") or output_clip["id"],
            }
        )

    job, created = job_service.create_or_get_active_job(
        task_id=task_id,
        job_type=job_service.JOB_TYPE_SUBTITLE,
        payload={
            "items": items,
            "continue_pipeline": continue_pipeline,
            "subtitle_delivery_mode": "subtitled",
        },
    )
    if continue_pipeline and not bool((job.get("payload_json") or {}).get("continue_pipeline")):
        raise ValueError("已有单条字幕任务正在运行，请等待完成或取消后再启动批量烧录")
    if continue_pipeline:
        _set_subtitle_delivery_mode(task_id, "subtitled")
    append_task_log(
        task_id,
        f"字幕批量烧录{'已加入队列' if created else '已在队列中'}：{len(items)} 条",
    )
    return {
        "status": job["status"],
        "message": "字幕批量烧录已加入持久化队列" if created else "已有字幕烧录任务正在排队或运行",
        "job": job,
        "job_id": job["id"],
        "created": created,
        "item_count": len(items),
    }


def skip_task_subtitles_and_resume(task_id: str) -> dict[str, Any]:
    from app.services import task_service

    task = task_service.get_task(task_id, include_video_probe=False)
    if not task:
        raise ValueError("任务不存在")
    if not task.get("auto_mode"):
        raise ValueError("只有全自动任务需要执行字幕跳过决策")
    if task.get("status") != TaskStatus.PENDING_SUBTITLE_REVIEW.value:
        raise ValueError("当前任务不在字幕审核暂停状态")
    active_subtitle_jobs = [
        job
        for job in job_service.list_jobs(task_id=task_id)
        if job.get("job_type") == job_service.JOB_TYPE_SUBTITLE
        and job.get("status") in {job_service.JOB_STATUS_QUEUED, job_service.JOB_STATUS_RUNNING}
    ]
    if active_subtitle_jobs:
        raise ValueError("字幕烧录仍在运行，请先取消并等待停止后再选择跳过字幕")
    _set_subtitle_delivery_mode(task_id, "original")
    job, created = _enqueue_pipeline_resume(task_id)
    append_task_log(task_id, "用户已明确跳过字幕，后续发布内容将使用原片切片")
    return {
        "status": job["status"],
        "message": "已明确跳过字幕，流水线恢复任务已加入队列" if created else "流水线恢复任务已经在队列中",
        "job": job,
        "job_id": job["id"],
        "created": created,
    }


def execute_subtitle_render_job(job_id: str, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    items = payload.get("items") or []
    if not isinstance(items, list) or not items:
        raise ValueError("字幕 Job 没有待渲染条目")
    job = job_service.get_job(job_id) or {}
    checkpoint = job.get("checkpoint_json") if isinstance(job.get("checkpoint_json"), dict) else {}
    completed = dict(checkpoint.get("completed") or {})
    total = len(items)
    for index, item in enumerate(items):
        output_clip_id = str(item.get("output_clip_id") or "")
        revision_id = str(item.get("revision_id") or "")
        if not output_clip_id or not revision_id:
            raise ValueError("字幕 Job 条目缺少 output_clip_id 或 revision_id")
        if _checkpoint_result_is_valid(task_id, output_clip_id, revision_id, completed.get(output_clip_id)):
            continue
        if job_service.is_cancel_requested(job_id):
            raise SubtitleRenderCancelled("用户已取消字幕批量烧录")
        item_start = 5 + round(index / total * 88)
        item_end = 5 + round((index + 1) / total * 88)
        job_service.update_job_progress(
            job_id,
            item_start,
            f"正在烧录第 {index + 1}/{total} 条：{item.get('output_file_name') or output_clip_id}",
        )
        result = render_subtitles_for_output_clip(
            task_id,
            output_clip_id,
            revision_id=revision_id,
            workflow_job_id=job_id,
            progress_start=item_start,
            progress_end=item_end,
        )
        completed[output_clip_id] = {
            "revision_id": revision_id,
            "subtitle_job_id": (result.get("job") or {}).get("id") or "",
            "output_file_path": (result.get("job") or {}).get("output_file_path") or "",
        }
        job_service.update_job_checkpoint(
            job_id,
            {"completed": completed, "completed_count": len(completed), "total_count": total},
        )

    resume_job = None
    if payload.get("continue_pipeline"):
        resume_job, _ = _enqueue_pipeline_resume(task_id)
        append_task_log(task_id, "字幕成片全部验证通过，已排队恢复自动文案与发送中心流程")
    return {
        "task_id": task_id,
        "completed_count": len(completed),
        "total_count": total,
        "completed": completed,
        "resume_job_id": resume_job["id"] if resume_job else "",
    }


def cleanup_interrupted_subtitle_job(workflow_job_id: str, *, status: str, message: str) -> None:
    """父 Worker 强制终止子进程后，修正从属字幕记录并清理精确临时文件。"""
    from app.services.task_service import _now_iso

    now = _now_iso()
    with get_connection() as connection:
        row = connection.execute(
            "SELECT task_id FROM workflow_jobs WHERE id = ?",
            (workflow_job_id,),
        ).fetchone()
        connection.execute(
            """
            UPDATE subtitle_jobs
            SET status = ?, error_message = ?, updated_at = ?
            WHERE workflow_job_id = ? AND status = 'processing' AND is_active = 0
            """,
            (status, message, now, workflow_job_id),
        )
        connection.commit()
    if not row:
        return
    directory = get_artifact_paths(str(row["task_id"]))["subtitled_dir"]
    if directory.exists():
        for path in directory.glob(f"*.{workflow_job_id}.part.mp4"):
            if path.is_file():
                path.unlink(missing_ok=True)


def _checkpoint_result_is_valid(
    task_id: str,
    output_clip_id: str,
    revision_id: str,
    checkpoint: Any,
) -> bool:
    if not isinstance(checkpoint, dict) or checkpoint.get("revision_id") != revision_id:
        return False
    subtitle_job_id = str(checkpoint.get("subtitle_job_id") or "")
    if not subtitle_job_id:
        return False
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT output_file_path FROM subtitle_jobs
            WHERE id = ? AND task_id = ? AND output_clip_id = ? AND revision_id = ?
              AND status = 'completed' AND validation_status = 'verified'
            """,
            (subtitle_job_id, task_id, output_clip_id, revision_id),
        ).fetchone()
    path = resolve_video_file_path(row["output_file_path"]) if row else None
    return bool(path and path.exists() and path.is_file())


def _set_subtitle_delivery_mode(task_id: str, mode: str) -> None:
    if mode not in {"subtitled", "original"}:
        raise ValueError("字幕交付模式无效")
    from app.services.task_service import _now_iso

    now = _now_iso()
    with get_connection() as connection:
        row = connection.execute("SELECT auto_config_json FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            raise ValueError("任务不存在")
        try:
            config = json.loads(row["auto_config_json"] or "{}")
        except json.JSONDecodeError:
            config = {}
        config["subtitle_delivery_mode"] = mode
        config["subtitle_decided_at"] = now
        connection.execute(
            "UPDATE tasks SET auto_config_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(config, ensure_ascii=False), now, task_id),
        )
        connection.commit()


def _enqueue_pipeline_resume(task_id: str) -> tuple[dict[str, Any], bool]:
    return job_service.create_or_get_active_job(
        task_id=task_id,
        job_type=job_service.JOB_TYPE_AUTO_PIPELINE,
        payload={"retry": False, "start_step": TaskStatus.METADATA_GENERATING.value},
    )
