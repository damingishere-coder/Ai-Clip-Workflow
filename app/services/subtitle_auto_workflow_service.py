"""字幕审核暂停、批量烧录与自动流水线恢复。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.db.database import get_connection
from app.models.task import TaskStatus
from app.services import job_service
from app.services.storage_service import get_artifact_paths, resolve_video_file_path
from app.services.subtitle_data_service import (
    SubtitleRevisionConflict,
    approve_revisions_with_connection,
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
    allowed_statuses = {
        TaskStatus.VIDEO_CUTTING.value,
        TaskStatus.SUBTITLE_DRAFTING.value,
        TaskStatus.PENDING_SUBTITLE_REVIEW.value,
    }
    if task.get("status") not in allowed_statuses:
        raise ValueError("当前任务状态不能进入字幕审核，请刷新后重试")
    source_track = ensure_source_track(task_id)
    output_clips = [
        item
        for item in task_service.list_output_clips(task_id)
        if item.get("status") == "completed" and item.get("file_exists")
    ]
    if not output_clips:
        raise ValueError("没有可生成字幕草稿的成功切片")
    tracks = [ensure_clip_track(task_id, item["id"]) for item in output_clips]
    lease = job_service.current_job_lease()
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        active_job = connection.execute(
            """
            SELECT id FROM workflow_jobs
            WHERE task_id = ? AND status IN (?, ?)
              AND (? = '' OR id != ?)
            LIMIT 1
            """,
            (
                task_id,
                job_service.JOB_STATUS_QUEUED,
                job_service.JOB_STATUS_RUNNING,
                lease[0] if lease else "",
                lease[0] if lease else "",
            ),
        ).fetchone()
        if active_job:
            connection.rollback()
            raise ValueError("任务已有其他后台 Job，不能重复进入字幕审核")
        lease_condition = ""
        lease_params: tuple[str, ...] = ()
        if lease:
            lease_condition = """
                AND EXISTS (
                    SELECT 1 FROM workflow_jobs
                    WHERE id = ? AND task_id = ? AND status = 'running'
                      AND lease_owner = ? AND lease_token = ?
                      AND lease_expires_at > strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now')
                      AND cancel_requested = 0
                )
            """
            lease_params = (lease[0], task_id, lease[1], lease[2])
        cursor = connection.execute(
            f"""
            UPDATE tasks
            SET status = ?, progress = ?, error_message = NULL, last_error = NULL, updated_at = ?
            WHERE id = ? AND COALESCE(is_deleted, 0) = 0
              AND status IN (?, ?, ?) {lease_condition}
            """,
            (
                TaskStatus.PENDING_SUBTITLE_REVIEW.value,
                task_service.STATUS_PROGRESS[TaskStatus.PENDING_SUBTITLE_REVIEW.value],
                task_service._now_iso(),
                task_id,
                TaskStatus.VIDEO_CUTTING.value,
                TaskStatus.SUBTITLE_DRAFTING.value,
                TaskStatus.PENDING_SUBTITLE_REVIEW.value,
                *lease_params,
            ),
        )
        if cursor.rowcount != 1:
            connection.rollback()
            if lease:
                raise job_service.JobLeaseLostError("字幕草稿提交前 Workflow Job 租约已失效")
            raise ValueError("任务状态已变化，不能进入字幕审核")
        connection.commit()
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
    approvals: list[tuple[str, str]] = []
    for output_clip in output_clips:
        track = ensure_clip_track(task_id, output_clip["id"])
        revision_id = str(track.get("active_revision_id") or "")
        if not revision_id:
            raise ValueError(f"{output_clip.get('output_file_name') or output_clip['id']} 没有字幕 revision")
        revision = get_revision(revision_id)
        if int(revision.get("cue_count") or 0) <= 0:
            raise ValueError(f"{output_clip.get('output_file_name') or output_clip['id']} 没有可烧录的字幕内容")
        approvals.append((str(track["id"]), revision_id))
        items.append(
            {
                "output_clip_id": output_clip["id"],
                "revision_id": revision_id,
                "output_file_name": output_clip.get("output_file_name") or output_clip["id"],
            }
        )

    job_payload = {
        "items": items,
        "continue_pipeline": continue_pipeline,
        "subtitle_delivery_mode": "subtitled",
    }
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        if continue_pipeline:
            current_task = connection.execute(
                "SELECT status FROM tasks WHERE id = ? AND COALESCE(is_deleted, 0) = 0",
                (task_id,),
            ).fetchone()
            if not current_task or current_task["status"] != TaskStatus.PENDING_SUBTITLE_REVIEW.value:
                connection.rollback()
                raise ValueError("当前任务已离开字幕审核暂停状态，请刷新后重试")
            active_resume = connection.execute(
                """
                SELECT 1 FROM workflow_jobs
                WHERE task_id = ? AND job_type = ? AND status IN (?, ?)
                LIMIT 1
                """,
                (
                    task_id,
                    job_service.JOB_TYPE_AUTO_PIPELINE,
                    job_service.JOB_STATUS_QUEUED,
                    job_service.JOB_STATUS_RUNNING,
                ),
            ).fetchone()
            if active_resume:
                connection.rollback()
                raise ValueError("后续自动流水线已排队或运行，不能重复执行字幕烧录")
        if approve_active_revisions:
            approve_revisions_with_connection(connection, approvals)
        else:
            _validate_approved_revisions_with_connection(connection, approvals)
        job_id, created = job_service.create_or_get_active_job_with_connection(
            connection,
            task_id=task_id,
            job_type=job_service.JOB_TYPE_SUBTITLE,
            payload=job_payload,
        )
        existing_payload_row = connection.execute(
            "SELECT payload_json FROM workflow_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        try:
            existing_payload = json.loads(existing_payload_row["payload_json"] or "{}")
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("已有字幕 Job 的 payload 已损坏，请先处理该任务") from exc
        if existing_payload != job_payload:
            raise ValueError("已有不同内容的字幕任务正在排队或运行，请等待完成或取消后重试")
        if continue_pipeline:
            _set_subtitle_delivery_mode_with_connection(connection, task_id, "subtitled")
        connection.commit()
    job = job_service.get_job(job_id)
    if not job:
        raise RuntimeError("字幕 Job 创建后无法读取")
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


def skip_task_subtitles_to_review(task_id: str) -> dict[str, Any]:
    from app.services import task_service

    now = task_service._now_iso()
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        task = connection.execute(
            """
            SELECT auto_mode, status, auto_config_json
            FROM tasks
            WHERE id = ? AND COALESCE(is_deleted, 0) = 0
            """,
            (task_id,),
        ).fetchone()
        if not task:
            connection.rollback()
            raise ValueError("任务不存在")
        if not bool(task["auto_mode"]):
            connection.rollback()
            raise ValueError("只有全自动任务需要执行字幕跳过决策")
        if task["status"] != TaskStatus.PENDING_SUBTITLE_REVIEW.value:
            connection.rollback()
            raise ValueError("当前任务不在字幕审核暂停状态")
        active_subtitle_job = connection.execute(
            """
            SELECT 1 FROM workflow_jobs
            WHERE task_id = ? AND job_type IN (?, ?) AND status IN (?, ?)
            LIMIT 1
            """,
            (
                task_id,
                job_service.JOB_TYPE_SUBTITLE,
                job_service.JOB_TYPE_AUTO_PIPELINE,
                job_service.JOB_STATUS_QUEUED,
                job_service.JOB_STATUS_RUNNING,
            ),
        ).fetchone()
        if active_subtitle_job:
            connection.rollback()
            raise ValueError("字幕烧录或后续自动流水线仍在运行，请先取消并等待停止后再选择跳过字幕")
        try:
            config = json.loads(task["auto_config_json"] or "{}")
        except (TypeError, json.JSONDecodeError) as exc:
            connection.rollback()
            raise ValueError("任务字幕配置已损坏，请先修复配置后重试") from exc
        if not isinstance(config, dict):
            connection.rollback()
            raise ValueError("任务字幕配置格式无效，请先修复配置后重试")
        config["subtitle_delivery_mode"] = "original"
        config["subtitle_decided_at"] = now
        connection.execute(
            """
            UPDATE tasks
            SET auto_config_json = ?, status = ?, progress = ?,
                error_message = NULL, last_error = NULL, updated_at = ?
            WHERE id = ? AND status = ?
            """,
            (
                json.dumps(config, ensure_ascii=False),
                TaskStatus.pending_review.value,
                task_service.STATUS_PROGRESS[TaskStatus.pending_review.value],
                now,
                task_id,
                TaskStatus.PENDING_SUBTITLE_REVIEW.value,
            ),
        )
        connection.commit()
    review_url = f"/tasks/{task_id}/clips/review"
    append_task_log(task_id, "用户已明确跳过字幕，已进入片段审核；后续发送中心使用原片切片")
    return {
        "status": TaskStatus.pending_review.value,
        "message": "已跳过字幕，请审核片段后再同步发送中心。",
        "review_url": review_url,
    }


def skip_task_subtitles_and_resume(task_id: str) -> dict[str, Any]:
    """兼容旧页面缓存；行为已调整为跳过字幕后进入片段审核。"""
    return skip_task_subtitles_to_review(task_id)


def execute_subtitle_render_job(job_id: str, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    items = payload.get("items") or []
    if not isinstance(items, list) or not items:
        raise ValueError("字幕 Job 没有待渲染条目")
    reconcile_interrupted_subtitle_job(job_id)
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
        recovered = _find_recoverable_subtitle_result(
            task_id,
            output_clip_id,
            revision_id,
            workflow_job_id=job_id,
        )
        if recovered:
            completed[output_clip_id] = recovered
            job_service.update_job_checkpoint(
                job_id,
                {"completed": completed, "completed_count": len(completed), "total_count": total},
            )
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

    return {
        "task_id": task_id,
        "completed_count": len(completed),
        "total_count": total,
        "completed": completed,
        "resume_requested": bool(payload.get("continue_pipeline")),
        "resume_job_id": "",
    }


def cleanup_interrupted_subtitle_job(
    workflow_job_id: str,
    *,
    lease_owner: str,
    lease_token: str,
    status: str,
    message: str,
) -> bool:
    """父 Worker 强制终止子进程后，修正从属字幕记录并清理精确临时文件。"""
    from app.services.task_service import _now_iso

    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        now = _now_iso()
        row = connection.execute(
            """
            SELECT task_id FROM workflow_jobs
            WHERE id = ? AND status = 'running' AND lease_owner = ? AND lease_token = ?
              AND lease_expires_at > ?
            """,
            (workflow_job_id, lease_owner, lease_token, now),
        ).fetchone()
        if not row:
            connection.rollback()
            return False
        connection.execute(
            """
            UPDATE subtitle_jobs
            SET status = ?, error_message = ?, updated_at = ?
            WHERE workflow_job_id = ? AND status = 'processing' AND is_active = 0
            """,
            (status, message, now, workflow_job_id),
        )
        connection.commit()
    _, failures = _cleanup_subtitle_attempt_files(str(row["task_id"]), workflow_job_id)
    if failures:
        append_task_log(
            str(row["task_id"]),
            f"字幕中断产物有 {len(failures)} 个暂时无法删除，将在下次接管时重试：{'; '.join(failures)}",
        )
    return True


def reconcile_interrupted_subtitle_job(workflow_job_id: str) -> dict[str, Any]:
    """新执行接管后收口旧 processing 记录，并再次清理本 Job 的临时文件。"""
    from app.services.task_service import _now_iso

    lease = job_service.current_job_lease()
    if not lease or lease[0] != workflow_job_id:
        raise job_service.JobLeaseLostError(f"字幕 Job 缺少当前执行租约：{workflow_job_id}")
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        now = _now_iso()
        row = connection.execute(
            """
            SELECT task_id FROM workflow_jobs
            WHERE id = ? AND status = 'running' AND lease_owner = ? AND lease_token = ?
              AND lease_expires_at > ? AND cancel_requested = 0
            """,
            (workflow_job_id, lease[1], lease[2], now),
        ).fetchone()
        if not row:
            connection.rollback()
            raise job_service.JobLeaseLostError(f"字幕 Job 接管前租约已失效：{workflow_job_id}")
        cursor = connection.execute(
            """
            UPDATE subtitle_jobs
            SET status = 'failed', error_message = '上一次执行中断，已由当前 Worker 接管', updated_at = ?
            WHERE workflow_job_id = ? AND status IN ('processing', 'queued') AND is_active = 0
            """,
            (now, workflow_job_id),
        )
        interrupted_count = cursor.rowcount
        connection.commit()
    deleted_count, failures = _cleanup_subtitle_attempt_files(str(row["task_id"]), workflow_job_id)
    if failures:
        append_task_log(
            str(row["task_id"]),
            f"字幕接管时有 {len(failures)} 个中断产物仍被占用，将保留为非活跃残留：{'; '.join(failures)}",
        )
    return {
        "interrupted_count": interrupted_count,
        "deleted_artifact_count": deleted_count,
        "cleanup_failures": failures,
    }


def _cleanup_subtitle_attempt_files(task_id: str, workflow_job_id: str) -> tuple[int, list[str]]:
    directory = get_artifact_paths(task_id)["subtitled_dir"]
    if not directory.exists() or not directory.is_dir():
        return 0, []
    workflow_marker = hashlib.sha256(workflow_job_id.encode("utf-8")).hexdigest()[:12]
    expected_temp_suffixes = {
        f".{workflow_job_id}.part.mp4",
        f".{workflow_marker}.part.mp4",
    }
    expected_final_marker = f"_subtitled_{workflow_marker}_"
    with get_connection() as connection:
        referenced_names = {
            Path(str(row["output_file_path"])).name
            for row in connection.execute(
                """
                SELECT output_file_path FROM subtitle_jobs
                WHERE task_id = ? AND COALESCE(output_file_path, '') != ''
                """,
                (task_id,),
            ).fetchall()
        }
    deleted = 0
    failures: list[str] = []
    try:
        candidates = list(directory.iterdir())
    except OSError as exc:
        return 0, [f"{directory.name}: {exc}"]
    for path in candidates:
        try:
            is_owned_temp = any(path.name.endswith(suffix) for suffix in expected_temp_suffixes)
            is_owned_orphan_final = (
                expected_final_marker in path.name
                and path.name.endswith(".mp4")
                and path.name not in referenced_names
            )
            if (not is_owned_temp and not is_owned_orphan_final) or not path.is_file():
                continue
            path.unlink(missing_ok=True)
            deleted += 1
        except OSError as exc:
            failures.append(f"{path.name}: {exc}")
    return deleted, failures


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
              AND status = 'completed' AND validation_status = 'verified' AND is_active = 1
            """,
            (subtitle_job_id, task_id, output_clip_id, revision_id),
        ).fetchone()
    path = resolve_video_file_path(row["output_file_path"]) if row else None
    return bool(path and path.exists() and path.is_file())


def _find_recoverable_subtitle_result(
    task_id: str,
    output_clip_id: str,
    revision_id: str,
    *,
    workflow_job_id: str,
) -> dict[str, str] | None:
    """恢复“DB 已提交但 checkpoint 尚未写入”的同一执行结果。"""
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT sj.id, sj.output_file_path
            FROM subtitle_jobs sj
            JOIN subtitle_tracks st
              ON st.task_id = sj.task_id AND st.output_clip_id = sj.output_clip_id
             AND st.track_type = 'clip' AND st.is_active = 1
            JOIN subtitle_revisions sr ON sr.id = sj.revision_id AND sr.track_id = st.id
            WHERE sj.task_id = ? AND sj.output_clip_id = ? AND sj.revision_id = ?
              AND sj.workflow_job_id = ? AND sj.status = 'completed'
              AND sj.validation_status = 'verified' AND sj.is_active = 1
              AND st.active_revision_id = sj.revision_id AND sr.status = 'approved'
            ORDER BY sj.updated_at DESC LIMIT 1
            """,
            (task_id, output_clip_id, revision_id, workflow_job_id),
        ).fetchone()
    path = resolve_video_file_path(row["output_file_path"]) if row else None
    if not path or not path.exists() or not path.is_file():
        return None
    return {
        "revision_id": revision_id,
        "subtitle_job_id": str(row["id"]),
        "output_file_path": str(row["output_file_path"]),
    }


def _validate_approved_revisions_with_connection(
    connection,
    approvals: list[tuple[str, str]],
) -> None:
    for track_id, revision_id in approvals:
        row = connection.execute(
            """
            SELECT st.active_revision_id, sr.track_id, sr.status, sr.cue_count
            FROM subtitle_tracks st
            JOIN subtitle_revisions sr ON sr.id = ?
            WHERE st.id = ? AND st.is_active = 1
            """,
            (revision_id, track_id),
        ).fetchone()
        if not row or row["track_id"] != track_id:
            raise ValueError("字幕 revision 不属于当前字幕轨")
        if str(row["active_revision_id"] or "") != revision_id:
            raise SubtitleRevisionConflict("字幕已产生新版本，请刷新后重新烧录")
        if row["status"] != "approved":
            raise ValueError("存在尚未审核的字幕 revision")
        if int(row["cue_count"] or 0) <= 0:
            raise ValueError("存在没有可烧录内容的字幕 revision")


def _set_subtitle_delivery_mode_with_connection(connection, task_id: str, mode: str) -> None:
    if mode not in {"subtitled", "original"}:
        raise ValueError("字幕交付模式无效")
    from app.services.task_service import _now_iso

    now = _now_iso()
    row = connection.execute("SELECT auto_config_json FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not row:
        raise ValueError("任务不存在")
    try:
        config = json.loads(row["auto_config_json"] or "{}")
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("任务字幕配置已损坏，请先修复配置后重试") from exc
    if not isinstance(config, dict):
        raise ValueError("任务字幕配置格式无效，请先修复配置后重试")
    config["subtitle_delivery_mode"] = mode
    config["subtitle_decided_at"] = now
    connection.execute(
        "UPDATE tasks SET auto_config_json = ?, updated_at = ? WHERE id = ?",
        (json.dumps(config, ensure_ascii=False), now, task_id),
    )
