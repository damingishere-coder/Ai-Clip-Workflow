from pathlib import Path
from uuid import uuid4

from app.db.database import get_connection
from app.models.task import PublishJobCreate, PublishJobStatusUpdate
from app.services.storage_service import resolve_video_file_path


PUBLISH_PLATFORM_LABELS = {
    "douyin": "抖音",
    "bilibili": "B站",
}

PUBLISH_STATUS_LABELS = {
    "draft": "草稿",
    "ready": "待真实平台接入",
    "publishing": "推送中",
    "published": "已发布",
    "failed": "失败",
    "cancelled": "已取消",
}

PUBLISH_STATUS_TONES = {
    "draft": "amber",
    "ready": "blue",
    "publishing": "purple",
    "published": "green",
    "failed": "red",
    "cancelled": "amber",
}

VIDEO_SOURCE_LABELS = {
    "original": "原始切片",
    "subtitled": "带字幕成片",
}


def _now_iso() -> str:
    from datetime import datetime

    return datetime.now().isoformat(timespec="seconds")


def _normalize_publish_job(row) -> dict:
    job = dict(row)
    platform = job.get("platform") or ""
    status = job.get("status") or "ready"
    video_source = job.get("video_source") or "original"
    job.update(
        {
            "platform_label": PUBLISH_PLATFORM_LABELS.get(platform, platform),
            "status_label": PUBLISH_STATUS_LABELS.get(status, status),
            "status_tone": PUBLISH_STATUS_TONES.get(status, "blue"),
            "video_source_label": VIDEO_SOURCE_LABELS.get(video_source, video_source),
        }
    )
    return job


def list_publish_jobs(limit: int | None = None) -> list[dict]:
    sql = """
        SELECT
            publish_jobs.*,
            tasks.task_name,
            output_clip.output_file_name
        FROM publish_jobs
        LEFT JOIN tasks ON tasks.id = publish_jobs.task_id
        LEFT JOIN output_clip ON output_clip.id = publish_jobs.output_clip_id
        ORDER BY publish_jobs.created_at DESC
    """
    params: tuple = ()
    if limit:
        sql += " LIMIT ?"
        params = (limit,)
    with get_connection() as connection:
        rows = connection.execute(sql, params).fetchall()
    return [_normalize_publish_job(row) for row in rows]


def _get_output_clip_for_publish(task_id: str, output_clip_id: str) -> dict | None:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT
                tasks.id AS task_id,
                tasks.task_name,
                output_clip.id AS output_clip_id,
                output_clip.output_file_path,
                output_clip.output_file_name,
                output_clip.status AS output_status,
                clip_candidates.title AS clip_title,
                subtitle_jobs.status AS subtitle_status,
                subtitle_jobs.output_file_path AS subtitled_output_file_path
            FROM output_clip
            JOIN tasks ON tasks.id = output_clip.task_id
            LEFT JOIN clip_candidates ON clip_candidates.id = output_clip.clip_candidate_id
            LEFT JOIN subtitle_jobs ON subtitle_jobs.output_clip_id = output_clip.id
            WHERE output_clip.task_id = ? AND output_clip.id = ?
            """,
            (task_id, output_clip_id),
        ).fetchone()
    return dict(row) if row else None


def _resolve_publish_video_path(output_clip: dict, video_source: str) -> tuple[str, Path]:
    if video_source == "subtitled":
        raw_path = (output_clip.get("subtitled_output_file_path") or "").strip()
        if output_clip.get("subtitle_status") != "completed" or not raw_path:
            raise ValueError("这条切片还没有生成带字幕成片，不能选择“带字幕成片”。")
    else:
        raw_path = (output_clip.get("output_file_path") or "").strip()
        if output_clip.get("output_status") != "completed" or not raw_path:
            raise ValueError("这条原始切片还没有生成完成，不能推送。")

    resolved_path = resolve_video_file_path(raw_path) or Path(raw_path)
    if not resolved_path.exists() or not resolved_path.is_file():
        raise ValueError(f"视频文件不存在，不能创建推送任务：{raw_path}")
    return raw_path, resolved_path


def create_publish_jobs(payload: PublishJobCreate) -> dict:
    output_clip = _get_output_clip_for_publish(payload.task_id, payload.output_clip_id)
    if not output_clip:
        raise ValueError("切片记录不存在")

    raw_video_path, _ = _resolve_publish_video_path(output_clip, payload.video_source)
    now = _now_iso()
    created_jobs = []
    with get_connection() as connection:
        for platform in payload.platforms:
            job_id = uuid4().hex[:12]
            connection.execute(
                """
                INSERT INTO publish_jobs (
                    id, task_id, output_clip_id, platform, video_source, video_file_path,
                    title, description, tags, status, error_message, provider_response,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    payload.task_id,
                    payload.output_clip_id,
                    platform,
                    payload.video_source,
                    raw_video_path,
                    payload.title.strip(),
                    (payload.description or "").strip(),
                    (payload.tags or "").strip(),
                    "ready",
                    "",
                    "真实平台上传尚未接入；当前已完成本地发布任务记录和人工确认队列。",
                    now,
                    now,
                ),
            )
            created_jobs.append(job_id)
        connection.commit()

    jobs = [get_publish_job(job_id) for job_id in created_jobs]
    return {
        "status": "ok",
        "message": f"已创建 {len(created_jobs)} 条推送任务，等待后续接入真实平台发布。",
        "jobs": [job for job in jobs if job],
    }


def get_publish_job(job_id: str) -> dict | None:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT
                publish_jobs.*,
                tasks.task_name,
                output_clip.output_file_name
            FROM publish_jobs
            LEFT JOIN tasks ON tasks.id = publish_jobs.task_id
            LEFT JOIN output_clip ON output_clip.id = publish_jobs.output_clip_id
            WHERE publish_jobs.id = ?
            """,
            (job_id,),
        ).fetchone()
    return _normalize_publish_job(row) if row else None


def update_publish_job_status(job_id: str, payload: PublishJobStatusUpdate) -> dict:
    if not get_publish_job(job_id):
        raise ValueError("推送任务不存在")
    now = _now_iso()
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE publish_jobs
            SET status = ?, error_message = ?, updated_at = ?
            WHERE id = ?
            """,
            (payload.status, payload.error_message or "", now, job_id),
        )
        connection.commit()
    return {
        "status": "ok",
        "message": "推送任务状态已更新。",
        "job": get_publish_job(job_id),
    }


def cancel_publish_job(job_id: str) -> dict:
    job = get_publish_job(job_id)
    if not job:
        raise ValueError("推送任务不存在")
    if job.get("status") == "published":
        raise ValueError("已发布的任务不能取消。")
    payload = PublishJobStatusUpdate(status="cancelled", error_message="")
    result = update_publish_job_status(job_id, payload)
    result["message"] = "推送任务已取消。"
    return result


def _group_jobs_by_output_clip() -> dict[str, list[dict]]:
    jobs_by_output: dict[str, list[dict]] = {}
    for job in list_publish_jobs():
        jobs_by_output.setdefault(job["output_clip_id"], []).append(job)
    return jobs_by_output


def get_publish_center_context() -> dict:
    jobs_by_output = _group_jobs_by_output_clip()
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                tasks.id AS task_id,
                tasks.task_name,
                tasks.platform AS task_platform,
                output_clip.id AS output_clip_id,
                output_clip.output_file_path,
                output_clip.output_file_name,
                output_clip.status AS output_status,
                clip_candidates.title AS clip_title,
                subtitle_jobs.status AS subtitle_status,
                subtitle_jobs.output_file_path AS subtitled_output_file_path
            FROM output_clip
            JOIN tasks ON tasks.id = output_clip.task_id
            LEFT JOIN clip_candidates ON clip_candidates.id = output_clip.clip_candidate_id
            LEFT JOIN subtitle_jobs ON subtitle_jobs.output_clip_id = output_clip.id
            WHERE tasks.is_deleted = 0 AND output_clip.status = 'completed'
            ORDER BY output_clip.created_at DESC
            """
        ).fetchall()

    publish_items = []
    for row in rows:
        item = dict(row)
        original_path = resolve_video_file_path(item.get("output_file_path") or "")
        subtitled_path = resolve_video_file_path(item.get("subtitled_output_file_path") or "")
        output_name = item.get("output_file_name") or "未命名切片"
        default_title = item.get("clip_title") or Path(output_name).stem or item.get("task_name") or "直播切片"
        item_jobs = jobs_by_output.get(item["output_clip_id"], [])
        created_platforms = sorted({job["platform"] for job in item_jobs})
        publish_items.append(
            {
                **item,
                "default_title": default_title,
                "original_available": bool(original_path and original_path.exists() and original_path.is_file()),
                "subtitled_available": bool(subtitled_path and subtitled_path.exists() and subtitled_path.is_file()),
                "subtitle_status_label": "已加字幕" if item.get("subtitle_status") == "completed" else "未加字幕",
                "jobs": item_jobs,
                "created_platform_labels": "、".join(PUBLISH_PLATFORM_LABELS.get(code, code) for code in created_platforms),
            }
        )

    jobs = list_publish_jobs(limit=80)
    ready_count = sum(1 for job in jobs if job.get("status") == "ready")
    published_count = sum(1 for job in jobs if job.get("status") == "published")
    failed_count = sum(1 for job in jobs if job.get("status") == "failed")
    return {
        "publish_items": publish_items,
        "publish_jobs": jobs,
        "stats": [
            {"label": "可推送切片", "value": len(publish_items), "tone": "green"},
            {"label": "已建推送任务", "value": len(jobs), "tone": "blue"},
            {"label": "待平台接入", "value": ready_count, "tone": "amber"},
            {"label": "已发布", "value": published_count, "tone": "green"},
            {"label": "失败", "value": failed_count, "tone": "red"},
        ],
    }
