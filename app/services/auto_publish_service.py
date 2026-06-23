"""全自动模式发布任务创建服务。"""

from __future__ import annotations

import json
from uuid import uuid4

from app.db.database import get_connection
from app.services.publish_service import DEFAULT_BILIBILI_TID, get_publish_job
from app.services.task_service import _now_iso


def platforms_for_task(task: dict) -> list[str]:
    platform = (task.get("platform") or "general").strip().lower()
    if platform in {"douyin", "bilibili"}:
        return [platform]
    return ["douyin", "bilibili"]


def create_auto_publish_jobs(task: dict, scheduled_items: list[dict]) -> dict:
    """为全自动流水线生成发布任务。

    本轮只创建任务记录，不调用平台 API，也不启动 opencli 发送。
    """

    created_ids: list[str] = []
    skipped_ids: list[str] = []
    now = _now_iso()
    with get_connection() as connection:
        for item in scheduled_items:
            output_clip = item["output_clip"]
            metadata = item["metadata"]
            platform = metadata["platform"]
            existing = connection.execute(
                """
                SELECT id
                FROM publish_jobs
                WHERE output_clip_id = ? AND platform = ? AND publish_mode = 'opencli_publish'
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (output_clip["id"], platform),
            ).fetchone()
            if existing:
                skipped_ids.append(existing["id"])
                continue

            status = "NEED_REVIEW" if metadata.get("risk_flags") else "ready"
            job_id = uuid4().hex[:12]
            provider_response = {
                "source": "auto_pipeline",
                "metadata_source": metadata.get("source") or "",
                "metadata_error": metadata.get("error") or "",
                "cover_text": metadata.get("cover_text") or "",
                "risk_flags": metadata.get("risk_flags") or [],
                "note": "v1.3.0 只创建发布任务，不执行真实发送。",
            }
            connection.execute(
                """
                INSERT INTO publish_jobs (
                    id, task_id, output_clip_id, account_id, platform, publish_mode,
                    video_source, video_file_path, title, description, tags, visibility,
                    cover_mode, cover_time_seconds, allow_download, bilibili_tid,
                    bilibili_copyright, bilibili_source, cover_file_path, scheduled_at,
                    status, audit_status, error_message, last_error, provider_response,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, NULL, ?, 'opencli_publish', 'original', ?, ?, ?, ?, 'public',
                    'auto', 0, 1, ?, 'original', '', '', ?, ?, 'not_submitted', '', '', ?, ?, ?)
                """,
                (
                    job_id,
                    task["id"],
                    output_clip["id"],
                    platform,
                    output_clip.get("output_file_path") or "",
                    metadata.get("title") or "精彩片段",
                    metadata.get("caption") or "",
                    ", ".join(metadata.get("hashtags") or []),
                    DEFAULT_BILIBILI_TID,
                    item["scheduled_at"],
                    status,
                    json.dumps(provider_response, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            created_ids.append(job_id)
        connection.commit()

    return {
        "created": [get_publish_job(job_id) for job_id in created_ids],
        "skipped": [get_publish_job(job_id) for job_id in skipped_ids],
        "created_count": len(created_ids),
        "skipped_count": len(skipped_ids),
    }
