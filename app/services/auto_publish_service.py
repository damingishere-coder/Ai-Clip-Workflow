"""全自动模式发布任务创建服务。"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from app.core.config import settings
from app.db.database import get_connection
from app.services import job_service
from app.services.publish_copy_rules import PUBLISH_COPY_RULE_VERSION
from app.services.publish_service import DEFAULT_BILIBILI_TID, USER_REMOVED_ERROR_CODE, get_publish_job
from app.services.publish_domain import AUTO_PUBLISH_PLATFORMS, validate_publish_mode, validate_target_platform
from app.services.storage_service import (
    IMAGE_EXTENSIONS,
    resolve_task_media_file_path,
    resolve_video_file_path,
)
from app.services.task_service import _now_iso
from app.services.transcription_checkpoint_service import fingerprint_file


def platforms_for_task(task: dict) -> list[str]:
    del task
    return list(AUTO_PUBLISH_PLATFORMS)


def create_auto_publish_jobs(
    task: dict,
    scheduled_items: list[dict],
    *,
    subtitle_delivery_mode: str,
    workflow_job_id: str | None = None,
) -> dict:
    """为全自动流水线生成发布任务。

    本轮只创建任务记录，不调用平台 API，也不启动 opencli 发送。
    """

    if subtitle_delivery_mode not in {"subtitled", "original"}:
        raise ValueError("字幕交付模式必须是 subtitled 或 original")
    created_ids: list[str] = []
    skipped_ids: list[str] = []
    lease = job_service.current_job_lease() if workflow_job_id else None
    if workflow_job_id and (not lease or lease[0] != workflow_job_id):
        raise job_service.JobLeaseLostError(f"发布草稿创建缺少当前 Workflow Job 租约：{workflow_job_id}")
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        now = _now_iso()
        if workflow_job_id:
            active_lease = connection.execute(
                """
                SELECT 1 FROM workflow_jobs
                WHERE id = ? AND status = 'running' AND lease_owner = ? AND lease_token = ?
                  AND lease_expires_at > strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now')
                  AND cancel_requested = 0
                """,
                (workflow_job_id, lease[1], lease[2]),
            ).fetchone()
            if not active_lease:
                connection.rollback()
                raise job_service.JobLeaseLostError(
                    f"发布草稿创建前 Workflow Job 租约已失效：{workflow_job_id}"
                )
        for item in scheduled_items:
            output_clip = item["output_clip"]
            video_source, video_file_path, resolved_video, subtitle_evidence = _resolve_auto_video_source(
                task,
                output_clip,
                subtitle_delivery_mode,
                require_managed_path=bool(workflow_job_id),
            )
            metadata = item["metadata"]
            cover = item.get("cover") or {}
            platform = validate_target_platform(metadata["platform"])
            publish_mode = validate_publish_mode(settings.publish_default_mode)
            cover_file_path = str(cover.get("cover_file_path") or "").strip()
            if not cover_file_path:
                raise ValueError(f"{output_clip.get('id') or '未知切片'} 没有生成封面，已停止创建不完整的发布任务")
            resolved_cover = (
                resolve_task_media_file_path(
                    cover_file_path,
                    task_id=str(task["id"]),
                    task_dir_name=str(task.get("task_dir_name") or "") or None,
                    allowed_subdirectories=("07_covers",),
                    allowed_extensions=IMAGE_EXTENSIONS,
                )
                if workflow_job_id
                else resolve_video_file_path(cover_file_path)
            )
            if (
                resolved_cover is None
                or not resolved_cover.exists()
                or not resolved_cover.is_file()
                or resolved_cover.stat().st_size <= 0
            ):
                raise ValueError(f"{output_clip.get('id') or '未知切片'} 的封面文件无效或不在受控目录")
            cover_file_path = str(resolved_cover)
            scheduled_at = str(item.get("scheduled_at") or "").strip()
            if scheduled_at:
                from app.services.publish_time import to_utc_iso

                scheduled_at = to_utc_iso(scheduled_at, settings.app_timezone)
            latest = connection.execute(
                """
                SELECT id, status, error_code
                FROM publish_jobs
                WHERE output_clip_id = ? AND platform = ?
                ORDER BY COALESCE(NULLIF(updated_at, ''), created_at) DESC, created_at DESC, id DESC
                LIMIT 1
                """,
                (output_clip["id"], platform),
            ).fetchone()
            if (
                latest
                and str(latest["status"] or "").upper() == "CANCELLED"
                and str(latest["error_code"] or "") == USER_REMOVED_ERROR_CODE
            ):
                skipped_ids.append(latest["id"])
                continue
            if latest and str(latest["status"] or "").upper() in {"PUBLISHED", "EXPORTED"}:
                skipped_ids.append(latest["id"])
                continue
            existing = connection.execute(
                """
                SELECT id, video_file_path, cover_file_path, provider_response,
                       title, description, caption, tags, hashtags, cover_text,
                       scheduled_at, cover_time_seconds
                FROM publish_jobs
                WHERE output_clip_id = ? AND platform = ? AND publish_mode = ?
                  AND status IN ('DRAFT', 'WAITING', 'SCHEDULED', 'PUBLISHING', 'NEED_REVIEW')
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (output_clip["id"], platform, publish_mode),
            ).fetchone()
            if existing:
                if workflow_job_id:
                    try:
                        evidence = json.loads(str(existing["provider_response"] or "{}"))
                    except json.JSONDecodeError as exc:
                        raise ValueError("已有发布草稿证据已损坏，拒绝复用") from exc
                    expected_caption = str(metadata.get("caption") or "")
                    expected_hashtags = ", ".join(metadata.get("hashtags") or [])
                    existing_video = resolve_task_media_file_path(
                        str(existing["video_file_path"] or ""),
                        task_id=str(task["id"]),
                        task_dir_name=str(task.get("task_dir_name") or "") or None,
                        allowed_subdirectories=(
                            ("06_subtitled",)
                            if video_source == "subtitled"
                            else ("05_clips", "clips")
                        ),
                    )
                    existing_cover = resolve_task_media_file_path(
                        str(existing["cover_file_path"] or ""),
                        task_id=str(task["id"]),
                        task_dir_name=str(task.get("task_dir_name") or "") or None,
                        allowed_subdirectories=("07_covers",),
                        allowed_extensions=IMAGE_EXTENSIONS,
                    )
                    if (
                        not isinstance(evidence, dict)
                        or existing_video is None
                        or existing_cover is None
                        or not existing_video.is_file()
                        or not existing_cover.is_file()
                        or existing_video.resolve() != resolved_video.resolve()
                        or existing_cover.resolve() != resolved_cover.resolve()
                        or int(evidence.get("video_file_size") or -1) != existing_video.stat().st_size
                        or str(evidence.get("video_file_fingerprint") or "")
                        != fingerprint_file(existing_video)
                        or int(evidence.get("cover_file_size") or -1) != existing_cover.stat().st_size
                        or str(evidence.get("cover_file_fingerprint") or "")
                        != fingerprint_file(existing_cover)
                        or str(evidence.get("metadata_policy_version") or "")
                        != PUBLISH_COPY_RULE_VERSION
                        or str(evidence.get("subtitle_delivery_mode") or "")
                        != subtitle_delivery_mode
                        or str(existing["title"] or "")
                        != str(metadata.get("title") or "精彩片段")
                        or str(existing["description"] or "") != expected_caption
                        or str(existing["caption"] or "") != expected_caption
                        or str(existing["tags"] or "") != expected_hashtags
                        or str(existing["hashtags"] or "") != expected_hashtags
                        or str(existing["cover_text"] or "")
                        != str(metadata.get("cover_text") or "")
                        or str(existing["scheduled_at"] or "") != scheduled_at
                        or float(existing["cover_time_seconds"] or 0)
                        != float(cover.get("cover_time_seconds") or 0)
                    ):
                        raise ValueError("已有发布草稿的媒体或文案证据已失效，拒绝复用或重复创建")
                skipped_ids.append(existing["id"])
                continue
            cover_time_seconds = float(cover.get("cover_time_seconds") or 0)
            account_rows = connection.execute(
                """
                SELECT id, login_status FROM publish_accounts
                WHERE platform = ?
                ORDER BY created_at, id
                """,
                (platform,),
            ).fetchall()
            account_id = (
                account_rows[0]["id"]
                if len(account_rows) == 1 and str(account_rows[0]["login_status"] or "") == "normal"
                else None
            )
            status = "NEED_REVIEW" if metadata.get("risk_flags") else (
                "SCHEDULED" if scheduled_at and (publish_mode != "local_browser" or account_id) else "WAITING"
            )
            job_id = uuid4().hex[:12]
            provider_response = {
                "source": "auto_pipeline",
                "workflow_job_id": workflow_job_id or "",
                "target_platform": platform,
                "metadata_source": metadata.get("source") or "",
                "metadata_error": metadata.get("error") or "",
                "metadata_policy_version": PUBLISH_COPY_RULE_VERSION,
                "metadata_upgrade_status": "generated",
                "cover_text": metadata.get("cover_text") or "",
                "cover_source": cover.get("cover_source") or "midpoint_fallback",
                "cover_time_seconds": cover_time_seconds,
                "risk_flags": metadata.get("risk_flags") or [],
                "publish_mode": publish_mode,
                "subtitle_delivery_mode": subtitle_delivery_mode,
                "video_file_size": int(resolved_video.stat().st_size),
                "video_file_fingerprint": fingerprint_file(resolved_video),
                "cover_file_size": int(resolved_cover.stat().st_size),
                "cover_file_fingerprint": fingerprint_file(resolved_cover),
                **subtitle_evidence,
                "note": "全自动流水线已直接创建最终发布任务，可在发送中心设置排期。",
            }
            connection.execute(
                """
                INSERT INTO publish_jobs (
                    id, task_id, output_clip_id, clip_id, account_id, platform, publish_mode,
                    video_source, video_file_path, video_path, title, description, caption,
                    tags, hashtags, cover_text, risk_flags, visibility,
                    cover_mode, cover_time_seconds, allow_download, bilibili_tid,
                    bilibili_copyright, bilibili_source, cover_file_path, scheduled_at,
                    schedule_timezone, timezone, status, audit_status, error_message, last_error,
                    provider_response, publish_result, max_attempts, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'public',
                    'time', ?, 1, ?, 'original', '', ?, ?, ?, ?, ?, 'not_submitted', '', '', ?, '', ?, ?, ?)
                """,
                (
                    job_id,
                    task["id"],
                    output_clip["id"],
                    output_clip["id"],
                    account_id,
                    platform,
                    publish_mode,
                    video_source,
                    video_file_path,
                    video_file_path,
                    metadata.get("title") or "精彩片段",
                    metadata.get("caption") or "",
                    metadata.get("caption") or "",
                    ", ".join(metadata.get("hashtags") or []),
                    ", ".join(metadata.get("hashtags") or []),
                    metadata.get("cover_text") or "",
                    json.dumps(metadata.get("risk_flags") or [], ensure_ascii=False),
                    cover_time_seconds,
                    DEFAULT_BILIBILI_TID,
                    cover_file_path,
                    scheduled_at,
                    settings.app_timezone,
                    settings.app_timezone,
                    status,
                    json.dumps(provider_response, ensure_ascii=False),
                    settings.publish_scheduler_max_retry_count,
                    now,
                    now,
                ),
            )
            created_ids.append(job_id)
        if workflow_job_id:
            active_lease = connection.execute(
                """
                SELECT 1 FROM workflow_jobs
                WHERE id = ? AND status = 'running' AND lease_owner = ? AND lease_token = ?
                  AND lease_expires_at > strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now')
                  AND cancel_requested = 0
                """,
                (workflow_job_id, lease[1], lease[2]),
            ).fetchone()
            if not active_lease:
                connection.rollback()
                raise job_service.JobLeaseLostError(
                    f"发布草稿提交前 Workflow Job 租约已失效：{workflow_job_id}"
                )
        connection.commit()

    return {
        "created": [get_publish_job(job_id) for job_id in created_ids],
        "skipped": [get_publish_job(job_id) for job_id in skipped_ids],
        "created_count": len(created_ids),
        "skipped_count": len(skipped_ids),
    }


def _resolve_auto_video_source(
    task: dict,
    output_clip: dict,
    delivery_mode: str,
    *,
    require_managed_path: bool,
) -> tuple[str, str, Path, dict]:
    if delivery_mode == "original":
        raw_path = str(output_clip.get("output_file_path") or "")
        path = (
            resolve_task_media_file_path(
                raw_path,
                task_id=str(task["id"]),
                task_dir_name=str(task.get("task_dir_name") or "") or None,
                allowed_subdirectories=("05_clips", "clips"),
            )
            if require_managed_path
            else (resolve_video_file_path(raw_path) if raw_path else None)
        )
        if not path or not path.exists() or not path.is_file() or path.stat().st_size <= 0:
            raise ValueError("原片切片文件不存在，不能创建发布任务")
        return "original", str(path), path, {"subtitle_skip_confirmed": True}

    raw_path = str(output_clip.get("subtitled_output_file_path") or "")
    path = (
        resolve_task_media_file_path(
            raw_path,
            task_id=str(task["id"]),
            task_dir_name=str(task.get("task_dir_name") or "") or None,
            allowed_subdirectories=("06_subtitled",),
        )
        if require_managed_path
        else (resolve_video_file_path(raw_path) if raw_path else None)
    )
    if (
        output_clip.get("subtitle_status") != "completed"
        or output_clip.get("subtitle_validation_status") != "verified"
        or output_clip.get("subtitle_revision_status") != "approved"
        or not path
        or not path.exists()
        or not path.is_file()
        or path.stat().st_size <= 0
    ):
        raise ValueError("字幕成片尚未同时通过 revision 审核和 FFprobe 验证，不能进入发送中心")
    return (
        "subtitled",
        str(path),
        path,
        {
            "subtitle_revision_id": output_clip.get("subtitle_revision_id") or "",
            "subtitle_revision_status": output_clip.get("subtitle_revision_status") or "",
            "subtitle_validation_status": output_clip.get("subtitle_validation_status") or "",
            "subtitle_verified_at": output_clip.get("subtitle_verified_at") or "",
        },
    )
