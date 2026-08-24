"""v1.3.0 全自动任务流水线调度器。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any

from app.db.database import get_connection
from app.models.task import TaskStatus
from app.services import job_service, task_service
from app.services.auto_publish_service import create_auto_publish_jobs, platforms_for_task
from app.services.metadata_generator import MetadataGenerator
from app.services.pipeline_checkpoint_service import (
    AutoPipelineCheckpoint,
    PipelineCheckpointError,
)
from app.services.publish_service import generate_publish_cover_for_item
from app.services.publish_time import next_allowed_schedule_time
from app.services.storage_service import (
    IMAGE_EXTENSIONS,
    create_task_directory,
    get_artifact_paths,
    get_source_video_path,
    resolve_task_media_file_path,
    validate_source_video_path,
)
from app.services.task_log_service import append_task_log
from app.services.video_cut_service import parse_time_to_seconds


STEP_STATUSES = [
    TaskStatus.PREPARING_SOURCE,
    TaskStatus.TRANSCRIBING,
    TaskStatus.AI_ANALYZING,
    TaskStatus.CLIP_SELECTING,
    TaskStatus.VIDEO_CUTTING,
    TaskStatus.SUBTITLE_DRAFTING,
    TaskStatus.METADATA_GENERATING,
    TaskStatus.SCHEDULE_CREATING,
    TaskStatus.PUBLISH_JOB_CREATING,
]

FAILED_BY_STEP = {
    TaskStatus.PREPARING_SOURCE: TaskStatus.FAILED_PREPARING_SOURCE,
    TaskStatus.TRANSCRIBING: TaskStatus.FAILED_TRANSCRIBING,
    TaskStatus.AI_ANALYZING: TaskStatus.FAILED_AI_ANALYZING,
    TaskStatus.CLIP_SELECTING: TaskStatus.FAILED_CLIP_SELECTING,
    TaskStatus.VIDEO_CUTTING: TaskStatus.FAILED_VIDEO_CUTTING,
    TaskStatus.SUBTITLE_DRAFTING: TaskStatus.FAILED_SUBTITLE_DRAFTING,
    TaskStatus.METADATA_GENERATING: TaskStatus.FAILED_METADATA_GENERATING,
    TaskStatus.SCHEDULE_CREATING: TaskStatus.FAILED_SCHEDULE_CREATING,
    TaskStatus.PUBLISH_JOB_CREATING: TaskStatus.FAILED_PUBLISH_JOB_CREATING,
}

RETRY_START_BY_FAILED_STATUS = {
    TaskStatus.FAILED_PREPARING_SOURCE.value: TaskStatus.PREPARING_SOURCE,
    TaskStatus.FAILED_TRANSCRIBING.value: TaskStatus.TRANSCRIBING,
    TaskStatus.FAILED_AI_ANALYZING.value: TaskStatus.AI_ANALYZING,
    TaskStatus.FAILED_CLIP_SELECTING.value: TaskStatus.CLIP_SELECTING,
    TaskStatus.FAILED_VIDEO_CUTTING.value: TaskStatus.VIDEO_CUTTING,
    TaskStatus.FAILED_SUBTITLE_DRAFTING.value: TaskStatus.SUBTITLE_DRAFTING,
    TaskStatus.FAILED_METADATA_GENERATING.value: TaskStatus.METADATA_GENERATING,
    TaskStatus.FAILED_SCHEDULE_CREATING.value: TaskStatus.SCHEDULE_CREATING,
    TaskStatus.FAILED_PUBLISH_JOB_CREATING.value: TaskStatus.PUBLISH_JOB_CREATING,
}

DEFAULT_AUTO_CONFIG = {
    "auto_clip_count": "auto",
    "auto_min_clip_seconds": 15,
    "auto_max_clip_seconds": 300,
    "auto_schedule_mode": "default",
    "auto_schedule_start_at": "",
    "auto_schedule_interval_hours": 3,
    "auto_schedule_daily_start_time": "07:00",
    "auto_schedule_daily_end_time": "00:00",
    "auto_metadata_use_ai": False,
}


class PipelineCancelledError(RuntimeError):
    """用户请求取消当前全自动流水线。"""


class PipelineEngine:
    """只做流程调度，复用现有转写、AI、切片和发布任务服务。"""

    def run(
        self,
        task_id: str,
        retry: bool = False,
        start_step: TaskStatus | str | None = None,
        job_id: str | None = None,
    ) -> dict:
        task = self._get_task(task_id)
        if not task.get("auto_mode"):
            raise ValueError("该任务未开启 auto_mode，手动流程不会被自动流水线接管。")

        resolved_start_step = (
            start_step
            if isinstance(start_step, TaskStatus)
            else TaskStatus(start_step) if start_step else self._resolve_start_step(task, retry=retry)
        )
        config = self._load_auto_config(task)
        checkpoint: AutoPipelineCheckpoint | None = None
        if job_id:
            active_lease = job_service.current_job_lease()
            if not active_lease or active_lease[0] != job_id:
                raise job_service.JobLeaseLostError(
                    f"自动流水线缺少当前 Workflow Job 租约：{job_id}"
                )
            checkpoint = AutoPipelineCheckpoint.load(
                job_id=job_id,
                task_id=task_id,
                start_step=resolved_start_step.value,
                run_key=self._pipeline_run_key(task, config, resolved_start_step),
                ordered_steps=[step.value for step in STEP_STATUSES],
            )

        if checkpoint and checkpoint.has_history:
            append_task_log(
                task_id,
                f"从持久化 checkpoint 恢复全自动流水线：{resolved_start_step.value}",
            )
        elif not retry and start_step is None:
            task_service.update_task_status(task_id, TaskStatus.CREATED)
            append_task_log(task_id, "全自动流水线启动")
        elif start_step is not None:
            append_task_log(task_id, f"从已有 AI 结果继续全自动流水线：{resolved_start_step.value}")
        else:
            append_task_log(task_id, f"从失败步骤重试全自动流水线：{resolved_start_step.value}")

        context: dict[str, Any] = {"config": config, "workflow_job_id": job_id or ""}
        steps = STEP_STATUSES[STEP_STATUSES.index(resolved_start_step) :]
        handlers = {
            TaskStatus.PREPARING_SOURCE: self._prepare_source,
            TaskStatus.TRANSCRIBING: self._transcribe_or_read_text,
            TaskStatus.AI_ANALYZING: self._run_ai_analysis,
            TaskStatus.CLIP_SELECTING: self._select_clips,
            TaskStatus.VIDEO_CUTTING: self._cut_video,
            TaskStatus.SUBTITLE_DRAFTING: self._prepare_subtitle_drafts,
            TaskStatus.METADATA_GENERATING: self._generate_metadata,
            TaskStatus.SCHEDULE_CREATING: self._create_schedule,
            TaskStatus.PUBLISH_JOB_CREATING: self._create_publish_jobs,
        }

        for step in steps:
            try:
                if job_id:
                    self._raise_if_cancelled(job_id)
                    step_index = STEP_STATUSES.index(step)
                    job_service.update_job_progress(
                        job_id,
                        5 + round(step_index / max(1, len(STEP_STATUSES)) * 90),
                        f"正在执行：{step.value}",
                    )
                    job_service.heartbeat_job(job_id)

                if checkpoint and checkpoint.is_completed(step.value):
                    context[step.value] = self._restore_checkpoint_step(
                        task_id,
                        step,
                        checkpoint.step_record(step.value).get("outputs") or {},
                    )
                    append_task_log(task_id, f"checkpoint 证据有效，跳过已完成步骤：{step.value}")
                else:
                    recovered = None
                    record = checkpoint.step_record(step.value) if checkpoint else {}
                    if checkpoint and checkpoint.current_step == step.value and record.get("state") == "running":
                        recovered = self._reconcile_interrupted_step(task_id, step, record)
                    if recovered is not None:
                        outputs = self._checkpoint_outputs(task_id, step, recovered)
                        checkpoint.complete_step(step.value, outputs=outputs, recovered=True)
                        context[step.value] = recovered
                        append_task_log(task_id, f"已根据持久化产物恢复中断步骤：{step.value}")
                    else:
                        if checkpoint:
                            checkpoint.begin_step(
                                step.value,
                                baseline=self._checkpoint_baseline(task_id, step),
                            )
                        task_service.update_task_status(task_id, step)
                        context[step.value] = handlers[step](task_id, context)
                        if job_id:
                            job_service.heartbeat_job(job_id)
                            self._raise_if_cancelled(job_id)
                        if checkpoint:
                            checkpoint.complete_step(
                                step.value,
                                outputs=self._checkpoint_outputs(task_id, step, context[step.value]),
                            )
                if step == TaskStatus.SUBTITLE_DRAFTING:
                    return self._pending_subtitle_review_result(task_id)
            except PipelineCancelledError as exc:
                return self._cancel_pipeline(task_id, str(exc), context)
            except job_service.JobLeaseLostError:
                raise
            except PipelineCheckpointError as exc:
                error = str(exc)
                if checkpoint:
                    if checkpoint.is_completed(step.value):
                        checkpoint.invalidate_from(step.value, error)
                    else:
                        checkpoint.fail_step(step.value, error)
                return self._pipeline_failure(task_id, step, error)
            except Exception as exc:
                error = str(exc) or f"{step.value} 失败"
                if checkpoint:
                    checkpoint.fail_step(step.value, error)
                return self._pipeline_failure(task_id, step, error)

        if job_id:
            try:
                self._raise_if_cancelled(job_id)
            except PipelineCancelledError as exc:
                return self._cancel_pipeline(task_id, str(exc), context)
        if not self._mark_ready_to_publish(task_id, job_id):
            return self._cancel_pipeline(task_id, "用户已取消全自动流水线", context)
        append_task_log(task_id, "发布任务已创建，进入待人工确认发布状态")
        summary = self._safe_write_task_summary(task_id, "ready_to_publish", "")
        append_task_log(task_id, "全自动流水线准备完成，发布内容等待人工确认。")
        return {
            "status": "ready_to_publish",
            "summary_path": summary["summary_path"],
            "task": task_service.get_task(task_id, include_video_probe=False),
        }

    def _raise_if_cancelled(self, job_id: str) -> None:
        if job_service.is_cancel_requested(job_id):
            raise PipelineCancelledError("用户已取消全自动流水线")

    def _mark_ready_to_publish(self, task_id: str, job_id: str | None) -> bool:
        if not job_id:
            task_service.update_task_status(task_id, TaskStatus.READY_TO_PUBLISH)
            return True

        active_lease = job_service.current_job_lease()
        if not active_lease or active_lease[0] != job_id:
            raise job_service.JobLeaseLostError(
                f"Workflow Job 没有当前执行代际，不能写入 READY：{job_id}"
            )
        _, lease_owner, lease_token = active_lease
        with get_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            now = task_service._now_iso()
            job = connection.execute(
                """
                SELECT status, cancel_requested, lease_owner, lease_token, lease_expires_at
                FROM workflow_jobs WHERE id = ?
                """,
                (job_id,),
            ).fetchone()
            if not job:
                connection.rollback()
                raise ValueError("全自动 Workflow Job 不存在")
            if int(job["cancel_requested"] or 0) or job["status"] == job_service.JOB_STATUS_CANCELLED:
                connection.rollback()
                return False
            if (
                job["status"] != job_service.JOB_STATUS_RUNNING
                or job["lease_owner"] != lease_owner
                or job["lease_token"] != lease_token
                or not job["lease_expires_at"]
                or str(job["lease_expires_at"]) <= now
            ):
                connection.rollback()
                raise job_service.JobLeaseLostError(f"Workflow Job 租约已失效，不能写入 READY：{job_id}")
            final_now = task_service._now_iso()
            cursor = connection.execute(
                """
                UPDATE tasks
                SET status = ?, progress = 100, error_message = NULL,
                    last_error = NULL, updated_at = ?
                WHERE id = ? AND COALESCE(is_deleted, 0) = 0
                  AND EXISTS (
                      SELECT 1 FROM workflow_jobs
                      WHERE id = ? AND task_id = ? AND status = ?
                        AND lease_owner = ? AND lease_token = ?
                        AND lease_expires_at > ? AND cancel_requested = 0
                  )
                """,
                (
                    TaskStatus.READY_TO_PUBLISH.value,
                    final_now,
                    task_id,
                    job_id,
                    task_id,
                    job_service.JOB_STATUS_RUNNING,
                    lease_owner,
                    lease_token,
                    final_now,
                ),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise job_service.JobLeaseLostError(
                    f"Workflow Job 租约已失效或任务不可写，不能写入 READY：{job_id}"
                )
            connection.commit()
        return True

    def _cancel_pipeline(self, task_id: str, message: str, context: dict | None = None) -> dict:
        cancelled_publish_jobs = self._cancel_unpublished_auto_jobs(task_id, context or {})
        task_service.update_task_status(task_id, TaskStatus.CANCELLED, message)
        append_task_log(task_id, f"全自动流水线已取消：{message}")
        summary = self._safe_write_task_summary(task_id, "cancelled", message)
        return {
            "status": "cancelled",
            "last_error": message,
            "cancelled_publish_jobs": cancelled_publish_jobs,
            "summary_path": summary["summary_path"],
            "task": task_service.get_task(task_id, include_video_probe=False),
        }

    def _cancel_unpublished_auto_jobs(self, task_id: str, context: dict) -> int:
        publish_result = context.get(TaskStatus.PUBLISH_JOB_CREATING.value) or {}
        created_ids = [
            str(item.get("id") or "")
            for item in publish_result.get("created") or []
            if isinstance(item, dict) and item.get("id")
        ]
        if not created_ids:
            return 0
        placeholders = ", ".join("?" for _ in created_ids)
        now = task_service._now_iso()
        lease_clause = ""
        lease_params: tuple[str, ...] = ()
        workflow_job_id = str(context.get("workflow_job_id") or "")
        if workflow_job_id:
            active_lease = job_service.require_active_job_lease()
            if not active_lease or active_lease[0] != workflow_job_id:
                raise job_service.JobLeaseLostError(
                    f"取消发布草稿时 Workflow Job 租约已失效：{workflow_job_id}"
                )
            _, lease_owner, lease_token = active_lease
            lease_clause = """
                  AND EXISTS (
                      SELECT 1 FROM workflow_jobs
                        WHERE id = ? AND task_id = ? AND status = ?
                        AND lease_owner = ? AND lease_token = ?
                        AND julianday(lease_expires_at) > julianday('now')
                  )
            """
            lease_params = (
                workflow_job_id,
                task_id,
                job_service.JOB_STATUS_RUNNING,
                lease_owner,
                lease_token,
            )
        with get_connection() as connection:
            cursor = connection.execute(
                f"""
                UPDATE publish_jobs
                SET status = 'CANCELLED', error_code = 'pipeline_cancelled',
                    error_message = '全自动流水线已取消', last_error = '全自动流水线已取消',
                    finished_at = ?, updated_at = ?
                WHERE task_id = ? AND id IN ({placeholders})
                  AND status IN ('DRAFT', 'WAITING', 'SCHEDULED', 'NEED_REVIEW')
                  {lease_clause}
                """,
                (now, now, task_id, *created_ids, *lease_params),
            )
            connection.commit()
        if cursor.rowcount:
            append_task_log(task_id, f"已取消本轮新建但尚未发布的发送任务：{cursor.rowcount} 条")
        return cursor.rowcount

    def _pending_subtitle_review_result(self, task_id: str) -> dict:
        summary = self._safe_write_task_summary(
            task_id,
            TaskStatus.PENDING_SUBTITLE_REVIEW.value,
            "",
        )
        return {
            "status": "pending_subtitle_review",
            "message": "字幕草稿已生成，等待人工审核后再继续。",
            "summary_path": summary["summary_path"],
            "task": task_service.get_task(task_id, include_video_probe=False),
        }

    def _pipeline_failure(self, task_id: str, step: TaskStatus, error: str) -> dict:
        failed_status = FAILED_BY_STEP[step]
        task_service.update_task_status(task_id, failed_status, error)
        append_task_log(task_id, f"全自动流水线失败：{step.value}，原因：{error}")
        summary = self._safe_write_task_summary(task_id, "failed", error)
        return {
            "status": "failed",
            "failed_step": step.value,
            "failed_status": failed_status.value,
            "last_error": error,
            "summary_path": summary["summary_path"],
            "task": task_service.get_task(task_id, include_video_probe=False),
        }

    def _pipeline_run_key(self, task: dict, config: dict, start_step: TaskStatus) -> str:
        from app.services.transcription_checkpoint_service import fingerprint_file

        source = get_source_video_path(task)
        source_path = str(Path(source).resolve()) if source else ""
        source_fingerprint = ""
        if source and Path(source).exists() and Path(source).is_file():
            source_fingerprint = fingerprint_file(source)
        payload = {
            "task_id": task.get("id") or "",
            "source_path": source_path,
            "source_fingerprint": source_fingerprint,
            "start_step": start_step.value,
            "selection_profile": task.get("selection_profile") or "",
            "candidate_clip_count": int(task.get("candidate_clip_count") or 0),
            "final_clip_target": int(task.get("final_clip_target") or 0),
            "max_clip_duration": int(task.get("max_clip_duration") or 0),
            "config": config,
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def _enabled_candidate_ids(self, task_id: str) -> list[str]:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT id FROM clip_candidates
                WHERE task_id = ? AND enabled = 1 AND COALESCE(is_deleted, 0) = 0
                ORDER BY id
                """,
                (task_id,),
            ).fetchall()
        return [str(row["id"]) for row in rows]

    def _active_output_ids(self, task_id: str) -> list[str]:
        return sorted(
            str(item.get("id") or "")
            for item in task_service.list_output_clips(task_id)
            if item.get("status") == "completed" and item.get("file_exists") and item.get("id")
        )

    def _cut_output_evidence(self, task_id: str, cut_run_id: str) -> list[dict]:
        from app.services.transcription_checkpoint_service import fingerprint_file

        task = self._get_task(task_id)
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT id, clip_candidate_id, cut_run_id, output_file_path
                FROM output_clip
                WHERE task_id = ? AND status = 'completed' AND is_active = 1
                ORDER BY id
                """,
                (task_id,),
            ).fetchall()
        if not rows:
            raise PipelineCheckpointError("切片步骤没有 active output")

        evidence: list[dict] = []
        for row in rows:
            row_run_id = str(row["cut_run_id"] or "")
            if not row_run_id or row_run_id != cut_run_id:
                raise PipelineCheckpointError("active output 不属于当前 cut run")
            path = resolve_task_media_file_path(
                str(row["output_file_path"] or ""),
                task_id=task_id,
                task_dir_name=task.get("task_dir_name"),
                allowed_subdirectories=("05_clips", "clips"),
            )
            if path is None or not path.exists() or not path.is_file():
                raise PipelineCheckpointError("切片输出文件缺失或不在当前任务目录")
            size_bytes = int(path.stat().st_size)
            if size_bytes <= 0:
                raise PipelineCheckpointError("切片输出文件为空")
            evidence.append(
                {
                    "id": str(row["id"]),
                    "clip_candidate_id": str(row["clip_candidate_id"] or ""),
                    "cut_run_id": row_run_id,
                    "file": {
                        "path": str(path.resolve()),
                        "size_bytes": size_bytes,
                        "fingerprint": fingerprint_file(path),
                    },
                }
            )
        return evidence

    def _publish_job_evidence(self, task_id: str, job_ids: list[str]) -> list[dict]:
        normalized_ids = sorted({str(job_id) for job_id in job_ids if job_id})
        if not normalized_ids:
            raise PipelineCheckpointError("发布任务 checkpoint 没有记录任何草稿")
        placeholders = ", ".join("?" for _ in normalized_ids)
        with get_connection() as connection:
            rows = connection.execute(
                f"""
                SELECT id, output_clip_id, platform, publish_mode, video_source,
                       video_file_path, cover_file_path, scheduled_at, status,
                       title, description, caption, tags, hashtags, cover_text,
                       error_code, provider_response
                FROM publish_jobs
                WHERE task_id = ? AND id IN ({placeholders})
                ORDER BY id
                """,
                (task_id, *normalized_ids),
            ).fetchall()
        if len(rows) != len(normalized_ids):
            raise PipelineCheckpointError("发布任务 checkpoint 对应的草稿已缺失")

        evidence: list[dict] = []
        task = self._get_task(task_id)
        allowed_statuses = {"DRAFT", "WAITING", "SCHEDULED", "NEED_REVIEW"}
        for row in rows:
            try:
                provider_response = json.loads(str(row["provider_response"] or "{}"))
            except json.JSONDecodeError as exc:
                raise PipelineCheckpointError("发布任务 provider_response 已损坏") from exc
            if not isinstance(provider_response, dict):
                raise PipelineCheckpointError("发布任务 provider_response 不是对象")
            status = str(row["status"] or "").upper()
            if status not in allowed_statuses and not (
                status == "CANCELLED" and str(row["error_code"] or "") == "user_removed"
            ):
                raise PipelineCheckpointError(f"发布任务不在可恢复草稿状态：{status or 'UNKNOWN'}")
            video_source = str(row["video_source"] or "")
            video_subdirectories = (
                ("06_subtitled",)
                if video_source == "subtitled"
                else ("05_clips", "clips")
            )
            video_path = resolve_task_media_file_path(
                str(row["video_file_path"] or ""),
                task_id=task_id,
                task_dir_name=task.get("task_dir_name"),
                allowed_subdirectories=video_subdirectories,
            )
            cover_path = resolve_task_media_file_path(
                str(row["cover_file_path"] or ""),
                task_id=task_id,
                task_dir_name=task.get("task_dir_name"),
                allowed_subdirectories=("07_covers",),
                allowed_extensions=IMAGE_EXTENSIONS,
            )
            if video_path is None or not video_path.is_file() or video_path.stat().st_size <= 0:
                raise PipelineCheckpointError("发布草稿的视频文件无效")
            if cover_path is None or not cover_path.is_file() or cover_path.stat().st_size <= 0:
                raise PipelineCheckpointError("发布草稿的封面文件无效")
            from app.services.transcription_checkpoint_service import fingerprint_file

            evidence.append(
                {
                    "id": str(row["id"]),
                    "output_clip_id": str(row["output_clip_id"] or ""),
                    "platform": str(row["platform"] or ""),
                    "publish_mode": str(row["publish_mode"] or ""),
                    "video_source": video_source,
                    "video_file_path": str(video_path.resolve()),
                    "video_file_size": int(video_path.stat().st_size),
                    "video_file_fingerprint": fingerprint_file(video_path),
                    "cover_file_path": str(cover_path.resolve()),
                    "cover_file": self._file_evidence(cover_path),
                    "subtitle_delivery_mode": str(
                        provider_response.get("subtitle_delivery_mode") or ""
                    ),
                    "workflow_job_id": str(provider_response.get("workflow_job_id") or ""),
                    "scheduled_at": str(row["scheduled_at"] or ""),
                    "status": status,
                    "title": str(row["title"] or ""),
                    "description": str(row["description"] or ""),
                    "caption": str(row["caption"] or ""),
                    "tags": str(row["tags"] or ""),
                    "hashtags": str(row["hashtags"] or ""),
                    "cover_text": str(row["cover_text"] or ""),
                }
            )
        return evidence

    def _verify_publish_schedule_pairs(self, schedule_path: Path, job_evidence: list[dict]) -> None:
        scheduled_items = self._read_json_list(schedule_path)
        self._validate_metadata_items(scheduled_items)
        expected_pairs = sorted(
            (
                str((item.get("output_clip") or {}).get("id") or ""),
                str((item.get("metadata") or {}).get("platform") or ""),
            )
            for item in scheduled_items
        )
        actual_pairs = sorted(
            (str(item.get("output_clip_id") or ""), str(item.get("platform") or ""))
            for item in job_evidence
        )
        if not expected_pairs or expected_pairs != actual_pairs:
            raise PipelineCheckpointError("发布草稿与当前 schedule 的切片/平台不一致")

    def _checkpoint_baseline(self, task_id: str, step: TaskStatus) -> dict:
        paths = get_artifact_paths(task_id)
        if step == TaskStatus.AI_ANALYZING:
            with get_connection() as connection:
                row = connection.execute(
                    """
                    SELECT id FROM ai_analysis_runs
                    WHERE task_id = ? AND COALESCE(is_active, 0) = 1
                    ORDER BY run_number DESC LIMIT 1
                    """,
                    (task_id,),
                ).fetchone()
            return {
                "active_ai_run_id": str(row["id"] or "") if row else "",
                "artifact": self._safe_file_evidence(paths["analysis_path"]),
                "input_transcript": self._safe_file_evidence(paths["transcript_path"]),
            }
        if step == TaskStatus.VIDEO_CUTTING:
            with get_connection() as connection:
                row = connection.execute(
                    "SELECT COALESCE(MAX(run_number), 0) AS value FROM cut_runs WHERE task_id = ?",
                    (task_id,),
                ).fetchone()
            return {
                "max_cut_run_number": int(row["value"] or 0),
                "selected_candidate_ids": self._enabled_candidate_ids(task_id),
            }
        if step == TaskStatus.METADATA_GENERATING:
            return {
                "artifact": self._safe_file_evidence(
                    paths["analysis_path"].parent / "auto_publish_metadata.json"
                ),
                "output_clip_ids": self._active_output_ids(task_id),
            }
        if step == TaskStatus.SCHEDULE_CREATING:
            return {
                "artifact": self._safe_file_evidence(
                    paths["analysis_path"].parent / "auto_publish_schedule.json"
                ),
                "metadata_input": self._safe_file_evidence(
                    paths["analysis_path"].parent / "auto_publish_metadata.json"
                ),
            }
        if step == TaskStatus.PUBLISH_JOB_CREATING:
            return {
                "schedule": self._safe_file_evidence(
                    paths["analysis_path"].parent / "auto_publish_schedule.json"
                )
            }
        artifact_by_step = {
            TaskStatus.PREPARING_SOURCE: paths["task_dir"] / "source" / "source_reference.json",
            TaskStatus.TRANSCRIBING: paths["transcript_path"],
            TaskStatus.CLIP_SELECTING: paths["analysis_path"].parent / "auto_selected_clips.json",
        }
        artifact = artifact_by_step.get(step)
        if artifact:
            baseline = {"artifact": self._safe_file_evidence(artifact)}
            if step == TaskStatus.CLIP_SELECTING:
                baseline["input_analysis"] = self._safe_file_evidence(paths["analysis_path"])
            return baseline
        if step == TaskStatus.SUBTITLE_DRAFTING:
            from app.services.subtitle_data_service import list_task_tracks

            return {
                "track_ids": sorted(
                    str(item.get("id") or "")
                    for item in list_task_tracks(task_id, ensure=False)
                    if item.get("id")
                ),
                "output_clip_ids": self._active_output_ids(task_id),
            }
        return {}

    def _checkpoint_outputs(self, task_id: str, step: TaskStatus, result: dict) -> dict:
        paths = get_artifact_paths(task_id)
        if step == TaskStatus.PREPARING_SOURCE:
            return {
                "source_reference": self._file_evidence(
                    paths["task_dir"] / "source" / "source_reference.json"
                ),
                "source_fingerprint": str(result.get("source_fingerprint") or ""),
            }
        if step == TaskStatus.TRANSCRIBING:
            return {
                "transcript": self._file_evidence(paths["transcript_path"]),
                "source": str(result.get("source") or ""),
            }
        if step == TaskStatus.AI_ANALYZING:
            run_id = str(result.get("analysis_run_id") or "")
            if not run_id:
                raise PipelineCheckpointError("AI 分析完成但没有 analysis_run_id")
            return {
                "analysis_run_id": run_id,
                "analysis": self._file_evidence(paths["analysis_path"]),
                "input_transcript": self._file_evidence(paths["transcript_path"]),
                "clip_count": int(result.get("clip_count") or len(result.get("clips") or [])),
            }
        if step == TaskStatus.CLIP_SELECTING:
            selected_ids = [
                str(item.get("clip_id") or "")
                for item in result.get("selected") or []
                if isinstance(item, dict) and item.get("clip_id")
            ]
            return {
                "selection": self._file_evidence(
                    paths["analysis_path"].parent / "auto_selected_clips.json"
                ),
                "input_analysis": self._file_evidence(paths["analysis_path"]),
                "selected_ids": selected_ids,
            }
        if step == TaskStatus.VIDEO_CUTTING:
            run_id = str(result.get("cut_run_id") or "")
            if not run_id:
                raise PipelineCheckpointError("切片步骤完成但缺少有效 cut_run 或输出文件")
            output_files = self._cut_output_evidence(task_id, run_id)
            return {
                "cut_run_id": run_id,
                "cut_run_number": int(result.get("cut_run_number") or 0),
                "output_files": output_files,
                "output_clip_ids": [item["id"] for item in output_files],
                "selected_candidate_ids": self._enabled_candidate_ids(task_id),
            }
        if step == TaskStatus.SUBTITLE_DRAFTING:
            tracks = result.get("tracks") or []
            return {
                "source_track_id": str(result.get("source_track_id") or ""),
                "track_ids": sorted(
                    str(item.get("id") or "")
                    for item in tracks
                    if isinstance(item, dict) and item.get("id")
                ),
                "track_count": int(result.get("track_count") or len(tracks)),
                "output_clip_ids": self._active_output_ids(task_id),
            }
        if step == TaskStatus.METADATA_GENERATING:
            return {
                "metadata": self._file_evidence(
                    paths["analysis_path"].parent / "auto_publish_metadata.json"
                ),
                "metadata_count": int(result.get("metadata_count") or 0),
                "need_review_count": int(result.get("need_review_count") or 0),
                "output_clip_ids": self._active_output_ids(task_id),
            }
        if step == TaskStatus.SCHEDULE_CREATING:
            scheduled_items = result.get("scheduled_items") or []
            return {
                "schedule": self._file_evidence(
                    paths["analysis_path"].parent / "auto_publish_schedule.json"
                ),
                "metadata_input": self._file_evidence(
                    paths["analysis_path"].parent / "auto_publish_metadata.json"
                ),
                "scheduled_count": len(scheduled_items),
            }
        if step == TaskStatus.PUBLISH_JOB_CREATING:
            created_ids = [
                str(item.get("id") or "")
                for item in result.get("created") or []
                if isinstance(item, dict) and item.get("id")
            ]
            skipped_ids = [
                str(item.get("id") or "")
                for item in result.get("skipped") or []
                if isinstance(item, dict) and item.get("id")
            ]
            schedule_path = paths["analysis_path"].parent / "auto_publish_schedule.json"
            job_evidence = self._publish_job_evidence(task_id, [*created_ids, *skipped_ids])
            self._verify_publish_schedule_pairs(schedule_path, job_evidence)
            return {
                "created_ids": created_ids,
                "skipped_ids": skipped_ids,
                "created_count": int(result.get("created_count") or len(created_ids)),
                "skipped_count": int(result.get("skipped_count") or len(skipped_ids)),
                "schedule_input": self._file_evidence(schedule_path),
                "job_evidence": job_evidence,
            }
        raise PipelineCheckpointError(f"没有定义 checkpoint 证据的步骤：{step.value}")

    def _restore_checkpoint_step(self, task_id: str, step: TaskStatus, outputs: dict) -> dict:
        paths = get_artifact_paths(task_id)
        if step == TaskStatus.PREPARING_SOURCE:
            reference_path = paths["task_dir"] / "source" / "source_reference.json"
            self._verify_file_evidence(reference_path, outputs.get("source_reference") or {})
            payload = self._read_json_object(reference_path)
            source_path = str(payload.get("source_path") or "")
            valid, error = validate_source_video_path(source_path)
            if not valid:
                raise PipelineCheckpointError(f"准备素材 checkpoint 已失效：{error}")
            return {**payload, "source_fingerprint": outputs.get("source_fingerprint") or ""}
        if step == TaskStatus.TRANSCRIBING:
            transcript_path = paths["transcript_path"]
            self._verify_file_evidence(transcript_path, outputs.get("transcript") or {})
            if not self._has_text_file(transcript_path):
                raise PipelineCheckpointError("转写 checkpoint 对应的 transcript.md 为空")
            return {
                "source": str(outputs.get("source") or "checkpoint"),
                "transcript_path": str(transcript_path),
            }
        if step == TaskStatus.AI_ANALYZING:
            analysis_path = paths["analysis_path"]
            self._verify_file_evidence(
                paths["transcript_path"],
                outputs.get("input_transcript") or {},
            )
            self._verify_file_evidence(analysis_path, outputs.get("analysis") or {})
            analysis_payload = self._read_json_object(analysis_path)
            run_id = str(outputs.get("analysis_run_id") or "")
            with get_connection() as connection:
                row = connection.execute(
                    """
                    SELECT id, clip_count, is_active, analysis_payload_json
                    FROM ai_analysis_runs
                    WHERE id = ? AND task_id = ?
                    """,
                    (run_id, task_id),
                ).fetchone()
                candidate_rows = connection.execute(
                    """
                    SELECT clip_key FROM clip_candidates
                    WHERE task_id = ? AND COALESCE(is_deleted, 0) = 0
                    ORDER BY clip_key
                    """,
                    (task_id,),
                ).fetchall()
            try:
                run_payload = json.loads(str(row["analysis_payload_json"] or "{}")) if row else {}
            except json.JSONDecodeError as exc:
                raise PipelineCheckpointError("AI run 的分析 JSON 已损坏") from exc
            clips = analysis_payload.get("clips")
            if not isinstance(clips, list) or not all(isinstance(item, dict) for item in clips):
                raise PipelineCheckpointError("AI checkpoint 的 clips 结构无效")
            analysis_clip_ids = sorted(str(item.get("clip_id") or "") for item in clips)
            candidate_clip_ids = sorted(str(item["clip_key"] or "") for item in candidate_rows)
            if (
                not row
                or not int(row["is_active"] or 0)
                or int(row["clip_count"] or 0) <= 0
                or run_payload != analysis_payload
                or not analysis_clip_ids
                or "" in analysis_clip_ids
                or candidate_clip_ids != analysis_clip_ids
                or len(candidate_clip_ids) != int(row["clip_count"] or 0)
            ):
                raise PipelineCheckpointError("AI checkpoint 与 active run/候选片段不一致")
            return {
                "analysis_run_id": run_id,
                "analysis_path": str(analysis_path),
                "clip_count": int(row["clip_count"] or 0),
            }
        if step == TaskStatus.CLIP_SELECTING:
            selection_path = paths["analysis_path"].parent / "auto_selected_clips.json"
            self._verify_file_evidence(
                paths["analysis_path"],
                outputs.get("input_analysis") or {},
            )
            self._verify_file_evidence(selection_path, outputs.get("selection") or {})
            payload = self._read_json_object(selection_path)
            selected_ids = {str(item) for item in outputs.get("selected_ids") or [] if item}
            with get_connection() as connection:
                enabled_ids = {
                    str(row["id"])
                    for row in connection.execute(
                        """
                        SELECT id FROM clip_candidates
                        WHERE task_id = ? AND enabled = 1 AND COALESCE(is_deleted, 0) = 0
                        """,
                        (task_id,),
                    ).fetchall()
                }
            if not selected_ids or enabled_ids != selected_ids:
                raise PipelineCheckpointError("选片 checkpoint 与当前启用候选不一致")
            return {**payload, "selection_path": str(selection_path)}
        if step == TaskStatus.VIDEO_CUTTING:
            run_id = str(outputs.get("cut_run_id") or "")
            with get_connection() as connection:
                run = connection.execute(
                    """
                    SELECT run_number, status, is_active FROM cut_runs
                    WHERE id = ? AND task_id = ?
                    """,
                    (run_id, task_id),
                ).fetchone()
            if (
                not run
                or run["status"] not in {"completed", "completed_with_errors"}
                or not int(run["is_active"] or 0)
            ):
                raise PipelineCheckpointError("切片 checkpoint 对应的 active cut run 不存在")
            current_output_files = self._cut_output_evidence(task_id, run_id)
            expected_output_files = outputs.get("output_files")
            if not isinstance(expected_output_files, list) or current_output_files != expected_output_files:
                raise PipelineCheckpointError("切片 checkpoint 与当前输出文件证据不一致")
            output_ids = [item["id"] for item in current_output_files]
            if output_ids != sorted(str(item) for item in outputs.get("output_clip_ids") or []):
                raise PipelineCheckpointError("切片 checkpoint 与当前有效输出 ID 不一致")
            selected_ids = self._enabled_candidate_ids(task_id)
            output_candidate_ids = sorted(
                item["clip_candidate_id"] for item in current_output_files if item["clip_candidate_id"]
            )
            if (
                selected_ids != sorted(str(item) for item in outputs.get("selected_candidate_ids") or [])
                or output_candidate_ids != selected_ids
            ):
                raise PipelineCheckpointError("切片 checkpoint 与当前启用候选边界不一致")
            return {
                "cut_run_id": run_id,
                "cut_run_number": int(run["run_number"] or 0),
                "success_count": len(current_output_files),
                "failed_count": 0,
                "failed": [],
            }
        if step == TaskStatus.SUBTITLE_DRAFTING:
            from app.services.subtitle_data_service import list_task_tracks

            tracks = list_task_tracks(task_id, ensure=False)
            track_ids = sorted(str(item.get("id") or "") for item in tracks if item.get("id"))
            expected = sorted(
                {
                    str(outputs.get("source_track_id") or ""),
                    *(str(item) for item in outputs.get("track_ids") or [] if item),
                }
                - {""}
            )
            task = self._get_task(task_id)
            if (
                not expected
                or track_ids != expected
                or self._active_output_ids(task_id)
                != sorted(str(item) for item in outputs.get("output_clip_ids") or [])
                or task.get("status") != TaskStatus.PENDING_SUBTITLE_REVIEW.value
            ):
                raise PipelineCheckpointError("字幕草稿 checkpoint 与当前审核状态不一致")
            clip_tracks = [item for item in tracks if item.get("track_type") == "clip"]
            return {
                "status": "pending_subtitle_review",
                "source_track_id": str(outputs.get("source_track_id") or ""),
                "track_count": len(clip_tracks),
                "tracks": clip_tracks,
            }
        if step == TaskStatus.METADATA_GENERATING:
            metadata_path = paths["analysis_path"].parent / "auto_publish_metadata.json"
            self._verify_file_evidence(metadata_path, outputs.get("metadata") or {})
            if self._active_output_ids(task_id) != sorted(
                str(item) for item in outputs.get("output_clip_ids") or []
            ):
                raise PipelineCheckpointError("文案 checkpoint 与当前 active output 不一致")
            metadata_items = self._read_json_list(metadata_path)
            self._validate_metadata_items(metadata_items)
            metadata_output_ids = {
                str((item.get("output_clip") or {}).get("id") or "")
                for item in metadata_items
            }
            if metadata_output_ids != set(self._active_output_ids(task_id)):
                raise PipelineCheckpointError("文案 checkpoint 未完整覆盖当前 active output")
            return {
                "metadata_path": str(metadata_path),
                "metadata_count": len(metadata_items),
                "need_review_count": sum(
                    1 for item in metadata_items if (item.get("metadata") or {}).get("risk_flags")
                ),
                "metadata_items": metadata_items,
            }
        if step == TaskStatus.SCHEDULE_CREATING:
            schedule_path = paths["analysis_path"].parent / "auto_publish_schedule.json"
            self._verify_file_evidence(
                paths["analysis_path"].parent / "auto_publish_metadata.json",
                outputs.get("metadata_input") or {},
            )
            self._verify_file_evidence(schedule_path, outputs.get("schedule") or {})
            scheduled_items = self._read_json_list(schedule_path)
            self._validate_metadata_items(scheduled_items)
            return {"schedule_path": str(schedule_path), "scheduled_items": scheduled_items}
        if step == TaskStatus.PUBLISH_JOB_CREATING:
            self._verify_file_evidence(
                paths["analysis_path"].parent / "auto_publish_schedule.json",
                outputs.get("schedule_input") or {},
            )
            created_ids = [str(item) for item in outputs.get("created_ids") or [] if item]
            skipped_ids = [str(item) for item in outputs.get("skipped_ids") or [] if item]
            all_ids = [*created_ids, *skipped_ids]
            current_evidence = self._publish_job_evidence(task_id, all_ids)
            expected_evidence = outputs.get("job_evidence")
            if not isinstance(expected_evidence, list) or current_evidence != expected_evidence:
                raise PipelineCheckpointError("发布任务 checkpoint 与当前草稿字段不一致")
            self._verify_publish_schedule_pairs(
                paths["analysis_path"].parent / "auto_publish_schedule.json",
                current_evidence,
            )
            from app.services.publish_service import get_publish_job

            return {
                "created": [get_publish_job(job_id) for job_id in created_ids],
                "skipped": [get_publish_job(job_id) for job_id in skipped_ids],
                "created_count": len(created_ids),
                "skipped_count": len(skipped_ids),
            }
        raise PipelineCheckpointError(f"无法恢复未知步骤：{step.value}")

    def _reconcile_interrupted_step(
        self,
        task_id: str,
        step: TaskStatus,
        record: dict,
    ) -> dict | None:
        baseline = record.get("baseline") if isinstance(record.get("baseline"), dict) else {}
        paths = get_artifact_paths(task_id)
        if step == TaskStatus.AI_ANALYZING:
            baseline_transcript = (
                baseline.get("input_transcript")
                if isinstance(baseline.get("input_transcript"), dict)
                else {}
            )
            if (
                not baseline_transcript
                or self._safe_file_evidence(paths["transcript_path"]) != baseline_transcript
            ):
                return None
            with get_connection() as connection:
                row = connection.execute(
                    """
                    SELECT id, clip_count FROM ai_analysis_runs
                    WHERE task_id = ? AND COALESCE(is_active, 0) = 1
                    ORDER BY run_number DESC LIMIT 1
                    """,
                    (task_id,),
                ).fetchone()
            if row and str(row["id"] or "") != str(baseline.get("active_ai_run_id") or ""):
                provisional = {
                    "analysis_run_id": str(row["id"]),
                    "analysis_path": str(paths["analysis_path"]),
                    "clip_count": int(row["clip_count"] or 0),
                }
                outputs = self._checkpoint_outputs(task_id, step, provisional)
                return self._restore_checkpoint_step(task_id, step, outputs)
            return None
        if step == TaskStatus.VIDEO_CUTTING:
            baseline_candidates = sorted(
                str(item) for item in baseline.get("selected_candidate_ids") or [] if item
            )
            if not baseline_candidates or self._enabled_candidate_ids(task_id) != baseline_candidates:
                return None
            with get_connection() as connection:
                row = connection.execute(
                    """
                    SELECT id, run_number FROM cut_runs
                    WHERE task_id = ? AND COALESCE(is_active, 0) = 1
                      AND status IN ('completed', 'completed_with_errors')
                    ORDER BY run_number DESC LIMIT 1
                    """,
                    (task_id,),
                ).fetchone()
            if row and int(row["run_number"] or 0) > int(baseline.get("max_cut_run_number") or 0):
                provisional = {
                    "cut_run_id": str(row["id"]),
                    "cut_run_number": int(row["run_number"] or 0),
                }
                outputs = self._checkpoint_outputs(task_id, step, provisional)
                return self._restore_checkpoint_step(task_id, step, outputs)
            return None
        if step == TaskStatus.SUBTITLE_DRAFTING:
            from app.services.subtitle_data_service import list_task_tracks

            tracks = list_task_tracks(task_id, ensure=False)
            source_tracks = [item for item in tracks if item.get("track_type") == "source"]
            clip_tracks = [item for item in tracks if item.get("track_type") == "clip"]
            current_track_ids = sorted(str(item.get("id") or "") for item in tracks if item.get("id"))
            baseline_output_ids = sorted(
                str(item) for item in baseline.get("output_clip_ids") or [] if item
            )
            task = self._get_task(task_id)
            if (
                source_tracks
                and clip_tracks
                and current_track_ids
                and self._active_output_ids(task_id) == baseline_output_ids
                and task.get("status") == TaskStatus.PENDING_SUBTITLE_REVIEW.value
            ):
                provisional = {
                    "status": "pending_subtitle_review",
                    "source_track_id": str(source_tracks[0].get("id") or ""),
                    "track_count": len(clip_tracks),
                    "tracks": clip_tracks,
                }
                outputs = self._checkpoint_outputs(task_id, step, provisional)
                return self._restore_checkpoint_step(task_id, step, outputs)
            return None

        artifact_by_step = {
            TaskStatus.PREPARING_SOURCE: paths["task_dir"] / "source" / "source_reference.json",
            TaskStatus.TRANSCRIBING: paths["transcript_path"],
            TaskStatus.CLIP_SELECTING: paths["analysis_path"].parent / "auto_selected_clips.json",
            TaskStatus.METADATA_GENERATING: paths["analysis_path"].parent / "auto_publish_metadata.json",
            TaskStatus.SCHEDULE_CREATING: paths["analysis_path"].parent / "auto_publish_schedule.json",
        }
        artifact = artifact_by_step.get(step)
        if artifact:
            if step == TaskStatus.CLIP_SELECTING:
                baseline_analysis = (
                    baseline.get("input_analysis")
                    if isinstance(baseline.get("input_analysis"), dict)
                    else {}
                )
                if (
                    not baseline_analysis
                    or self._safe_file_evidence(paths["analysis_path"]) != baseline_analysis
                ):
                    return None
            elif step == TaskStatus.METADATA_GENERATING:
                baseline_outputs = sorted(
                    str(item) for item in baseline.get("output_clip_ids") or [] if item
                )
                if not baseline_outputs or self._active_output_ids(task_id) != baseline_outputs:
                    return None
            elif step == TaskStatus.SCHEDULE_CREATING:
                baseline_metadata = (
                    baseline.get("metadata_input")
                    if isinstance(baseline.get("metadata_input"), dict)
                    else {}
                )
                if (
                    not baseline_metadata
                    or self._safe_file_evidence(
                        paths["analysis_path"].parent / "auto_publish_metadata.json"
                    )
                    != baseline_metadata
                ):
                    return None
            current = self._safe_file_evidence(artifact)
            previous = baseline.get("artifact") if isinstance(baseline.get("artifact"), dict) else {}
            if current and current != previous:
                if step == TaskStatus.PREPARING_SOURCE:
                    provisional = self._read_json_object(artifact)
                elif step == TaskStatus.TRANSCRIBING:
                    if not self._has_text_file(artifact):
                        return None
                    provisional = {
                        "source": "checkpoint",
                        "transcript_path": str(artifact),
                    }
                elif step == TaskStatus.CLIP_SELECTING:
                    payload = self._read_json_object(artifact)
                    provisional = {**payload, "selection_path": str(artifact)}
                elif step == TaskStatus.METADATA_GENERATING:
                    items = self._read_json_list(artifact)
                    self._validate_metadata_items(items)
                    provisional = {
                        "metadata_path": str(artifact),
                        "metadata_count": len(items),
                        "need_review_count": sum(
                            1 for item in items if (item.get("metadata") or {}).get("risk_flags")
                        ),
                        "metadata_items": items,
                    }
                else:
                    items = self._read_json_list(artifact)
                    self._validate_metadata_items(items)
                    provisional = {"schedule_path": str(artifact), "scheduled_items": items}
                outputs = self._checkpoint_outputs(task_id, step, provisional)
                return self._restore_checkpoint_step(task_id, step, outputs)
        return None

    def _safe_file_evidence(self, path: Path) -> dict:
        try:
            return self._file_evidence(path)
        except (OSError, PipelineCheckpointError):
            return {}

    def _file_evidence(self, path: Path) -> dict:
        resolved = path.resolve()
        if not resolved.exists() or not resolved.is_file():
            raise PipelineCheckpointError(f"checkpoint 产物不存在：{resolved}")
        digest = hashlib.sha256()
        with resolved.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        stat = resolved.stat()
        return {
            "path": str(resolved),
            "size_bytes": int(stat.st_size),
            "mtime_ns": int(stat.st_mtime_ns),
            "sha256": digest.hexdigest(),
        }

    def _verify_file_evidence(self, path: Path, evidence: dict) -> None:
        if not isinstance(evidence, dict) or not evidence.get("sha256"):
            raise PipelineCheckpointError(f"checkpoint 缺少文件校验值：{path}")
        current = self._file_evidence(path)
        if (
            current["path"] != str(evidence.get("path") or "")
            or current["size_bytes"] != int(evidence.get("size_bytes") or -1)
            or current["sha256"] != str(evidence.get("sha256") or "")
        ):
            raise PipelineCheckpointError(f"checkpoint 文件证据已变化：{path}")

    def _read_json_object(self, path: Path) -> dict:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PipelineCheckpointError(f"checkpoint JSON 无法读取：{path}：{exc}") from exc
        if not isinstance(payload, dict):
            raise PipelineCheckpointError(f"checkpoint JSON 不是对象：{path}")
        return payload

    def _read_json_list(self, path: Path) -> list[dict]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PipelineCheckpointError(f"checkpoint JSON 无法读取：{path}：{exc}") from exc
        if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
            raise PipelineCheckpointError(f"checkpoint JSON 不是对象列表：{path}")
        return payload

    def _validate_metadata_items(self, items: list[dict]) -> None:
        if not items:
            raise PipelineCheckpointError("checkpoint 文案/排期列表为空")
        for item in items:
            output_clip = item.get("output_clip")
            metadata = item.get("metadata")
            if (
                not isinstance(output_clip, dict)
                or not output_clip.get("id")
                or not isinstance(metadata, dict)
                or not metadata.get("platform")
            ):
                raise PipelineCheckpointError("checkpoint 文案/排期条目缺少切片或平台")

    def _safe_write_task_summary(self, task_id: str, status: str, error: str) -> dict:
        try:
            return self._write_task_summary(task_id, status, error)
        except Exception as exc:
            try:
                append_task_log(task_id, f"任务汇总写入失败，但主流程状态已保留：{exc}")
            except Exception:
                pass
            return {"summary_path": ""}

    def _get_task(self, task_id: str) -> dict:
        task = task_service.get_task(task_id, include_video_probe=False)
        if not task:
            raise ValueError("任务不存在")
        if task.get("is_deleted"):
            raise ValueError("任务已永久删除，已停止后续自动处理")
        return task

    def _load_auto_config(self, task: dict) -> dict:
        config = dict(DEFAULT_AUTO_CONFIG)
        try:
            stored = json.loads(task.get("auto_config_json") or "{}")
        except json.JSONDecodeError:
            stored = {}
        if isinstance(stored, dict):
            config.update(stored)
        config["auto_schedule_interval_hours"] = int(config.get("auto_schedule_interval_hours") or 3)
        config["auto_metadata_use_ai"] = bool(config.get("auto_metadata_use_ai"))
        return config

    def _resolve_start_step(self, task: dict, retry: bool) -> TaskStatus:
        if retry:
            return RETRY_START_BY_FAILED_STATUS.get(task.get("status"), TaskStatus.PREPARING_SOURCE)
        return TaskStatus.PREPARING_SOURCE

    def _prepare_source(self, task_id: str, context: dict) -> dict:
        from app.services.transcription_checkpoint_service import fingerprint_file

        task = self._get_task(task_id)
        create_task_directory(task_id, task.get("task_dir_name"))
        source_path = get_source_video_path(task)
        valid, error_message = validate_source_video_path(str(source_path) if source_path else None)
        if not valid:
            raise ValueError(error_message)
        source = Path(source_path)
        paths = get_artifact_paths(task_id, task.get("task_dir_name"))
        reference_path = paths["task_dir"] / "source" / "source_reference.json"
        reference_path.parent.mkdir(parents=True, exist_ok=True)
        reference_payload = {
            "mode": "reference",
            "source_type": task.get("source_type") or "",
            "source_path": str(source),
            "exists": source.exists(),
            "size_bytes": source.stat().st_size if source.exists() else 0,
            "source_fingerprint": fingerprint_file(source),
        }
        reference_path.write_text(json.dumps(reference_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        append_task_log(task_id, f"全自动准备视频完成：{source}")
        return reference_payload

    def _transcribe_or_read_text(self, task_id: str, context: dict) -> dict:
        paths = get_artifact_paths(task_id)
        transcript_path = paths["transcript_path"]
        if self._has_text_file(transcript_path):
            append_task_log(task_id, "全自动模式：已发现转写 Markdown，跳过重新转写")
            return {"source": "existing", "transcript_path": str(transcript_path)}

        existing_text_path = self._find_existing_text(paths)
        if existing_text_path:
            transcript_path.parent.mkdir(parents=True, exist_ok=True)
            transcript_path.write_text(existing_text_path.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
            append_task_log(task_id, f"全自动模式：读取已有字幕/文本作为 AI 分析输入：{existing_text_path}")
            return {"source": "existing_text", "transcript_path": str(transcript_path)}

        append_task_log(task_id, "全自动模式：未发现转写文本，开始调用现有转写流程")
        task_service.process_task_transcript_workflow(task_id, background_tasks=None, force=False)
        if not self._has_text_file(transcript_path):
            raise ValueError("转写流程结束后仍未生成 transcript.md")
        return {"source": "generated", "transcript_path": str(transcript_path)}

    def _run_ai_analysis(self, task_id: str, context: dict) -> dict:
        result = task_service.process_task_ai_analysis(task_id)
        clip_count = len(result.get("clips") or [])
        append_task_log(task_id, f"全自动 AI 分析完成，候选片段：{clip_count} 条")
        return {
            "clip_count": clip_count,
            "analysis_run_id": result.get("analysis_run_id") or "",
            "analysis_path": result.get("analysis_path") or "",
            "analysis_meta": (result.get("analysis_run") or {}).get("analysis_meta") or {},
        }

    def _select_clips(self, task_id: str, context: dict) -> dict:
        task = self._get_task(task_id)
        config = context["config"]
        if task.get("selection_profile") == "long_live_talk":
            meta = task_service.get_task_ai_analysis_meta(task_id)
            if meta.get("analysis_incomplete") or float(meta.get("coverage_ratio") or 0) < 0.90:
                coverage = float(meta.get("coverage_percent") or 0)
                raise ValueError(
                    f"长直播分析覆盖率仅 {coverage:.2f}%，低于 90%；"
                    "请重试 AI 分析补齐缺失窗口，当前不会进入自动切片或发送中心。"
                )
        candidates = self._list_raw_candidates(task_id)
        if not candidates:
            latest_run = task_service.get_latest_ai_analysis_run(task_id)
            if latest_run and latest_run.get("clips"):
                task_service.restore_ai_analysis_run(task_id, latest_run["id"])
                candidates = self._list_raw_candidates(task_id)
                append_task_log(task_id, f"已从第 {latest_run['run_number']} 次 AI 历史恢复候选片段")
        if not candidates:
            raise ValueError("没有可用于自动切片的候选片段，请先重新运行 AI 分析")

        target_count = self._resolve_target_count(task, config)
        max_duration_seconds = max(1, int(task.get("max_clip_duration") or 10)) * 60
        valid_candidates = []
        skipped = []
        for clip in candidates:
            try:
                start = parse_time_to_seconds(str(clip.get("start_time") or ""))
                end = parse_time_to_seconds(str(clip.get("end_time") or ""))
                if end <= start:
                    raise ValueError("结束时间必须晚于开始时间")
                duration = end - start
                if duration > max_duration_seconds:
                    raise ValueError(f"时长 {int(duration)} 秒超过单条切片最长 {max_duration_seconds} 秒")
            except Exception as exc:
                skipped.append({"clip_id": clip.get("id") or "", "reason": str(exc)})
                continue
            valid_candidates.append({**clip, "start_seconds": start, "end_seconds": end, "duration": duration})

        eligible = [item for item in valid_candidates if bool(item.get("selected_by_default"))]
        if task.get("selection_profile") == "variety_comedy":
            eligible = [item for item in eligible if item.get("quality_tier") == "A"]
        selected = sorted(
            eligible,
            key=lambda item: float(item.get("quality_score") or item.get("confidence_score") or 0),
            reverse=True,
        )
        selected = sorted(selected[:target_count], key=lambda item: float(item["start_seconds"]))
        if not selected:
            if task.get("selection_profile") == "variety_comedy":
                raise ValueError("本集没有达到 A 级质量门槛的综艺片段，已停止自动切片，避免为了数量强行输出")
            raise ValueError("没有同时满足默认入选和时间戳要求的候选片段")
        selected_ids = {clip["id"] for clip in selected}
        self._update_selected_clips(task_id, selected_ids)
        payload = {
            "target_count": target_count,
            "selected_count": len(selected),
            "skipped_count": len(skipped),
            "selected": [
                {
                    "clip_id": clip["id"],
                    "title": clip.get("title") or "",
                    "start_time": clip.get("start_time") or "",
                    "end_time": clip.get("end_time") or "",
                    "recommend_reason": clip.get("highlight_reason") or clip.get("reason") or "",
                }
                for clip in selected
            ],
            "skipped": skipped,
        }
        paths = get_artifact_paths(task_id)
        (paths["analysis_path"].parent / "auto_selected_clips.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        payload["selection_path"] = str(paths["analysis_path"].parent / "auto_selected_clips.json")
        append_task_log(task_id, f"全自动选片完成：选中 {len(selected)} 条，跳过 {len(skipped)} 条")
        return payload

    def _cut_video(self, task_id: str, context: dict) -> dict:
        append_task_log(task_id, "全自动模式：开始原视频裁切，完成后生成字幕草稿")
        result = task_service.process_task_video_cuts(task_id, sync_publish_jobs=False)
        output_clips = task_service.list_output_clips(task_id)
        success = [clip for clip in output_clips if clip.get("status") == "completed" and clip.get("file_exists")]
        failed = [item for item in result.get("results") or [] if item.get("status") == "failed"]
        if not success:
            raise ValueError("全部切片失败，未生成可发布的原视频裁切片段")
        self._write_clip_metadata(task_id, output_clips, metadata_items=[])
        append_task_log(task_id, f"全自动原片切割完成：成功 {len(success)} 条，失败 {len(failed)} 条")
        return {
            "success_count": len(success),
            "failed_count": len(failed),
            "failed": failed,
            "cut_run_id": result.get("cut_run_id") or "",
            "cut_run_number": int(result.get("cut_run_number") or 0),
        }

    def _prepare_subtitle_drafts(self, task_id: str, context: dict) -> dict:
        from app.services.subtitle_auto_workflow_service import prepare_task_subtitle_review

        del context
        return prepare_task_subtitle_review(task_id)

    def _generate_metadata(self, task_id: str, context: dict) -> dict:
        task = self._get_task(task_id)
        config = context["config"]
        output_clips = [
            clip for clip in task_service.list_output_clips(task_id)
            if clip.get("status") == "completed" and clip.get("file_exists")
        ]
        if not output_clips:
            raise ValueError("没有可生成文案的成功切片")
        generator = MetadataGenerator(use_ai=config["auto_metadata_use_ai"])
        metadata_items = []
        for output_clip in output_clips:
            cover = generate_publish_cover_for_item(
                output_clip,
                preferred_time_seconds=output_clip.get("cover_time_seconds"),
            )
            for platform in platforms_for_task(task):
                metadata_items.append(
                    {
                        "output_clip": output_clip,
                        "metadata": generator.generate(output_clip, platform),
                        "cover": cover,
                    }
                )
        paths = get_artifact_paths(task_id)
        metadata_path = paths["analysis_path"].parent / "auto_publish_metadata.json"
        metadata_path.write_text(json.dumps(metadata_items, ensure_ascii=False, indent=2), encoding="utf-8")
        self._write_clip_metadata(task_id, output_clips, metadata_items)
        need_review = [item for item in metadata_items if item["metadata"].get("risk_flags")]
        append_task_log(task_id, f"全自动标题文案生成完成：{len(metadata_items)} 条，需复核 {len(need_review)} 条")
        return {
            "metadata_path": str(metadata_path),
            "metadata_count": len(metadata_items),
            "need_review_count": len(need_review),
            "metadata_items": metadata_items,
        }

    def _create_schedule(self, task_id: str, context: dict) -> dict:
        metadata_result = context.get(TaskStatus.METADATA_GENERATING.value) or {}
        metadata_items = metadata_result.get("metadata_items") or []
        if not metadata_items:
            raise ValueError("没有可加入发送队列的文案记录")
        scheduled_items = [{**item, "scheduled_at": ""} for item in metadata_items]
        paths = get_artifact_paths(task_id)
        schedule_path = paths["analysis_path"].parent / "auto_publish_schedule.json"
        schedule_path.write_text(json.dumps(scheduled_items, ensure_ascii=False, indent=2), encoding="utf-8")
        append_task_log(task_id, f"全自动发送队列准备完成：{len(scheduled_items)} 条，发布时间待发送中心设置")
        return {
            "schedule_path": str(schedule_path),
            "scheduled_count": len(scheduled_items),
            "scheduled_items": scheduled_items,
        }

    def _create_publish_jobs(self, task_id: str, context: dict) -> dict:
        task = self._get_task(task_id)
        schedule_result = context.get(TaskStatus.SCHEDULE_CREATING.value) or {}
        scheduled_items = schedule_result.get("scheduled_items") or []
        if not scheduled_items:
            raise ValueError("没有可创建发布任务的排期记录")
        delivery_mode = str(context["config"].get("subtitle_delivery_mode") or "")
        if delivery_mode not in {"subtitled", "original"}:
            raise ValueError("尚未确认字幕交付方式，不能创建发布任务")
        result = create_auto_publish_jobs(
            task,
            scheduled_items,
            subtitle_delivery_mode=delivery_mode,
            workflow_job_id=str(context.get("workflow_job_id") or "") or None,
        )
        append_task_log(
            task_id,
            f"全自动发布任务创建完成：新增 {result['created_count']} 条，跳过已有 {result['skipped_count']} 条",
        )
        return result

    def _has_text_file(self, path: Path) -> bool:
        return path.exists() and path.is_file() and bool(path.read_text(encoding="utf-8", errors="replace").strip())

    def _find_existing_text(self, paths: dict[str, Path]) -> Path | None:
        transcripts_dir = paths["transcript_path"].parent
        for pattern in ("*.md", "*.txt", "*.srt", "*.vtt"):
            for candidate in sorted(transcripts_dir.glob(pattern)):
                if candidate == paths["transcript_path"]:
                    continue
                if self._has_text_file(candidate):
                    return candidate
        return None

    def _list_raw_candidates(self, task_id: str) -> list[dict]:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT id, task_id, clip_key, title, start_time, end_time, duration_seconds,
                       summary, reason, highlight_reason, spread_value, suggested_editing,
                       confidence_score, quality_tier, quality_score, humor_score,
                       completeness_score, audio_reaction_score,
                       selected_by_default, enabled, reviewed, is_deleted
                FROM clip_candidates
                WHERE task_id = ? AND is_deleted = 0
                ORDER BY start_time ASC
                """,
                (task_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def _resolve_target_count(self, task: dict, config: dict) -> int:
        if task.get("selection_profile") == "variety_comedy":
            return max(1, min(12, int(task.get("final_clip_target") or 5)))
        if task.get("selection_profile") == "long_live_talk":
            return max(1, min(50, int(task.get("highlight_total_limit") or 30)))
        return max(1, min(50, int(task.get("candidate_clip_count") or 12)))

    def _update_selected_clips(self, task_id: str, selected_ids: set[str]) -> None:
        now = task_service._now_iso()
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE clip_candidates
                SET enabled = 0, selected_by_default = 0, reviewed = 1, updated_at = ?
                WHERE task_id = ? AND is_deleted = 0
                """,
                (now, task_id),
            )
            for clip_id in selected_ids:
                connection.execute(
                    """
                    UPDATE clip_candidates
                    SET enabled = 1, selected_by_default = 1, reviewed = 1, updated_at = ?
                    WHERE task_id = ? AND id = ? AND is_deleted = 0
                    """,
                    (now, task_id, clip_id),
                )
            connection.commit()

    def _write_clip_metadata(self, task_id: str, output_clips: list[dict], metadata_items: list[dict]) -> None:
        paths = get_artifact_paths(task_id)
        metadata_by_clip: dict[str, list[dict]] = {}
        cover_by_clip: dict[str, dict] = {}
        for item in metadata_items:
            clip_id = item["output_clip"]["id"]
            metadata_by_clip.setdefault(clip_id, []).append(item["metadata"])
            cover_by_clip[clip_id] = item.get("cover") or {}
        payload = []
        for clip in output_clips:
            payload.append(
                {
                    "output_clip_id": clip.get("id") or "",
                    "clip_candidate_id": clip.get("clip_candidate_id") or "",
                    "output_file_path": clip.get("output_file_path") or "",
                    "status": clip.get("status") or "",
                    "error_message": clip.get("error_message") or "",
                    "recommend_reason": clip.get("highlight_reason") or clip.get("clip_summary") or "",
                    "cover": cover_by_clip.get(clip.get("id") or "", {}),
                    "metadata": metadata_by_clip.get(clip.get("id") or "", []),
                }
            )
        metadata_path = paths["clips_dir"] / "clip_metadata.json"
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        metadata_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _write_task_summary(self, task_id: str, status: str, error: str) -> dict:
        task = self._get_task(task_id)
        paths = get_artifact_paths(task_id, task.get("task_dir_name"))
        source_path = get_source_video_path(task)
        candidate_count = self._count_rows("clip_candidates", "task_id = ? AND is_deleted = 0", [task_id])
        output_counts = self._output_clip_counts(task_id)
        publish_job_count = self._count_rows("publish_jobs", "task_id = ?", [task_id])
        metadata_payload = self._read_json(paths["analysis_path"].parent / "auto_publish_metadata.json", [])
        need_review = [
            item for item in metadata_payload
            if item.get("metadata", {}).get("risk_flags")
        ] if isinstance(metadata_payload, list) else []
        failures = []
        if error:
            failures.append(error)
        failures.extend(output_counts["errors"])
        next_step = (
            "打开字幕工作台审核草稿；确认后批量烧录，或明确跳过字幕继续使用原片。"
            if status == TaskStatus.PENDING_SUBTITLE_REVIEW.value
            else "打开发送中心核对内容、账号与北京时间；确认后可立即发送或设置排期。"
        )
        summary = {
            "task_id": task_id,
            "task_name": task.get("task_name") or "",
            "status": status,
            "generated_at": task_service._now_iso(),
            "source_video": {
                "path": str(source_path) if source_path else "",
                "exists": bool(source_path and source_path.exists()),
                "size_bytes": source_path.stat().st_size if source_path and source_path.exists() else 0,
            },
            "ai_analysis_result_count": candidate_count,
            "successful_clip_count": output_counts["completed"],
            "failed_clip_count": output_counts["failed"],
            "metadata_count": len(metadata_payload) if isinstance(metadata_payload, list) else 0,
            "publish_job_count": publish_job_count,
            "need_review_clips": need_review,
            "failures": failures,
            "next_step": next_step,
            "subtitle_note": "切片后必须人工确认字幕交付方式；只有已审核并验证的字幕成片才会自动进入发送中心。",
        }
        summary_path = paths["analysis_path"].parent / "task_summary.json"
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        summary["summary_path"] = str(summary_path)
        return summary

    def _count_rows(self, table: str, where: str, params: list[Any]) -> int:
        with get_connection() as connection:
            row = connection.execute(f"SELECT COUNT(*) AS total FROM {table} WHERE {where}", params).fetchone()
        return int(row["total"] or 0)

    def _output_clip_counts(self, task_id: str) -> dict:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT status, error_message
                FROM output_clip
                WHERE task_id = ? AND is_active = 1
                """,
                (task_id,),
            ).fetchall()
        completed = 0
        failed = 0
        errors = []
        for row in rows:
            if row["status"] == "completed":
                completed += 1
            elif row["status"] == "failed":
                failed += 1
                if row["error_message"]:
                    errors.append(row["error_message"])
        return {"completed": completed, "failed": failed, "errors": errors}

    def _read_json(self, path: Path, fallback: Any) -> Any:
        if not path.exists():
            return fallback
        try:
            return json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            return fallback


def _parse_start_at(value: str | None, default: datetime) -> datetime:
    text = (value or "").strip()
    if not text:
        return default
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return default
    if parsed.tzinfo is None:
        return parsed.astimezone()
    return parsed.astimezone()


def _parse_clock(value: str, fallback: time) -> time:
    try:
        hour_text, minute_text = value.split(":", 1)
        return time(hour=int(hour_text), minute=int(minute_text[:2]))
    except (ValueError, TypeError):
        return fallback


def build_schedule_times(count: int, config: dict, now: datetime | None = None) -> list[str]:
    if count <= 0:
        return []
    current_now = now or datetime.now().astimezone()
    mode = str(config.get("auto_schedule_mode") or "default")
    interval_hours = max(1, int(config.get("auto_schedule_interval_hours") or 3))

    if mode == "immediate":
        start = _parse_start_at(config.get("auto_schedule_start_at"), current_now)
        return [(start + timedelta(minutes=index)).isoformat(timespec="seconds") for index in range(count)]

    default_start = current_now + timedelta(minutes=10)
    start = _parse_start_at(config.get("auto_schedule_start_at"), default_start)
    if mode in {"default", "interval"}:
        interval = timedelta(hours=interval_hours if mode == "interval" else 3)
        return [(start + index * interval).isoformat(timespec="seconds") for index in range(count)]

    if mode == "daily_window":
        window_start = _parse_clock(
            str(config.get("auto_schedule_daily_start_time") or "07:00"),
            time(7, 0),
        ).isoformat(timespec="minutes")
        window_end = _parse_clock(
            str(config.get("auto_schedule_daily_end_time") or "00:00"),
            time(0, 0),
        ).isoformat(timespec="minutes")
        scheduled = []
        cursor = next_allowed_schedule_time(
            start,
            daily_start_time=window_start,
            daily_end_time=window_end,
        )
        while len(scheduled) < count:
            scheduled.append(cursor.isoformat(timespec="seconds"))
            cursor = next_allowed_schedule_time(
                cursor + timedelta(hours=interval_hours),
                daily_start_time=window_start,
                daily_end_time=window_end,
            )
        return scheduled

    return [(start + index * timedelta(hours=3)).isoformat(timespec="seconds") for index in range(count)]


def run_auto_pipeline(
    task_id: str,
    retry: bool = False,
    start_step: TaskStatus | str | None = None,
    job_id: str | None = None,
) -> dict:
    return PipelineEngine().run(task_id, retry=retry, start_step=start_step, job_id=job_id)


def start_auto_pipeline(
    task_id: str,
    background_tasks: Any | None = None,
    retry: bool = False,
    start_step: TaskStatus | str | None = None,
) -> dict:
    if background_tasks is not None:
        from app.services import job_service

        if retry:
            job, requeued = job_service.retry_latest_or_get_active_job(
                task_id,
                job_service.JOB_TYPE_AUTO_PIPELINE,
            )
            if job:
                return {
                    "status": job["status"],
                    "message": (
                        "已使用原 Workflow Job 的 checkpoint 重新加入队列。"
                        if requeued
                        else "全自动流水线已经在排队或运行。"
                    ),
                    "task_id": task_id,
                    "job_id": job["id"],
                }
        job, created = job_service.create_or_get_active_job(
            task_id=task_id,
            job_type=job_service.JOB_TYPE_AUTO_PIPELINE,
            payload={
                "retry": retry,
                "start_step": start_step.value if isinstance(start_step, TaskStatus) else start_step,
            },
        )
        return {
            "status": job["status"],
            "message": "全自动流水线已加入持久化队列。" if created else "全自动流水线已经在排队或运行。",
            "task_id": task_id,
            "job_id": job["id"],
        }
    return run_auto_pipeline(task_id, retry=retry, start_step=start_step)
