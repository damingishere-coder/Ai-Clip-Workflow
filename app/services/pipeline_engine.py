"""v1.3.0 全自动任务流水线调度器。"""

from __future__ import annotations

import json
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any

from app.db.database import get_connection
from app.models.task import TaskStatus
from app.services import task_service
from app.services.auto_publish_service import create_auto_publish_jobs, platforms_for_task
from app.services.metadata_generator import MetadataGenerator
from app.services.publish_service import generate_publish_cover_for_item
from app.services.publish_time import next_allowed_schedule_time
from app.services.storage_service import (
    create_task_directory,
    get_artifact_paths,
    get_source_video_path,
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

        config = self._load_auto_config(task)
        resolved_start_step = (
            start_step
            if isinstance(start_step, TaskStatus)
            else TaskStatus(start_step) if start_step else self._resolve_start_step(task, retry=retry)
        )
        if not retry and start_step is None:
            task_service.update_task_status(task_id, TaskStatus.CREATED)
            append_task_log(task_id, "全自动流水线启动")
        elif start_step is not None:
            append_task_log(task_id, f"从已有 AI 结果继续全自动流水线：{resolved_start_step.value}")
        else:
            append_task_log(task_id, f"从失败步骤重试全自动流水线：{resolved_start_step.value}")

        context: dict[str, Any] = {"config": config}
        steps = STEP_STATUSES[STEP_STATUSES.index(resolved_start_step) :]
        handlers = {
            TaskStatus.PREPARING_SOURCE: self._prepare_source,
            TaskStatus.TRANSCRIBING: self._transcribe_or_read_text,
            TaskStatus.AI_ANALYZING: self._run_ai_analysis,
            TaskStatus.CLIP_SELECTING: self._select_clips,
            TaskStatus.VIDEO_CUTTING: self._cut_video,
            TaskStatus.METADATA_GENERATING: self._generate_metadata,
            TaskStatus.SCHEDULE_CREATING: self._create_schedule,
            TaskStatus.PUBLISH_JOB_CREATING: self._create_publish_jobs,
        }

        for step in steps:
            try:
                if job_id:
                    from app.services import job_service
                    if job_service.is_cancel_requested(job_id):
                        raise RuntimeError("用户已取消全自动流水线")
                    step_index = STEP_STATUSES.index(step)
                    job_service.update_job_progress(
                        job_id,
                        5 + round(step_index / max(1, len(STEP_STATUSES)) * 90),
                        f"正在执行：{step.value}",
                    )
                    job = job_service.get_job(job_id)
                    if job and job.get("lease_owner"):
                        job_service.heartbeat_job(job_id, str(job["lease_owner"]))
                task_service.update_task_status(task_id, step)
                context[step.value] = handlers[step](task_id, context)
            except Exception as exc:
                failed_status = FAILED_BY_STEP[step]
                error = str(exc) or f"{step.value} 失败"
                task_service.update_task_status(task_id, failed_status, error)
                append_task_log(task_id, f"全自动流水线失败：{step.value}，原因：{error}")
                summary = self._write_task_summary(task_id, "failed", error)
                return {
                    "status": "failed",
                    "failed_step": step.value,
                    "failed_status": failed_status.value,
                    "last_error": error,
                    "summary_path": summary["summary_path"],
                    "task": task_service.get_task(task_id, include_video_probe=False),
                }

        task_service.update_task_status(task_id, TaskStatus.READY_TO_PUBLISH)
        append_task_log(task_id, "发布任务已创建，进入待人工确认发布状态")
        summary = self._write_task_summary(task_id, "ready_to_publish", "")
        task_service.update_task_status(task_id, TaskStatus.COMPLETED)
        append_task_log(task_id, "全自动流水线完成。本轮已跳过字幕烧录，只保留原视频裁切片段。")
        return {
            "status": "completed",
            "summary_path": summary["summary_path"],
            "task": task_service.get_task(task_id, include_video_probe=False),
        }

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
        return {"clip_count": clip_count, "analysis_path": result.get("analysis_path") or ""}

    def _select_clips(self, task_id: str, context: dict) -> dict:
        task = self._get_task(task_id)
        config = context["config"]
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
        append_task_log(task_id, f"全自动选片完成：选中 {len(selected)} 条，跳过 {len(skipped)} 条")
        return payload

    def _cut_video(self, task_id: str, context: dict) -> dict:
        append_task_log(task_id, "全自动模式：开始原视频裁切，本轮明确跳过字幕烧录")
        result = task_service.process_task_video_cuts(task_id, sync_publish_jobs=False)
        output_clips = task_service.list_output_clips(task_id)
        success = [clip for clip in output_clips if clip.get("status") == "completed" and clip.get("file_exists")]
        failed = [item for item in result.get("results") or [] if item.get("status") == "failed"]
        if not success:
            raise ValueError("全部切片失败，未生成可发布的原视频裁切片段")
        self._write_clip_metadata(task_id, output_clips, metadata_items=[])
        append_task_log(task_id, f"全自动原片切割完成：成功 {len(success)} 条，失败 {len(failed)} 条")
        return {"success_count": len(success), "failed_count": len(failed), "failed": failed}

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
        return {"schedule_path": str(schedule_path), "scheduled_items": scheduled_items}

    def _create_publish_jobs(self, task_id: str, context: dict) -> dict:
        task = self._get_task(task_id)
        schedule_result = context.get(TaskStatus.SCHEDULE_CREATING.value) or {}
        scheduled_items = schedule_result.get("scheduled_items") or []
        if not scheduled_items:
            raise ValueError("没有可创建发布任务的排期记录")
        result = create_auto_publish_jobs(task, scheduled_items)
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
            "next_step": "打开发送中心核对内容、账号与北京时间；确认后可立即发送或设置排期。",
            "subtitle_note": "v2.1 全自动模式继续跳过加字幕、字幕样式渲染和字幕烧录。",
        }
        summary_path = paths["analysis_path"].parent / "task_summary.json"
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
