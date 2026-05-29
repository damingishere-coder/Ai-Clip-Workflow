import json
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from app.db.database import get_connection
from app.models.task import (
    PublishAccountCreate,
    PublishBatchJobCreate,
    PublishCoverCreate,
    PublishJobCreate,
    PublishPlatformConfigUpdate,
)
from app.services.publish_providers import (
    BilibiliPublishProvider,
    DouyinPublishProvider,
    PublishProviderError,
)
from app.services.storage_service import get_artifact_paths, resolve_video_file_path
from app.services.video_cut_service import ensure_ffmpeg_available, sanitize_filename_part, summarize_stderr


PLATFORM_LABELS = {
    "douyin": "抖音",
    "bilibili": "B站",
}

STATUS_LABELS = {
    "draft": "草稿",
    "ready": "待人工发布",
    "publishing": "发布中",
    "published": "已发布",
    "failed": "发布失败",
    "cancelled": "已取消",
}

STATUS_TONES = {
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

PUBLISH_MODE_LABELS = {
    "draft": "保存草稿",
    "manual_review": "人工发布任务",
    "api_publish": "真实接口发布",
}

COVER_WIDTH = 1280
COVER_HEIGHT = 720


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _row_to_dict(row) -> dict | None:
    return dict(row) if row else None


def _mask_secret(value: str | None) -> str:
    value = value or ""
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}{'*' * max(4, len(value) - 8)}{value[-4:]}"


def _cover_media_url(task_id: str, cover_file_path: str | None) -> str:
    if not cover_file_path:
        return ""
    return f"/media/tasks/{task_id}/covers/{Path(cover_file_path).name}"


def _normalize_config(row) -> dict:
    config = dict(row)
    client_key = config.get("client_key") or ""
    client_secret = config.get("client_secret") or ""
    config.update(
        {
            "platform_label": PLATFORM_LABELS.get(config.get("platform"), config.get("platform")),
            "configured": bool(client_key and client_secret),
            "client_key_masked": _mask_secret(client_key),
            "client_secret_masked": _mask_secret(client_secret),
        }
    )
    return config


def _normalize_account(row) -> dict:
    account = dict(row)
    account.update(
        {
            "platform_label": PLATFORM_LABELS.get(account.get("platform"), account.get("platform")),
            "access_token_masked": _mask_secret(account.get("access_token")),
            "refresh_token_masked": _mask_secret(account.get("refresh_token")),
            "is_authorized": account.get("authorization_status") == "authorized",
        }
    )
    return account


def _normalize_job(row) -> dict:
    job = dict(row)
    status = job.get("status") or "ready"
    job.update(
        {
            "platform_label": PLATFORM_LABELS.get(job.get("platform"), job.get("platform")),
            "status_label": STATUS_LABELS.get(status, status),
            "status_tone": STATUS_TONES.get(status, "blue"),
            "video_source_label": VIDEO_SOURCE_LABELS.get(job.get("video_source"), job.get("video_source")),
            "publish_mode_label": PUBLISH_MODE_LABELS.get(job.get("publish_mode"), job.get("publish_mode")),
            "account_name": job.get("account_name") or "未选择账号",
            "cover_media_url": _cover_media_url(job.get("task_id") or "", job.get("cover_file_path")),
        }
    )
    return job


def list_platform_configs() -> list[dict]:
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM publish_platform_configs ORDER BY CASE platform WHEN 'douyin' THEN 1 WHEN 'bilibili' THEN 2 ELSE 9 END"
        ).fetchall()
    return [_normalize_config(row) for row in rows]


def get_platform_config(platform: str) -> dict | None:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM publish_platform_configs WHERE platform = ?",
            (platform,),
        ).fetchone()
    return _normalize_config(row) if row else None


def update_platform_config(platform: str, payload: PublishPlatformConfigUpdate) -> dict:
    existing = get_platform_config(platform)
    if not existing:
        raise ValueError("发布平台不存在。")

    now = _now_iso()
    client_secret = payload.client_secret.strip() or existing.get("client_secret") or ""
    status = "configured" if payload.client_key.strip() and client_secret else "not_configured"
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE publish_platform_configs
            SET app_name = ?, client_key = ?, client_secret = ?, redirect_uri = ?,
                scope = ?, api_base_url = ?, auth_url = ?, token_url = ?,
                refresh_url = ?, upload_url = ?, create_url = ?, extra_config = ?,
                status = ?, updated_at = ?
            WHERE platform = ?
            """,
            (
                payload.app_name.strip(),
                payload.client_key.strip(),
                client_secret,
                payload.redirect_uri.strip(),
                payload.scope.strip(),
                payload.api_base_url.strip(),
                payload.auth_url.strip(),
                payload.token_url.strip(),
                payload.refresh_url.strip(),
                payload.upload_url.strip(),
                payload.create_url.strip(),
                (payload.extra_config or "").strip(),
                status,
                now,
                platform,
            ),
        )
        connection.commit()
    return {"status": "ok", "message": f"{PLATFORM_LABELS.get(platform, platform)} 配置已保存。", "config": get_platform_config(platform)}


def test_platform_config(platform: str) -> dict:
    config = get_platform_config(platform)
    if not config:
        raise ValueError("发布平台不存在。")
    provider = _get_provider(platform, config)
    now = _now_iso()
    try:
        provider.validate_config()
        status = "ok"
        message = "本地配置检查通过。真实发布前仍需要确认账号授权和平台权限。"
    except PublishProviderError as exc:
        status = "failed"
        message = exc.message
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE publish_platform_configs
            SET last_test_status = ?, last_test_message = ?, updated_at = ?
            WHERE platform = ?
            """,
            (status, message, now, platform),
        )
        connection.commit()
    return {"status": status, "message": message, "config": get_platform_config(platform)}


def list_accounts(platform: str | None = None) -> list[dict]:
    sql = "SELECT * FROM publish_accounts"
    params: tuple = ()
    if platform:
        sql += " WHERE platform = ?"
        params = (platform,)
    sql += " ORDER BY updated_at DESC"
    with get_connection() as connection:
        rows = connection.execute(sql, params).fetchall()
    return [_normalize_account(row) for row in rows]


def get_account(account_id: str) -> dict | None:
    if not account_id:
        return None
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM publish_accounts WHERE id = ?", (account_id,)).fetchone()
    return _normalize_account(row) if row else None


def create_account(payload: PublishAccountCreate) -> dict:
    account_id = uuid4().hex[:12]
    now = _now_iso()
    auth_status = "authorized" if (payload.access_token or "").strip() else "manual"
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO publish_accounts (
                id, platform, account_name, account_uid, open_id, access_token,
                refresh_token, token_expires_at, refresh_expires_at, authorization_status,
                scopes, remark, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                account_id,
                payload.platform,
                payload.account_name.strip(),
                (payload.account_uid or "").strip(),
                (payload.open_id or "").strip(),
                (payload.access_token or "").strip(),
                (payload.refresh_token or "").strip(),
                (payload.token_expires_at or "").strip(),
                (payload.refresh_expires_at or "").strip(),
                auth_status,
                (payload.scopes or "").strip(),
                (payload.remark or "").strip(),
                now,
                now,
            ),
        )
        connection.commit()
    return {"status": "ok", "message": "发布账号已保存。", "account": get_account(account_id)}


def build_douyin_oauth_url() -> dict:
    config = get_platform_config("douyin")
    if not config:
        raise ValueError("抖音配置不存在。")
    state = uuid4().hex
    url = DouyinPublishProvider(config).build_oauth_url(state)
    return {"status": "ok", "url": url, "state": state}


def save_douyin_oauth_account(code: str) -> dict:
    config = get_platform_config("douyin")
    if not config:
        raise ValueError("抖音配置不存在。")
    response = DouyinPublishProvider(config).exchange_code(code)
    data = response.get("data") if isinstance(response.get("data"), dict) else response
    expires_in = int(data.get("expires_in") or 0)
    refresh_expires_in = int(data.get("refresh_expires_in") or 0)
    now = datetime.now()
    token_expires_at = (now + timedelta(seconds=expires_in)).isoformat(timespec="seconds") if expires_in else ""
    refresh_expires_at = (now + timedelta(seconds=refresh_expires_in)).isoformat(timespec="seconds") if refresh_expires_in else ""
    payload = PublishAccountCreate(
        platform="douyin",
        account_name=data.get("nickname") or "抖音授权账号",
        account_uid=data.get("union_id") or "",
        open_id=data.get("open_id") or "",
        access_token=data.get("access_token") or "",
        refresh_token=data.get("refresh_token") or "",
        token_expires_at=token_expires_at,
        refresh_expires_at=refresh_expires_at,
        scopes=data.get("scope") or config.get("scope") or "",
        remark="OAuth 授权创建",
    )
    result = create_account(payload)
    result["provider_response"] = response
    return result


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
    return _row_to_dict(row)


def _get_output_clip_by_id(output_clip_id: str) -> dict | None:
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
            WHERE output_clip.id = ?
            """,
            (output_clip_id,),
        ).fetchone()
    return _row_to_dict(row)


def _resolve_publish_video_path(output_clip: dict, video_source: str) -> tuple[str, Path]:
    if video_source == "subtitled":
        raw_path = (output_clip.get("subtitled_output_file_path") or "").strip()
        if output_clip.get("subtitle_status") != "completed" or not raw_path:
            raise ValueError("这条切片还没有生成带字幕成片，不能选择“带字幕成片”。")
    else:
        raw_path = (output_clip.get("output_file_path") or "").strip()
        if output_clip.get("output_status") != "completed" or not raw_path:
            raise ValueError("这条原始切片还没有生成完成，不能发布。")

    resolved_path = resolve_video_file_path(raw_path) or Path(raw_path)
    if not resolved_path.exists() or not resolved_path.is_file():
        raise ValueError(f"视频文件不存在，不能创建发布任务：{raw_path}")
    return raw_path, resolved_path


def _wrap_cover_title(title: str) -> str:
    text = re.sub(r"\s+", " ", (title or "").strip()) or "精彩片段"
    if len(text) > 34:
        text = f"{text[:34]}..."
    if len(text) <= 15:
        return text
    split_at = min(17, max(10, len(text) // 2))
    return f"{text[:split_at]}\n{text[split_at:]}"


def _escape_drawtext_value(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace(",", "\\,")
        .replace("%", "\\%")
        .replace("\n", "\\n")
    )


def _cover_font_option() -> str:
    candidates = [
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/msyh.ttf"),
        Path("C:/Windows/Fonts/simhei.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
    ]
    for font_path in candidates:
        if font_path.exists():
            return f"fontfile='{str(font_path).replace('\\', '/').replace(':', '\\:')}'"
    return "font='Microsoft YaHei'"


def _build_cover_filter(title: str) -> str:
    cover_title = _escape_drawtext_value(_wrap_cover_title(title))
    return ",".join(
        [
            f"scale={COVER_WIDTH}:{COVER_HEIGHT}:force_original_aspect_ratio=increase",
            f"crop={COVER_WIDTH}:{COVER_HEIGHT}",
            "format=yuv420p",
            "drawbox=x=0:y=0:w=iw:h=ih:color=black@0.18:t=fill",
            "drawbox=x=0:y=ih*0.50:w=iw:h=ih*0.50:color=black@0.40:t=fill",
            (
                "drawtext="
                f"{_cover_font_option()}:"
                f"text='{cover_title}':"
                "x=(w-text_w)/2:"
                "y=h*0.58:"
                "fontsize=64:"
                "fontcolor=white:"
                "borderw=4:"
                "bordercolor=black@0.55:"
                "line_spacing=16"
            ),
        ]
    )


def _unique_cover_path(task_id: str, output_clip_id: str, video_source: str, title: str) -> Path:
    cover_dir = get_artifact_paths(task_id)["covers_dir"]
    cover_dir.mkdir(parents=True, exist_ok=True)
    safe_title = sanitize_filename_part(title, fallback="cover")
    base_name = f"{output_clip_id}_{video_source}_{safe_title}"
    output_path = cover_dir / f"{base_name}.jpg"
    if not output_path.exists():
        return output_path
    for index in range(2, 1000):
        candidate = cover_dir / f"{base_name}_{index}.jpg"
        if not candidate.exists():
            return candidate
    return cover_dir / f"{base_name}_{uuid4().hex[:6]}.jpg"


def generate_publish_cover(payload: PublishCoverCreate, job_id: str | None = None) -> dict:
    output_clip = _get_output_clip_for_publish(payload.task_id, payload.output_clip_id)
    if not output_clip:
        raise ValueError("切片记录不存在。")

    _, video_path = _resolve_publish_video_path(output_clip, payload.video_source)
    ffmpeg_path = ensure_ffmpeg_available()
    cover_path = _unique_cover_path(payload.task_id, payload.output_clip_id, payload.video_source, payload.title)
    command = [
        ffmpeg_path,
        "-y",
        "-ss",
        f"{float(payload.cover_time_seconds or 0):.3f}",
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-vf",
        _build_cover_filter(payload.title),
        "-q:v",
        "2",
        str(cover_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise ValueError(f"封面生成失败：{summarize_stderr(result.stderr)}")
    if not cover_path.exists() or cover_path.stat().st_size == 0:
        raise ValueError("封面生成失败：FFmpeg 没有输出有效图片。")

    if job_id:
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE publish_jobs
                SET cover_mode = 'time', cover_time_seconds = ?, cover_file_path = ?, updated_at = ?
                WHERE id = ?
                """,
                (float(payload.cover_time_seconds or 0), str(cover_path), _now_iso(), job_id),
            )
            connection.commit()

    return {
        "status": "ok",
        "message": "封面已生成。",
        "cover_file_path": str(cover_path),
        "cover_media_url": _cover_media_url(payload.task_id, str(cover_path)),
    }


def generate_publish_job_cover(job_id: str, payload: PublishCoverCreate) -> dict:
    job = get_publish_job(job_id)
    if not job:
        raise ValueError("发布任务不存在。")
    if job.get("task_id") != payload.task_id or job.get("output_clip_id") != payload.output_clip_id:
        raise ValueError("封面参数和发布任务不一致。")
    result = generate_publish_cover(payload, job_id=job_id)
    result["job"] = get_publish_job(job_id)
    return result


def _validate_api_publish_ready(payload: PublishJobCreate) -> tuple[dict, dict]:
    account = get_account(payload.account_id or "")
    if not account:
        raise ValueError("真实发布必须先选择一个已配置的发布账号。")
    if account.get("platform") != payload.platform:
        raise ValueError("账号平台和发布平台不一致。")
    config = get_platform_config(payload.platform)
    if not config:
        raise ValueError("发布平台配置不存在。")
    _get_provider(payload.platform, config).validate_config()
    return config, account


def create_publish_job(payload: PublishJobCreate) -> dict:
    output_clip = _get_output_clip_for_publish(payload.task_id, payload.output_clip_id)
    if not output_clip:
        raise ValueError("切片记录不存在。")
    raw_video_path, resolved_video_path = _resolve_publish_video_path(output_clip, payload.video_source)

    config = None
    account = None
    if payload.publish_mode == "api_publish":
        config, account = _validate_api_publish_ready(payload)

    job_id = uuid4().hex[:12]
    now = _now_iso()
    status = "draft" if payload.publish_mode == "draft" else "ready"
    if payload.publish_mode == "api_publish":
        status = "publishing"
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO publish_jobs (
                id, task_id, output_clip_id, account_id, platform, publish_mode,
                video_source, video_file_path, title, description, tags, visibility,
                cover_mode, cover_time_seconds, allow_download, bilibili_tid,
                bilibili_copyright, bilibili_source, cover_file_path, scheduled_at,
                status, audit_status, provider_response, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                payload.task_id,
                payload.output_clip_id,
                payload.account_id or "",
                payload.platform,
                payload.publish_mode,
                payload.video_source,
                raw_video_path,
                payload.title.strip(),
                (payload.description or "").strip(),
                (payload.tags or "").strip(),
                payload.visibility,
                payload.cover_mode,
                float(payload.cover_time_seconds or 0),
                1 if payload.allow_download else 0,
                (payload.bilibili_tid or "").strip(),
                payload.bilibili_copyright,
                (payload.bilibili_source or "").strip(),
                (payload.cover_file_path or "").strip(),
                (payload.scheduled_at or "").strip(),
                status,
                "not_submitted",
                "真实发布任务已创建，等待执行。" if payload.publish_mode == "api_publish" else "本地发布任务已创建，等待人工确认。",
                now,
                now,
            ),
        )
        connection.commit()

    if payload.publish_mode == "api_publish" and config and account:
        return _execute_publish_job(job_id, config=config, account=account, video_path=resolved_video_path)
    return {"status": "ok", "message": "发布任务已创建。", "job": get_publish_job(job_id)}


def create_batch_publish_jobs(payload: PublishBatchJobCreate) -> dict:
    created = []
    for output_clip_id in payload.output_clip_ids:
        output_clip = _get_output_clip_by_id(output_clip_id)
        if not output_clip:
            continue
        title = output_clip.get("clip_title") or Path(output_clip.get("output_file_name") or "直播切片").stem
        if payload.title_prefix:
            title = f"{payload.title_prefix.strip()}{title}"
        job_payload = PublishJobCreate(
            task_id=output_clip["task_id"],
            output_clip_id=output_clip["output_clip_id"],
            platform=payload.platform,
            account_id=payload.account_id or "",
            publish_mode=payload.publish_mode,
            video_source=payload.video_source,
            title=title[:120],
            description=payload.description or "",
            tags=payload.tags or "",
        )
        created.append(create_publish_job(job_payload)["job"])
    return {"status": "ok", "message": f"已创建 {len(created)} 条批量发布任务。", "jobs": created}


def get_publish_job(job_id: str) -> dict | None:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT
                publish_jobs.*,
                tasks.task_name,
                output_clip.output_file_name,
                publish_accounts.account_name
            FROM publish_jobs
            LEFT JOIN tasks ON tasks.id = publish_jobs.task_id
            LEFT JOIN output_clip ON output_clip.id = publish_jobs.output_clip_id
            LEFT JOIN publish_accounts ON publish_accounts.id = publish_jobs.account_id
            WHERE publish_jobs.id = ?
            """,
            (job_id,),
        ).fetchone()
    return _normalize_job(row) if row else None


def list_publish_jobs(limit: int | None = 100) -> list[dict]:
    sql = """
        SELECT
            publish_jobs.*,
            tasks.task_name,
            output_clip.output_file_name,
            publish_accounts.account_name
        FROM publish_jobs
        LEFT JOIN tasks ON tasks.id = publish_jobs.task_id
        LEFT JOIN output_clip ON output_clip.id = publish_jobs.output_clip_id
        LEFT JOIN publish_accounts ON publish_accounts.id = publish_jobs.account_id
        ORDER BY publish_jobs.created_at DESC
    """
    params: tuple = ()
    if limit:
        sql += " LIMIT ?"
        params = (limit,)
    with get_connection() as connection:
        rows = connection.execute(sql, params).fetchall()
    return [_normalize_job(row) for row in rows]


def update_publish_job_status(job_id: str, status: str, error_message: str = "") -> dict:
    if not get_publish_job(job_id):
        raise ValueError("发布任务不存在。")
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE publish_jobs
            SET status = ?, error_message = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, error_message, _now_iso(), job_id),
        )
        connection.commit()
    return {"status": "ok", "message": "发布任务状态已更新。", "job": get_publish_job(job_id)}


def retry_publish_job(job_id: str) -> dict:
    job = get_publish_job(job_id)
    if not job:
        raise ValueError("发布任务不存在。")
    if job.get("publish_mode") != "api_publish":
        raise ValueError("只有真实接口发布任务可以重试。")
    output_clip = _get_output_clip_for_publish(job["task_id"], job["output_clip_id"])
    if not output_clip:
        raise ValueError("切片记录不存在。")
    _, video_path = _resolve_publish_video_path(output_clip, job["video_source"])
    config = get_platform_config(job["platform"])
    account = get_account(job.get("account_id") or "")
    if not config or not account:
        raise ValueError("平台配置或账号不存在。")
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE publish_jobs
            SET status = 'publishing', retry_count = retry_count + 1,
                error_code = '', error_message = '', updated_at = ?
            WHERE id = ?
            """,
            (_now_iso(), job_id),
        )
        connection.commit()
    return _execute_publish_job(job_id, config=config, account=account, video_path=video_path)


def _execute_publish_job(job_id: str, config: dict, account: dict, video_path: Path) -> dict:
    job = get_publish_job(job_id)
    if not job:
        raise ValueError("发布任务不存在。")
    provider = _get_provider(job["platform"], config)
    now = _now_iso()
    try:
        result = provider.publish(account, job, video_path)
    except PublishProviderError as exc:
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE publish_jobs
                SET status = 'failed', audit_status = 'not_submitted',
                    error_code = ?, error_message = ?, provider_response = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    exc.error_code,
                    exc.message,
                    json.dumps(exc.response, ensure_ascii=False),
                    now,
                    job_id,
                ),
            )
            connection.commit()
        return {"status": "failed", "message": exc.message, "job": get_publish_job(job_id)}

    with get_connection() as connection:
        connection.execute(
            """
            UPDATE publish_jobs
            SET status = 'published', audit_status = ?, platform_item_id = ?,
                platform_upload_id = ?, error_code = '', error_message = '',
                provider_response = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                result.audit_status,
                result.item_id,
                result.upload_id,
                json.dumps(result.response or {}, ensure_ascii=False),
                now,
                job_id,
            ),
        )
        connection.commit()
    return {"status": "ok", "message": "平台发布请求已提交。", "job": get_publish_job(job_id)}


def _get_provider(platform: str, config: dict):
    if platform == "douyin":
        return DouyinPublishProvider(config)
    if platform == "bilibili":
        return BilibiliPublishProvider(config)
    raise PublishProviderError("暂不支持这个发布平台。", "unsupported_platform")


def get_publish_center_context() -> dict:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                tasks.id AS task_id,
                tasks.task_name,
                output_clip.id AS output_clip_id,
                output_clip.output_file_path,
                output_clip.output_file_name,
                output_clip.status AS output_status,
                output_clip.created_at,
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
        publish_items.append(
            {
                **item,
                "default_title": default_title,
                "original_available": bool(original_path and original_path.exists() and original_path.is_file()),
                "subtitled_available": bool(subtitled_path and subtitled_path.exists() and subtitled_path.is_file()),
                "subtitle_status_label": "已加字幕" if item.get("subtitle_status") == "completed" else "未加字幕",
            }
        )

    configs = list_platform_configs()
    accounts = list_accounts()
    jobs = list_publish_jobs(limit=100)
    return {
        "publish_items": publish_items,
        "publish_jobs": jobs,
        "publish_configs": configs,
        "publish_accounts": accounts,
        "accounts_by_platform": {
            platform: [account for account in accounts if account["platform"] == platform]
            for platform in PLATFORM_LABELS
        },
        "jobs_by_platform": {
            platform: [job for job in jobs if job["platform"] == platform]
            for platform in PLATFORM_LABELS
        },
        "stats": [
            {"label": "可发布切片", "value": len(publish_items), "tone": "green"},
            {"label": "发布账号", "value": len(accounts), "tone": "blue"},
            {"label": "待人工发布", "value": sum(1 for job in jobs if job.get("status") == "ready"), "tone": "amber"},
            {"label": "已发布", "value": sum(1 for job in jobs if job.get("status") == "published"), "tone": "green"},
            {"label": "发布失败", "value": sum(1 for job in jobs if job.get("status") == "failed"), "tone": "red"},
        ],
    }
