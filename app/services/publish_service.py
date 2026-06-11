import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any, Callable
from urllib.parse import urlsplit
from uuid import uuid4

from app.core.config import settings
from app.db.database import get_connection
from app.models.task import (
    PublishAccountCreate,
    PublishBatchJobCreate,
    PublishCoverCreate,
    PublishCoverFrameBatchCreate,
    PublishJobCreate,
    PublishPlatformConfigUpdate,
    PublishSendJobUpdate,
    PublishSendStart,
)
from app.services.ai.ai_clip_analyzer import build_remote_provider
from app.services.ai.base import AIProviderError
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
    "ready": "待发送",
    "publishing": "发送中",
    "published": "已发布",
    "failed": "发送失败",
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
    "opencli_publish": "opencli 网页发送",
}

COVER_WIDTH = 1280
COVER_HEIGHT = 720
OPENCLI_TIMEOUT_SECONDS = 900
DEFAULT_BILIBILI_TID = "娱乐"
_SEND_LOCK = Lock()

SAFE_TOPIC_FALLBACKS = ("精彩片段", "高光片段", "直播切片")
CONTENT_SAFETY_REPLACEMENTS = (
    ("笑死我了", "笑到停不下"),
    ("笑死", "笑到停不下"),
    ("气死", "太上头"),
    ("社死", "大型尴尬"),
    ("作死", "危险尝试"),
    ("死亡", "危险"),
    ("死了", "没绷住"),
    ("死", "不妙"),
    ("屎一样", "有点离谱"),
    ("这坨", "这段"),
    ("拉屎", "尴尬瞬间"),
    ("屎", "糟糕"),
    ("尿", "尴尬"),
    ("屁", "离谱"),
    ("傻逼", "离谱"),
    ("傻X", "离谱"),
    ("傻x", "离谱"),
    ("色情", "成人向内容"),
    ("黄色", "成人向内容"),
    ("裸露", "不适内容"),
    ("约炮", "不当邀约"),
    ("招嫖", "违法行为"),
    ("嫖娼", "违法行为"),
    ("强奸", "严重违法行为"),
    ("血腥", "强刺激"),
    ("暴力", "冲突"),
    ("杀人", "危险事件"),
    ("自杀", "危险行为"),
    ("自残", "危险行为"),
    ("尸体", "不适画面"),
    ("赌博", "风险行为"),
    ("博彩", "风险行为"),
    ("诈骗", "风险套路"),
    ("洗钱", "违法风险"),
    ("毒品", "违禁内容"),
    ("吸毒", "危险行为"),
    ("加微信", "交流"),
    ("微信号", "联系方式"),
    ("QQ群", "社群"),
)
CONTENT_SAFETY_REMOVE_WORDS = (
    "全网第一",
    "唯一官方",
    "100%",
    "稳赚",
    "稳赚不赔",
    "无风险",
    "包过",
    "必赚",
    "点击领取",
    "私加",
    "加我",
    "买粉",
    "刷单",
)

CommandRunner = Callable[[list[str]], subprocess.CompletedProcess]


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _unique_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    unique: list[Path] = []
    for path in paths:
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def _npm_global_opencli_dirs() -> list[Path]:
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if not npm:
        return []

    directories: list[Path] = []
    for command in ([npm, "root", "-g"], [npm, "config", "get", "prefix"]):
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode != 0:
            continue
        lines = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
        if not lines:
            continue
        directory = Path(lines[-1])
        if directory.name.lower() == "node_modules":
            directory = directory.parent
        directories.append(directory)
    return _unique_paths(directories)


def _opencli_search_dirs() -> list[Path]:
    search_dirs: list[Path] = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        search_dirs.append(Path(appdata) / "npm")
    user_profile = os.environ.get("USERPROFILE")
    if user_profile:
        search_dirs.append(Path(user_profile) / "AppData" / "Roaming" / "npm")
    search_dirs.extend(_npm_global_opencli_dirs())
    return _unique_paths(search_dirs)


def _opencli_executable() -> str | None:
    candidates = ["opencli.cmd", "opencli.exe", "opencli", "opencli.ps1"]
    for candidate in candidates:
        resolved = shutil.which(candidate)
        if resolved:
            return resolved

    for directory in _opencli_search_dirs():
        for candidate in candidates:
            path = directory / candidate
            if path.exists():
                return str(path)
    return None


def _opencli_node_command(executable: str) -> list[str] | None:
    wrapper_path = Path(executable)
    main_js = wrapper_path.parent / "node_modules" / "@jackwener" / "opencli" / "dist" / "src" / "main.js"
    if not main_js.exists():
        return None
    local_node = wrapper_path.parent / "node.exe"
    node = str(local_node) if local_node.exists() else (shutil.which("node") or "node")
    return [node, str(main_js)]


def _opencli_command() -> list[str]:
    executable = _opencli_executable()
    if not executable:
        return ["opencli"]
    path = Path(executable)
    if path.suffix.lower() in {".cmd", ".ps1"} or path.name.lower() == "opencli":
        node_command = _opencli_node_command(executable)
        if node_command:
            return node_command
    return [executable]


def _opencli_bridge_url() -> str:
    return settings.opencli_host_bridge_url.rstrip("/")


def _opencli_bridge_health() -> dict:
    bridge_url = _opencli_bridge_url()
    if not bridge_url:
        return {"available": False, "message": "未配置 Windows opencli 辅助服务。"}
    try:
        with urllib.request.urlopen(f"{bridge_url}/health", timeout=1) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except (OSError, TimeoutError, ValueError, urllib.error.URLError) as exc:
        return {"available": False, "message": f"Windows opencli 辅助服务未连接：{exc}"}
    return {
        "available": bool(payload.get("opencli_available")),
        "message": payload.get("message") or "Windows opencli 辅助服务已连接。",
        "executable": payload.get("opencli_executable") or "",
        "url": bridge_url,
    }


def _opencli_bridge_command_runner(command: list[str]) -> subprocess.CompletedProcess:
    bridge_url = _opencli_bridge_url()
    if not bridge_url:
        return subprocess.CompletedProcess(command, 127, "", "未配置 Windows opencli 辅助服务。")
    request = urllib.request.Request(
        f"{bridge_url}/run",
        data=json.dumps(
            {"command": command, "timeout": OPENCLI_TIMEOUT_SECONDS},
            ensure_ascii=False,
        ).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=OPENCLI_TIMEOUT_SECONDS + 10) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except (OSError, TimeoutError, ValueError, urllib.error.URLError) as exc:
        return subprocess.CompletedProcess(command, 127, "", f"Windows opencli 辅助服务调用失败：{exc}")
    return subprocess.CompletedProcess(
        command,
        int(payload.get("returncode", 1)),
        payload.get("stdout") or "",
        payload.get("stderr") or "",
    )


def _opencli_local_port() -> int:
    parsed = urlsplit(settings.opencli_local_base_url)
    if parsed.port:
        return parsed.port
    return 443 if parsed.scheme == "https" else 80


def _opencli_restart_command() -> str:
    return ".\\scripts\\start_docker_opencli.ps1"


def _opencli_status() -> dict:
    executable = _opencli_executable()
    bridge = _opencli_bridge_health() if not executable else {"available": False}
    base_url = settings.opencli_local_base_url.rstrip("/")
    status = {
        "available": bool(executable) or bool(bridge.get("available")),
        "executable": executable or str(bridge.get("executable") or ""),
        "command": " ".join(_opencli_command()) if executable else "",
        "restart_command": _opencli_restart_command(),
        "publish_url": f"{base_url}/publish",
        "manual_check_command": "where opencli",
        "mode": "local" if executable else ("host_bridge" if bridge.get("available") else "missing"),
        "message": "",
    }
    if executable:
        status["message"] = "已检测到 opencli，可以使用发送中心自动发送。"
    elif bridge.get("available"):
        status["message"] = "Docker 8001 已连接 Windows opencli 辅助服务，可以使用发送中心自动发送。"
    else:
        status["message"] = "Docker 页面已启动，但还没有连接到 Windows opencli 辅助服务。发送中心可以先整理队列，自动发送需要先启动辅助服务。"
    return status


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


def _video_media_url(task_id: str, output_clip_id: str, video_source: str) -> str:
    if video_source == "subtitled":
        return f"/media/tasks/{task_id}/subtitled-clips/{output_clip_id}"
    return f"/media/tasks/{task_id}/output-clips/{output_clip_id}"


def _parse_json_text(value: str | None) -> dict:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {"raw": value}
    return parsed if isinstance(parsed, dict) else {"data": parsed}


def _truncate(value: str, max_length: int) -> str:
    text = re.sub(r"\s+", " ", (value or "").strip())
    return text[:max_length]


def _apply_content_safety(value: str | None) -> str:
    text = value or ""
    for source, replacement in sorted(CONTENT_SAFETY_REPLACEMENTS, key=lambda item: len(item[0]), reverse=True):
        text = re.sub(re.escape(source), replacement, text, flags=re.IGNORECASE)
    for source in sorted(CONTENT_SAFETY_REMOVE_WORDS, key=len, reverse=True):
        text = re.sub(re.escape(source), "", text, flags=re.IGNORECASE)
    text = re.sub(r"[#＃]{2,}", "#", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" \t\r\n,，。.!！?？、；;:-_#＃")


def _sanitize_publish_title(value: str | None, fallback: str = "精彩片段") -> str:
    title = _apply_content_safety(value)
    title = re.sub(r"[#＃]+", "", title)
    title = _truncate(title, 80)
    return title or fallback


def _sanitize_publish_description(value: str | None, fallback: str = "") -> str:
    description = _apply_content_safety(value)
    return _truncate(description or fallback, 700)


def _sanitize_publish_content(
    title: str | None,
    tags: list[str] | str | None,
    description: str | None,
    title_fallback: str = "精彩片段",
    description_fallback: str = "",
) -> dict:
    safe_title = _sanitize_publish_title(title, title_fallback)
    safe_tags = _format_tags(tags) or _format_tags(SAFE_TOPIC_FALLBACKS)
    safe_description = _sanitize_publish_description(description, description_fallback)
    return {"title": safe_title, "tags": safe_tags, "description": safe_description}


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
    provider_payload = _parse_json_text(job.get("provider_response"))
    job.update(
        {
            "platform_label": PLATFORM_LABELS.get(job.get("platform"), job.get("platform")),
            "status_label": STATUS_LABELS.get(status, status),
            "status_tone": STATUS_TONES.get(status, "blue"),
            "video_source_label": VIDEO_SOURCE_LABELS.get(job.get("video_source"), job.get("video_source")),
            "publish_mode_label": PUBLISH_MODE_LABELS.get(job.get("publish_mode"), job.get("publish_mode")),
            "account_name": job.get("account_name") or "未选择账号",
            "cover_media_url": _cover_media_url(job.get("task_id") or "", job.get("cover_file_path")),
            "video_media_url": _video_media_url(
                job.get("task_id") or "",
                job.get("output_clip_id") or "",
                job.get("video_source") or "original",
            ),
            "provider_payload": provider_payload,
            "platform_url": provider_payload.get("url") or provider_payload.get("platform_url") or "",
            "trace_path": provider_payload.get("trace_path") or "",
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
    # 保存 state，10 分钟过期
    now = datetime.now()
    expires_at = (now + timedelta(minutes=10)).isoformat(timespec="seconds")
    with get_connection() as connection:
        connection.execute(
            "INSERT INTO oauth_states (state, platform, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (state, "douyin", now.isoformat(timespec="seconds"), expires_at),
        )
        connection.commit()
    return {"status": "ok", "url": url, "state": state}


def _validate_and_consume_oauth_state(state: str, platform: str) -> bool:
    """校验 OAuth state 参数，校验通过后删除记录。"""
    if not state:
        return False
    now = datetime.now().isoformat(timespec="seconds")
    with get_connection() as connection:
        row = connection.execute(
            "SELECT state, expires_at FROM oauth_states WHERE state = ? AND platform = ?",
            (state, platform),
        ).fetchone()
        if not row:
            return False
        if row["expires_at"] < now:
            # 过期 state，清理掉
            connection.execute("DELETE FROM oauth_states WHERE state = ?", (state,))
            connection.commit()
            return False
        # 校验通过，消费 state
        connection.execute("DELETE FROM oauth_states WHERE state = ?", (state,))
        connection.commit()
    return True


def save_douyin_oauth_account(code: str, state: str = "") -> dict:
    # 先校验 state
    if not _validate_and_consume_oauth_state(state, "douyin"):
        raise ValueError("OAuth state 无效或已过期，请重新发起授权")

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
            LEFT JOIN subtitle_jobs ON subtitle_jobs.output_clip_id = output_clip.id AND subtitle_jobs.is_active = 1
            WHERE output_clip.task_id = ? AND output_clip.id = ? AND output_clip.is_active = 1
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
            LEFT JOIN subtitle_jobs ON subtitle_jobs.output_clip_id = output_clip.id AND subtitle_jobs.is_active = 1
            WHERE output_clip.id = ? AND output_clip.is_active = 1
            """,
            (output_clip_id,),
        ).fetchone()
    return _row_to_dict(row)


def _list_completed_publish_clips() -> list[dict]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                tasks.id AS task_id,
                tasks.task_name,
                tasks.task_dir_name,
                output_clip.id AS output_clip_id,
                output_clip.output_file_path,
                output_clip.output_file_name,
                output_clip.status AS output_status,
                output_clip.created_at,
                clip_candidates.id AS clip_candidate_id,
                clip_candidates.title AS clip_title,
                clip_candidates.summary AS clip_summary,
                clip_candidates.highlight_reason,
                clip_candidates.spread_value,
                clip_candidates.suggested_editing,
                clip_candidates.start_time,
                clip_candidates.end_time,
                clip_candidates.duration_seconds,
                subtitle_jobs.status AS subtitle_status,
                subtitle_jobs.output_file_path AS subtitled_output_file_path
            FROM output_clip
            JOIN tasks ON tasks.id = output_clip.task_id
            LEFT JOIN clip_candidates ON clip_candidates.id = output_clip.clip_candidate_id
            LEFT JOIN subtitle_jobs ON subtitle_jobs.output_clip_id = output_clip.id AND subtitle_jobs.is_active = 1
            WHERE tasks.is_deleted = 0 AND output_clip.status = 'completed' AND output_clip.is_active = 1
            ORDER BY output_clip.created_at DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def _get_completed_publish_clip_by_output(output_clip_id: str) -> dict | None:
    for item in _list_completed_publish_clips():
        if item.get("output_clip_id") == output_clip_id:
            return item
    return None


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


def _default_title_for_clip(item: dict) -> str:
    output_name = item.get("output_file_name") or "直播切片"
    return _truncate(item.get("clip_title") or Path(output_name).stem or item.get("task_name") or "直播切片", 80)


def _keyword_candidates(text: str) -> list[str]:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff]+", " ", text or "")
    raw_parts = re.split(r"\s+", cleaned)
    keywords: list[str] = []
    stop_words = {"这个", "那个", "我们", "你们", "他们", "一个", "内容", "视频", "直播", "切片"}
    for part in raw_parts:
        word = part.strip("_")
        if not word or word in stop_words:
            continue
        if len(word) < 2:
            continue
        if word not in keywords:
            keywords.append(word[:12])
        if len(keywords) >= 8:
            break
    return keywords


def _fallback_tags(item: dict) -> list[str]:
    text = " ".join(
        str(item.get(key) or "")
        for key in ("clip_title", "clip_summary", "highlight_reason", "spread_value", "suggested_editing", "task_name")
    )
    tags = _keyword_candidates(text)
    for default_tag in ("直播切片", "高光片段"):
        if default_tag not in tags:
            tags.append(default_tag)
    return tags[:6]


def _format_tags(tags: list[str] | str | None) -> str:
    if isinstance(tags, str):
        hashtag_tags = re.findall(r"[#＃]\s*([^#＃,，\s]+)", tags)
        raw_tags = hashtag_tags if hashtag_tags else re.split(r"[,，#＃\s]+", tags)
    else:
        raw_tags = tags or []
    cleaned: list[str] = []
    for tag in raw_tags:
        value = _apply_content_safety(str(tag).strip().lstrip("#"))
        if re.search(r"(这是一段|这是|标题|简介|解释|适合|内容说明)", value):
            continue
        if len(value) > 12:
            value = re.sub(r"(这是一段|这是|关于|适合|标题|简介|解释|内容|视频|片段|话题)", "", value)
        value = re.sub(r"[^\w\u4e00-\u9fff]+", "", value)
        if len(value) > 10:
            continue
        if value and value not in cleaned:
            cleaned.append(value)
    return ", ".join(cleaned[:8])


def _compose_description(item: dict, title: str, tags: str) -> str:
    summary = (item.get("clip_summary") or item.get("highlight_reason") or "").strip()
    if summary:
        return _sanitize_publish_description(summary)
    tag_text = " ".join(f"#{tag.strip()}" for tag in tags.split(",") if tag.strip())
    return _sanitize_publish_description(f"{title}\n{tag_text}".strip())


def _metadata_prompt(item: dict) -> str:
    return (
        "请根据下面的直播切片信息，为抖音和B站发布生成标题和话题。"
        "只输出 JSON，不要 Markdown。"
        "tags 必须是真正的平台 #话题关键词，不是标题解释，每个话题 2 到 10 个字，返回时不要带 #。"
        "标题、话题、简介都要主动规避低俗脏话、排泄词、死亡血腥、暴力恐怖、色情、赌博博彩、诈骗引流、绝对化夸张等平台高风险表达；"
        "遇到类似表达请换成温和说法。JSON 格式："
        '{"title":"不超过30字的中文标题","tags":["话题1","话题2","话题3","话题4","话题5"],"description":"不超过180字的简介"}。'
        "\n\n"
        f"任务：{item.get('task_name') or ''}\n"
        f"原标题：{item.get('clip_title') or ''}\n"
        f"摘要：{item.get('clip_summary') or ''}\n"
        f"推荐理由：{item.get('highlight_reason') or ''}\n"
        f"传播价值：{item.get('spread_value') or ''}\n"
        f"剪辑建议：{item.get('suggested_editing') or ''}\n"
    )


def generate_publish_metadata(item: dict, use_ai: bool = False) -> dict:
    fallback_title = _sanitize_publish_title(_default_title_for_clip(item))
    fallback_tags = _format_tags(_fallback_tags(item)) or _format_tags(SAFE_TOPIC_FALLBACKS)
    fallback_description = _compose_description(item, fallback_title, fallback_tags)
    metadata = {
        "title": fallback_title,
        "tags": fallback_tags,
        "description": fallback_description,
        "source": "rule",
        "error": "",
    }
    if not use_ai:
        return metadata

    try:
        publish_model = settings.ai_publish_remote_model or "deepseek-v4-flash"
        provider = build_remote_provider(publish_model, purpose="publish")
        parsed = json.loads(provider.generate_json(_metadata_prompt(item)))
        safe_content = _sanitize_publish_content(
            parsed.get("title") or fallback_title,
            parsed.get("tags") or fallback_tags,
            parsed.get("description") or fallback_description,
            title_fallback=fallback_title,
            description_fallback=fallback_description,
        )
        return {
            "title": safe_content["title"],
            "tags": safe_content["tags"],
            "description": safe_content["description"],
            "source": f"ai:remote-publish:{publish_model}",
            "error": "",
        }
    except (AIProviderError, json.JSONDecodeError, TypeError, ValueError) as exc:
        metadata["error"] = str(exc)
        return metadata


def _find_opencli_job(output_clip_id: str, platform: str) -> dict | None:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT * FROM publish_jobs
            WHERE output_clip_id = ? AND platform = ? AND publish_mode = 'opencli_publish'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (output_clip_id, platform),
        ).fetchone()
    return _normalize_job(row) if row else None


def _batch_find_opencli_jobs(output_clip_ids: list[str]) -> dict[str, dict[str, dict]]:
    """一次查询获得所有 output_clip 在各平台的 opencli 发布任务。

    返回: {output_clip_id: {platform: normalized_job}}
    """
    if not output_clip_ids:
        return {}
    placeholders = ",".join("?" for _ in output_clip_ids)
    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT * FROM publish_jobs
            WHERE output_clip_id IN ({placeholders}) AND publish_mode = 'opencli_publish'
            ORDER BY created_at DESC
            """,
            output_clip_ids,
        ).fetchall()
    result: dict[str, dict[str, dict]] = {}
    for row in rows:
        job = _normalize_job(row)
        if job is None:
            continue
        oc_id = job["output_clip_id"]
        platform = job["platform"]
        if oc_id not in result:
            result[oc_id] = {}
        # 只保留每个 (output_clip_id, platform) 的第一条（按 created_at DESC）
        if platform not in result[oc_id]:
            result[oc_id][platform] = job
    return result


def _publish_provider_payload(metadata: dict, cover: dict | None = None) -> str:
    cover = cover or {}
    return json.dumps(
        {
            "metadata_source": metadata.get("source", ""),
            "metadata_error": metadata.get("error", ""),
            "cover_source": "auto_frame" if cover.get("cover_file_path") else "",
            "cover_error": cover.get("cover_error", ""),
        },
        ensure_ascii=False,
    )


def _insert_opencli_job(item: dict, platform: str, metadata: dict, cover: dict | None = None) -> dict:
    raw_video_path, _ = _resolve_publish_video_path(
        {
            **item,
            "output_status": item.get("output_status") or "completed",
            "subtitle_status": item.get("subtitle_status"),
            "subtitled_output_file_path": item.get("subtitled_output_file_path"),
        },
        "original",
    )
    job_id = uuid4().hex[:12]
    now = _now_iso()
    cover = cover or {}
    cover_file_path = str(cover.get("cover_file_path") or "")
    cover_mode = "time" if cover_file_path else "auto"
    cover_time_seconds = float(cover.get("cover_time_seconds") or 0)
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
            VALUES (?, ?, ?, '', ?, 'opencli_publish', 'original', ?, ?, ?, ?, 'public',
                ?, ?, 1, ?, 'original', '', ?, '', 'ready', 'not_submitted', ?, ?, ?)
            """,
            (
                job_id,
                item["task_id"],
                item["output_clip_id"],
                platform,
                raw_video_path,
                metadata["title"],
                metadata["description"],
                metadata["tags"],
                cover_mode,
                cover_time_seconds,
                DEFAULT_BILIBILI_TID,
                cover_file_path,
                _publish_provider_payload(metadata, cover),
                now,
                now,
            ),
        )
        connection.commit()
    return get_publish_job(job_id)


def refresh_send_queue(use_ai: bool = False) -> dict:
    created: list[dict] = []
    updated_covers = 0
    skipped = 0
    errors: list[str] = []
    for item in _list_completed_publish_clips():
        item_metadata: dict | None = None
        cover_state: dict[str, Any] = {"attempted": False, "cover": None}

        def ensure_cover_for_item() -> dict:
            if not cover_state["attempted"]:
                cover_state["attempted"] = True
                try:
                    cover_state["cover"] = _generate_default_publish_cover(item)
                except Exception as exc:
                    cover_state["cover"] = {"cover_error": str(exc)}
                    errors.append(f"{item.get('output_file_name') or item.get('output_clip_id')} / 自动封面：{exc}")
            return cover_state["cover"] or {}

        for platform in PLATFORM_LABELS:
            existing_job = _find_opencli_job(item["output_clip_id"], platform)
            if existing_job:
                skipped += 1
                if not existing_job.get("cover_file_path"):
                    cover = ensure_cover_for_item()
                    if cover.get("cover_file_path"):
                        _update_job_cover(existing_job["id"], cover)
                        updated_covers += 1
                continue
            try:
                if item_metadata is None:
                    item_metadata = generate_publish_metadata(item, use_ai=use_ai)
                created.append(_insert_opencli_job(item, platform, item_metadata, ensure_cover_for_item()))
            except Exception as exc:
                errors.append(f"{item.get('output_file_name') or item.get('output_clip_id')} / {PLATFORM_LABELS[platform]}：{exc}")
                if item_metadata is None:
                    item_metadata = {}
    return {
        "status": "ok" if not errors else "partial",
        "message": f"已新增 {len(created)} 条发送任务，自动选择 {len(created) + updated_covers} 张封面帧，跳过 {skipped} 条已存在任务，{len(errors)} 条需要处理。",
        "created": created,
        "errors": errors,
        **get_publish_center_context(),
    }


def _wrap_cover_title(title: str) -> str:
    text = re.sub(r"\s+", " ", _sanitize_publish_title(title or "精彩片段")) or "精彩片段"
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


def _plain_cover_filter() -> str:
    return ",".join(
        [
            f"scale={COVER_WIDTH}:{COVER_HEIGHT}:force_original_aspect_ratio=increase",
            f"crop={COVER_WIDTH}:{COVER_HEIGHT}",
            "format=yuv420p",
        ]
    )


def _get_video_duration_seconds(video_path: Path) -> float:
    ffprobe_path = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=settings.ffprobe_timeout,
    )
    if ffprobe_path.returncode != 0:
        return 0
    try:
        return max(0, float(ffprobe_path.stdout.strip()))
    except ValueError:
        return 0


def _cover_frame_times(duration: float, frame_count: int) -> list[float]:
    if duration <= 0:
        return [0]
    if frame_count <= 1:
        return [min(1.0, duration * 0.25)]
    step = duration / (frame_count + 1)
    return [max(0, min(duration - 0.1, step * index)) for index in range(1, frame_count + 1)]


def _default_cover_time_seconds(duration: float) -> float:
    if duration <= 1:
        return 0
    return max(0, min(duration - 0.1, max(1.0, duration * 0.25)))


def _unique_frame_cover_path(task_id: str, output_clip_id: str, video_source: str, seconds: float) -> Path:
    cover_dir = get_artifact_paths(task_id)["covers_dir"]
    cover_dir.mkdir(parents=True, exist_ok=True)
    ms = int(seconds * 1000)
    base_name = f"{output_clip_id}_{video_source}_frame_{ms}"
    output_path = cover_dir / f"{base_name}.jpg"
    if not output_path.exists():
        return output_path
    return cover_dir / f"{base_name}_{uuid4().hex[:6]}.jpg"


def _write_plain_cover_frame(video_path: Path, cover_path: Path, seconds: float) -> None:
    ffmpeg_path = ensure_ffmpeg_available()
    command = [
        ffmpeg_path,
        "-y",
        "-ss",
        f"{seconds:.3f}",
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-vf",
        _plain_cover_filter(),
        "-q:v",
        "2",
        str(cover_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                            timeout=settings.ffmpeg_cover_timeout)
    if result.returncode != 0:
        raise ValueError(f"封面帧生成失败：{summarize_stderr(result.stderr)}")
    if not cover_path.exists() or cover_path.stat().st_size == 0:
        raise ValueError("封面帧生成失败：FFmpeg 没有输出有效图片。")


def _cover_frame_payload(task_id: str, cover_path: Path, seconds: float) -> dict:
    return {
        "cover_file_path": str(cover_path),
        "cover_media_url": _cover_media_url(task_id, str(cover_path)),
        "cover_time_seconds": round(seconds, 3),
    }


def _generate_default_publish_cover(item: dict, video_source: str = "original") -> dict:
    _, video_path = _resolve_publish_video_path(
        {
            **item,
            "output_status": item.get("output_status") or "completed",
            "subtitle_status": item.get("subtitle_status"),
            "subtitled_output_file_path": item.get("subtitled_output_file_path"),
        },
        video_source,
    )
    duration = _get_video_duration_seconds(video_path)
    seconds = _default_cover_time_seconds(duration)
    cover_path = _unique_frame_cover_path(item["task_id"], item["output_clip_id"], video_source, seconds)
    _write_plain_cover_frame(video_path, cover_path, seconds)
    return _cover_frame_payload(item["task_id"], cover_path, seconds)


def _update_job_cover(job_id: str, cover: dict) -> None:
    if not cover.get("cover_file_path"):
        return
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE publish_jobs
            SET cover_mode = 'time', cover_time_seconds = ?, cover_file_path = ?, updated_at = ?
            WHERE id = ?
            """,
            (float(cover.get("cover_time_seconds") or 0), str(cover.get("cover_file_path") or ""), _now_iso(), job_id),
        )
        connection.commit()


def generate_publish_cover_frames(payload: PublishCoverFrameBatchCreate) -> dict:
    output_clip = _get_output_clip_for_publish(payload.task_id, payload.output_clip_id)
    if not output_clip:
        raise ValueError("切片记录不存在。")
    _, video_path = _resolve_publish_video_path(output_clip, payload.video_source)
    duration = _get_video_duration_seconds(video_path)
    frames = []
    for seconds in _cover_frame_times(duration, payload.frame_count):
        cover_path = _unique_frame_cover_path(payload.task_id, payload.output_clip_id, payload.video_source, seconds)
        _write_plain_cover_frame(video_path, cover_path, seconds)
        frames.append(_cover_frame_payload(payload.task_id, cover_path, seconds))
    if not frames:
        raise ValueError("封面帧生成失败：没有得到有效图片。")
    return {"status": "ok", "message": f"已生成 {len(frames)} 张候选封面。", "frames": frames}


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
        _plain_cover_filter(),
        "-q:v",
        "2",
        str(cover_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                            timeout=settings.ffmpeg_cover_timeout)
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
    safe_content = _sanitize_publish_content(payload.title, payload.tags, payload.description, title_fallback="精彩片段")
    cover_file_path = (payload.cover_file_path or "").strip()
    cover_time_seconds = float(payload.cover_time_seconds or 0)
    cover_mode = payload.cover_mode
    provider_payload = "真实发布任务已创建，等待执行。" if payload.publish_mode == "api_publish" else "本地发布任务已创建，等待人工确认。"
    if not cover_file_path:
        try:
            auto_cover = _generate_default_publish_cover(
                {
                    **output_clip,
                    "task_id": payload.task_id,
                    "output_clip_id": payload.output_clip_id,
                    "output_status": output_clip.get("output_status") or "completed",
                },
                payload.video_source,
            )
            cover_file_path = str(auto_cover.get("cover_file_path") or "")
            cover_time_seconds = float(auto_cover.get("cover_time_seconds") or 0)
            cover_mode = "time" if cover_file_path else cover_mode
            provider_payload = json.dumps({"cover_source": "auto_frame"}, ensure_ascii=False)
        except Exception as exc:
            provider_payload = json.dumps({"cover_error": str(exc)}, ensure_ascii=False)

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
                safe_content["title"],
                safe_content["description"],
                safe_content["tags"],
                payload.visibility,
                cover_mode,
                cover_time_seconds,
                1 if payload.allow_download else 0,
                (payload.bilibili_tid or "").strip(),
                payload.bilibili_copyright,
                (payload.bilibili_source or "").strip(),
                cover_file_path,
                (payload.scheduled_at or "").strip(),
                status,
                "not_submitted",
                provider_payload,
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


def update_send_job(job_id: str, payload: PublishSendJobUpdate) -> dict:
    job = get_publish_job(job_id)
    if not job:
        raise ValueError("发送任务不存在。")
    if job.get("publish_mode") != "opencli_publish":
        raise ValueError("只能编辑 opencli 发送任务。")
    safe_content = _sanitize_publish_content(
        payload.title,
        payload.tags,
        payload.description,
        title_fallback=job.get("title") or "精彩片段",
        description_fallback=job.get("description") or "",
    )
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE publish_jobs
            SET title = ?, description = ?, tags = ?, visibility = ?,
                cover_file_path = ?, cover_time_seconds = ?, allow_download = ?,
                bilibili_tid = ?, bilibili_copyright = ?, bilibili_source = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                safe_content["title"],
                safe_content["description"],
                safe_content["tags"],
                payload.visibility,
                (payload.cover_file_path or "").strip(),
                float(payload.cover_time_seconds or 0),
                1 if payload.allow_download else 0,
                (payload.bilibili_tid or DEFAULT_BILIBILI_TID).strip(),
                payload.bilibili_copyright,
                (payload.bilibili_source or "").strip(),
                _now_iso(),
                job_id,
            ),
        )
        connection.commit()
    return {"status": "ok", "message": "发送内容已保存。", "job": get_publish_job(job_id)}


def regenerate_send_job_metadata(job_id: str, use_ai: bool = True) -> dict:
    job = get_publish_job(job_id)
    if not job:
        raise ValueError("发送任务不存在。")
    item = _get_completed_publish_clip_by_output(job["output_clip_id"])
    if not item:
        raise ValueError("找不到这条发送任务对应的切片。")
    metadata = generate_publish_metadata(item, use_ai=use_ai)
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE publish_jobs
            SET title = ?, description = ?, tags = ?, provider_response = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                metadata["title"],
                metadata["description"],
                metadata["tags"],
                _publish_provider_payload(
                    metadata,
                    {
                        "cover_file_path": job.get("cover_file_path") or "",
                        "cover_error": (job.get("provider_payload") or {}).get("cover_error", ""),
                    },
                ),
                _now_iso(),
                job_id,
            ),
        )
        connection.commit()
    return {
        "status": "ok",
        "message": "AI 元数据已刷新。" if metadata["source"].startswith("ai:") else "已使用本地规则刷新标题和话题。",
        "metadata": metadata,
        "job": get_publish_job(job_id),
    }


def _default_command_runner(command: list[str]) -> subprocess.CompletedProcess:
    if not _opencli_executable() and _opencli_bridge_url():
        return _opencli_bridge_command_runner(command)
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=OPENCLI_TIMEOUT_SECONDS,
    )


def _should_retry_opencli_detached(command: list[str], result: subprocess.CompletedProcess, attempt: int) -> bool:
    if attempt >= 3:
        return False
    output_text = f"{result.stdout or ''}\n{result.stderr or ''}"
    if "Detached while handling command" not in output_text:
        return False
    script = str(command[-1]) if command else ""
    return "douyin_cover_retryable:true" in script


def _hashtags(tags: str) -> str:
    normalized_tags = _format_tags(tags)
    return " ".join(f"#{tag.strip().lstrip('#')}" for tag in re.split(r"[,，]+", normalized_tags or "") if tag.strip())


def _douyin_description_for_job(job: dict, fallback_title: str) -> str:
    description = _sanitize_publish_description(job.get("description") or fallback_title, fallback_title)
    tag_text = _hashtags(job.get("tags") or "")
    parts = [description] if description else []
    if tag_text:
        parts.append(tag_text)
    return "\n".join(parts).strip()[:1000]


def _caption_for_job(job: dict) -> str:
    parts = []
    description = _sanitize_publish_description(job.get("description") or "")
    if description:
        parts.append(description)
    tag_text = _hashtags(job.get("tags") or "")
    if tag_text:
        parts.append(tag_text)
    return "\n".join(parts).strip()[:1000]


def _job_video_path(job: dict) -> Path:
    raw_path = (job.get("video_file_path") or "").strip()
    path = resolve_video_file_path(raw_path) or Path(raw_path)
    if not path.exists() or not path.is_file():
        raise ValueError(f"待发送视频文件不存在：{raw_path}")
    return path


def _job_cover_path(job: dict) -> Path | None:
    raw_path = (job.get("cover_file_path") or "").strip()
    if not raw_path:
        return None
    path = Path(raw_path)
    if not path.exists() or not path.is_file():
        return None
    return path


def _browser_open_command(session: str, url: str) -> list[str]:
    return [*_opencli_command(), "browser", session, "--window", "foreground", "open", url]


def _browser_wait_command(session: str, seconds: int) -> list[str]:
    return [*_opencli_command(), "browser", session, "wait", "time", str(seconds)]


def _browser_eval_command(session: str, script: str) -> list[str]:
    return [*_opencli_command(), "browser", session, "eval", script]


def _browser_close_command(session: str) -> list[str]:
    return [*_opencli_command(), "browser", session, "close"]


_TITLE_FIELD_SELECTOR = ",".join(
    [
        "input[placeholder*='标题']",
        "textarea[placeholder*='标题']",
        "[contenteditable='true'][aria-label*='标题']",
        "[contenteditable='true'][data-placeholder*='标题']",
    ]
)


_BILIBILI_DESCRIPTION_FIELD_SELECTOR = ",".join(
    [
        "textarea[placeholder*='简介']",
        "textarea[placeholder*='介绍']",
        "textarea",
    ]
)


def _fill_visible_field_script(selector: str, value: str, field_name: str) -> str:
    return (
        "(()=>{"
        f"const selector={json.dumps(selector, ensure_ascii=False)};"
        f"const value={json.dumps(value, ensure_ascii=False)};"
        f"const fieldName={json.dumps(field_name, ensure_ascii=False)};"
        f"const missingError={json.dumps(field_name + '_field_not_found', ensure_ascii=False)};"
        "const all=[...document.querySelectorAll(selector)];"
        "const usable=(el)=>!el.disabled&&!el.readOnly;"
        "const visible=(el)=>{"
        "const style=window.getComputedStyle(el);"
        "const rect=el.getBoundingClientRect();"
        "return usable(el)&&style.display!=='none'&&style.visibility!=='hidden'&&style.opacity!=='0'&&rect.width>0&&rect.height>0;"
        "};"
        "const el=all.find(visible)||all.find(usable);"
        "if(!el){throw new Error(missingError);}"
        "el.scrollIntoView({block:'center',inline:'center'});"
        "el.focus();"
        "if(el.isContentEditable){"
        "const selection=window.getSelection();"
        "const range=document.createRange();"
        "range.selectNodeContents(el);"
        "selection.removeAllRanges();"
        "selection.addRange(range);"
        "const inserted=document.execCommand?.('insertText',false,value);"
        "const actual=(el.textContent||'').replace(/[\\u200B-\\u200D\\uFEFF]/g,'');"
        "if(!inserted||actual!==value){el.textContent=value;}"
        "}"
        "else{"
        "const proto=el instanceof HTMLTextAreaElement?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;"
        "const setter=Object.getOwnPropertyDescriptor(proto,'value')?.set;"
        "if(setter){setter.call(el,value);}else{el.value=value;}"
        "}"
        "el.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertText',data:value}));"
        "el.dispatchEvent(new Event('change',{bubbles:true}));"
        "return {filled:true,fieldName,tagName:el.tagName,placeholder:el.getAttribute('placeholder')||'',value:el.isContentEditable?el.textContent:el.value};"
        "})()"
    )


def _browser_fill_title_command(session: str, title: str) -> list[str]:
    return _browser_eval_command(session, _fill_visible_field_script(_TITLE_FIELD_SELECTOR, title, "title"))


def _douyin_set_description_script(description: str) -> str:
    return (
        "(()=>{"
        f"const value={json.dumps(description, ensure_ascii=False)};"
        "const normalize=(text)=>String(text||'').replace(/[\\u200B-\\u200D\\uFEFF\\u00A0]/g,'').replace(/\\s+/g,' ').trim();"
        "const visible=(el)=>{const style=window.getComputedStyle(el);const rect=el.getBoundingClientRect();return !el.disabled&&!el.readOnly&&style.display!=='none'&&style.visibility!=='hidden'&&style.opacity!=='0'&&rect.width>0&&rect.height>0;};"
        "const textOf=(el)=>(el.textContent||'').replace(/\\s+/g,'').trim();"
        "const fieldScore=(el)=>{const rect=el.getBoundingClientRect();let score=rect.height>70?20:0;const attrs=[el.getAttribute('placeholder'),el.getAttribute('aria-label'),el.getAttribute('data-placeholder')].filter(Boolean).join('');if(attrs.includes('作品描述')||attrs.includes('简介')||attrs.includes('描述')){score+=120;}if(attrs.includes('标题')){score-=120;}let node=el;for(let i=0;i<7&&node;i+=1){const context=textOf(node);if(context.includes('作品描述')){score+=100-i*5;}if(context.includes('简介')||context.includes('描述')){score+=35-i*3;}if(context.includes('作品标题')||context.includes('标题')){score-=80-i*3;}if(context.includes('添加话题')||context.includes('@好友')){score-=20;}node=node.parentElement;}return score;};"
        "const candidates=[...document.querySelectorAll('div[contenteditable=\"true\"],textarea')].filter(visible).map((el)=>({el,score:fieldScore(el)})).sort((a,b)=>b.score-a.score);"
        "const editor=candidates[0]?.el;"
        "if(!editor){throw new Error('douyin_description_editor_not_found');}"
        "const before=normalize(editor.innerText||editor.textContent||'');"
        "const expected=normalize(value);"
        "const beforeCount=expected?before.split(expected).length-1:0;"
        "editor.scrollIntoView({block:'center',inline:'center'});"
        "editor.focus();"
        "if(editor.tagName==='TEXTAREA'){const setter=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value')?.set;setter?setter.call(editor,value):(editor.value=value);}else{const selection=window.getSelection();const range=document.createRange();range.selectNodeContents(editor);selection.removeAllRanges();selection.addRange(range);document.execCommand?.('delete',false,null);editor.innerHTML='';const lines=String(value||'').split('\\n');lines.forEach((line,index)=>{if(index){editor.appendChild(document.createElement('br'));}editor.appendChild(document.createTextNode(line));});}"
        "editor.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertText',data:value||''}));"
        "editor.dispatchEvent(new Event('change',{bubbles:true}));"
        "editor.dispatchEvent(new Event('blur',{bubbles:true}));"
        "const actual=normalize(editor.tagName==='TEXTAREA'?editor.value:(editor.innerText||editor.textContent||''));"
        "if(actual!==expected){throw new Error('douyin_description_set_failed');}"
        "const actualCount=expected?actual.split(expected).length-1:0;"
        "if(actualCount>1){throw new Error('douyin_description_duplicated');}"
        "return {description_set:true,plain_hashtags_removed:true,duplicate_removed:beforeCount>1||before!==actual,editor_score:candidates[0]?.score||0,actual};"
        "})()"
    )


def _douyin_cover_helpers_script() -> str:
    return (
        "const retryMarker='douyin_cover_retryable:true';"
        "const sleep=(ms)=>new Promise((resolve)=>setTimeout(resolve,ms));"
        "const visible=(el)=>{const style=window.getComputedStyle(el);const rect=el.getBoundingClientRect();return !el.disabled&&el.getAttribute('aria-disabled')!=='true'&&style.display!=='none'&&style.visibility!=='hidden'&&style.opacity!=='0'&&rect.width>0&&rect.height>0;};"
        "const textOf=(el)=>(el.textContent||'').replace(/\\s+/g,'').trim();"
        "const labels=['AI智能推荐封面','智能推荐封面','推荐封面'];"
        "const successTexts=['封面效果检测通过','封面检测通过','检测通过'];"
        "const successDetected=()=>successTexts.some((item)=>textOf(document.body).includes(item));"
        "const hover=(el)=>{['mouseover','mouseenter','mousemove'].forEach((type)=>el.dispatchEvent(new MouseEvent(type,{bubbles:true,view:window})));};"
        "const labelNodes=()=>[...document.querySelectorAll('button,div,span,section')].filter((el)=>{if(!visible(el)){return false;}const rect=el.getBoundingClientRect();const text=textOf(el);return labels.some((label)=>text.includes(label))&&text.length<=80&&rect.width>0&&rect.height>0&&rect.width<720&&rect.height<220;});"
        "const nearLabel=(img,labelRect)=>{const rect=img.getBoundingClientRect();return rect.left>=labelRect.left-30&&rect.top>=labelRect.top-20&&rect.top<=labelRect.top+300;};"
        "const candidateSections=()=>{const entries=[];for(const label of labelNodes()){let best=null;let node=label;const labelRect=label.getBoundingClientRect();for(let i=0;i<9&&node;i+=1){const rect=node.getBoundingClientRect();const imgs=[...node.querySelectorAll('img')].filter((img)=>visible(img)&&nearLabel(img,labelRect));if(imgs.length&&rect.width>0&&rect.height>0){const area=rect.width*rect.height;if(!best||area<best.area){best={section:node,label,area};}}node=node.parentElement;}if(best){entries.push(best);}}const seen=new Set();return entries.filter((item)=>{if(seen.has(item.section)){return false;}seen.add(item.section);return true;});};"
        "const badImage=(src)=>/logo|avatar|favicon|icon|douyin-creator-logo|static\\/image/i.test(src||'');"
        "const realImages=()=>candidateSections().flatMap((entry)=>[...entry.section.querySelectorAll('img')].filter((img)=>{const rect=img.getBoundingClientRect();const src=img.currentSrc||img.src||img.getAttribute('src')||'';const owner=img.closest('button,[role=\"button\"],[class*=\"recommendCover\"],[class*=\"cover\"],div')||img;const context=textOf(owner);const ratio=rect.width/Math.max(rect.height,1);return visible(img)&&nearLabel(img,entry.label.getBoundingClientRect())&&src&&!badImage(src)&&img.complete!==false&&img.naturalWidth>60&&img.naturalHeight>60&&rect.width>=48&&rect.height>=48&&ratio>0.55&&ratio<2.4&&rect.top>80&&!context.includes('暂无更多推荐')&&!context.includes('生成中');}).map((img)=>({img,rect:img.getBoundingClientRect()}))).sort((a,b)=>a.rect.left-b.rect.left||a.rect.top-b.rect.top);"
        "const clickText=async(names,timeoutMs=12000)=>{const started=Date.now();let sawDialog=false;while(Date.now()-started<timeoutMs){const bodyText=textOf(document.body);sawDialog=sawDialog||bodyText.includes('是否确认应用此封面')||bodyText.includes('确认应用此封面');const candidates=[...document.querySelectorAll('button,[role=\"button\"],div,span')].filter(visible).map((el)=>({el,text:textOf(el)})).filter((item)=>names.some((name)=>item.text===name||item.text.includes(name))).sort((a,b)=>a.text.length-b.text.length);const target=candidates[0]?.el;if(target){target.scrollIntoView({block:'center',inline:'center'});target.click();await sleep(900);return {clicked:true,text:textOf(target),saw_dialog:sawDialog};}if(successDetected()){return {clicked:false,success_detected:true,saw_dialog:sawDialog};}await sleep(350);}return {clicked:false,saw_dialog:sawDialog};};"
    )


def _douyin_wait_ai_cover_script(timeout_seconds: int = 150) -> str:
    return (
        "(async()=>{"
        f"const timeoutMs={int(timeout_seconds) * 1000};"
        f"{_douyin_cover_helpers_script()}"
        "const started=Date.now();let lastCount=0;while(Date.now()-started<timeoutMs){const images=realImages();lastCount=images.length;if(images.length){images[0].img.scrollIntoView({block:'center',inline:'center'});return {ai_cover_ready:true,leftmost_ai_cover_found:true,image_count:images.length,waited_ms:Date.now()-started,retryMarker};}await sleep(1000);}"
        "throw new Error('douyin_ai_cover_not_ready:'+lastCount);"
        "})()"
    )


def _douyin_click_ai_cover_script() -> str:
    return (
        "(async()=>{"
        f"{_douyin_cover_helpers_script()}"
        "const images=realImages();"
        "if(!images.length){throw new Error('douyin_ai_cover_not_ready:0');}"
        "const beforeUrl=location.href;"
        "const img=images[0].img;"
        "const clickable=img.closest('button,[role=\"button\"],[class*=\"recommendCover\"],[class*=\"cover\"],div')||img;"
        "clickable.scrollIntoView({block:'center',inline:'center'});hover(clickable);hover(img);await sleep(350);clickable.click();img.click();"
        "await sleep(900);"
        "if(location.href!==beforeUrl&&/content\\/manage|manage/.test(location.href)){throw new Error('douyin_cover_click_navigated:'+location.href);}"
        "return {ai_cover_clicked:true,leftmost_ai_cover_selected:true,src:img.currentSrc||img.src||img.getAttribute('src')||'',retryMarker};"
        "})()"
    )


def _douyin_confirm_cover_script(timeout_seconds: int = 20) -> str:
    return (
        "(async()=>{"
        f"const timeoutMs={int(timeout_seconds) * 1000};"
        f"{_douyin_cover_helpers_script()}"
        "const confirmTexts=['设为封面','设置为封面','使用封面','确认使用','确定','确认','应用'];"
        "const result=await clickText(confirmTexts,timeoutMs);"
        "if(result.clicked||result.success_detected){return {cover_confirm_clicked:result.clicked,cover_confirmed:true,cover_confirm_text:result.text||'',cover_dialog_seen:result.saw_dialog,cover_success_detected:successDetected(),retryMarker};}"
        "if(result.saw_dialog){throw new Error('douyin_cover_confirm_dialog_not_clicked');}"
        "throw new Error('douyin_cover_confirm_not_found');"
        "})()"
    )


def _douyin_verify_cover_applied_script(timeout_seconds: int = 45) -> str:
    return (
        "(async()=>{"
        f"const timeoutMs={int(timeout_seconds) * 1000};"
        f"{_douyin_cover_helpers_script()}"
        "const started=Date.now();let lastText='';while(Date.now()-started<timeoutMs){lastText=textOf(document.body).slice(-300);if(successDetected()){return {cover_applied:true,cover_success_detected:true,waited_ms:Date.now()-started,retryMarker};}const selected=[...document.querySelectorAll('button,[role=\"button\"],[class*=\"cover\"],div')].filter(visible).find((el)=>/selected|active|checked|current/i.test((el.className||'').toString())||textOf(el).includes('已选择')||textOf(el).includes('已选'));if(selected){return {cover_applied:true,selected_state:true,waited_ms:Date.now()-started,retryMarker};}await sleep(800);}"
        "throw new Error('douyin_cover_not_applied:'+lastText);"
        "})()"
    )


def _douyin_verify_publish_ready_script(title: str, description: str) -> str:
    return (
        "(()=>{"
        f"const expectedTitle=String({json.dumps(_truncate(title, 30), ensure_ascii=False)}||'');"
        f"const expectedDescription=String({json.dumps(description, ensure_ascii=False)}||'');"
        "const normalize=(text)=>String(text||'').replace(/[\\u200B-\\u200D\\uFEFF\\u00A0]/g,'').replace(/\\s+/g,' ').trim();"
        "const compact=(text)=>normalize(text).replace(/\\s/g,'');"
        "const visible=(el)=>{const style=window.getComputedStyle(el);const rect=el.getBoundingClientRect();return !el.disabled&&!el.readOnly&&style.display!=='none'&&style.visibility!=='hidden'&&style.opacity!=='0'&&rect.width>0&&rect.height>0;};"
        "const textOf=(el)=>(el.textContent||'').replace(/\\s+/g,'').trim();"
        "const titleValue=()=>{const fields=[...document.querySelectorAll('input,textarea,[contenteditable=\"true\"]')].filter(visible);for(const el of fields){const attrs=[el.getAttribute('placeholder'),el.getAttribute('aria-label'),el.getAttribute('data-placeholder')].filter(Boolean).join('');const value=el.isContentEditable?el.textContent:el.value;if(attrs.includes('标题')&&normalize(value)){return normalize(value);}}return '';};"
        "const descriptionFields=[...document.querySelectorAll('div[contenteditable=\"true\"],textarea')].filter(visible).map((el)=>{let score=0;const attrs=[el.getAttribute('placeholder'),el.getAttribute('aria-label'),el.getAttribute('data-placeholder')].filter(Boolean).join('');if(attrs.includes('作品描述')||attrs.includes('简介')||attrs.includes('描述')){score+=120;}if(attrs.includes('标题')){score-=120;}let node=el;for(let i=0;i<7&&node;i+=1){const context=textOf(node);if(context.includes('作品描述')){score+=100-i*5;}if(context.includes('简介')||context.includes('描述')){score+=35-i*3;}if(context.includes('作品标题')||context.includes('标题')){score-=80-i*3;}node=node.parentElement;}return {el,score};}).sort((a,b)=>b.score-a.score);"
        "const editor=descriptionFields[0]?.el;"
        "const actualDescription=normalize(editor?(editor.tagName==='TEXTAREA'?editor.value:(editor.innerText||editor.textContent||'')):'');"
        "const expectedCompact=compact(expectedDescription);"
        "const actualCompact=compact(actualDescription);"
        "const bodyPiece=compact(expectedDescription.split('\\n')[0]||'').slice(0,16);"
        "if(expectedCompact&&(!actualCompact||(!actualCompact.includes(expectedCompact)&&!(bodyPiece&&actualCompact.includes(bodyPiece))))){throw new Error('douyin_description_missing_after_set');}"
        "const titleActual=titleValue();"
        "if(compact(expectedTitle)&&!compact(titleActual).includes(compact(expectedTitle).slice(0,12))){throw new Error('douyin_title_missing_after_set');}"
        "const previewLabels=['预览视频','预览封面/标题','预览封面','平台投稿预览'];"
        "const previewRoots=[...document.querySelectorAll('section,aside,div')].filter((el)=>visible(el)&&previewLabels.some((label)=>textOf(el).includes(label)));"
        "const previewReady=previewRoots.some((root)=>[...root.querySelectorAll('img,video,canvas')].some(visible));"
        "if(!previewReady){throw new Error('douyin_preview_not_ready');}"
        "return {publish_ready:true,title_checked:true,description_checked:true,preview_checked:true,description_length:actualDescription.length,preview_roots:previewRoots.length};"
        "})()"
    )


def _douyin_select_ai_cover_script(timeout_seconds: int = 150) -> str:
    return (
        "(async()=>{"
        f"const timeoutMs={int(timeout_seconds) * 1000};"
        f"{_douyin_cover_helpers_script()}"
        "const started=Date.now();let lastSeen=null;while(Date.now()-started<timeoutMs){const images=realImages();if(images.length){const img=images[0].img;const clickable=img.closest('button,[role=\"button\"],[class*=\"recommendCover\"],[class*=\"cover\"],div')||img;lastSeen={selected:true,src:img.currentSrc||img.src||img.getAttribute('src'),waited_ms:Date.now()-started};clickable.scrollIntoView({block:'center',inline:'center'});hover(clickable);hover(img);await sleep(300);clickable.click();img.click();await sleep(800);const confirm=await clickText(['设为封面','设置为封面','使用封面','确认使用','确定','确认','应用']);const success=successDetected();if(confirm.clicked||success){return {ai_cover_selected:true,leftmost_ai_cover_selected:true,cover_confirmed:true,cover_success_detected:success,cover_wait_timeout_ms:timeoutMs,selected:lastSeen,confirmed:confirm,retryMarker};}}await sleep(1000);}if(lastSeen){throw new Error('douyin_cover_confirm_not_found');}throw new Error('douyin_ai_cover_not_ready');"
        "})()"
    )


def _douyin_click_publish_script() -> str:
    return (
        "(async()=>{"
        "const sleep=(ms)=>new Promise((resolve)=>setTimeout(resolve,ms));"
        "const visible=(el)=>{const style=window.getComputedStyle(el);const rect=el.getBoundingClientRect();return !el.disabled&&el.getAttribute('aria-disabled')!=='true'&&style.display!=='none'&&style.visibility!=='hidden'&&style.opacity!=='0'&&rect.width>0&&rect.height>0;};"
        "const textOf=(el)=>(el.textContent||'').replace(/\\s+/g,'').trim();"
        "const names=['发布','立即发布','确认发布','发布作品'];"
        "const blocked=['高清发布','发布助手','发布设置','发布记录','发文助手'];"
        "const isMatch=(text)=>names.some((name)=>text===name||(text.includes(name)&&text.length<=12))&&!blocked.some((name)=>text.includes(name));"
        "const clickKnownTip=()=>{const tip=[...document.querySelectorAll('button,[role=\"button\"],div,span')].filter(visible).find((el)=>['我知道了','知道了'].includes(textOf(el)));if(tip){tip.click();return true;}return false;};"
        "const findButton=()=>{const seen=new Set();const candidates=[];for(const el of [...document.querySelectorAll('button,[role=\"button\"],a,div,span')]){const text=textOf(el);if(!text||!isMatch(text)){continue;}const clickable=el.closest('button,[role=\"button\"],a')||el;if(seen.has(clickable)||!visible(clickable)){continue;}seen.add(clickable);const rect=clickable.getBoundingClientRect();if(rect.left<180&&text.includes('发布')){continue;}const exact=text==='发布'?0:1;const tag=clickable.tagName==='BUTTON'?0:1;candidates.push({el:clickable,text,rect,score:exact*10+tag});}return candidates.sort((a,b)=>a.score-b.score||b.rect.top-a.rect.top||b.rect.left-a.rect.left)[0];};"
        "const started=Date.now();let lastTexts=[];while(Date.now()-started<45000){clickKnownTip();let found=findButton();if(found){found.el.scrollIntoView({block:'center',inline:'center'});await sleep(500);found=findButton()||found;setTimeout(()=>found.el.click(),50);return {click_scheduled:true,text:found.text,waited_ms:Date.now()-started};}lastTexts=[...document.querySelectorAll('button,[role=\"button\"],a')].filter(visible).map(textOf).filter(Boolean).slice(-20);window.scrollTo({top:document.documentElement.scrollHeight||document.body.scrollHeight,behavior:'instant'});await sleep(1000);}"
        "throw new Error('douyin_publish_button_not_found:'+lastTexts.join('|'));"
        "})()"
    )


def _douyin_wait_publish_result_script(title: str, timeout_seconds: int = 120) -> str:
    return (
        "(async()=>{"
        f"const expectedTitle=String({json.dumps(_truncate(title, 30), ensure_ascii=False)}||'').replace(/\\s+/g,'').trim();"
        f"const timeoutMs={int(timeout_seconds) * 1000};"
        "const sleep=(ms)=>new Promise((resolve)=>setTimeout(resolve,ms));"
        "const visible=(el)=>{const style=window.getComputedStyle(el);const rect=el.getBoundingClientRect();return !el.disabled&&el.getAttribute('aria-disabled')!=='true'&&style.display!=='none'&&style.visibility!=='hidden'&&style.opacity!=='0'&&rect.width>0&&rect.height>0;};"
        "const textOf=(el)=>(el.textContent||'').replace(/\\s+/g,'').trim();"
        "const pageText=()=>textOf(document.body);"
        "const successTexts=['发布成功','作品发布成功','发布完成','提交成功','已提交审核','审核中','投稿成功','发布已提交'];"
        "const blockedTexts=['验证码','安全验证','登录失效','请先登录','未登录','发布失败','提交失败','内容违规','无法发布','风控','频繁'];"
        "const confirmTexts=['确认发布','立即发布','继续发布','仍要发布','同意并发布','确认','确定'];"
        "const hasSuccess=()=>successTexts.find((item)=>pageText().includes(item));"
        "const hasBlock=()=>blockedTexts.find((item)=>pageText().includes(item));"
        "const clickConfirm=()=>{const candidates=[...document.querySelectorAll('button,[role=\"button\"],div,span')].filter(visible).map((el)=>({el,text:textOf(el)})).filter((item)=>confirmTexts.some((name)=>item.text===name||item.text.includes(name))).sort((a,b)=>a.text.length-b.text.length);const target=candidates[0]?.el;if(target){target.scrollIntoView({block:'center',inline:'center'});target.click();return {clicked:true,text:textOf(target)};}return {clicked:false};};"
        "const titleVisible=()=>{const text=pageText();return expectedTitle&&expectedTitle.length>=4&&text.includes(expectedTitle)&&!/共0个作品|共0件作品/.test(text);};"
        "const started=Date.now();let confirms=[];let last='';while(Date.now()-started<timeoutMs){const success=hasSuccess();if(success){return {publish_confirmed:true,success_text:success,url:location.href,waited_ms:Date.now()-started,confirms};}if(titleVisible()&&/content\\/manage|manage/.test(location.href)){return {publish_confirmed:true,success_text:'作品管理出现标题',url:location.href,waited_ms:Date.now()-started,confirms};}const blocked=hasBlock();if(blocked){throw new Error('douyin_publish_blocked:'+blocked);}const confirm=clickConfirm();if(confirm.clicked){confirms.push(confirm);await sleep(2000);continue;}last=[...document.querySelectorAll('button,[role=\"button\"],a')].filter(visible).map(textOf).filter(Boolean).slice(-20).join('|');await sleep(1500);}"
        "throw new Error('douyin_publish_not_confirmed:'+location.href+'|'+last);"
        "})()"
    )


def _browser_set_douyin_description_command(session: str, description: str) -> list[str]:
    return _browser_eval_command(session, _douyin_set_description_script(description))


def _browser_select_douyin_ai_cover_command(session: str) -> list[str]:
    return _browser_eval_command(session, _douyin_select_ai_cover_script())


def _browser_wait_douyin_ai_cover_command(session: str) -> list[str]:
    return _browser_eval_command(session, _douyin_wait_ai_cover_script())


def _browser_click_douyin_ai_cover_command(session: str) -> list[str]:
    return _browser_eval_command(session, _douyin_click_ai_cover_script())


def _browser_confirm_douyin_cover_command(session: str) -> list[str]:
    return _browser_eval_command(session, _douyin_confirm_cover_script())


def _browser_verify_douyin_cover_command(session: str) -> list[str]:
    return _browser_eval_command(session, _douyin_verify_cover_applied_script())


def _browser_verify_douyin_publish_ready_command(session: str, title: str, description: str) -> list[str]:
    return _browser_eval_command(session, _douyin_verify_publish_ready_script(title, description))


def _browser_click_douyin_publish_command(session: str) -> list[str]:
    return _browser_eval_command(session, _douyin_click_publish_script())


def _browser_wait_douyin_publish_result_command(session: str, title: str) -> list[str]:
    return _browser_eval_command(session, _douyin_wait_publish_result_script(title))


def _browser_fill_bilibili_description_command(session: str, description: str) -> list[str]:
    return _browser_eval_command(
        session,
        _fill_visible_field_script(_BILIBILI_DESCRIPTION_FIELD_SELECTOR, description, "description"),
    )


def _local_media_url(job: dict) -> str:
    base_url = settings.opencli_local_base_url.rstrip("/")
    return f"{base_url}{_video_media_url(job['task_id'], job['output_clip_id'], job.get('video_source') or 'original')}"


def _douyin_video_upload_script(job: dict, video_path: Path) -> str:
    media_url = _local_media_url(job)
    file_name = video_path.name
    return (
        "(async()=>{"
        "const input=document.querySelector('input[type=\"file\"]');"
        "if(!input){throw new Error('douyin_file_input_not_found');}"
        f"const response=await fetch({json.dumps(media_url, ensure_ascii=False)});"
        "if(!response.ok){throw new Error(`local_media_fetch_failed:${response.status}`);}"
        "const blob=await response.blob();"
        f"const file=new File([blob],{json.dumps(file_name, ensure_ascii=False)},{{type:blob.type||'video/mp4'}});"
        "const transfer=new DataTransfer();"
        "transfer.items.add(file);"
        "input.files=transfer.files;"
        "input.dispatchEvent(new Event('input',{bubbles:true}));"
        "input.dispatchEvent(new Event('change',{bubbles:true}));"
        "return {uploaded:input.files.length,fileName:input.files[0]?.name||'',size:file.size,type:file.type};"
        "})()"
    )


def _douyin_close_preview_tip_script() -> str:
    return (
        "(()=>{"
        "const buttons=[...document.querySelectorAll('button')];"
        "const button=buttons.find((item)=>(item.textContent||'').includes('我知道了'));"
        "if(button){button.click();return {closed:true};}"
        "return {closed:false};"
        "})()"
    )


def _bilibili_dismiss_local_draft_script() -> str:
    return (
        "(()=>{"
        "const visible=(el)=>{const style=window.getComputedStyle(el);const rect=el.getBoundingClientRect();return style.display!=='none'&&style.visibility!=='hidden'&&style.opacity!=='0'&&rect.width>0&&rect.height>0;};"
        "const textOf=(el)=>(el.textContent||'').replace(/\\s+/g,'').trim();"
        "const pageText=textOf(document.body);"
        "if(!pageText.includes('未提交的视频')&&!pageText.includes('未提交视频')){return {bilibili_local_draft_prompt:false};}"
        "const target=[...document.querySelectorAll('button,[role=\"button\"],a,div,span')].filter(visible).find((el)=>textOf(el)==='不用了'||textOf(el).includes('不用了'));"
        "if(target){target.click();return {bilibili_local_draft_prompt:true,dismissed:true,text:textOf(target)};}"
        "return {bilibili_local_draft_prompt:true,dismissed:false};"
        "})()"
    )


def _bilibili_video_upload_script(job: dict, video_path: Path) -> str:
    media_url = _local_media_url(job)
    file_name = video_path.name
    return (
        "(async()=>{"
        "const inputs=[...document.querySelectorAll('input[type=\"file\"]')];"
        "const contextText=(el)=>{let node=el;const parts=[];for(let i=0;i<6&&node;i+=1){parts.push(node.textContent||'');node=node.parentElement;}return parts.join('').replace(/\\s+/g,'');};"
        "const scored=inputs.map((input,index)=>{const accept=(input.getAttribute('accept')||'').toLowerCase();const context=contextText(input);let score=0;if(/video|mp4|mov|mkv|avi|flv|wmv/.test(accept)){score+=120;}if(/image|jpg|jpeg|png|webp/.test(accept)){score-=160;}if(context.includes('上传视频')||context.includes('点击上传')||context.includes('拖拽到此区域')){score+=40;}if(context.includes('封面')){score-=80;}return {input,index,accept,score,context:context.slice(0,120)};}).sort((a,b)=>b.score-a.score||a.index-b.index);"
        "const picked=scored.find((item)=>item.score>=0)||scored[0];"
        "if(!picked?.input){throw new Error('bilibili_video_file_input_not_found:'+inputs.length);}"
        f"const response=await fetch({json.dumps(media_url, ensure_ascii=False)});"
        "if(!response.ok){throw new Error(`local_media_fetch_failed:${response.status}`);}"
        "const blob=await response.blob();"
        f"const file=new File([blob],{json.dumps(file_name, ensure_ascii=False)},{{type:blob.type||'video/mp4'}});"
        "const transfer=new DataTransfer();"
        "transfer.items.add(file);"
        "picked.input.files=transfer.files;"
        "picked.input.dispatchEvent(new Event('input',{bubbles:true}));"
        "picked.input.dispatchEvent(new Event('change',{bubbles:true}));"
        "return {bilibili_video_uploaded:true,matched_file_inputs:inputs.length,picked_index:picked.index,picked_accept:picked.accept,fileName:picked.input.files[0]?.name||'',size:file.size,type:file.type};"
        "})()"
    )


def _bilibili_wait_video_uploaded_script(timeout_seconds: int = 180) -> str:
    return (
        "(async()=>{"
        f"const timeoutMs={int(timeout_seconds) * 1000};"
        "const sleep=(ms)=>new Promise((resolve)=>setTimeout(resolve,ms));"
        "const textOf=(el)=>(el.textContent||'').replace(/\\s+/g,'').trim();"
        "const pageText=()=>textOf(document.body);"
        "const started=Date.now();let last='';while(Date.now()-started<timeoutMs){last=pageText();if(last.includes('上传完成')||last.includes('上传成功')||last.includes('基本设置')||last.includes('发布视频')){return {bilibili_video_upload_complete:true,waited_ms:Date.now()-started};}if(last.includes('上传失败')||last.includes('文件格式错误')||last.includes('视频处理失败')){throw new Error('bilibili_video_upload_failed:'+last.slice(-200));}await sleep(1500);}"
        "throw new Error('bilibili_video_upload_not_complete:'+last.slice(-200));"
        "})()"
    )


def _bilibili_select_recommended_cover_script(timeout_seconds: int = 120) -> str:
    return (
        "(async()=>{"
        f"const timeoutMs={int(timeout_seconds) * 1000};"
        "const sleep=(ms)=>new Promise((resolve)=>setTimeout(resolve,ms));"
        "const visible=(el)=>{const style=window.getComputedStyle(el);const rect=el.getBoundingClientRect();return style.display!=='none'&&style.visibility!=='hidden'&&style.opacity!=='0'&&rect.width>0&&rect.height>0;};"
        "const textOf=(el)=>(el.textContent||'').replace(/\\s+/g,'').trim();"
        "const badImage=(src)=>/logo|avatar|favicon|icon|bili-avatar|emoji/i.test(src||'');"
        "const imageCandidates=()=>[...document.querySelectorAll('img,canvas,video')].filter((el)=>{const rect=el.getBoundingClientRect();const src=el.currentSrc||el.src||el.getAttribute('src')||'';return visible(el)&&rect.width>=50&&rect.height>=40&&!badImage(src);}).map((el)=>({el,rect:el.getBoundingClientRect(),text:textOf(el.closest('div,button,[role=\"button\"]')||el)})).filter((item)=>!item.text.includes('花生创作')&&!item.text.includes('首页')).sort((a,b)=>a.rect.top-b.rect.top||a.rect.left-b.rect.left);"
        "const clickConfirm=()=>{const target=[...document.querySelectorAll('button,[role=\"button\"],div,span')].filter(visible).find((el)=>['确定','确认','完成','使用','设为封面'].some((name)=>textOf(el)===name||textOf(el).includes(name)));if(target){target.click();return textOf(target);}return '';};"
        "const started=Date.now();let count=0;while(Date.now()-started<timeoutMs){const images=imageCandidates();count=images.length;if(images.length){const preferred=images.find((item)=>item.rect.top>260)||images[0];const clickable=preferred.el.closest('button,[role=\"button\"],div')||preferred.el;clickable.scrollIntoView({block:'center',inline:'center'});clickable.click();preferred.el.click?.();await sleep(700);const confirmText=clickConfirm();return {bilibili_cover_ready:true,bilibili_cover_selected:true,image_count:images.length,confirm_text:confirmText,waited_ms:Date.now()-started};}await sleep(1200);}"
        "throw new Error('bilibili_cover_not_ready:'+count);"
        "})()"
    )


def _bilibili_select_declaration_script() -> str:
    return (
        "(async()=>{"
        "const sleep=(ms)=>new Promise((resolve)=>setTimeout(resolve,ms));"
        "const visible=(el)=>{const style=window.getComputedStyle(el);const rect=el.getBoundingClientRect();return !el.disabled&&el.getAttribute('aria-disabled')!=='true'&&style.display!=='none'&&style.visibility!=='hidden'&&style.opacity!=='0'&&rect.width>0&&rect.height>0;};"
        "const textOf=(el)=>(el.textContent||'').replace(/\\s+/g,'').trim();"
        "const hasSelected=()=>textOf(document.body).includes('内容无需标注')&&!textOf(document.body).includes('请选择符合您视频内容的创作声明');"
        "const clickOption=()=>{const option=[...document.querySelectorAll('li,button,[role=\"option\"],[role=\"button\"],div,span')].filter(visible).map((el)=>({el,text:textOf(el)})).filter((item)=>item.text==='内容无需标注'||item.text.includes('内容无需标注')).sort((a,b)=>a.text.length-b.text.length)[0];if(option){option.el.scrollIntoView({block:'center',inline:'center'});option.el.click();return option.text;}return '';};"
        "if(hasSelected()){return {bilibili_declaration_selected:true,already_selected:true,value:'内容无需标注'};}"
        "const triggers=[...document.querySelectorAll('input,button,[role=\"button\"],[role=\"combobox\"],div,span')].filter(visible).map((el)=>({el,text:textOf(el),placeholder:el.getAttribute('placeholder')||''})).filter((item)=>item.text.includes('创作声明')||item.placeholder.includes('创作声明')||item.placeholder.includes('请选择符合您视频内容')).sort((a,b)=>a.text.length-b.text.length);"
        "for(const item of triggers){(item.el.closest('button,[role=\"button\"],[role=\"combobox\"],div')||item.el).click();await sleep(500);const optionText=clickOption();if(optionText||hasSelected()){return {bilibili_declaration_selected:true,value:'内容无需标注',option_text:optionText,trigger_text:item.text||item.placeholder};}}"
        "const optionText=clickOption();if(optionText||hasSelected()){return {bilibili_declaration_selected:true,value:'内容无需标注',option_text:optionText};}"
        "throw new Error('bilibili_declaration_option_not_found');"
        "})()"
    )


def _bilibili_select_category_if_empty_script(category: str) -> str:
    return (
        "(async()=>{"
        f"const category={json.dumps(category or DEFAULT_BILIBILI_TID, ensure_ascii=False)};"
        "const sleep=(ms)=>new Promise((resolve)=>setTimeout(resolve,ms));"
        "const visible=(el)=>{const style=window.getComputedStyle(el);const rect=el.getBoundingClientRect();return !el.disabled&&el.getAttribute('aria-disabled')!=='true'&&style.display!=='none'&&style.visibility!=='hidden'&&style.opacity!=='0'&&rect.width>0&&rect.height>0;};"
        "const textOf=(el)=>(el.textContent||'').replace(/\\s+/g,'').trim();"
        "const bodyText=()=>textOf(document.body);"
        "if(bodyText().includes(category)&&bodyText().includes('分区')){return {bilibili_category_ready:true,already_selected:true,value:category};}"
        "const clickOption=()=>{const target=[...document.querySelectorAll('li,button,[role=\"option\"],[role=\"button\"],div,span')].filter(visible).map((el)=>({el,text:textOf(el)})).filter((item)=>item.text===category||item.text.includes(category)).sort((a,b)=>a.text.length-b.text.length)[0];if(target){target.el.scrollIntoView({block:'center',inline:'center'});target.el.click();return target.text;}return '';};"
        "const triggers=[...document.querySelectorAll('input,button,[role=\"button\"],[role=\"combobox\"],div,span')].filter(visible).map((el)=>({el,text:textOf(el),placeholder:el.getAttribute('placeholder')||''})).filter((item)=>item.text.includes('分区')||item.placeholder.includes('分区')||item.placeholder.includes('请选择')).sort((a,b)=>a.text.length-b.text.length);"
        "for(const item of triggers){(item.el.closest('button,[role=\"button\"],[role=\"combobox\"],div')||item.el).click();await sleep(500);const optionText=clickOption();if(optionText||bodyText().includes(category)){return {bilibili_category_ready:true,value:category,option_text:optionText,trigger_text:item.text||item.placeholder};}}"
        "return {bilibili_category_ready:false,kept_default:true,value:category};"
        "})()"
    )


def _bilibili_set_description_script(description: str) -> str:
    return (
        "(()=>{"
        f"const value=String({json.dumps(description, ensure_ascii=False)}||'');"
        "const normalize=(text)=>String(text||'').replace(/[\\u200B-\\u200D\\uFEFF\\u00A0]/g,'').replace(/\\s+/g,' ').trim();"
        "const visible=(el)=>{const style=window.getComputedStyle(el);const rect=el.getBoundingClientRect();return !el.disabled&&!el.readOnly&&style.display!=='none'&&style.visibility!=='hidden'&&style.opacity!=='0'&&rect.width>0&&rect.height>0;};"
        "const score=(el)=>{let score=0;const attrs=[el.getAttribute('placeholder'),el.getAttribute('aria-label'),el.getAttribute('data-placeholder')].filter(Boolean).join('');if(attrs.includes('简介')||attrs.includes('相关信息')){score+=100;}if(attrs.includes('标题')){score-=120;}const rect=el.getBoundingClientRect();if(rect.height>100){score+=30;}let node=el;for(let i=0;i<5&&node;i+=1){const text=(node.textContent||'').replace(/\\s+/g,'');if(text.includes('简介')){score+=40-i*4;}if(text.includes('标题')){score-=50-i*3;}node=node.parentElement;}return score;};"
        "const candidates=[...document.querySelectorAll('textarea,div[contenteditable=\"true\"]')].filter(visible).map((el)=>({el,score:score(el)})).sort((a,b)=>b.score-a.score);"
        "const editor=candidates[0]?.el;if(!editor){throw new Error('bilibili_description_field_not_found');}"
        "editor.scrollIntoView({block:'center',inline:'center'});editor.focus();"
        "if(editor.tagName==='TEXTAREA'){const setter=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value')?.set;setter?setter.call(editor,value):(editor.value=value);}else{editor.textContent=value;}"
        "editor.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertText',data:value}));"
        "editor.dispatchEvent(new Event('change',{bubbles:true}));"
        "const actual=normalize(editor.tagName==='TEXTAREA'?editor.value:(editor.innerText||editor.textContent||''));"
        "const expected=normalize(value);if(expected&&actual!==expected){throw new Error('bilibili_description_set_failed');}"
        "return {bilibili_description_set:true,description_set:true,score:candidates[0].score,length:actual.length};"
        "})()"
    )


def _bilibili_verify_publish_ready_script(title: str, description: str) -> str:
    return (
        "(()=>{"
        f"const expectedTitle=String({json.dumps(_truncate(title, 80), ensure_ascii=False)}||'');"
        f"const expectedDescription=String({json.dumps(description, ensure_ascii=False)}||'');"
        "const normalize=(text)=>String(text||'').replace(/[\\u200B-\\u200D\\uFEFF\\u00A0]/g,'').replace(/\\s+/g,' ').trim();"
        "const compact=(text)=>normalize(text).replace(/\\s/g,'');"
        "const visible=(el)=>{const style=window.getComputedStyle(el);const rect=el.getBoundingClientRect();return !el.disabled&&!el.readOnly&&style.display!=='none'&&style.visibility!=='hidden'&&style.opacity!=='0'&&rect.width>0&&rect.height>0;};"
        "const textOf=(el)=>(el.textContent||'').replace(/\\s+/g,'').trim();"
        "const fields=[...document.querySelectorAll('input,textarea,div[contenteditable=\"true\"]')].filter(visible);"
        "const titleActual=fields.map((el)=>el.isContentEditable?el.textContent:el.value).map(normalize).find((value)=>value&&compact(value).includes(compact(expectedTitle).slice(0,12)))||'';"
        "if(compact(expectedTitle)&&!titleActual){throw new Error('bilibili_title_missing_after_set');}"
        "const expectedBody=compact(expectedDescription).slice(0,16);"
        "const fieldText=fields.map((el)=>el.isContentEditable?el.textContent:el.value).join('');"
        "const bodyText=compact((document.body.textContent||'')+fieldText);"
        "if(expectedBody&&!bodyText.includes(expectedBody)){throw new Error('bilibili_description_missing_after_set');}"
        "if(!bodyText.includes('内容无需标注')){throw new Error('bilibili_declaration_missing_after_set');}"
        "const coverReady=[...document.querySelectorAll('img,canvas,video')].filter(visible).some((el)=>{const rect=el.getBoundingClientRect();const src=el.currentSrc||el.src||'';return rect.width>=50&&rect.height>=40&&!/logo|avatar|favicon|icon/i.test(src);});"
        "if(!coverReady){throw new Error('bilibili_cover_missing_after_select');}"
        "return {bilibili_publish_ready:true,title_checked:true,description_checked:Boolean(expectedBody),declaration_checked:true,cover_checked:true,bilibili_default_tags_kept:true};"
        "})()"
    )


def _bilibili_click_publish_script() -> str:
    return (
        "(async()=>{"
        "const sleep=(ms)=>new Promise((resolve)=>setTimeout(resolve,ms));"
        "const visible=(el)=>{const style=window.getComputedStyle(el);const rect=el.getBoundingClientRect();return !el.disabled&&el.getAttribute('aria-disabled')!=='true'&&style.display!=='none'&&style.visibility!=='hidden'&&style.opacity!=='0'&&rect.width>0&&rect.height>0;};"
        "const textOf=(el)=>(el.textContent||'').replace(/\\s+/g,'').trim();"
        "const names=['立即投稿','投稿','确认投稿'];"
        "const blocked=['存草稿','批量操作','添加视频','更换视频','投稿管理'];"
        "const findButton=()=>[...document.querySelectorAll('button,[role=\"button\"],a,div,span')].filter(visible).map((el)=>({el:el.closest('button,[role=\"button\"],a')||el,text:textOf(el)})).filter((item)=>names.some((name)=>item.text===name||item.text.includes(name))&&!blocked.some((name)=>item.text.includes(name))).sort((a,b)=>a.text.length-b.text.length)[0];"
        "const started=Date.now();let last='';while(Date.now()-started<60000){const found=findButton();if(found){found.el.scrollIntoView({block:'center',inline:'center'});await sleep(500);found.el.click();return {bilibili_publish_click_scheduled:true,text:found.text,waited_ms:Date.now()-started};}last=[...document.querySelectorAll('button,[role=\"button\"],a')].filter(visible).map(textOf).filter(Boolean).slice(-20).join('|');window.scrollTo({top:document.documentElement.scrollHeight||document.body.scrollHeight,behavior:'instant'});await sleep(1000);}"
        "throw new Error('bilibili_publish_button_not_found:'+last);"
        "})()"
    )


def _bilibili_wait_publish_result_script(title: str, timeout_seconds: int = 180) -> str:
    return (
        "(async()=>{"
        f"const expectedTitle=String({json.dumps(_truncate(title, 30), ensure_ascii=False)}||'').replace(/\\s+/g,'').trim();"
        f"const timeoutMs={int(timeout_seconds) * 1000};"
        "const sleep=(ms)=>new Promise((resolve)=>setTimeout(resolve,ms));"
        "const visible=(el)=>{const style=window.getComputedStyle(el);const rect=el.getBoundingClientRect();return !el.disabled&&el.getAttribute('aria-disabled')!=='true'&&style.display!=='none'&&style.visibility!=='hidden'&&style.opacity!=='0'&&rect.width>0&&rect.height>0;};"
        "const textOf=(el)=>(el.textContent||'').replace(/\\s+/g,'').trim();"
        "const pageText=()=>textOf(document.body);"
        "const successTexts=['投稿成功','提交成功','已提交审核','审核中','发布成功','稿件已提交','稿件提交成功'];"
        "const blockedTexts=['验证码','安全验证','登录失效','请先登录','未登录','投稿失败','提交失败','内容违规','无法投稿','风控','频繁','请填写标题','请填写标签','请选择分区','请选择创作声明','请选择符合您视频内容的创作声明','标题不能为空','分区不能为空'];"
        "const confirmTexts=['确认投稿','继续投稿','仍要投稿','同意并投稿','确认','确定'];"
        "const hasSuccess=()=>successTexts.find((item)=>pageText().includes(item));"
        "const hasBlock=()=>blockedTexts.find((item)=>pageText().includes(item));"
        "const clickConfirm=()=>{const target=[...document.querySelectorAll('button,[role=\"button\"],div,span')].filter(visible).map((el)=>({el,text:textOf(el)})).filter((item)=>confirmTexts.some((name)=>item.text===name||item.text.includes(name))).sort((a,b)=>a.text.length-b.text.length)[0];if(target){target.el.scrollIntoView({block:'center',inline:'center'});target.el.click();return {clicked:true,text:target.text};}return {clicked:false};};"
        "const titleVisible=()=>{const text=pageText();return expectedTitle&&expectedTitle.length>=4&&text.includes(expectedTitle)&&/content|manager|platform/.test(location.href);};"
        "const started=Date.now();let confirms=[];let last='';while(Date.now()-started<timeoutMs){const success=hasSuccess();if(success){return {bilibili_publish_confirmed:true,success_text:success,url:location.href,waited_ms:Date.now()-started,confirms};}if(titleVisible()&&/archive|content|manage/.test(location.href)){return {bilibili_publish_confirmed:true,success_text:'页面出现标题',url:location.href,waited_ms:Date.now()-started,confirms};}const blocked=hasBlock();if(blocked){throw new Error('bilibili_publish_blocked:'+blocked);}const confirm=clickConfirm();if(confirm.clicked){confirms.push(confirm);await sleep(2000);continue;}last=[...document.querySelectorAll('button,[role=\"button\"],a')].filter(visible).map(textOf).filter(Boolean).slice(-20).join('|');await sleep(1500);}"
        "throw new Error('bilibili_publish_not_confirmed:'+location.href+'|'+last);"
        "})()"
    )


def _browser_bilibili_video_upload_command(session: str, job: dict, video_path: Path) -> list[str]:
    return _browser_eval_command(session, _bilibili_video_upload_script(job, video_path))


def _browser_wait_bilibili_video_uploaded_command(session: str) -> list[str]:
    return _browser_eval_command(session, _bilibili_wait_video_uploaded_script())


def _browser_select_bilibili_cover_command(session: str) -> list[str]:
    return _browser_eval_command(session, _bilibili_select_recommended_cover_script())


def _browser_select_bilibili_declaration_command(session: str) -> list[str]:
    return _browser_eval_command(session, _bilibili_select_declaration_script())


def _browser_select_bilibili_category_command(session: str, category: str) -> list[str]:
    return _browser_eval_command(session, _bilibili_select_category_if_empty_script(category))


def _browser_set_bilibili_description_command(session: str, description: str) -> list[str]:
    return _browser_eval_command(session, _bilibili_set_description_script(description))


def _browser_verify_bilibili_publish_ready_command(session: str, title: str, description: str) -> list[str]:
    return _browser_eval_command(session, _bilibili_verify_publish_ready_script(title, description))


def _browser_click_bilibili_publish_command(session: str) -> list[str]:
    return _browser_eval_command(session, _bilibili_click_publish_script())


def _browser_wait_bilibili_publish_result_command(session: str, title: str) -> list[str]:
    return _browser_eval_command(session, _bilibili_wait_publish_result_script(title))


def _build_douyin_browser_commands(job: dict, video_path: Path, cover_path: Path | None) -> list[list[str]]:
    session = f"send-douyin-{job['id']}"
    title = _truncate(_sanitize_publish_title(job.get("title") or "直播切片"), 30)
    description = _douyin_description_for_job(job, title)
    commands = [
        _browser_open_command(session, "https://creator.douyin.com/creator-micro/content/upload"),
        _browser_wait_command(session, 5),
        _browser_eval_command(session, _douyin_video_upload_script(job, video_path)),
        _browser_wait_command(session, 8),
        _browser_eval_command(session, _douyin_close_preview_tip_script()),
        _browser_fill_title_command(session, title),
        _browser_set_douyin_description_command(session, description),
        _browser_verify_douyin_publish_ready_command(session, title, description),
    ]
    commands.extend(
        [
            _browser_wait_douyin_ai_cover_command(session),
            _browser_click_douyin_ai_cover_command(session),
            _browser_confirm_douyin_cover_command(session),
            _browser_verify_douyin_cover_command(session),
            _browser_verify_douyin_publish_ready_command(session, title, description),
            _browser_click_douyin_publish_command(session),
            _browser_wait_command(session, 2),
            _browser_wait_douyin_publish_result_command(session, title),
        ]
    )
    return commands


def _build_bilibili_browser_commands(job: dict, video_path: Path, cover_path: Path | None) -> list[list[str]]:
    session = f"send-bilibili-{job['id']}"
    title = _truncate(_sanitize_publish_title(job.get("title") or "直播切片"), 80)
    description = _sanitize_publish_description(job.get("description") or title)
    category = (job.get("bilibili_tid") or DEFAULT_BILIBILI_TID).strip() or DEFAULT_BILIBILI_TID
    commands = [
        _browser_open_command(session, "https://member.bilibili.com/platform/upload/video/frame"),
        _browser_wait_command(session, 5),
        _browser_eval_command(session, _bilibili_dismiss_local_draft_script()),
        _browser_wait_command(session, 2),
        _browser_bilibili_video_upload_command(session, job, video_path),
        _browser_wait_bilibili_video_uploaded_command(session),
        _browser_select_bilibili_cover_command(session),
        _browser_fill_title_command(session, title),
        _browser_select_bilibili_declaration_command(session),
        _browser_select_bilibili_category_command(session, category),
    ]
    if description:
        commands.append(_browser_set_bilibili_description_command(session, description))
    commands.extend(
        [
            _browser_verify_bilibili_publish_ready_command(session, title, description),
            _browser_click_bilibili_publish_command(session),
            _browser_wait_command(session, 2),
            _browser_wait_bilibili_publish_result_command(session, title),
        ]
    )
    return commands


def _opencli_commands_for_job(job: dict) -> list[list[str]]:
    video_path = _job_video_path(job)
    cover_path = _job_cover_path(job)
    if job["platform"] == "douyin":
        return _build_douyin_browser_commands(job, video_path, cover_path)
    if job["platform"] == "bilibili":
        return _build_bilibili_browser_commands(job, video_path, cover_path)
    raise ValueError("暂不支持这个发送平台。")


def _opencli_cleanup_commands_for_job(job: dict) -> list[list[str]]:
    return []


def _command_summary(command: list[str]) -> str:
    hidden = []
    for part in command:
        text = str(part)
        if len(text) > 120:
            text = f"{text[:120]}..."
        hidden.append(text)
    return " ".join(hidden)


def _mark_job_failed(job_id: str, error_code: str, message: str, payload: dict | None = None) -> dict:
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE publish_jobs
            SET status = 'failed', audit_status = 'not_submitted', error_code = ?,
                error_message = ?, provider_response = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                error_code,
                message,
                json.dumps(payload or {}, ensure_ascii=False),
                _now_iso(),
                job_id,
            ),
        )
        connection.commit()
    return get_publish_job(job_id)


def execute_opencli_send_job(job_id: str, runner: CommandRunner | None = None) -> dict:
    job = get_publish_job(job_id)
    if not job:
        raise ValueError("发送任务不存在。")
    if job.get("publish_mode") != "opencli_publish":
        raise ValueError("只能执行 opencli 发送任务。")
    if job.get("status") == "published":
        return {"status": "ok", "message": "这条任务已经标记为已发布。", "job": job}

    runner = runner or _default_command_runner
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE publish_jobs
            SET status = 'publishing', error_code = '', error_message = '',
                provider_response = ?, updated_at = ?
            WHERE id = ?
            """,
            (json.dumps({"opencli": "started"}, ensure_ascii=False), _now_iso(), job_id),
        )
        connection.commit()

    try:
        commands = _opencli_commands_for_job(get_publish_job(job_id))
    except Exception as exc:
        failed_job = _mark_job_failed(job_id, "prepare_failed", str(exc), {"stage": "prepare"})
        return {"status": "failed", "message": str(exc), "job": failed_job}

    outputs: list[dict[str, Any]] = []
    for index, command in enumerate(commands, start=1):
        attempt = 1
        while True:
            try:
                result = runner(command)
            except subprocess.TimeoutExpired as exc:
                message = f"opencli 第 {index} 步超时：{_command_summary(command)}"
                failed_job = _mark_job_failed(job_id, "opencli_timeout", message, {"outputs": outputs})
                return {"status": "failed", "message": message, "job": failed_job}
            except Exception as exc:
                message = f"opencli 第 {index} 步启动失败：{exc}"
                failed_job = _mark_job_failed(job_id, "opencli_start_failed", message, {"outputs": outputs})
                return {"status": "failed", "message": message, "job": failed_job}

            output = {
                "step": index,
                "attempt": attempt,
                "command": _command_summary(command),
                "returncode": result.returncode,
                "stdout": (result.stdout or "")[-2000:],
                "stderr": (result.stderr or "")[-2000:],
            }
            outputs.append(output)
            if result.returncode == 0:
                break
            if _should_retry_opencli_detached(command, result, attempt):
                output["retry_reason"] = "opencli_detached"
                attempt += 1
                continue
            message = output["stderr"] or output["stdout"] or f"opencli 第 {index} 步失败"
            failed_job = _mark_job_failed(job_id, "opencli_failed", message[:1000], {"outputs": outputs})
            return {"status": "failed", "message": message, "job": failed_job}

    cleanup_outputs: list[dict[str, Any]] = []
    for command in _opencli_cleanup_commands_for_job(get_publish_job(job_id)):
        try:
            result = runner(command)
            cleanup_outputs.append(
                {
                    "command": _command_summary(command),
                    "returncode": result.returncode,
                    "stdout": (result.stdout or "")[-2000:],
                    "stderr": (result.stderr or "")[-2000:],
                }
            )
        except Exception as exc:
            cleanup_outputs.append(
                {
                    "command": _command_summary(command),
                    "returncode": -1,
                    "stdout": "",
                    "stderr": str(exc),
                }
            )

    now = _now_iso()
    response = {
        "opencli": "completed",
        "platform_url": "",
        "outputs": outputs,
        "cleanup_outputs": cleanup_outputs,
        "completed_at": now,
    }
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE publish_jobs
            SET status = 'published', audit_status = 'submitted',
                error_code = '', error_message = '', provider_response = ?, updated_at = ?
            WHERE id = ?
            """,
            (json.dumps(response, ensure_ascii=False), now, job_id),
        )
        connection.commit()
    return {"status": "ok", "message": "opencli 发送流程已执行完成。", "job": get_publish_job(job_id)}


def _ready_opencli_job_ids(job_ids: list[str] | None = None) -> list[str]:
    params: list[str] = []
    where = "publish_mode = 'opencli_publish' AND status IN ('ready', 'failed')"
    if job_ids:
        placeholders = ",".join("?" for _ in job_ids)
        where += f" AND id IN ({placeholders})"
        params.extend(job_ids)
    with get_connection() as connection:
        rows = connection.execute(
            f"SELECT id FROM publish_jobs WHERE {where} ORDER BY created_at ASC",
            params,
        ).fetchall()
    return [row["id"] for row in rows]


def run_opencli_send_batch(job_ids: list[str] | None = None, runner: CommandRunner | None = None) -> dict:
    if not _SEND_LOCK.acquire(blocking=False):
        return {"status": "busy", "message": "发送队列正在运行，请等待当前批次结束。", "jobs": list_publish_jobs(limit=100)}
    try:
        ids = _ready_opencli_job_ids(job_ids)
        results = [execute_opencli_send_job(job_id, runner=runner) for job_id in ids]
        return {"status": "ok", "message": f"发送批次已处理 {len(results)} 条任务。", "results": results, **get_publish_center_context()}
    finally:
        _SEND_LOCK.release()


def start_opencli_send_batch(payload: PublishSendStart, background_tasks: Any | None = None) -> dict:
    ids = _ready_opencli_job_ids(payload.job_ids)
    if not ids:
        return {"status": "empty", "message": "当前没有待发送或失败可重试的任务。", **get_publish_center_context()}
    if _SEND_LOCK.locked():
        return {"status": "busy", "message": "发送队列正在运行，请稍后刷新查看进度。", **get_publish_center_context()}
    opencli_status = _opencli_status()
    if not opencli_status["available"]:
        return {
            "status": "missing_opencli",
            "message": (
                f"{opencli_status['message']} 如果你刚安装或更新过 opencli，"
                f"请运行 {opencli_status['restart_command']} 重启 Windows 本地后台后再试。"
            ),
            **get_publish_center_context(),
        }
    if background_tasks is not None:
        background_tasks.add_task(run_opencli_send_batch, ids)
        return {"status": "started", "message": f"已开始后台发送 {len(ids)} 条任务。", **get_publish_center_context()}
    return run_opencli_send_batch(ids)


def retry_publish_job(job_id: str) -> dict:
    job = get_publish_job(job_id)
    if not job:
        raise ValueError("发布任务不存在。")
    if job.get("publish_mode") == "opencli_publish":
        return execute_opencli_send_job(job_id)
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
    publish_items = []
    queue_items = []
    raw_items = _list_completed_publish_clips()
    output_clip_ids = [item["output_clip_id"] for item in raw_items]
    opencli_jobs_map = _batch_find_opencli_jobs(output_clip_ids)
    for item in raw_items:
        original_path = resolve_video_file_path(item.get("output_file_path") or "")
        subtitled_path = resolve_video_file_path(item.get("subtitled_output_file_path") or "")
        default_title = _sanitize_publish_title(_default_title_for_clip(item))
        original_available = bool(original_path and original_path.exists() and original_path.is_file())
        subtitled_available = bool(subtitled_path and subtitled_path.exists() and subtitled_path.is_file())
        normalized_item = {
            **item,
            "default_title": default_title,
            "default_tags": _format_tags(_fallback_tags(item)) or _format_tags(SAFE_TOPIC_FALLBACKS),
            "original_available": original_available,
            "subtitled_available": subtitled_available,
            "subtitle_status_label": "已加字幕" if item.get("subtitle_status") == "completed" else "未加字幕",
            "video_media_url": _video_media_url(item["task_id"], item["output_clip_id"], "original"),
        }
        publish_items.append(normalized_item)
        jobs_for_oc = opencli_jobs_map.get(item["output_clip_id"], {})
        for platform in PLATFORM_LABELS:
            job = jobs_for_oc.get(platform)
            if job:
                queue_items.append(
                    {
                        **normalized_item,
                        "job": job,
                        "job_id": job["id"],
                        "platform": platform,
                        "platform_label": PLATFORM_LABELS[platform],
                        "title": _sanitize_publish_title(job.get("title") or default_title, default_title),
                        "description": _sanitize_publish_description(job.get("description") or ""),
                        "tags": _hashtags(job.get("tags") or normalized_item["default_tags"]),
                        "status": job.get("status"),
                        "status_label": job.get("status_label"),
                        "status_tone": job.get("status_tone"),
                        "cover_media_url": job.get("cover_media_url"),
                        "cover_file_path": job.get("cover_file_path") or "",
                        "error_message": job.get("error_message") or "",
                        "platform_url": job.get("platform_url") or "",
                    }
                )
            else:
                queue_items.append(
                    {
                        **normalized_item,
                        "job": None,
                        "job_id": "",
                        "platform": platform,
                        "platform_label": PLATFORM_LABELS[platform],
                        "title": _sanitize_publish_title(default_title),
                        "description": _compose_description(item, default_title, normalized_item["default_tags"]),
                        "tags": _hashtags(normalized_item["default_tags"]),
                        "status": "not_queued",
                        "status_label": "待入队",
                        "status_tone": "amber",
                        "cover_media_url": "",
                        "cover_file_path": "",
                        "error_message": "",
                        "platform_url": "",
                    }
                )

    jobs = [job for job in list_publish_jobs(limit=200) if job.get("publish_mode") == "opencli_publish"]
    jobs_by_platform = {
        platform: [job for job in jobs if job["platform"] == platform]
        for platform in PLATFORM_LABELS
    }
    ready_count = sum(1 for job in jobs if job.get("status") == "ready")
    sending_count = sum(1 for job in jobs if job.get("status") == "publishing")
    published_count = sum(1 for job in jobs if job.get("status") == "published")
    failed_count = sum(1 for job in jobs if job.get("status") == "failed")
    opencli_status = _opencli_status()
    return {
        "publish_items": publish_items,
        "send_queue_items": queue_items,
        "publish_jobs": jobs,
        "jobs_by_platform": jobs_by_platform,
        "platforms": [{"id": platform, "label": label} for platform, label in PLATFORM_LABELS.items()],
        "opencli_available": opencli_status["available"],
        "opencli_status": opencli_status,
        "stats": [
            {"label": "可入队切片", "value": len(publish_items), "tone": "green"},
            {"label": "待发送", "value": ready_count, "tone": "blue"},
            {"label": "发送中", "value": sending_count, "tone": "purple"},
            {"label": "已发布", "value": published_count, "tone": "green"},
            {"label": "发送失败", "value": failed_count, "tone": "red"},
        ],
    }
