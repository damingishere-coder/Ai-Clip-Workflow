import json
import math
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Callable
from urllib.parse import urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.config import settings
from app.db.database import get_connection
from app.models.task import (
    PublishAccountCreate,
    PublishBatchJobCreate,
    PublishBatchTargetUpdate,
    PublishCoverCreate,
    PublishCoverFrameBatchCreate,
    PublishJobContentUpdate,
    PublishJobCreate,
    PublishJobScheduleUpdate,
    PublishJobTargetUpdate,
    PublishPlatformConfigUpdate,
    PublishSendJobUpdate,
    TaskStatus,
)
from app.services.ai.ai_clip_analyzer import build_provider, loads_ai_json
from app.services.ai.base import AIProviderError, generate_json_with_safe_retry
from app.services.publishers.base import parse_public_json_dict
from app.services.database_backup_service import create_publish_migration_backup
from app.services.publish_copy_rules import (
    BILIBILI_TITLE_MAX,
    DOUYIN_FALLBACK_TAGS,
    DOUYIN_TITLE_MAX,
    PUBLISH_COPY_RULE_VERSION,
    format_douyin_tags,
    normalize_douyin_description,
    normalize_douyin_title,
    split_publish_tags,
    validate_douyin_publish_copy,
)
from app.services.publish_providers import (
    BilibiliPublishProvider,
    DouyinPublishProvider,
    PublishProviderError,
)
from app.services.publish_domain import (
    AUTO_PUBLISH_PLATFORMS,
    PUBLISH_MODES,
    TARGET_PLATFORMS,
    safe_platform_url,
)
from app.services.publish_readiness import PublishPlatformIsolationBlocked
from app.services.publish_time import app_zone, local_display, parse_datetime, utc_now_iso
from app.services.storage_service import get_artifact_paths, resolve_video_file_path
from app.services.video_cut_service import ensure_ffmpeg_available, sanitize_filename_part, summarize_stderr


PLATFORM_LABELS = TARGET_PLATFORMS

STATUS_LABELS = {
    "draft": "草稿",
    "ready": "待发送",
    "NEED_REVIEW": "需人工复核",
    "publishing": "发送中",
    "published": "已发布",
    "failed": "发送失败",
    "cancelled": "已取消",
}

STATUS_TONES = {
    "draft": "amber",
    "ready": "blue",
    "NEED_REVIEW": "amber",
    "publishing": "purple",
    "published": "green",
    "failed": "red",
    "cancelled": "amber",
}

EXECUTION_PHASE_LABELS = {
    "claimed": "正在领取任务",
    "received": "Worker 已接收",
    "browser_opening": "正在打开抖音",
    "browser_opened": "抖音页面已打开",
    "upload_started": "已选择视频",
    "upload_waiting": "正在上传并解析视频",
    "upload_completed": "视频上传完成",
    "title_filled": "标题已填写",
    "description_filled": "正文和话题已填写",
    "form_verified_before_cover": "内容校验通过",
    "recommended_cover_ready": "推荐封面已生成",
    "recommended_cover_clicked": "正在设置推荐封面",
    "recommended_cover_confirmed": "推荐封面已确认",
    "recommended_cover_verified": "推荐封面已生效",
    "form_verified_before_submit": "发布内容最终校验通过",
    "visibility_verified": "可见范围已验证",
    "precise_publish_clicked": "正在点击发布",
    "submit_clicked": "已提交，等待平台结果",
    "publish_result_checked": "正在确认发布结果",
    "manual_review_waiting": "已暂停，等待人工处理",
    "confirmed_success": "平台已确认发布成功",
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

PUBLISH_STATUS_DRAFT = "DRAFT"
PUBLISH_STATUS_SCHEDULED = "SCHEDULED"
PUBLISH_STATUS_WAITING = "WAITING"
PUBLISH_STATUS_PUBLISHING = "PUBLISHING"
PUBLISH_STATUS_PUBLISHED = "PUBLISHED"
PUBLISH_STATUS_EXPORTED = "EXPORTED"
PUBLISH_STATUS_FAILED = "FAILED"
PUBLISH_STATUS_CANCELLED = "CANCELLED"
PUBLISH_STATUS_NEED_REVIEW = "NEED_REVIEW"
USER_REMOVED_ERROR_CODE = "user_removed_from_preparation"
SUPERSEDED_BY_RECUT_ERROR_CODE = "superseded_by_recut"
ACTIVE_PREPARATION_STATUSES = {
    PUBLISH_STATUS_DRAFT,
    PUBLISH_STATUS_WAITING,
    PUBLISH_STATUS_SCHEDULED,
}
PUBLISH_HISTORY_STATUSES = {
    PUBLISH_STATUS_SCHEDULED,
    PUBLISH_STATUS_PUBLISHING,
    PUBLISH_STATUS_PUBLISHED,
    PUBLISH_STATUS_EXPORTED,
    PUBLISH_STATUS_FAILED,
    PUBLISH_STATUS_NEED_REVIEW,
    PUBLISH_STATUS_CANCELLED,
}
PUBLISH_HISTORY_HIDEABLE_STATUSES = {
    PUBLISH_STATUS_PUBLISHED,
    PUBLISH_STATUS_EXPORTED,
    PUBLISH_STATUS_FAILED,
    PUBLISH_STATUS_CANCELLED,
}

LEGACY_STATUS_MAP = {
    "draft": PUBLISH_STATUS_DRAFT,
    "ready": PUBLISH_STATUS_SCHEDULED,
    "scheduled": PUBLISH_STATUS_SCHEDULED,
    "publishing": PUBLISH_STATUS_PUBLISHING,
    "published": PUBLISH_STATUS_PUBLISHED,
    "failed": PUBLISH_STATUS_FAILED,
    "cancelled": PUBLISH_STATUS_CANCELLED,
    "need_review": PUBLISH_STATUS_NEED_REVIEW,
}

PUBLISH_MODE_LABELS.update(PUBLISH_MODES)

STATUS_LABELS = {
    PUBLISH_STATUS_DRAFT: "草稿",
    PUBLISH_STATUS_SCHEDULED: "待发送",
    PUBLISH_STATUS_WAITING: "等待处理",
    PUBLISH_STATUS_PUBLISHING: "发布中",
    PUBLISH_STATUS_PUBLISHED: "已发布",
    PUBLISH_STATUS_EXPORTED: "已导出发布包",
    PUBLISH_STATUS_FAILED: "发送失败",
    PUBLISH_STATUS_CANCELLED: "已取消",
    PUBLISH_STATUS_NEED_REVIEW: "需人工复核",
    "draft": "草稿",
    "ready": "待发送",
    "publishing": "发送中",
    "published": "已发布",
    "failed": "发送失败",
    "cancelled": "已取消",
}

STATUS_TONES = {
    PUBLISH_STATUS_DRAFT: "amber",
    PUBLISH_STATUS_SCHEDULED: "blue",
    PUBLISH_STATUS_WAITING: "amber",
    PUBLISH_STATUS_PUBLISHING: "purple",
    PUBLISH_STATUS_PUBLISHED: "green",
    PUBLISH_STATUS_EXPORTED: "blue",
    PUBLISH_STATUS_FAILED: "red",
    PUBLISH_STATUS_CANCELLED: "amber",
    PUBLISH_STATUS_NEED_REVIEW: "amber",
    "draft": "amber",
    "ready": "blue",
    "publishing": "purple",
    "published": "green",
    "failed": "red",
    "cancelled": "amber",
}

COVER_WIDTH = 1280
COVER_HEIGHT = 720
OPENCLI_TIMEOUT_SECONDS = 900
DEFAULT_BILIBILI_TID = "娱乐"
_SEND_LOCK = Lock()
_METADATA_UPGRADE_LOCK = Lock()

SAFE_TOPIC_FALLBACKS = DOUYIN_FALLBACK_TAGS
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


def _version_iso() -> str:
    """publish_jobs.updated_at 的乐观并发版本；创建时间继续保持旧显示顺序。"""

    return utc_now_iso()


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
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {settings.publish_worker_token}",
        },
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


def _sanitize_publish_title(
    value: str | None,
    fallback: str = "精彩片段",
    *,
    platform: str = "bilibili",
    generated: bool = False,
) -> str:
    title = _apply_content_safety(value)
    title = re.sub(r"[#＃]+", "", title)
    if platform == "douyin":
        return normalize_douyin_title(title, generated=generated, fallback=fallback)
    title = _truncate(title, BILIBILI_TITLE_MAX)
    return title or fallback


def _sanitize_publish_description(
    value: str | None,
    fallback: str = "",
    *,
    platform: str = "bilibili",
    generated: bool = False,
    title: str = "",
) -> str:
    description = _apply_content_safety(value)
    if platform == "douyin":
        return normalize_douyin_description(
            description or fallback,
            title=title,
            generated=generated,
        )
    return _truncate(description or fallback, 700)


def _sanitize_publish_content(
    title: str | None,
    tags: list[str] | str | None,
    description: str | None,
    title_fallback: str = "精彩片段",
    description_fallback: str = "",
    *,
    platform: str = "douyin",
    generated: bool = False,
    validate: bool = False,
) -> dict:
    safe_title = _sanitize_publish_title(
        title,
        title_fallback,
        platform=platform,
        generated=generated,
    )
    if platform == "douyin":
        safe_tag_values = [_apply_content_safety(tag) for tag in split_publish_tags(tags)]
        safe_tags = format_douyin_tags(safe_tag_values, generated=generated)
        safe_description = _sanitize_publish_description(
            description,
            description_fallback,
            platform=platform,
            generated=generated,
            title=safe_title,
        )
        if validate:
            validate_douyin_publish_copy(safe_title, safe_description, safe_tags)
    else:
        safe_tags = _format_tags(tags) or _format_tags(SAFE_TOPIC_FALLBACKS)
        safe_description = _sanitize_publish_description(description, description_fallback, platform=platform)
    return {"title": safe_title, "tags": safe_tags, "description": safe_description}


def _normalize_config(row) -> dict:
    config = dict(row)
    client_key = config.get("client_key") or ""
    client_secret = config.get("client_secret") or ""
    config.pop("client_secret", None)
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
    login_status = account.get("login_status") or "login_required"
    access_token = account.get("access_token")
    refresh_token = account.get("refresh_token")
    account.pop("access_token", None)
    account.pop("refresh_token", None)
    account.update(
        {
            "platform_label": PLATFORM_LABELS.get(account.get("platform"), account.get("platform")),
            "access_token_masked": _mask_secret(access_token),
            "refresh_token_masked": _mask_secret(refresh_token),
            "is_authorized": account.get("authorization_status") == "authorized",
            "auth_type": account.get("auth_type") or "browser_profile",
            "login_status": login_status,
            "login_status_label": {
                "normal": "正常",
                "invalid": "登录失效",
                "login_pending": "等待登录完成",
                "busy": "账号操作中",
            }.get(login_status, "需要重新登录"),
            "login_message": account.get("login_message") or "",
        }
    )
    return account


def _normalize_publish_status(status: str | None) -> str:
    raw = (status or "").strip()
    if not raw:
        return PUBLISH_STATUS_SCHEDULED
    if raw in STATUS_LABELS and raw.isupper():
        return raw
    return LEGACY_STATUS_MAP.get(raw.lower(), raw)


def _format_publish_schedule(value: str | None, timezone_name: str = "Asia/Shanghai") -> str:
    text = (value or "").strip()
    if not text:
        return "未排期"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        try:
            display_zone = ZoneInfo(timezone_name or "Asia/Shanghai")
        except ZoneInfoNotFoundError:
            display_zone = ZoneInfo("Asia/Shanghai")
        return parsed.astimezone(display_zone).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return text


def _format_task_created_at(value: str | None) -> str:
    text = (value or "").strip()
    if not text:
        return "未知时间"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(ZoneInfo(settings.app_timezone))
        return parsed.strftime("%Y-%m-%d %H:%M")
    except (ValueError, ZoneInfoNotFoundError):
        return text


def _task_source_file_name(job: dict) -> str:
    source_path = job.get("task_original_video_path")
    source_text = str(source_path or "").strip()
    if not source_text:
        return "未记录原视频文件名"
    return Path(source_text).name or "未记录原视频文件名"


def _normalize_job(
    row,
    *,
    accounts: list[dict] | None = None,
    worker_state: dict | None = None,
) -> dict:
    job = dict(row)
    status = _normalize_publish_status(job.get("status"))
    provider_payload = parse_public_json_dict(job.get("provider_response"))
    publish_result_payload = parse_public_json_dict(job.get("publish_result")) if "publish_result" in job else {}
    # 旧记录和兼容写入可能包含未脱敏 JSON；公共 DTO 只返回清洗后的结构。
    job.pop("provider_response", None)
    job.pop("publish_result", None)
    caption = job.get("caption") or job.get("description") or ""
    hashtags = job.get("hashtags") or job.get("tags") or ""
    clip_id = job.get("clip_id") or job.get("output_clip_id") or ""
    video_path = job.get("video_path") or job.get("video_file_path") or ""
    error_message = job.get("error_message") or job.get("last_error") or ""
    schedule_timezone = job.get("schedule_timezone") or "Asia/Shanghai"
    scheduled_at_utc = job.get("scheduled_at") or ""
    execution_phase = str(job.get("execution_phase") or "")
    execution_phase_label = EXECUTION_PHASE_LABELS.get(execution_phase, execution_phase)
    scheduled_at_local = ""
    if scheduled_at_utc:
        try:
            parse_datetime_value = datetime.fromisoformat(scheduled_at_utc.replace("Z", "+00:00"))
            if parse_datetime_value.tzinfo is None:
                parse_datetime_value = parse_datetime_value.replace(tzinfo=timezone.utc)
            scheduled_at_local = parse_datetime_value.astimezone(ZoneInfo(schedule_timezone)).isoformat(timespec="seconds")
        except (ValueError, ZoneInfoNotFoundError):
            scheduled_at_local = scheduled_at_utc
    missing_fields: list[str] = []
    if not str(job.get("title") or "").strip():
        missing_fields.append("标题")
    if not str(caption).strip():
        missing_fields.append("正文/简介")
    if not str(hashtags).strip():
        missing_fields.append("话题/标签")
    if not str(job.get("cover_file_path") or "").strip():
        missing_fields.append("封面")
    if str(job.get("platform") or "") == "bilibili":
        if not str(job.get("bilibili_tid") or "").strip():
            missing_fields.append("B站分区")
        if job.get("bilibili_copyright") == "repost" and not str(job.get("bilibili_source") or "").strip():
            missing_fields.append("转载来源")
    job.update(
        {
            "status": status,
            "legacy_status": job.get("status") or "",
            "clip_id": clip_id,
            "video_path": video_path,
            "video_file_path": job.get("video_file_path") or video_path,
            "caption": caption,
            "description": job.get("description") or caption,
            "hashtags": hashtags,
            "tags": job.get("tags") or hashtags,
            "error_message": error_message,
            "last_error": job.get("last_error") or error_message,
            "attempt_count": int(job.get("attempt_count") or job.get("retry_count") or 0),
            "platform_label": PLATFORM_LABELS.get(job.get("platform"), job.get("platform")),
            "status_label": (
                execution_phase_label
                if status == PUBLISH_STATUS_PUBLISHING and execution_phase_label
                else STATUS_LABELS.get(status, status)
            ),
            "execution_phase_label": execution_phase_label,
            "status_tone": STATUS_TONES.get(status, "blue"),
            "video_source_label": VIDEO_SOURCE_LABELS.get(job.get("video_source"), job.get("video_source")),
            "publish_mode_label": PUBLISH_MODE_LABELS.get(job.get("publish_mode"), job.get("publish_mode")),
            "schedule_timezone": schedule_timezone,
            "scheduled_at_utc": scheduled_at_utc,
            "scheduled_at_local": scheduled_at_local,
            "scheduled_at_display": _format_publish_schedule(scheduled_at_utc, schedule_timezone),
            "account_name": job.get("account_name") or "未选择账号",
            "cover_media_url": _cover_media_url(job.get("task_id") or "", job.get("cover_file_path")),
            "video_media_url": _video_media_url(
                job.get("task_id") or "",
                job.get("output_clip_id") or "",
                job.get("video_source") or "original",
            ),
            "provider_payload": provider_payload,
            "publish_result_payload": publish_result_payload,
            "platform_url": safe_platform_url(
                str(job.get("platform") or ""),
                job.get("platform_url") or provider_payload.get("url") or provider_payload.get("platform_url") or "",
            ),
            "trace_path": provider_payload.get("trace_path") or "",
            "content_complete": not missing_fields,
            "missing_fields": missing_fields,
            "account_login_status": job.get("account_login_status") or "login_required",
            "account_login_message": job.get("account_login_message") or "",
            "task_source_file_name": _task_source_file_name(job),
            "task_created_at_display": _format_task_created_at(job.get("task_created_at")),
            "is_user_removed": (
                status == PUBLISH_STATUS_CANCELLED
                and str(job.get("error_code") or "") == USER_REMOVED_ERROR_CODE
            ),
            "output_is_active": bool(job.get("output_is_active", 1)),
            "is_superseded_by_recut": (
                status == PUBLISH_STATUS_CANCELLED
                and str(job.get("error_code") or "") == SUPERSEDED_BY_RECUT_ERROR_CODE
            ),
            "history_hidden": bool(job.get("history_hidden")),
            "history_hidden_at": str(job.get("history_hidden_at") or ""),
            "started_at_display": local_display(job.get("started_at"), settings.app_timezone) if job.get("started_at") else "—",
            "finished_at_display": local_display(job.get("finished_at"), settings.app_timezone) if job.get("finished_at") else "—",
            "history_hidden_at_display": (
                local_display(job.get("history_hidden_at"), settings.app_timezone)
                if job.get("history_hidden_at")
                else ""
            ),
        }
    )
    from app.services.publish_readiness import build_send_readiness

    readiness = build_send_readiness(
        job,
        accounts=accounts,
        worker_available=(worker_state or {}).get("worker_available"),
        worker_message=str((worker_state or {}).get("worker_message") or ""),
    )
    job["send_readiness"] = readiness
    account_issue_codes = {
        "account_missing",
        "account_not_found",
        "account_selection_required",
        "account_platform_mismatch",
    }
    issue_codes = {str(issue.get("code") or "") for issue in readiness.get("issues") or []}
    if issue_codes & account_issue_codes and "发布账号" not in missing_fields:
        missing_fields.append("发布账号")
    content_invalid = "content_invalid" in issue_codes
    account_login_required = "account_login_required" in issue_codes
    if missing_fields:
        content_status_message = f"缺少：{'、'.join(missing_fields)}"
        content_status_tone = "amber"
    elif account_login_required:
        content_status_message = "账号需登录"
        content_status_tone = "amber"
    elif content_invalid:
        content_status_message = "文案不符合抖音规则"
        content_status_tone = "amber"
    else:
        content_status_message = "内容完整"
        content_status_tone = "green"
    resolved_account_id = str(readiness.get("resolved_account_id") or "")
    resolved_account_name = str(readiness.get("resolved_account_name") or "")
    job.update(
        {
            "effective_account_id": resolved_account_id or str(job.get("account_id") or ""),
            "account_name": resolved_account_name or job.get("account_name") or "未选择账号",
            "content_complete": not missing_fields and not content_invalid and not account_login_required,
            "missing_fields": missing_fields,
            "content_status_message": content_status_message,
            "content_status_tone": content_status_tone,
            "metadata_policy_version": int(provider_payload.get("metadata_policy_version") or 0),
        }
    )
    return job


def _build_publish_task_groups(jobs: list[dict]) -> list[dict]:
    groups: dict[str, dict] = {}
    for job in jobs:
        if not job.get("output_is_active", True):
            continue
        task_id = str(job.get("task_id") or "unknown-task")
        group = groups.setdefault(
            task_id,
            {
                "task_id": task_id,
                "task_name": job.get("task_name") or "未命名任务",
                "task_source_file_name": job.get("task_source_file_name") or "未记录原视频文件名",
                "task_created_at": job.get("task_created_at") or job.get("created_at") or "",
                "task_created_at_display": job.get("task_created_at_display") or "未知时间",
                "jobs": [],
            },
        )
        group["jobs"].append(job)

    def job_sort_key(job: dict) -> tuple[str, str, int, str, str]:
        return (
            str(job.get("output_clip_created_at") or job.get("created_at") or ""),
            str(job.get("output_file_name") or ""),
            0 if job.get("platform") == "douyin" else 1,
            str(job.get("created_at") or ""),
            str(job.get("id") or ""),
        )

    for group in groups.values():
        group["jobs"].sort(key=job_sort_key)

    return sorted(
        groups.values(),
        key=lambda group: (str(group.get("task_created_at") or ""), str(group.get("task_id") or "")),
        reverse=True,
    )


def list_platform_configs() -> list[dict]:
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM publish_platform_configs ORDER BY CASE platform WHEN 'douyin' THEN 1 WHEN 'bilibili' THEN 2 ELSE 9 END"
        ).fetchall()
    return [_normalize_config(row) for row in rows]


def _get_platform_config_record(platform: str) -> dict | None:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM publish_platform_configs WHERE platform = ?",
            (platform,),
        ).fetchone()
    return dict(row) if row else None


def get_platform_config(platform: str) -> dict | None:
    row = _get_platform_config_record(platform)
    return _normalize_config(row) if row else None


def update_platform_config(platform: str, payload: PublishPlatformConfigUpdate) -> dict:
    existing = _get_platform_config_record(platform)
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
    config = _get_platform_config_record(platform)
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


def _unique_normal_account_id(connection, platform: str) -> str:
    rows = connection.execute(
        "SELECT id, login_status FROM publish_accounts WHERE platform = ? ORDER BY created_at, id",
        (platform,),
    ).fetchall()
    if len(rows) != 1 or str(rows[0]["login_status"] or "") != "normal":
        return ""
    return str(rows[0]["id"] or "")


def _get_account_record(account_id: str) -> dict | None:
    if not account_id:
        return None
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM publish_accounts WHERE id = ?", (account_id,)).fetchone()
    return dict(row) if row else None


def get_account(account_id: str) -> dict | None:
    row = _get_account_record(account_id)
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
                auth_type, login_status, login_message, scopes, remark, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                "oauth" if auth_status == "authorized" else "browser_profile",
                "normal" if auth_status == "authorized" else "login_required",
                "已保存平台 OAuth 授权" if auth_status == "authorized" else "请打开独立 Chrome 完成登录",
                (payload.scopes or "").strip(),
                (payload.remark or "").strip(),
                now,
                now,
            ),
        )
        connection.commit()
    return {"status": "ok", "message": "发布账号已保存。", "account": get_account(account_id)}


def check_browser_account(account_id: str) -> dict:
    account = get_account(account_id)
    if not account:
        raise ValueError("发布账号不存在")
    from app.services.publish_repository import PublishRepository
    from app.services.publishers.worker_client import PublishWorkerClient

    result = PublishWorkerClient().check_account(account["platform"], account_id)
    worker_status = str(result.get("login_status") or "").lower()
    normal = worker_status == "normal"
    previous_login = str(account.get("login_status") or "") == "normal" or bool(account.get("last_login_at"))
    if normal:
        stored_status = "normal"
    elif worker_status in {"busy", "login_pending"}:
        stored_status = worker_status
    else:
        stored_status = "invalid" if previous_login else "login_required"
    PublishRepository().update_account_status(
        account_id,
        stored_status,
        str(result.get("message") or ""),
        logged_in=normal,
    )
    return {"status": "ok", "account": get_account(account_id), "worker_result": result}


def start_browser_account_login(account_id: str) -> dict:
    account = get_account(account_id)
    if not account:
        raise ValueError("发布账号不存在")
    from app.services.publish_repository import PublishRepository
    from app.services.publishers.worker_client import PublishWorkerClient

    result = PublishWorkerClient().start_login(account["platform"], account_id)
    message = str(result.get("message") or "已打开登录窗口")
    PublishRepository().update_account_status(account_id, "login_pending", message)
    return {"status": "started", "message": message, "account": get_account(account_id)}


def open_browser_creator_center(account_id: str) -> dict:
    account = get_account(account_id)
    if not account:
        raise ValueError("发布账号不存在")
    from app.services.publishers.worker_client import PublishWorkerClient

    result = PublishWorkerClient().open_creator_center(account["platform"], account_id)
    return {"status": "started", "message": result.get("message") or "已打开创作者中心", "account": account}


def build_douyin_oauth_url() -> dict:
    config = _get_platform_config_record("douyin")
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

    config = _get_platform_config_record("douyin")
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
    return create_account(payload)


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
                clip_candidates.cover_time_seconds AS ai_cover_time_seconds,
                subtitle_jobs.status AS subtitle_status,
                subtitle_jobs.output_file_path AS subtitled_output_file_path,
                subtitle_jobs.revision_id AS subtitle_revision_id,
                subtitle_jobs.validation_status AS subtitle_validation_status,
                subtitle_jobs.verified_at AS subtitle_verified_at,
                subtitle_revisions.status AS subtitle_revision_status
            FROM output_clip
            JOIN tasks ON tasks.id = output_clip.task_id
            LEFT JOIN clip_candidates ON clip_candidates.id = output_clip.clip_candidate_id
            LEFT JOIN subtitle_jobs ON subtitle_jobs.output_clip_id = output_clip.id AND subtitle_jobs.is_active = 1
            LEFT JOIN subtitle_revisions ON subtitle_revisions.id = subtitle_jobs.revision_id
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
                clip_candidates.cover_time_seconds AS ai_cover_time_seconds,
                subtitle_jobs.status AS subtitle_status,
                subtitle_jobs.output_file_path AS subtitled_output_file_path,
                subtitle_jobs.revision_id AS subtitle_revision_id,
                subtitle_jobs.validation_status AS subtitle_validation_status,
                subtitle_jobs.verified_at AS subtitle_verified_at,
                subtitle_revisions.status AS subtitle_revision_status
            FROM output_clip
            JOIN tasks ON tasks.id = output_clip.task_id
            LEFT JOIN clip_candidates ON clip_candidates.id = output_clip.clip_candidate_id
            LEFT JOIN subtitle_jobs ON subtitle_jobs.output_clip_id = output_clip.id AND subtitle_jobs.is_active = 1
            LEFT JOIN subtitle_revisions ON subtitle_revisions.id = subtitle_jobs.revision_id
            WHERE output_clip.id = ? AND output_clip.is_active = 1
            """,
            (output_clip_id,),
        ).fetchone()
    return _row_to_dict(row)


def _list_completed_publish_clips(task_id: str | None = None) -> list[dict]:
    where_task = " AND tasks.id = ?" if task_id else ""
    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT
                tasks.id AS task_id,
                tasks.platform AS task_platform,
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
                clip_candidates.cover_time_seconds AS ai_cover_time_seconds,
                subtitle_jobs.status AS subtitle_status,
                subtitle_jobs.output_file_path AS subtitled_output_file_path,
                subtitle_jobs.revision_id AS subtitle_revision_id,
                subtitle_jobs.validation_status AS subtitle_validation_status,
                subtitle_jobs.verified_at AS subtitle_verified_at,
                subtitle_revisions.status AS subtitle_revision_status
            FROM output_clip
            JOIN tasks ON tasks.id = output_clip.task_id
            LEFT JOIN clip_candidates ON clip_candidates.id = output_clip.clip_candidate_id
            LEFT JOIN subtitle_jobs ON subtitle_jobs.output_clip_id = output_clip.id AND subtitle_jobs.is_active = 1
            LEFT JOIN subtitle_revisions ON subtitle_revisions.id = subtitle_jobs.revision_id
            WHERE tasks.is_deleted = 0 AND output_clip.status = 'completed' AND output_clip.is_active = 1
              {where_task}
            ORDER BY output_clip.created_at DESC
            """,
            (task_id,) if task_id else (),
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
        if not _subtitle_publish_ready(output_clip) or not raw_path:
            raise ValueError("带字幕成片尚未同时通过 revision 审核和 FFprobe 验证，不能选择。")
    else:
        raw_path = (output_clip.get("output_file_path") or "").strip()
        if output_clip.get("output_status") != "completed" or not raw_path:
            raise ValueError("这条原始切片还没有生成完成，不能发布。")

    resolved_path = resolve_video_file_path(raw_path) or Path(raw_path)
    if not resolved_path.exists() or not resolved_path.is_file():
        raise ValueError(f"视频文件不存在，不能创建发布任务：{raw_path}")
    return raw_path, resolved_path


def _default_title_for_clip(item: dict, *, platform: str = "douyin") -> str:
    output_name = item.get("output_file_name") or "直播切片"
    max_length = DOUYIN_TITLE_MAX if platform == "douyin" else BILIBILI_TITLE_MAX
    return _truncate(
        item.get("clip_title") or Path(output_name).stem or item.get("task_name") or "直播切片",
        max_length,
    )


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
    tags = [tag for tag in _keyword_candidates(text) if 2 <= len(tag) <= 3]
    if "康熙" in text and "康熙" not in tags:
        tags.append("康熙")
    for default_tag in DOUYIN_FALLBACK_TAGS:
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


def _compose_description(item: dict, title: str, tags: str, *, platform: str = "douyin") -> str:
    summary = (item.get("clip_summary") or item.get("highlight_reason") or "").strip()
    if summary:
        return _sanitize_publish_description(
            summary,
            platform=platform,
            generated=platform == "douyin",
            title=title,
        )
    tag_text = " ".join(f"#{tag.strip()}" for tag in tags.split(",") if tag.strip())
    return _sanitize_publish_description(
        f"{title}\n{tag_text}".strip(),
        platform=platform,
        generated=platform == "douyin",
        title=title,
    )


def _metadata_prompt(item: dict, platform: str = "douyin") -> str:
    if platform == "douyin":
        instructions = (
            "请只为抖音生成文案。标题目标 18～26 字、绝不能超过 30 字；"
            "tags 必须为 4～6 个互不重复的话题词，每个严格 2～3 字，人物名可以与标题重合；"
            "简介必须为 15～35 字，只保留一个最强冲突、笑点或悬念，"
            "不要使用‘现场爆笑’‘引发热议’‘不容错过’等模板化结尾。"
        )
        json_example = (
            '{"title":"18到26字的中文标题","tags":["话题","人物","笑点","反转"],'
            '"description":"15到35字、只有一个核心钩子的简介"}。'
        )
    else:
        instructions = "请为 B站生成文案。标题不超过 80 字，简介不超过 180 字，话题简洁准确。"
        json_example = (
            '{"title":"不超过80字的中文标题","tags":["话题1","话题2","话题3"],'
            '"description":"不超过180字的简介"}。'
        )
    return (
        f"请根据下面的直播切片信息生成发布文案。{instructions}"
        "只输出 JSON，不要 Markdown。"
        "tags 必须是真正的平台 #话题关键词，不是标题解释，返回时不要带 #。"
        "标题、话题、简介都要主动规避低俗脏话、排泄词、死亡血腥、暴力恐怖、色情、赌博博彩、诈骗引流、绝对化夸张等平台高风险表达；"
        f"遇到类似表达请换成温和说法。JSON 格式：{json_example}"
        "\n\n"
        f"任务：{item.get('task_name') or ''}\n"
        f"原标题：{item.get('clip_title') or ''}\n"
        f"摘要：{item.get('clip_summary') or ''}\n"
        f"推荐理由：{item.get('highlight_reason') or ''}\n"
        f"传播价值：{item.get('spread_value') or ''}\n"
        f"剪辑建议：{item.get('suggested_editing') or ''}\n"
    )


def generate_publish_metadata(item: dict, use_ai: bool = False, *, platform: str = "douyin") -> dict:
    fallback_title = _sanitize_publish_title(
        _default_title_for_clip(item, platform=platform),
        platform=platform,
        generated=platform == "douyin",
    )
    fallback_tags = (
        format_douyin_tags(_fallback_tags(item), generated=True)
        if platform == "douyin"
        else (_format_tags(_fallback_tags(item)) or _format_tags(SAFE_TOPIC_FALLBACKS))
    )
    fallback_description = _compose_description(item, fallback_title, fallback_tags, platform=platform)
    metadata = {
        "title": fallback_title,
        "tags": fallback_tags,
        "description": fallback_description,
        "source": "rule",
        "error": "",
        "policy_version": PUBLISH_COPY_RULE_VERSION if platform == "douyin" else 0,
    }
    if not use_ai:
        return metadata

    try:
        provider = build_provider(settings.ai_publish_provider, purpose="publish")
        parsed = loads_ai_json(generate_json_with_safe_retry(provider, _metadata_prompt(item, platform)))
        if not isinstance(parsed, dict):
            raise ValueError("AI 文案响应必须是 JSON 对象")
        provider_name = getattr(provider, "name", settings.ai_publish_provider)
        publish_model = (
            settings.ai_codex_model
            if provider_name == "codex"
            else settings.ai_local_model
            if provider_name == "local"
            else settings.ai_publish_remote_model
        )
        safe_content = _sanitize_publish_content(
            parsed.get("title") or fallback_title,
            parsed.get("tags") or fallback_tags,
            parsed.get("description") or fallback_description,
            title_fallback=fallback_title,
            description_fallback=fallback_description,
            platform=platform,
            generated=True,
            validate=platform == "douyin",
        )
        return {
            "title": safe_content["title"],
            "tags": safe_content["tags"],
            "description": safe_content["description"],
            "source": f"ai:{provider_name}-publish:{publish_model}",
            "error": "",
            "policy_version": PUBLISH_COPY_RULE_VERSION if platform == "douyin" else 0,
        }
    except (AIProviderError, json.JSONDecodeError, TypeError, ValueError) as exc:
        metadata["error"] = exc.checkpoint_message() if isinstance(exc, AIProviderError) else str(exc)
        return metadata


def _find_opencli_job(output_clip_id: str, platform: str) -> dict | None:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT * FROM publish_jobs
            WHERE output_clip_id = ? AND platform = ? AND publish_mode = 'opencli_publish'
              AND status NOT IN ('PUBLISHED', 'EXPORTED', 'CANCELLED')
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (output_clip_id, platform),
        ).fetchone()
    return _normalize_job(row) if row else None


def _find_active_publish_job(output_clip_id: str, platform: str) -> dict | None:
    """查找任意执行方式的有效任务，避免刷新队列改变用户已选择的执行方式。"""
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT * FROM publish_jobs
            WHERE output_clip_id = ? AND platform = ?
              AND status IN ('DRAFT', 'WAITING', 'SCHEDULED', 'PUBLISHING', 'NEED_REVIEW')
            ORDER BY COALESCE(NULLIF(updated_at, ''), created_at) DESC
            LIMIT 1
            """,
            (output_clip_id, platform),
        ).fetchone()
    return _normalize_job(row) if row else None


def _find_latest_publish_job(output_clip_id: str, platform: str) -> dict | None:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT * FROM publish_jobs
            WHERE output_clip_id = ? AND platform = ?
            ORDER BY COALESCE(NULLIF(updated_at, ''), created_at) DESC, created_at DESC, id DESC
            LIMIT 1
            """,
            (output_clip_id, platform),
        ).fetchone()
    return _normalize_job(row) if row else None


def _is_user_removed_job(job: dict | None) -> bool:
    return bool(
        job
        and str(job.get("status") or "").upper() == PUBLISH_STATUS_CANCELLED
        and str(job.get("error_code") or "") == USER_REMOVED_ERROR_CODE
    )


def _restore_removed_publish_job_for_sync(job: dict) -> dict:
    from app.services.publish_repository import PublishRepository

    active_statuses = (
        PUBLISH_STATUS_DRAFT,
        PUBLISH_STATUS_WAITING,
        PUBLISH_STATUS_SCHEDULED,
        PUBLISH_STATUS_PUBLISHING,
        PUBLISH_STATUS_NEED_REVIEW,
    )
    now = _now_iso()
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        duplicate = connection.execute(
            f"""
            SELECT id FROM publish_jobs
            WHERE id <> ? AND output_clip_id = ? AND platform = ?
              AND status IN ({','.join('?' for _ in active_statuses)})
            ORDER BY COALESCE(NULLIF(updated_at, ''), created_at) DESC
            LIMIT 1
            """,
            (
                job["id"],
                job.get("output_clip_id"),
                job.get("platform"),
                *active_statuses,
            ),
        ).fetchone()
        if duplicate:
            raise ValueError("同一裁剪片段在当前平台已有有效发布内容，不能重复恢复")
        cursor = connection.execute(
            """
            UPDATE publish_jobs
            SET status = 'WAITING', scheduled_at = '', next_attempt_at = NULL,
                finished_at = NULL, error_code = '', error_message = '', last_error = '',
                needs_manual_review = 0, execution_phase = '', updated_at = ?
            WHERE id = ? AND status = 'CANCELLED' AND error_code = ?
            """,
            (now, job["id"], USER_REMOVED_ERROR_CODE),
        )
        if not cursor.rowcount:
            raise ValueError("发布内容状态已经变化，请刷新后重试")
        PublishRepository().add_event(
            job["id"],
            "restored_to_preparation",
            from_status=PUBLISH_STATUS_CANCELLED,
            to_status=PUBLISH_STATUS_WAITING,
            message="任务级同步将当前平台内容重新加入内容准备",
            payload={
                "platform": job.get("platform") or "",
                "output_clip_id": job.get("output_clip_id") or "",
            },
            connection=connection,
        )
        connection.commit()
    return get_publish_job(job["id"])


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


def _batch_find_publish_jobs(output_clip_ids: list[str]) -> dict[str, dict[str, dict]]:
    """返回每个切片、每个平台最新的一条发布任务，不限定执行方式。"""
    if not output_clip_ids:
        return {}
    placeholders = ",".join("?" for _ in output_clip_ids)
    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT * FROM publish_jobs
            WHERE output_clip_id IN ({placeholders})
            ORDER BY COALESCE(NULLIF(updated_at, ''), created_at) DESC, created_at DESC
            """,
            output_clip_ids,
        ).fetchall()
    result: dict[str, dict[str, dict]] = {}
    for row in rows:
        job = _normalize_job(row)
        output_id = str(job.get("output_clip_id") or "")
        platform = str(job.get("platform") or "")
        if output_id and platform:
            result.setdefault(output_id, {}).setdefault(platform, job)
    return result


def _publish_provider_payload(
    metadata: dict,
    cover: dict | None = None,
    *,
    existing: dict | None = None,
    upgrade_status: str = "generated",
) -> str:
    cover = cover or {}
    payload = dict(existing or {})
    if metadata:
        payload.update(
            {
                "metadata_source": metadata.get("source", ""),
                "metadata_error": metadata.get("error", ""),
                "metadata_policy_version": int(metadata.get("policy_version") or PUBLISH_COPY_RULE_VERSION),
                "metadata_upgrade_status": upgrade_status,
            }
        )
    payload.update(
        {
            "cover_source": payload.get("cover_source") or ("auto_frame" if cover.get("cover_file_path") else ""),
            "cover_error": cover.get("cover_error", payload.get("cover_error", "")),
        }
    )
    return json.dumps(payload, ensure_ascii=False)


def _insert_opencli_job(
    item: dict,
    platform: str,
    metadata: dict,
    cover: dict | None = None,
    *,
    video_source: str = "original",
    inherited: dict | None = None,
) -> dict:
    inherited = inherited or {}
    raw_video_path, _ = _resolve_publish_video_path(
        {
            **item,
            "output_status": item.get("output_status") or "completed",
            "subtitle_status": item.get("subtitle_status"),
            "subtitled_output_file_path": item.get("subtitled_output_file_path"),
            "subtitle_revision_id": item.get("subtitle_revision_id"),
            "subtitle_revision_status": item.get("subtitle_revision_status"),
            "subtitle_validation_status": item.get("subtitle_validation_status"),
            "subtitle_verified_at": item.get("subtitle_verified_at"),
        },
        video_source,
    )
    job_id = uuid4().hex[:12]
    now = _now_iso()
    cover = cover or {}
    cover_file_path = str(cover.get("cover_file_path") or "")
    cover_mode = "time" if cover_file_path else "auto"
    cover_time_seconds = float(cover.get("cover_time_seconds") or 0)
    publish_mode = str(inherited.get("publish_mode") or settings.publish_default_mode)
    if publish_mode not in PUBLISH_MODES:
        publish_mode = settings.publish_default_mode
    with get_connection() as connection:
        inherited_account_id = str(inherited.get("account_id") or "")
        account = connection.execute(
            """
            SELECT id FROM publish_accounts
            WHERE platform = ? AND login_status = 'normal'
              AND (? = '' OR id = ?)
            ORDER BY COALESCE(last_login_at, updated_at) DESC LIMIT 1
            """,
            (platform, inherited_account_id, inherited_account_id),
        ).fetchone()
        if not account and inherited_account_id:
            account = connection.execute(
                "SELECT id FROM publish_accounts WHERE id = ? AND platform = ?",
                (inherited_account_id, platform),
            ).fetchone()
        account_id = str(account["id"] or "") if account else ""
        if not account_id:
            account_id = _unique_normal_account_id(connection, platform)
        title = str(inherited.get("title") or metadata["title"])
        description = str(inherited.get("description") or inherited.get("caption") or metadata["description"])
        tags = str(inherited.get("tags") or inherited.get("hashtags") or metadata["tags"])
        connection.execute(
            """
            INSERT INTO publish_jobs (
                id, task_id, output_clip_id, clip_id, account_id, platform, publish_mode,
                video_source, video_file_path, video_path, title, description, caption, tags, hashtags, visibility,
                cover_mode, cover_time_seconds, allow_download, bilibili_tid,
                bilibili_copyright, bilibili_source, cover_file_path, scheduled_at,
                schedule_timezone, timezone, status, audit_status, provider_response,
                max_attempts, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, '', ?, ?, 'WAITING', 'not_submitted', ?, ?, ?, ?)
            """,
            (
                job_id,
                item["task_id"],
                item["output_clip_id"],
                item["output_clip_id"],
                account_id or None,
                platform,
                publish_mode,
                video_source,
                raw_video_path,
                raw_video_path,
                title,
                description,
                description,
                tags,
                tags,
                str(inherited.get("visibility") or "public"),
                cover_mode,
                cover_time_seconds,
                1 if inherited.get("allow_download", True) else 0,
                str(inherited.get("bilibili_tid") or DEFAULT_BILIBILI_TID),
                str(inherited.get("bilibili_copyright") or "original"),
                str(inherited.get("bilibili_source") or ""),
                cover_file_path,
                settings.app_timezone,
                settings.app_timezone,
                _publish_provider_payload(
                    metadata,
                    cover,
                    existing=_subtitle_publish_evidence(item, video_source),
                ),
                settings.publish_scheduler_max_retry_count,
                now,
                now,
            ),
        )
        connection.commit()
    return get_publish_job(job_id)


def refresh_send_queue(use_ai: bool = False, platform: str | None = None) -> dict:
    if platform and platform not in PLATFORM_LABELS:
        raise ValueError("只支持补充抖音或 B站发送任务")
    target_platforms = [platform] if platform else list(AUTO_PUBLISH_PLATFORMS)
    created: list[dict] = []
    updated_covers = 0
    skipped = 0
    skipped_removed = 0
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

        for target_platform in target_platforms:
            existing_job = _find_active_publish_job(item["output_clip_id"], target_platform)
            if existing_job:
                skipped += 1
                if existing_job.get("publish_mode") == "opencli_publish" and not existing_job.get("cover_file_path"):
                    cover = ensure_cover_for_item()
                    if cover.get("cover_file_path"):
                        _update_job_cover(existing_job["id"], cover)
                        updated_covers += 1
                continue
            if _is_user_removed_job(_find_latest_publish_job(item["output_clip_id"], target_platform)):
                skipped_removed += 1
                continue
            try:
                if item_metadata is None:
                    item_metadata = generate_publish_metadata(item, use_ai=use_ai, platform=target_platform)
                created.append(_insert_opencli_job(item, target_platform, item_metadata, ensure_cover_for_item()))
            except Exception as exc:
                errors.append(
                    f"{item.get('output_file_name') or item.get('output_clip_id')} / "
                    f"{PLATFORM_LABELS[target_platform]}：{exc}"
                )
                if item_metadata is None:
                    item_metadata = {}
    return {
        "status": "ok" if not errors else "partial",
        "message": (
            f"已新增 {len(created)} 条发送任务，自动选择 {len(created) + updated_covers} 张封面帧，"
            f"跳过 {skipped} 条已存在任务、{skipped_removed} 条手动移除内容，{len(errors)} 条需要处理。"
        ),
        "created": created,
        "skipped_removed": skipped_removed,
        "errors": errors,
        **get_publish_center_context(),
    }


def _task_target_platforms(task_platform: str | None) -> list[str]:
    del task_platform
    return list(AUTO_PUBLISH_PLATFORMS)


def get_publish_link_states(task_ids: list[str]) -> dict[str, dict]:
    normalized_ids = list(dict.fromkeys(str(task_id).strip() for task_id in task_ids if str(task_id).strip()))
    if not normalized_ids:
        return {}
    placeholders = ",".join("?" for _ in normalized_ids)
    with get_connection() as connection:
        tasks = connection.execute(
            f"SELECT id, platform FROM tasks WHERE id IN ({placeholders})",
            normalized_ids,
        ).fetchall()
        outputs = connection.execute(
            f"""
            SELECT id, task_id
            FROM output_clip
            WHERE task_id IN ({placeholders}) AND is_active = 1 AND status = 'completed'
            """,
            normalized_ids,
        ).fetchall()
        jobs = connection.execute(
            f"""
            SELECT publish_jobs.id, publish_jobs.task_id, publish_jobs.output_clip_id,
                   publish_jobs.platform, publish_jobs.status, publish_jobs.error_code,
                   publish_jobs.updated_at, publish_jobs.created_at,
                   output_clip.is_active AS output_is_active
            FROM publish_jobs
            LEFT JOIN output_clip ON output_clip.id = publish_jobs.output_clip_id
            WHERE publish_jobs.task_id IN ({placeholders})
            ORDER BY COALESCE(NULLIF(publish_jobs.updated_at, ''), publish_jobs.created_at) DESC,
                     publish_jobs.created_at DESC, publish_jobs.id DESC
            """,
            normalized_ids,
        ).fetchall()

    task_platforms = {row["id"]: _task_target_platforms(row["platform"]) for row in tasks}
    active_outputs: dict[str, list[str]] = {task_id: [] for task_id in normalized_ids}
    for row in outputs:
        active_outputs.setdefault(row["task_id"], []).append(row["id"])

    latest: dict[tuple[str, str], dict] = {}
    stale_counts = {task_id: 0 for task_id in normalized_ids}
    for raw in jobs:
        job = dict(raw)
        if not bool(job.get("output_is_active")) and _normalize_publish_status(job.get("status")) in ACTIVE_PREPARATION_STATUSES:
            stale_counts[job["task_id"]] = stale_counts.get(job["task_id"], 0) + 1
        key = (str(job.get("output_clip_id") or ""), str(job.get("platform") or ""))
        latest.setdefault(key, job)

    states: dict[str, dict] = {}
    for task_id in task_platforms:
        platforms = task_platforms[task_id]
        output_ids = active_outputs.get(task_id, [])
        per_platform = {}
        linked_total = removed_total = missing_total = 0
        per_output: dict[str, dict[str, str]] = {}
        for platform in platforms:
            linked = removed = missing = 0
            for output_id in output_ids:
                job = latest.get((output_id, platform))
                status = _normalize_publish_status(job.get("status")) if job else ""
                error_code = str(job.get("error_code") or "") if job else ""
                if job and status != PUBLISH_STATUS_CANCELLED:
                    linked += 1
                    output_state = "已关联"
                elif job and error_code == USER_REMOVED_ERROR_CODE:
                    removed += 1
                    missing += 1
                    output_state = "已移出"
                else:
                    missing += 1
                    output_state = "待同步"
                per_output.setdefault(output_id, {})[platform] = output_state
            per_platform[platform] = {
                "label": PLATFORM_LABELS[platform],
                "expected": len(output_ids),
                "linked": linked,
                "removed": removed,
                "missing": missing,
            }
            linked_total += linked
            removed_total += removed
            missing_total += missing
        expected = len(output_ids) * len(platforms)
        stale = stale_counts.get(task_id, 0)
        if not output_ids:
            state = "not_ready"
            label = "等待生成切片"
        elif missing_total:
            state = "needs_sync"
            label = f"待同步 {missing_total} 条"
        elif stale:
            state = "attention"
            label = f"已关联 {linked_total}/{expected}，存在旧版记录"
        else:
            state = "linked"
            label = f"已关联 {linked_total}/{expected}"
        states[task_id] = {
            "task_id": task_id,
            "state": state,
            "label": label,
            "active_clip_count": len(output_ids),
            "expected_count": expected,
            "linked_count": linked_total,
            "missing_count": missing_total,
            "removed_count": removed_total,
            "stale_pending_count": stale,
            "platforms": per_platform,
            "per_output": per_output,
        }
    return states


def get_task_publish_link_state(task_id: str) -> dict:
    state = get_publish_link_states([task_id]).get(task_id)
    if state is None:
        raise ValueError("任务不存在")
    return state


def _find_inheritable_publish_job(item: dict, platform: str) -> dict:
    clip_candidate_id = str(item.get("clip_candidate_id") or "")
    if not clip_candidate_id:
        return {}
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT publish_jobs.*
            FROM publish_jobs
            JOIN output_clip ON output_clip.id = publish_jobs.output_clip_id
            WHERE publish_jobs.task_id = ? AND output_clip.clip_candidate_id = ?
              AND publish_jobs.platform = ? AND publish_jobs.output_clip_id != ?
            ORDER BY COALESCE(NULLIF(publish_jobs.updated_at, ''), publish_jobs.created_at) DESC,
                     publish_jobs.created_at DESC, publish_jobs.id DESC
            LIMIT 1
            """,
            (item["task_id"], clip_candidate_id, platform, item["output_clip_id"]),
        ).fetchone()
    return dict(row) if row else {}


def _preferred_video_source(item: dict, prefer_subtitled: bool) -> str:
    raw_path = str(item.get("subtitled_output_file_path") or "").strip()
    path = resolve_video_file_path(raw_path) if raw_path else None
    if prefer_subtitled and _subtitle_publish_ready(item) and path and path.exists():
        return "subtitled"
    return "original"


def _subtitle_publish_ready(item: dict) -> bool:
    return bool(
        item.get("subtitle_status") == "completed"
        and item.get("subtitle_validation_status") == "verified"
        and item.get("subtitle_revision_status") == "approved"
        and item.get("subtitle_revision_id")
    )


def _subtitle_publish_evidence(item: dict, video_source: str) -> dict:
    if video_source != "subtitled":
        return {}
    if not _subtitle_publish_ready(item):
        raise ValueError("字幕成片缺少审核或验证证据")
    return {
        "subtitle_delivery_mode": "subtitled",
        "subtitle_revision_id": item.get("subtitle_revision_id") or "",
        "subtitle_revision_status": item.get("subtitle_revision_status") or "",
        "subtitle_validation_status": item.get("subtitle_validation_status") or "",
        "subtitle_verified_at": item.get("subtitle_verified_at") or "",
    }


def _update_preparation_video_source(job: dict, item: dict, video_source: str) -> dict:
    from app.services.publish_repository import PublishRepository

    status = _normalize_publish_status(job.get("status"))
    if status not in {PUBLISH_STATUS_DRAFT, PUBLISH_STATUS_WAITING}:
        raise ValueError("只有未排期的内容准备记录可以更换视频版本")
    raw_video_path, _ = _resolve_publish_video_path(item, video_source)
    try:
        cover = _generate_default_publish_cover(item, video_source)
    except Exception as exc:
        cover = {"cover_error": str(exc)}
    cover_file_path = str(cover.get("cover_file_path") or "")
    cover_mode = "time" if cover_file_path else "auto"
    cover_time_seconds = float(cover.get("cover_time_seconds") or 0)
    now = _now_iso()
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        cursor = connection.execute(
            """
            UPDATE publish_jobs
            SET video_source = ?, video_file_path = ?, video_path = ?,
                cover_mode = ?, cover_time_seconds = ?, cover_file_path = ?,
                provider_response = ?, updated_at = ?
            WHERE id = ? AND status IN ('DRAFT', 'WAITING')
            """,
            (
                video_source,
                raw_video_path,
                raw_video_path,
                cover_mode,
                cover_time_seconds,
                cover_file_path,
                _publish_provider_payload(
                    {},
                    cover,
                    existing={
                        **(job.get("provider_payload") or {}),
                        **_subtitle_publish_evidence(item, video_source),
                    },
                ),
                now,
                job["id"],
            ),
        )
        if not cursor.rowcount:
            raise ValueError("发布内容状态已经变化，请刷新后重试")
        PublishRepository().add_event(
            job["id"],
            "video_source_updated",
            from_status=status,
            to_status=status,
            message="字幕工作台同步时改用带字幕成片，并重新生成候选封面",
            payload={
                "task_id": item["task_id"],
                "output_clip_id": item["output_clip_id"],
                "video_source": video_source,
            },
            connection=connection,
        )
        connection.commit()
    return get_publish_job(job["id"])


def _supersede_stale_publish_jobs(task_id: str) -> int:
    from app.services.publish_repository import PublishRepository

    repository = PublishRepository()
    now = _now_iso()
    affected = 0
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        rows = connection.execute(
            """
            SELECT publish_jobs.*
            FROM publish_jobs
            JOIN output_clip ON output_clip.id = publish_jobs.output_clip_id
            WHERE publish_jobs.task_id = ? AND output_clip.is_active = 0
              AND publish_jobs.platform = 'douyin'
              AND publish_jobs.status IN ('DRAFT', 'WAITING', 'SCHEDULED')
            """,
            (task_id,),
        ).fetchall()
        for raw in rows:
            job = dict(raw)
            cursor = connection.execute(
                """
                UPDATE publish_jobs
                SET status = 'CANCELLED', scheduled_at = '', next_attempt_at = NULL,
                    finished_at = ?, error_code = ?, error_message = '',
                    last_error = '', needs_manual_review = 0, execution_phase = '', updated_at = ?
                WHERE id = ? AND status = ?
                """,
                (now, SUPERSEDED_BY_RECUT_ERROR_CODE, now, job["id"], job["status"]),
            )
            if not cursor.rowcount:
                continue
            affected += 1
            repository.add_event(
                job["id"],
                "superseded_by_recut",
                from_status=_normalize_publish_status(job["status"]),
                to_status=PUBLISH_STATUS_CANCELLED,
                error_code=SUPERSEDED_BY_RECUT_ERROR_CODE,
                message="旧切片发布内容已被当前激活切片版本替代",
                payload={"task_id": task_id, "output_clip_id": job.get("output_clip_id") or ""},
                connection=connection,
            )
        connection.commit()
    return affected


def sync_task_publish_jobs(
    task_id: str,
    *,
    prefer_subtitled: bool = True,
    restore_removed: bool = True,
) -> dict:
    with get_connection() as connection:
        task = connection.execute(
            "SELECT id, platform, auto_mode, status FROM tasks WHERE id = ? AND COALESCE(is_deleted, 0) = 0",
            (task_id,),
        ).fetchone()
    if not task:
        raise ValueError("任务不存在")
    if bool(task["auto_mode"]) and task["status"] == TaskStatus.PENDING_SUBTITLE_REVIEW.value:
        raise ValueError("自动流水线正在等待字幕审核，请先批量烧录，或明确跳过字幕并完成片段审核")
    items = _list_completed_publish_clips(task_id)
    if not items:
        raise ValueError("当前任务还没有可同步的激活切片")

    superseded_count = _supersede_stale_publish_jobs(task_id)
    created: list[dict] = []
    restored: list[dict] = []
    updated: list[dict] = []
    skipped = 0
    errors: list[str] = []
    warnings: list[str] = []
    platforms = _task_target_platforms(task["platform"])

    for item in items:
        item_metadata: dict | None = None
        item_covers: dict[str, dict] = {}
        for platform in platforms:
            latest = _find_latest_publish_job(item["output_clip_id"], platform)
            latest_status = _normalize_publish_status(latest.get("status")) if latest else ""
            if latest and latest_status != PUBLISH_STATUS_CANCELLED:
                video_source = _preferred_video_source(item, prefer_subtitled)
                if video_source == "subtitled" and latest.get("video_source") != "subtitled":
                    if latest_status in {PUBLISH_STATUS_DRAFT, PUBLISH_STATUS_WAITING}:
                        try:
                            updated.append(_update_preparation_video_source(latest, item, video_source))
                        except Exception as exc:
                            errors.append(
                                f"{item.get('output_file_name') or item['output_clip_id']} / "
                                f"{PLATFORM_LABELS[platform]}：{exc}"
                            )
                    elif latest_status == PUBLISH_STATUS_SCHEDULED:
                        warnings.append(
                            f"{item.get('output_file_name') or item['output_clip_id']} / "
                            f"{PLATFORM_LABELS[platform]} 已排期，未静默更换为带字幕版本；请先取消排期。"
                        )
                skipped += 1
                continue
            if _is_user_removed_job(latest):
                if restore_removed:
                    try:
                        restored_job = _restore_removed_publish_job_for_sync(latest)
                        video_source = _preferred_video_source(item, prefer_subtitled)
                        if video_source == "subtitled" and restored_job.get("video_source") != "subtitled":
                            restored_job = _update_preparation_video_source(restored_job, item, video_source)
                        restored.append(restored_job)
                    except Exception as exc:
                        errors.append(f"{item.get('output_file_name') or item['output_clip_id']} / {PLATFORM_LABELS[platform]}：{exc}")
                else:
                    skipped += 1
                continue
            try:
                video_source = _preferred_video_source(item, prefer_subtitled)
                if item_metadata is None:
                    item_metadata = generate_publish_metadata(item, use_ai=False, platform=platform)
                if video_source not in item_covers:
                    try:
                        item_covers[video_source] = _generate_default_publish_cover(item, video_source)
                    except Exception as exc:
                        item_covers[video_source] = {"cover_error": str(exc)}
                inherited = _find_inheritable_publish_job(item, platform)
                created.append(
                    _insert_opencli_job(
                        item,
                        platform,
                        item_metadata,
                        item_covers[video_source],
                        video_source=video_source,
                        inherited=inherited,
                    )
                )
            except Exception as exc:
                errors.append(f"{item.get('output_file_name') or item['output_clip_id']} / {PLATFORM_LABELS[platform]}：{exc}")

    link_state = get_task_publish_link_state(task_id)
    from app.services.task_log_service import append_task_log

    append_task_log(
        task_id,
        (
            f"同步发送中心：新增 {len(created)} 条，恢复 {len(restored)} 条，更新视频 {len(updated)} 条，"
            f"旧版失效 {superseded_count} 条，跳过 {skipped} 条，失败 {len(errors)} 条"
        ),
    )
    return {
        "status": "partial" if errors else "ok",
        "message": (
            f"发送中心同步完成：新增 {len(created)} 条、恢复 {len(restored)} 条、"
            f"更新视频 {len(updated)} 条、旧版安全转入历史 {superseded_count} 条，"
            f"失败 {len(errors)} 条、提示 {len(warnings)} 条。"
        ),
        "created_count": len(created),
        "restored_count": len(restored),
        "updated_count": len(updated),
        "superseded_count": superseded_count,
        "skipped_count": skipped,
        "errors": errors,
        "warnings": warnings,
        "jobs": [*created, *restored, *updated],
        "link_state": link_state,
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
    if duration <= 0:
        return 0
    return max(0, min(duration - 0.001, duration / 2))


def _resolve_cover_time_seconds(duration: float, preferred_time_seconds: Any = None) -> tuple[float, str]:
    fallback = _default_cover_time_seconds(duration)
    try:
        preferred = float(preferred_time_seconds)
    except (TypeError, ValueError):
        return fallback, "midpoint_fallback"
    if not math.isfinite(preferred) or preferred < 0 or duration <= 0 or preferred >= duration:
        return fallback, "midpoint_fallback"
    return round(preferred, 3), "ai_frame"


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


def generate_publish_cover_for_item(
    item: dict,
    preferred_time_seconds: Any = None,
    video_source: str = "original",
) -> dict:
    output_clip_id = str(item.get("output_clip_id") or item.get("id") or "").strip()
    if not output_clip_id:
        raise ValueError("封面生成失败：缺少切片编号。")
    _, video_path = _resolve_publish_video_path(
        {
            **item,
            "output_status": item.get("output_status") or item.get("status") or "completed",
            "subtitle_status": item.get("subtitle_status"),
            "subtitled_output_file_path": item.get("subtitled_output_file_path"),
        },
        video_source,
    )
    duration = _get_video_duration_seconds(video_path)
    seconds, cover_source = _resolve_cover_time_seconds(duration, preferred_time_seconds)
    cover_path = _unique_frame_cover_path(item["task_id"], output_clip_id, video_source, seconds)
    _write_plain_cover_frame(video_path, cover_path, seconds)
    return {
        **_cover_frame_payload(item["task_id"], cover_path, seconds),
        "cover_source": cover_source,
    }


def _generate_default_publish_cover(item: dict, video_source: str = "original") -> dict:
    return generate_publish_cover_for_item(item, preferred_time_seconds=None, video_source=video_source)


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


def _list_missing_publish_cover_jobs(platform: str | None = None) -> list[dict]:
    normalized_platform = str(platform or "").strip().lower()
    if normalized_platform and normalized_platform not in PLATFORM_LABELS:
        raise ValueError("暂不支持这个发布平台。")
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                publish_jobs.id,
                publish_jobs.task_id,
                publish_jobs.output_clip_id,
                publish_jobs.video_source,
                publish_jobs.title,
                publish_jobs.provider_response,
                output_clip.output_file_path,
                output_clip.output_file_name,
                output_clip.status AS output_status,
                clip_candidates.cover_time_seconds AS ai_cover_time_seconds,
                subtitle_jobs.status AS subtitle_status,
                subtitle_jobs.output_file_path AS subtitled_output_file_path,
                subtitle_jobs.revision_id AS subtitle_revision_id,
                subtitle_jobs.validation_status AS subtitle_validation_status,
                subtitle_jobs.verified_at AS subtitle_verified_at,
                subtitle_revisions.status AS subtitle_revision_status
            FROM publish_jobs
            JOIN output_clip ON output_clip.id = publish_jobs.output_clip_id
            JOIN tasks ON tasks.id = publish_jobs.task_id
            LEFT JOIN clip_candidates ON clip_candidates.id = output_clip.clip_candidate_id
            LEFT JOIN subtitle_jobs
              ON subtitle_jobs.output_clip_id = output_clip.id
             AND subtitle_jobs.is_active = 1
            LEFT JOIN subtitle_revisions ON subtitle_revisions.id = subtitle_jobs.revision_id
            WHERE publish_jobs.status IN ('DRAFT', 'WAITING', 'SCHEDULED')
              AND TRIM(COALESCE(publish_jobs.cover_file_path, '')) = ''
              AND output_clip.is_active = 1
              AND tasks.is_deleted = 0
              AND (? = '' OR publish_jobs.platform = ?)
            ORDER BY output_clip.created_at ASC, publish_jobs.created_at ASC
            """,
            (normalized_platform, normalized_platform),
        ).fetchall()
    return [dict(row) for row in rows]


def _find_reusable_publish_cover(output_clip_id: str, video_source: str) -> dict | None:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT task_id, cover_file_path, cover_time_seconds
            FROM publish_jobs
            WHERE output_clip_id = ?
              AND video_source = ?
              AND TRIM(COALESCE(cover_file_path, '')) <> ''
            ORDER BY updated_at DESC, created_at DESC
            """,
            (output_clip_id, video_source),
        ).fetchall()
    for row in rows:
        cover_path = Path(str(row["cover_file_path"] or "").strip())
        if cover_path.is_file():
            return {
                "cover_file_path": str(cover_path),
                "cover_media_url": _cover_media_url(str(row["task_id"] or ""), str(cover_path)),
                "cover_time_seconds": float(row["cover_time_seconds"] or 0),
                "cover_source": "existing_clip_cover",
            }
    return None


def _apply_cover_to_missing_jobs(jobs: list[dict], cover: dict) -> list[str]:
    updated_ids: list[str] = []
    now = _now_iso()
    with get_connection() as connection:
        for job in jobs:
            provider_response = _parse_json_text(job.get("provider_response"))
            provider_response.update(
                {
                    "cover_source": cover.get("cover_source") or "midpoint_fallback",
                    "cover_time_seconds": float(cover.get("cover_time_seconds") or 0),
                }
            )
            cursor = connection.execute(
                """
                UPDATE publish_jobs
                SET cover_mode = 'time',
                    cover_time_seconds = ?,
                    cover_file_path = ?,
                    provider_response = ?,
                    updated_at = ?
                WHERE id = ?
                  AND status IN ('DRAFT', 'WAITING', 'SCHEDULED')
                  AND TRIM(COALESCE(cover_file_path, '')) = ''
                """,
                (
                    float(cover.get("cover_time_seconds") or 0),
                    str(cover.get("cover_file_path") or ""),
                    json.dumps(provider_response, ensure_ascii=False),
                    now,
                    job["id"],
                ),
            )
            if cursor.rowcount:
                updated_ids.append(job["id"])
        connection.commit()
    return updated_ids


def backfill_missing_publish_covers(platform: str | None = None) -> dict:
    missing_jobs = _list_missing_publish_cover_jobs(platform)
    grouped_jobs: dict[tuple[str, str], list[dict]] = {}
    for job in missing_jobs:
        key = (
            str(job.get("output_clip_id") or ""),
            str(job.get("video_source") or "original"),
        )
        grouped_jobs.setdefault(key, []).append(job)

    generated_cover_count = 0
    reused_cover_count = 0
    updated_ids: list[str] = []
    errors: list[dict] = []
    for (output_clip_id, video_source), jobs in grouped_jobs.items():
        item = jobs[0]
        try:
            cover = _find_reusable_publish_cover(output_clip_id, video_source)
            if cover:
                reused_cover_count += 1
            else:
                cover = generate_publish_cover_for_item(
                    item,
                    preferred_time_seconds=item.get("ai_cover_time_seconds"),
                    video_source=video_source,
                )
                generated_cover_count += 1
            updated_ids.extend(_apply_cover_to_missing_jobs(jobs, cover))
        except Exception as exc:
            errors.append(
                {
                    "output_clip_id": output_clip_id,
                    "output_file_name": item.get("output_file_name") or item.get("title") or output_clip_id,
                    "message": str(exc),
                }
            )

    updated_jobs = [job for job_id in updated_ids if (job := get_publish_job(job_id))]
    status = "partial" if errors else "ok"
    if not missing_jobs:
        message = "当前没有需要补充封面的未发布任务。"
    else:
        message = (
            f"已生成 {generated_cover_count} 张新封面、复用 {reused_cover_count} 张已有封面，"
            f"补齐 {len(updated_ids)} 条发布任务，{len(errors)} 条切片处理失败。"
        )
    return {
        "status": status,
        "message": message,
        "generated_cover_count": generated_cover_count,
        "reused_cover_count": reused_cover_count,
        "updated_job_count": len(updated_ids),
        "failed_clip_count": len(errors),
        "errors": errors,
        "jobs": updated_jobs,
    }


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
    account = _get_account_record(payload.account_id or "")
    if not account:
        raise ValueError("真实发布必须先选择一个已配置的发布账号。")
    if account.get("platform") != payload.platform:
        raise ValueError("账号平台和发布平台不一致。")
    config = _get_platform_config_record(payload.platform)
    if not config:
        raise ValueError("发布平台配置不存在。")
    _get_provider(payload.platform, config).validate_config()
    return config, account


def create_publish_job(payload: PublishJobCreate) -> dict:
    output_clip = _get_output_clip_for_publish(payload.task_id, payload.output_clip_id)
    if not output_clip:
        raise ValueError("切片记录不存在。")
    raw_video_path, resolved_video_path = _resolve_publish_video_path(output_clip, payload.video_source)
    safe_content = _sanitize_publish_content(
        payload.title,
        payload.tags,
        payload.description,
        title_fallback="精彩片段",
        platform=payload.platform,
        validate=payload.platform == "douyin" and payload.publish_mode == "local_browser",
    )
    cover_file_path = (payload.cover_file_path or "").strip()
    cover_time_seconds = float(payload.cover_time_seconds or 0)
    cover_mode = payload.cover_mode
    existing = _find_active_publish_job(payload.output_clip_id, payload.platform)
    if existing and existing.get("publish_mode") == payload.publish_mode:
        return {"status": "exists", "message": "同一切片、平台和执行方式已有有效任务。", "job": existing}
    provider_payload = "真实发布任务已创建，等待执行。"
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
    elif payload.publish_mode == "local_browser":
        account = get_account(payload.account_id or "")
        if not account:
            raise ValueError("真实浏览器发布必须先选择一个发布账号")
        if account.get("platform") != payload.platform:
            raise ValueError("发布账号与目标平台不一致")

    from app.services.publish_time import ensure_future, to_utc_iso

    scheduled_at = ""
    if (payload.scheduled_at or "").strip():
        ensure_future(payload.scheduled_at, settings.app_timezone)
        scheduled_at = to_utc_iso(payload.scheduled_at, settings.app_timezone)

    job_id = uuid4().hex[:12]
    now = _now_iso()
    status = PUBLISH_STATUS_WAITING
    if scheduled_at:
        status = PUBLISH_STATUS_SCHEDULED
    if payload.publish_mode == "api_publish" and not (payload.scheduled_at or "").strip():
        status = PUBLISH_STATUS_WAITING
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO publish_jobs (
                id, task_id, output_clip_id, clip_id, account_id, platform, publish_mode,
                video_source, video_file_path, video_path, title, description, caption, tags, hashtags, visibility,
                cover_mode, cover_time_seconds, allow_download, bilibili_tid,
                bilibili_copyright, bilibili_source, cover_file_path, scheduled_at,
                schedule_timezone, timezone, status, audit_status, provider_response,
                max_attempts, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                payload.task_id,
                payload.output_clip_id,
                payload.output_clip_id,
                payload.account_id or "",
                payload.platform,
                payload.publish_mode,
                payload.video_source,
                raw_video_path,
                raw_video_path,
                safe_content["title"],
                safe_content["description"],
                safe_content["description"],
                safe_content["tags"],
                safe_content["tags"],
                payload.visibility,
                cover_mode,
                cover_time_seconds,
                1 if payload.allow_download else 0,
                (payload.bilibili_tid or "").strip(),
                payload.bilibili_copyright,
                (payload.bilibili_source or "").strip(),
                cover_file_path,
                scheduled_at,
                settings.app_timezone,
                settings.app_timezone,
                status,
                "not_submitted",
                provider_payload,
                settings.publish_scheduler_max_retry_count,
                now,
                now,
            ),
        )
        connection.commit()

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
                tasks.source_type AS task_source_type,
                tasks.original_video_path AS task_original_video_path,
                tasks.nas_file_path AS task_nas_file_path,
                tasks.created_at AS task_created_at,
                output_clip.output_file_name,
                output_clip.created_at AS output_clip_created_at,
                output_clip.is_active AS output_is_active,
                publish_accounts.account_name,
                publish_accounts.login_status AS account_login_status,
                publish_accounts.login_message AS account_login_message,
                (
                    SELECT ei.experiment_id
                    FROM content_improvement_experiment_items ei
                    WHERE ei.publish_job_id = publish_jobs.id
                    LIMIT 1
                ) AS content_experiment_id
            FROM publish_jobs
            LEFT JOIN tasks ON tasks.id = publish_jobs.task_id
            LEFT JOIN output_clip ON output_clip.id = publish_jobs.output_clip_id
            LEFT JOIN publish_accounts ON publish_accounts.id = publish_jobs.account_id
            WHERE publish_jobs.id = ?
            """,
            (job_id,),
        ).fetchone()
    return _normalize_job(row) if row else None


def list_publish_jobs(limit: int | None = 100, *, worker_state: dict | None = None) -> list[dict]:
    sql = """
        SELECT
            publish_jobs.*,
            tasks.task_name,
            tasks.source_type AS task_source_type,
            tasks.original_video_path AS task_original_video_path,
            tasks.nas_file_path AS task_nas_file_path,
            tasks.created_at AS task_created_at,
            output_clip.output_file_name,
            output_clip.created_at AS output_clip_created_at,
            output_clip.is_active AS output_is_active,
            publish_accounts.account_name,
            publish_accounts.login_status AS account_login_status,
            publish_accounts.login_message AS account_login_message,
            (
                SELECT ei.experiment_id
                FROM content_improvement_experiment_items ei
                WHERE ei.publish_job_id = publish_jobs.id
                LIMIT 1
            ) AS content_experiment_id
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
    accounts = list_accounts()
    if worker_state is None:
        from app.services.publish_scheduler import scheduler_health

        worker_state = scheduler_health()
    return [_normalize_job(row, accounts=accounts, worker_state=worker_state) for row in rows]


def _publish_history_anchor(job: dict) -> datetime | None:
    timezone_name = str(job.get("schedule_timezone") or job.get("timezone") or settings.app_timezone)
    for field in ("scheduled_at", "started_at", "finished_at", "created_at"):
        value = str(job.get(field) or "").strip()
        if not value:
            continue
        try:
            return parse_datetime(value, timezone_name).astimezone(app_zone(settings.app_timezone))
        except ValueError:
            continue
    return None


def _validate_history_platform(platform: str) -> str:
    normalized = str(platform or "").strip().lower()
    if normalized not in PLATFORM_LABELS:
        raise ValueError("执行记录平台只支持抖音或 B站")
    return normalized


def _validate_history_month(month: str) -> tuple[int, int]:
    matched = re.fullmatch(r"(\d{4})-(\d{2})", str(month or "").strip())
    if not matched:
        raise ValueError("月份格式必须为 YYYY-MM")
    year, month_number = int(matched.group(1)), int(matched.group(2))
    if not 1 <= month_number <= 12:
        raise ValueError("月份必须在 01 到 12 之间")
    return year, month_number


def _query_publish_history_jobs(
    *,
    platform: str,
    deleted: bool,
    status: str = "all",
    worker_state: dict | None = None,
) -> list[dict]:
    normalized_platform = _validate_history_platform(platform)
    normalized_status = str(status or "all").strip().upper()
    if normalized_status != "ALL" and normalized_status not in PUBLISH_HISTORY_STATUSES:
        raise ValueError("执行记录状态筛选无效")
    params: list[Any] = [normalized_platform, 1 if deleted else 0]
    status_clause = ""
    if normalized_status != "ALL":
        status_clause = " AND publish_jobs.status = ?"
        params.append(normalized_status)
    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT
                publish_jobs.*,
                tasks.task_name,
                tasks.source_type AS task_source_type,
                tasks.original_video_path AS task_original_video_path,
                tasks.nas_file_path AS task_nas_file_path,
                tasks.created_at AS task_created_at,
                output_clip.output_file_name,
                output_clip.created_at AS output_clip_created_at,
                publish_accounts.account_name,
                publish_accounts.login_status AS account_login_status,
                publish_accounts.login_message AS account_login_message
            FROM publish_jobs
            LEFT JOIN tasks ON tasks.id = publish_jobs.task_id
            LEFT JOIN output_clip ON output_clip.id = publish_jobs.output_clip_id
            LEFT JOIN publish_accounts ON publish_accounts.id = publish_jobs.account_id
            WHERE publish_jobs.platform = ?
              AND COALESCE(publish_jobs.history_hidden, 0) = ?
              AND publish_jobs.status IN ({",".join("?" for _ in PUBLISH_HISTORY_STATUSES)})
              {status_clause}
            """,
            [*params[:2], *sorted(PUBLISH_HISTORY_STATUSES), *params[2:]],
        ).fetchall()
    accounts = list_accounts()
    if worker_state is None:
        from app.services.publish_scheduler import scheduler_health

        worker_state = scheduler_health()
    jobs = [_normalize_job(row, accounts=accounts, worker_state=worker_state) for row in rows]
    for job in jobs:
        anchor = _publish_history_anchor(job)
        job["history_date"] = anchor.date().isoformat() if anchor else ""
        job["history_anchor_at"] = anchor.isoformat(timespec="seconds") if anchor else ""
    return jobs


def get_publish_history_calendar(platform: str, month: str) -> dict:
    normalized_platform = _validate_history_platform(platform)
    year, month_number = _validate_history_month(month)
    counts_template = {status: 0 for status in sorted(PUBLISH_HISTORY_STATUSES)}
    days: dict[str, dict[str, Any]] = {}
    for job in _query_publish_history_jobs(platform=normalized_platform, deleted=False):
        anchor = _publish_history_anchor(job)
        if not anchor or anchor.year != year or anchor.month != month_number:
            continue
        date_key = anchor.date().isoformat()
        item = days.setdefault(date_key, {"date": date_key, "total": 0, "counts": dict(counts_template)})
        status = str(job.get("status") or "").upper()
        item["total"] += 1
        if status in item["counts"]:
            item["counts"][status] += 1
    return {
        "platform": normalized_platform,
        "month": f"{year:04d}-{month_number:02d}",
        "timezone": settings.app_timezone,
        "days": [days[key] for key in sorted(days)],
    }


def list_publish_history_records(
    *,
    platform: str,
    date: str = "",
    status: str = "all",
    deleted: bool = False,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    normalized_platform = _validate_history_platform(platform)
    normalized_date = str(date or "").strip()
    if normalized_date:
        try:
            datetime.strptime(normalized_date, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError("日期格式必须为 YYYY-MM-DD") from exc
    current_page = max(1, int(page))
    size = min(50, max(1, int(page_size)))
    jobs = _query_publish_history_jobs(
        platform=normalized_platform,
        deleted=bool(deleted),
        status=status,
    )
    if normalized_date:
        jobs = [job for job in jobs if job.get("history_date") == normalized_date]
    jobs.sort(
        key=lambda job: (
            str(job.get("history_anchor_at") or ""),
            str(job.get("created_at") or ""),
            str(job.get("id") or ""),
        ),
        reverse=True,
    )
    total = len(jobs)
    total_pages = math.ceil(total / size) if total else 0
    if total_pages and current_page > total_pages:
        current_page = total_pages
    offset = (current_page - 1) * size
    return {
        "jobs": jobs[offset:offset + size],
        "pagination": {
            "page": current_page,
            "page_size": size,
            "total": total,
            "total_pages": total_pages,
        },
        "filters": {
            "platform": normalized_platform,
            "date": normalized_date,
            "status": str(status or "all").upper(),
            "deleted": bool(deleted),
        },
        "timezone": settings.app_timezone,
    }


def _update_publish_history_visibility(
    job_ids: list[str],
    *,
    platform: str,
    hidden: bool,
) -> dict:
    from app.services.publish_repository import PublishRepository

    normalized_platform = _validate_history_platform(platform)
    ids = list(dict.fromkeys(str(job_id).strip() for job_id in job_ids if str(job_id).strip()))
    if not ids:
        raise ValueError("至少选择一条执行记录")
    if len(ids) > 100:
        raise ValueError("每次最多处理 100 条执行记录")
    placeholders = ",".join("?" for _ in ids)
    repository = PublishRepository()
    now = _now_iso()
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        rows = connection.execute(
            f"SELECT * FROM publish_jobs WHERE id IN ({placeholders})",
            ids,
        ).fetchall()
        if len(rows) != len(ids):
            raise ValueError("部分执行记录不存在")
        jobs = [dict(row) for row in rows]
        if {str(job.get("platform") or "") for job in jobs} != {normalized_platform}:
            raise PublishPlatformIsolationBlocked("当前平台与所选执行记录不一致")
        if hidden:
            blocked = [
                job for job in jobs
                if str(job.get("status") or "").upper() not in PUBLISH_HISTORY_HIDEABLE_STATUSES
            ]
            if blocked:
                raise ValueError("只有已发布、发送失败、已导出或已取消的终态记录可以删除")
        target_value = 1 if hidden else 0
        target_time = now if hidden else None
        connection.execute(
            f"""
            UPDATE publish_jobs
            SET history_hidden = ?, history_hidden_at = ?
            WHERE id IN ({placeholders})
            """,
            (target_value, target_time, *ids),
        )
        for job in jobs:
            if bool(job.get("history_hidden")) == hidden:
                continue
            repository.add_event(
                str(job["id"]),
                "history_record_hidden" if hidden else "history_record_restored",
                from_status=str(job.get("status") or ""),
                to_status=str(job.get("status") or ""),
                message="用户从执行记录中安全删除该记录" if hidden else "用户恢复已删除的执行记录",
                payload={
                    "platform": normalized_platform,
                    "files_deleted": False,
                    "platform_item_deleted": False,
                },
                connection=connection,
            )
        connection.commit()
    return {
        "status": "ok",
        "affected_count": len(ids),
        "job_ids": ids,
        "message": (
            f"已安全删除 {len(ids)} 条执行记录；视频、执行明细和平台作品均已保留。"
            if hidden
            else f"已恢复 {len(ids)} 条执行记录。"
        ),
    }


def hide_publish_history_records(job_ids: list[str], *, platform: str) -> dict:
    return _update_publish_history_visibility(job_ids, platform=platform, hidden=True)


def restore_publish_history_records(job_ids: list[str], *, platform: str) -> dict:
    return _update_publish_history_visibility(job_ids, platform=platform, hidden=False)


def dismiss_publish_job(job_id: str) -> dict:
    from app.services.publish_repository import PublishRepository

    repository = PublishRepository()
    now = _now_iso()
    allowed_statuses = {PUBLISH_STATUS_DRAFT, PUBLISH_STATUS_WAITING, PUBLISH_STATUS_SCHEDULED}
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT * FROM publish_jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            raise ValueError("发布内容不存在")
        job = dict(row)
        source_status = _normalize_publish_status(job.get("status"))
        if source_status not in allowed_statuses:
            raise ValueError("只有草稿、等待或已排期内容可以移出内容准备")
        cursor = connection.execute(
            """
            UPDATE publish_jobs
            SET status = 'CANCELLED', scheduled_at = '', next_attempt_at = NULL,
                finished_at = ?, error_code = ?, error_message = '', last_error = '',
                needs_manual_review = 0, execution_phase = '', updated_at = ?
            WHERE id = ? AND status = ?
            """,
            (now, USER_REMOVED_ERROR_CODE, now, job_id, job.get("status")),
        )
        if not cursor.rowcount:
            raise ValueError("发布内容状态已经变化，请刷新后重试")
        repository.add_event(
            job_id,
            "removed_from_preparation",
            from_status=source_status,
            to_status=PUBLISH_STATUS_CANCELLED,
            error_code=USER_REMOVED_ERROR_CODE,
            message="用户从内容准备移除当前平台发布内容",
            payload={
                "platform": job.get("platform") or "",
                "output_clip_id": job.get("output_clip_id") or "",
                "files_deleted": False,
            },
            connection=connection,
        )
        connection.commit()
    return {
        "status": "ok",
        "message": "已从当前平台的内容准备中移出；原视频和裁剪文件均已保留。",
        "job": get_publish_job(job_id),
    }


def restore_publish_job(job_id: str) -> dict:
    from app.services.publish_repository import PublishRepository

    repository = PublishRepository()
    now = _now_iso()
    active_statuses = (
        PUBLISH_STATUS_DRAFT,
        PUBLISH_STATUS_WAITING,
        PUBLISH_STATUS_SCHEDULED,
        PUBLISH_STATUS_PUBLISHING,
        PUBLISH_STATUS_NEED_REVIEW,
    )
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT * FROM publish_jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            raise ValueError("发布内容不存在")
        job = dict(row)
        if (
            _normalize_publish_status(job.get("status")) != PUBLISH_STATUS_CANCELLED
            or str(job.get("error_code") or "") != USER_REMOVED_ERROR_CODE
        ):
            raise ValueError("只有从内容准备手动移出的记录可以恢复")
        duplicate = connection.execute(
            f"""
            SELECT id FROM publish_jobs
            WHERE id <> ? AND output_clip_id = ? AND platform = ?
              AND status IN ({','.join('?' for _ in active_statuses)})
            ORDER BY COALESCE(NULLIF(updated_at, ''), created_at) DESC
            LIMIT 1
            """,
            (job_id, job.get("output_clip_id"), job.get("platform"), *active_statuses),
        ).fetchone()
        if duplicate:
            raise ValueError("同一裁剪片段在当前平台已有有效发布内容，不能重复恢复")
        cursor = connection.execute(
            """
            UPDATE publish_jobs
            SET status = 'WAITING', scheduled_at = '', next_attempt_at = NULL,
                finished_at = NULL, error_code = '', error_message = '', last_error = '',
                needs_manual_review = 0, execution_phase = '', updated_at = ?
            WHERE id = ? AND status = 'CANCELLED' AND error_code = ?
            """,
            (now, job_id, USER_REMOVED_ERROR_CODE),
        )
        if not cursor.rowcount:
            raise ValueError("发布内容状态已经变化，请刷新后重试")
        repository.add_event(
            job_id,
            "restored_to_preparation",
            from_status=PUBLISH_STATUS_CANCELLED,
            to_status=PUBLISH_STATUS_WAITING,
            message="用户将当前平台发布内容重新加入内容准备",
            payload={
                "platform": job.get("platform") or "",
                "output_clip_id": job.get("output_clip_id") or "",
            },
            connection=connection,
        )
        connection.commit()
    return {
        "status": "ok",
        "message": "已重新加入当前平台的内容准备，请重新确认账号和排期。",
        "job": get_publish_job(job_id),
    }


def update_publish_job_status(job_id: str, status: str, error_message: str = "") -> dict:
    normalized_status = _normalize_publish_status(status)
    published_at = _now_iso() if normalized_status == PUBLISH_STATUS_PUBLISHED else None
    if not get_publish_job(job_id):
        raise ValueError("发布任务不存在。")
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE publish_jobs
            SET status = ?, error_message = ?, last_error = ?,
                published_at = COALESCE(?, published_at), updated_at = ?
            WHERE id = ?
            """,
            (normalized_status, error_message, error_message, published_at, _now_iso(), job_id),
        )
        connection.commit()
    return {"status": "ok", "message": "发布任务状态已更新。", "job": get_publish_job(job_id)}


def update_send_job(job_id: str, payload: PublishSendJobUpdate) -> dict:
    job = get_publish_job(job_id)
    if not job:
        raise ValueError("发送任务不存在。")
    if job.get("status") not in {PUBLISH_STATUS_DRAFT, PUBLISH_STATUS_WAITING, PUBLISH_STATUS_SCHEDULED}:
        raise ValueError("只有草稿、等待或已排期任务可以编辑；失败任务请先创建重试任务。")
    safe_content = _sanitize_publish_content(
        payload.title,
        payload.tags,
        payload.description,
        title_fallback=job.get("title") or "精彩片段",
        description_fallback=job.get("description") or "",
        platform=str(job.get("platform") or "douyin"),
        validate=str(job.get("platform") or "") == "douyin",
    )
    with get_connection() as connection:
        account_id = str(job.get("account_id") or "")
        if not account_id and str(job.get("publish_mode") or "") == "local_browser":
            account_id = _unique_normal_account_id(connection, str(job.get("platform") or ""))
        provider_response = _publish_provider_payload(
            {
                "source": "manual",
                "error": "",
                "policy_version": PUBLISH_COPY_RULE_VERSION,
            },
            {
                "cover_file_path": (payload.cover_file_path or "").strip(),
                "cover_error": (job.get("provider_payload") or {}).get("cover_error", ""),
            },
            existing=job.get("provider_payload") or {},
            upgrade_status="manual_saved",
        )
        cursor = connection.execute(
            """
            UPDATE publish_jobs
            SET title = ?, description = ?, caption = ?, tags = ?, hashtags = ?, visibility = ?,
                cover_file_path = ?, cover_time_seconds = ?, allow_download = ?,
                bilibili_tid = ?, bilibili_copyright = ?, bilibili_source = ?,
                account_id = ?, provider_response = ?, updated_at = ?
            WHERE id = ? AND status = ? AND updated_at = ?
            """,
            (
                safe_content["title"],
                safe_content["description"],
                safe_content["description"],
                safe_content["tags"],
                safe_content["tags"],
                payload.visibility,
                (payload.cover_file_path or "").strip(),
                float(payload.cover_time_seconds or 0),
                1 if payload.allow_download else 0,
                (payload.bilibili_tid or DEFAULT_BILIBILI_TID).strip(),
                payload.bilibili_copyright,
                (payload.bilibili_source or "").strip(),
                account_id or None,
                provider_response,
                _version_iso(),
                job_id,
                job.get("status"),
                job.get("updated_at"),
            ),
        )
        if not cursor.rowcount:
            connection.rollback()
            raise ValueError("发送任务内容或状态已经变化，请刷新后重试。")
        connection.commit()
    return {"status": "ok", "message": "发送内容已保存。", "job": get_publish_job(job_id)}


def update_publish_job_schedule(job_id: str, payload: PublishJobScheduleUpdate) -> dict:
    from app.services.publish_scheduler import PublishScheduler

    return PublishScheduler().update_schedule(job_id, payload.scheduled_at)


def update_publish_job_target(job_id: str, payload: PublishJobTargetUpdate) -> dict:
    job = get_publish_job(job_id)
    if not job:
        raise ValueError("发布任务不存在")
    if job.get("status") not in {PUBLISH_STATUS_DRAFT, PUBLISH_STATUS_WAITING, PUBLISH_STATUS_SCHEDULED}:
        raise ValueError("当前状态不能修改发布平台或账号")
    if payload.platform != job.get("platform"):
        raise PublishPlatformIsolationBlocked("任务平台创建后不可修改；请到对应平台任务中选择账号")
    if job.get("publish_mode") == "opencli_publish":
        raise PublishPlatformIsolationBlocked("旧版任务不能覆盖转换；请使用“转换并发送”保留原记录并创建新任务")
    account_id = (payload.account_id or "").strip()
    if payload.publish_mode == "local_browser":
        account = get_account(account_id)
        if not account:
            raise ValueError("真实浏览器发布必须选择账号")
        if account.get("platform") != payload.platform:
            raise ValueError("账号与目标平台不一致")
    try:
        with get_connection() as connection:
            cursor = connection.execute(
                """
                UPDATE publish_jobs SET platform = ?, account_id = ?, publish_mode = ?, updated_at = ?
                WHERE id = ? AND status = ? AND updated_at = ?
                """,
                (
                    payload.platform,
                    account_id or None,
                    payload.publish_mode,
                    _version_iso(),
                    job_id,
                    job.get("status"),
                    job.get("updated_at"),
                ),
            )
            if not cursor.rowcount:
                connection.rollback()
                raise ValueError("发布任务内容或状态已经变化，请刷新后重试")
            connection.commit()
    except Exception as exc:
        if "UNIQUE constraint" in str(exc):
            raise ValueError("同一切片在该平台已有未完成任务") from exc
        raise
    return {"status": "ok", "job": get_publish_job(job_id)}


def update_publish_jobs_target_batch(payload: PublishBatchTargetUpdate) -> dict:
    jobs = [get_publish_job(job_id) for job_id in payload.job_ids]
    if any(job is None for job in jobs):
        raise ValueError("部分发布任务不存在")
    platforms = {str(job.get("platform") or "") for job in jobs if job}
    if len(platforms) != 1 or payload.platform not in platforms:
        raise PublishPlatformIsolationBlocked("抖音和 B站任务不能混合操作，也不能批量改成另一个平台")
    updated = []
    single = PublishJobTargetUpdate(
        platform=payload.platform,
        account_id=payload.account_id,
        publish_mode=payload.publish_mode,
    )
    for job_id in payload.job_ids:
        updated.append(update_publish_job_target(job_id, single)["job"])
    return {"status": "ok", "updated_count": len(updated), "jobs": updated}


def update_publish_job_content(job_id: str, payload: PublishJobContentUpdate) -> dict:
    job = get_publish_job(job_id)
    if not job:
        raise ValueError("publish job not found")
    if job.get("status") not in {PUBLISH_STATUS_DRAFT, PUBLISH_STATUS_WAITING, PUBLISH_STATUS_SCHEDULED}:
        raise ValueError("当前状态不能直接编辑；失败任务请先创建重试任务，需复核任务请先人工确认。")

    safe_content = _sanitize_publish_content(
        payload.title,
        payload.hashtags,
        payload.caption,
        title_fallback=job.get("title") or "精彩片段",
        description_fallback=job.get("description") or "",
        platform=str(job.get("platform") or "douyin"),
        validate=str(job.get("platform") or "") == "douyin",
    )
    scheduled_at = (job.get("scheduled_at") or "").strip()
    if (payload.scheduled_at or "").strip():
        from app.services.publish_time import ensure_future, to_utc_iso

        ensure_future(payload.scheduled_at, settings.app_timezone)
        scheduled_at = to_utc_iso(payload.scheduled_at, settings.app_timezone)
    status = PUBLISH_STATUS_SCHEDULED if scheduled_at else PUBLISH_STATUS_WAITING
    now = _version_iso()
    with get_connection() as connection:
        account_id = str(job.get("account_id") or "")
        if not account_id and str(job.get("publish_mode") or "") == "local_browser":
            account_id = _unique_normal_account_id(connection, str(job.get("platform") or ""))
        provider_response = _publish_provider_payload(
            {"source": "manual", "error": "", "policy_version": PUBLISH_COPY_RULE_VERSION},
            existing=job.get("provider_payload") or {},
            upgrade_status="manual_saved",
        )
        cursor = connection.execute(
            """
            UPDATE publish_jobs
            SET title = ?, description = ?, caption = ?, tags = ?, hashtags = ?,
                cover_text = ?, scheduled_at = ?, status = ?, risk_flags = '',
                account_id = ?, provider_response = ?,
                error_code = '', error_message = '', last_error = '', updated_at = ?
            WHERE id = ? AND status = ? AND updated_at = ?
            """,
            (
                safe_content["title"],
                safe_content["description"],
                safe_content["description"],
                safe_content["tags"],
                safe_content["tags"],
                (payload.cover_text or "").strip(),
                scheduled_at,
                status,
                account_id or None,
                provider_response,
                now,
                job_id,
                job.get("status"),
                job.get("updated_at"),
            ),
        )
        if not cursor.rowcount:
            connection.rollback()
            raise ValueError("发布任务内容或状态已经变化，请刷新后重试")
        connection.commit()
    return {"status": "ok", "message": "publish content saved", "job": get_publish_job(job_id)}


def regenerate_send_job_metadata(job_id: str, use_ai: bool = True) -> dict:
    job = get_publish_job(job_id)
    if not job:
        raise ValueError("发送任务不存在。")
    item = _get_completed_publish_clip_by_output(job["output_clip_id"])
    if not item:
        raise ValueError("找不到这条发送任务对应的切片。")
    metadata = generate_publish_metadata(
        item,
        use_ai=use_ai,
        platform=str(job.get("platform") or "douyin"),
    )
    if use_ai and (metadata.get("error") or not str(metadata.get("source") or "").startswith("ai:")):
        raise ValueError(f"AI 文案生成失败，已保留原文：{metadata.get('error') or '没有返回有效文案'}")
    with get_connection() as connection:
        account_id = str(job.get("account_id") or "")
        if not account_id and str(job.get("publish_mode") or "") == "local_browser":
            account_id = _unique_normal_account_id(connection, str(job.get("platform") or ""))
        cursor = connection.execute(
            """
            UPDATE publish_jobs
            SET title = ?, description = ?, caption = ?, tags = ?, hashtags = ?,
                account_id = ?, provider_response = ?, updated_at = ?
            WHERE id = ? AND status = ? AND updated_at = ?
            """,
            (
                metadata["title"],
                metadata["description"],
                metadata["description"],
                metadata["tags"],
                metadata["tags"],
                account_id or None,
                _publish_provider_payload(
                    metadata,
                    {
                        "cover_file_path": job.get("cover_file_path") or "",
                        "cover_error": (job.get("provider_payload") or {}).get("cover_error", ""),
                    },
                    existing=job.get("provider_payload") or {},
                    upgrade_status="manual_retry" if use_ai else "rule_regenerated",
                ),
                _version_iso(),
                job_id,
                job.get("status"),
                job.get("updated_at"),
            ),
        )
        if not cursor.rowcount:
            connection.rollback()
            raise ValueError("发送任务内容或状态已经变化，请刷新后重新生成文案。")
        connection.commit()
    return {
        "status": "ok",
        "message": "AI 元数据已刷新。" if metadata["source"].startswith("ai:") else "已使用本地规则刷新标题和话题。",
        "metadata": metadata,
        "job": get_publish_job(job_id),
    }


def upgrade_pending_douyin_metadata() -> dict:
    if not _METADATA_UPGRADE_LOCK.acquire(blocking=False):
        return {
            "status": "running",
            "message": "抖音旧草稿文案正在升级，本次不重复执行。",
            "upgraded_count": 0,
            "failed_count": 0,
            "skipped_count": 0,
            "jobs": [],
            "errors": [],
            "backup_created": False,
        }
    try:
        return _upgrade_pending_douyin_metadata()
    finally:
        _METADATA_UPGRADE_LOCK.release()


def _upgrade_pending_douyin_metadata() -> dict:
    """幂等升级未排期抖音草稿；生成失败只记状态，不覆盖原文。"""

    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT publish_jobs.id, publish_jobs.provider_response, publish_jobs.updated_at
            FROM publish_jobs
            INNER JOIN output_clip ON output_clip.id = publish_jobs.output_clip_id
            INNER JOIN tasks ON tasks.id = publish_jobs.task_id
            WHERE publish_jobs.platform = 'douyin'
              AND UPPER(publish_jobs.status) IN ('DRAFT', 'WAITING')
              AND TRIM(COALESCE(publish_jobs.scheduled_at, '')) = ''
              AND COALESCE(output_clip.is_active, 1) = 1
              AND output_clip.status = 'completed'
              AND COALESCE(tasks.is_deleted, 0) = 0
            ORDER BY publish_jobs.created_at, publish_jobs.id
            """
        ).fetchall()

    candidate_rows = {
        str(row["id"]): str(row["updated_at"] or "")
        for row in rows
        if int(_parse_json_text(row["provider_response"]).get("metadata_policy_version") or 0)
        < PUBLISH_COPY_RULE_VERSION
    }
    candidate_ids = list(candidate_rows)
    if not candidate_ids:
        return {
            "status": "ok",
            "message": "未排期抖音草稿已是最新文案规则。",
            "upgraded_count": 0,
            "failed_count": 0,
            "skipped_count": len(rows),
            "jobs": [],
            "errors": [],
            "backup_created": False,
        }

    backup_path = create_publish_migration_backup(
        settings.database_path,
        settings.data_dir / "backups",
        cooldown=timedelta(0),
    )
    upgraded_ids: list[str] = []
    failed_ids: list[str] = []
    errors: list[dict[str, str]] = []
    runtime_skipped = 0
    for job_id in candidate_ids:
        job = get_publish_job(job_id)
        if not job:
            runtime_skipped += 1
            continue
        item = _get_completed_publish_clip_by_output(str(job.get("output_clip_id") or ""))
        metadata: dict = {}
        failure_message = ""
        if not item:
            runtime_skipped += 1
            continue
        else:
            try:
                metadata = generate_publish_metadata(item, use_ai=True, platform="douyin")
                if metadata.get("error") or not str(metadata.get("source") or "").startswith("ai:"):
                    failure_message = str(metadata.get("error") or "AI 没有返回有效文案")
            except Exception as exc:
                failure_message = str(exc)

        with get_connection() as connection:
            account_id = str(job.get("account_id") or "") or _unique_normal_account_id(connection, "douyin")
            if failure_message:
                failure_payload = dict(job.get("provider_payload") or {})
                failure_payload.update(
                    {
                        "metadata_policy_version": PUBLISH_COPY_RULE_VERSION,
                        "metadata_upgrade_status": "failed",
                        "metadata_error": failure_message[:500],
                    }
                )
                cursor = connection.execute(
                    """
                    UPDATE publish_jobs
                    SET account_id = ?, provider_response = ?, updated_at = ?
                    WHERE id = ? AND platform = 'douyin'
                      AND UPPER(status) IN ('DRAFT', 'WAITING')
                      AND TRIM(COALESCE(scheduled_at, '')) = ''
                      AND COALESCE(updated_at, '') = ?
                      AND EXISTS (
                          SELECT 1 FROM output_clip
                          WHERE output_clip.id = publish_jobs.output_clip_id
                            AND COALESCE(output_clip.is_active, 1) = 1
                            AND output_clip.status = 'completed'
                      )
                    """,
                    (
                        account_id or None,
                        json.dumps(failure_payload, ensure_ascii=False),
                        _now_iso(),
                        job_id,
                        candidate_rows[job_id],
                    ),
                )
                connection.commit()
                if not cursor.rowcount:
                    runtime_skipped += 1
                    continue
                failed_ids.append(job_id)
                errors.append({"job_id": job_id, "message": failure_message[:500]})
                continue

            provider_response = _publish_provider_payload(
                metadata,
                {
                    "cover_file_path": job.get("cover_file_path") or "",
                    "cover_error": (job.get("provider_payload") or {}).get("cover_error", ""),
                },
                existing=job.get("provider_payload") or {},
                upgrade_status="upgraded",
            )
            cursor = connection.execute(
                """
                UPDATE publish_jobs
                SET title = ?, description = ?, caption = ?, tags = ?, hashtags = ?,
                    account_id = ?, provider_response = ?, updated_at = ?
                WHERE id = ? AND platform = 'douyin'
                  AND UPPER(status) IN ('DRAFT', 'WAITING')
                  AND TRIM(COALESCE(scheduled_at, '')) = ''
                  AND COALESCE(updated_at, '') = ?
                  AND EXISTS (
                      SELECT 1 FROM output_clip
                      WHERE output_clip.id = publish_jobs.output_clip_id
                        AND COALESCE(output_clip.is_active, 1) = 1
                        AND output_clip.status = 'completed'
                  )
                """,
                (
                    metadata["title"],
                    metadata["description"],
                    metadata["description"],
                    metadata["tags"],
                    metadata["tags"],
                    account_id or None,
                    provider_response,
                    _now_iso(),
                    job_id,
                    candidate_rows[job_id],
                ),
            )
            connection.commit()
            if cursor.rowcount:
                upgraded_ids.append(job_id)
            else:
                runtime_skipped += 1

    return {
        "status": "ok" if not failed_ids else "partial",
        "message": (
            f"抖音旧草稿升级完成：成功 {len(upgraded_ids)} 条，失败 {len(failed_ids)} 条；"
            "失败项已保留原文，可稍后手动重试。"
        ),
        "upgraded_count": len(upgraded_ids),
        "failed_count": len(failed_ids),
        "skipped_count": len(rows) - len(candidate_ids) + runtime_skipped,
        "jobs": [job for job_id in upgraded_ids if (job := get_publish_job(job_id))],
        "errors": errors,
        "backup_created": backup_path is not None,
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


def _hashtags(tags: str, *, platform: str = "bilibili") -> str:
    normalized_tags = format_douyin_tags(tags) if platform == "douyin" else _format_tags(tags)
    return " ".join(f"#{tag.strip().lstrip('#')}" for tag in re.split(r"[,，]+", normalized_tags or "") if tag.strip())


def _douyin_description_for_job(job: dict, fallback_title: str) -> str:
    description = _sanitize_publish_description(
        job.get("description") or job.get("caption") or fallback_title,
        fallback_title,
        platform="douyin",
        title=fallback_title,
    )
    tag_text = _hashtags(job.get("tags") or job.get("hashtags") or "", platform="douyin")
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
        "const busyMarkers=['文件解析中','正在上传','上传中','视频处理中','正在处理','转码中','等待上传','请等待上传完成','上传过程中请不要删除','上传过程中请勿删除'];"
        "const explanatoryMarkers=['点击发布后','如作品还在上传中','上传发布完成','视频预览功能','实际播放时'];"
        "const statusTexts=[...document.querySelectorAll('span,div,p')].filter(visible).map(textOf).filter((text)=>text&&text.length<=40&&!explanatoryMarkers.some((item)=>text.includes(item)));"
        "const progress=[...document.querySelectorAll('span,div,p')].filter(visible).map(textOf).filter((text)=>/^\\d{1,3}%$/.test(text)).map((text)=>Number(text.slice(0,-1))).filter(Number.isFinite);"
        "const stillBusy=busyMarkers.some((item)=>statusTexts.some((text)=>text===item||text.startsWith(`${item}，`)||text.startsWith(`${item},`)||text.startsWith(`${item}：`)||text.startsWith(`${item}:`)||text.startsWith(`${item}...`)||text.startsWith(`${item}…`)))||progress.some((value)=>value<100);"
        "const badImage=(src)=>/logo|avatar|favicon|icon|douyin-creator-logo|static\\/image/i.test(src||'');"
        "const videos=[...document.querySelectorAll('video')].filter((el)=>visible(el)&&(el.videoWidth>0||el.readyState>=2||Number.isFinite(el.duration)));"
        "const canvases=[...document.querySelectorAll('canvas')].filter((el)=>{const rect=el.getBoundingClientRect();return visible(el)&&el.width>=160&&el.height>=90&&rect.width>=120&&rect.height>=80;});"
        "const images=[...document.querySelectorAll('img')].filter((el)=>{const rect=el.getBoundingClientRect();const src=el.currentSrc||el.src||'';return visible(el)&&!badImage(src)&&el.complete!==false&&el.naturalWidth>=240&&el.naturalHeight>=135&&rect.width>=120&&rect.height>=80;});"
        "const previewCount=videos.length+canvases.length+images.length;"
        "const previewReady=!stillBusy&&previewCount>0;"
        "if(!previewReady){throw new Error('douyin_preview_not_ready');}"
        "return {publish_ready:true,title_checked:true,description_checked:true,preview_checked:true,description_length:actualDescription.length,preview_count:previewCount};"
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
        "const started=Date.now();let lastTexts=[];while(Date.now()-started<45000){clickKnownTip();let found=findButton();if(found){found.el.scrollIntoView({block:'center',inline:'center'});await sleep(500);found=findButton()||found;found.el.click();await sleep(500);return {clicked:true,text:found.text,waited_ms:Date.now()-started};}lastTexts=[...document.querySelectorAll('button,[role=\"button\"],a')].filter(visible).map(textOf).filter(Boolean).slice(-20);window.scrollTo({top:document.documentElement.scrollHeight||document.body.scrollHeight,behavior:'instant'});await sleep(1000);}"
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
    safe_content = _sanitize_publish_content(
        job.get("title"),
        job.get("tags") or job.get("hashtags"),
        job.get("description") or job.get("caption"),
        platform="douyin",
        validate=True,
    )
    title = safe_content["title"]
    description = _douyin_description_for_job({**job, **safe_content}, title)
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
    title = _truncate(_sanitize_publish_title(job.get("title") or "直播切片"), BILIBILI_TITLE_MAX)
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
            SET status = 'FAILED', audit_status = 'not_submitted', error_code = ?,
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
    runner = runner or _default_command_runner

    try:
        commands = _opencli_commands_for_job(job)
    except Exception as exc:
        return {"status": "failed", "message": str(exc), "error_code": "prepare_failed", "job": job}

    outputs: list[dict[str, Any]] = []
    for index, command in enumerate(commands, start=1):
        attempt = 1
        while True:
            try:
                result = runner(command)
            except subprocess.TimeoutExpired:
                message = f"opencli 第 {index} 步超时：{_command_summary(command)}"
                return {"status": "failed", "message": message, "error_code": "opencli_timeout", "outputs": outputs, "job": job}
            except Exception as exc:
                message = f"opencli 第 {index} 步启动失败：{exc}"
                return {"status": "failed", "message": message, "error_code": "opencli_start_failed", "outputs": outputs, "job": job}

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
            return {"status": "failed", "message": message, "error_code": "opencli_failed", "outputs": outputs, "job": job}

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
    return {
        "status": "ok",
        "confirmed": True,
        "message": "opencli 已完成平台结果确认步骤。",
        "provider_response": response,
        "job": job,
    }


def retry_publish_job(job_id: str) -> dict:
    job = get_publish_job(job_id)
    if not job:
        raise ValueError("发布任务不存在。")
    from app.services.publish_scheduler import PublishScheduler

    return PublishScheduler().retry_failed(job_id)


def execute_api_publish_job(job_id: str) -> dict:
    job = get_publish_job(job_id)
    if not job:
        raise ValueError("发布任务不存在。")
    if job.get("publish_mode") != "api_publish":
        raise ValueError("只能执行 api_publish 任务。")
    output_clip = _get_output_clip_for_publish(job["task_id"], job["output_clip_id"])
    if not output_clip:
        raise ValueError("切片记录不存在。")
    _, video_path = _resolve_publish_video_path(output_clip, job["video_source"])
    config = _get_platform_config_record(job["platform"])
    account = _get_account_record(job.get("account_id") or "")
    if not config or not account:
        raise ValueError("平台配置或账号不存在。")
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
        return {
            "status": "failed",
            "message": exc.message,
            "error_code": exc.error_code,
            "provider_response": exc.response,
            "job": job,
        }

    return {
        "status": "ok",
        "message": "平台发布请求已提交。",
        "remote_video_id": result.item_id,
        "platform_upload_id": result.upload_id,
        "published_at": now,
        "provider_response": result.response or {},
        "job": job,
    }


def _get_provider(platform: str, config: dict):
    if platform == "douyin":
        return DouyinPublishProvider(config)
    if platform == "bilibili":
        return BilibiliPublishProvider(config)
    raise PublishProviderError("暂不支持这个发布平台。", "unsupported_platform")


def get_publish_center_context(*, focus_task_id: str = "") -> dict:
    publish_items = []
    queue_items = []
    raw_items = _list_completed_publish_clips()
    output_clip_ids = [item["output_clip_id"] for item in raw_items]
    publish_jobs_map = _batch_find_publish_jobs(output_clip_ids)
    for item in raw_items:
        original_path = resolve_video_file_path(item.get("output_file_path") or "")
        subtitled_path = resolve_video_file_path(item.get("subtitled_output_file_path") or "")
        default_title = _sanitize_publish_title(
            _default_title_for_clip(item, platform="douyin"),
            platform="douyin",
            generated=True,
        )
        original_available = bool(original_path and original_path.exists() and original_path.is_file())
        subtitled_available = bool(
            _subtitle_publish_ready(item)
            and subtitled_path
            and subtitled_path.exists()
            and subtitled_path.is_file()
        )
        normalized_item = {
            **item,
            "default_title": default_title,
            "default_tags": format_douyin_tags(_fallback_tags(item), generated=True),
            "original_available": original_available,
            "subtitled_available": subtitled_available,
            "subtitle_status_label": "已审核并验证" if subtitled_available else "字幕未就绪",
            "video_media_url": _video_media_url(item["task_id"], item["output_clip_id"], "original"),
        }
        publish_items.append(normalized_item)
        jobs_for_oc = publish_jobs_map.get(item["output_clip_id"], {})
        for platform in AUTO_PUBLISH_PLATFORMS:
            job = jobs_for_oc.get(platform)
            if job:
                queue_items.append(
                    {
                        **normalized_item,
                        "job": job,
                        "job_id": job["id"],
                        "platform": platform,
                        "platform_label": PLATFORM_LABELS[platform],
                        "title": _sanitize_publish_title(
                            job.get("title") or default_title,
                            default_title,
                            platform="douyin",
                        ),
                        "description": _sanitize_publish_description(
                            job.get("description") or "",
                            platform="douyin",
                        ),
                        "tags": _hashtags(
                            job.get("tags") or normalized_item["default_tags"],
                            platform="douyin",
                        ),
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
                        "title": _sanitize_publish_title(default_title, platform="douyin", generated=True),
                        "description": _compose_description(
                            item,
                            default_title,
                            normalized_item["default_tags"],
                            platform="douyin",
                        ),
                        "tags": _hashtags(normalized_item["default_tags"], platform="douyin"),
                        "status": "not_queued",
                        "status_label": "待入队",
                        "status_tone": "amber",
                        "cover_media_url": "",
                        "cover_file_path": "",
                        "error_message": "",
                        "platform_url": "",
                    }
                )

    from app.services.publish_scheduler import scheduler_health

    current_scheduler_health = scheduler_health()
    jobs = list_publish_jobs(
        limit=None if focus_task_id else 200,
        worker_state=current_scheduler_health,
    )
    jobs = [job for job in jobs if job.get("platform") == "douyin"]
    pending_jobs = [
        job for job in jobs
        if job.get("status") in {PUBLISH_STATUS_DRAFT, PUBLISH_STATUS_WAITING, PUBLISH_STATUS_FAILED, PUBLISH_STATUS_NEED_REVIEW}
    ]
    scheduled_jobs = sorted(
        [job for job in jobs if job.get("status") in {PUBLISH_STATUS_SCHEDULED, PUBLISH_STATUS_PUBLISHING}],
        key=lambda job: (job.get("scheduled_at") or "", job.get("created_at") or ""),
    )
    history_jobs = [
        job for job in jobs
        if job.get("status") in {PUBLISH_STATUS_PUBLISHED, PUBLISH_STATUS_EXPORTED, PUBLISH_STATUS_FAILED, PUBLISH_STATUS_CANCELLED}
    ]
    jobs_by_platform = {
        platform: [job for job in jobs if job["platform"] == platform]
        for platform in AUTO_PUBLISH_PLATFORMS
    }
    ready_count = sum(1 for job in jobs if job.get("status") in {PUBLISH_STATUS_SCHEDULED, PUBLISH_STATUS_WAITING})
    sending_count = sum(1 for job in jobs if job.get("status") == PUBLISH_STATUS_PUBLISHING)
    published_count = sum(1 for job in jobs if job.get("status") == PUBLISH_STATUS_PUBLISHED)
    failed_count = sum(1 for job in jobs if job.get("status") == PUBLISH_STATUS_FAILED)
    need_review_count = sum(1 for job in jobs if job.get("status") == PUBLISH_STATUS_NEED_REVIEW)
    missing_cover_counts = {
        platform: sum(
            1
            for job in jobs
            if job.get("platform") == platform
            and job.get("status") in {
                PUBLISH_STATUS_DRAFT,
                PUBLISH_STATUS_WAITING,
                PUBLISH_STATUS_SCHEDULED,
            }
            and job.get("output_is_active") is not False
            and not str(job.get("cover_file_path") or "").strip()
        )
        for platform in AUTO_PUBLISH_PLATFORMS
    }
    missing_cover_count = sum(missing_cover_counts.values())
    opencli_status = _opencli_status()
    from app.services.content_review_service import list_active_content_experiments_for_publish

    return {
        "publish_items": publish_items,
        "send_queue_items": queue_items,
        "publish_jobs": jobs,
        "publish_task_groups": _build_publish_task_groups(jobs),
        "pending_jobs": pending_jobs,
        "scheduled_jobs": scheduled_jobs,
        "history_jobs": history_jobs,
        "missing_cover_count": missing_cover_count,
        "missing_cover_counts": missing_cover_counts,
        "jobs_by_platform": jobs_by_platform,
        "platforms": [{"id": platform, "label": PLATFORM_LABELS[platform]} for platform in AUTO_PUBLISH_PLATFORMS],
        "accounts": list_accounts(),
        "content_experiments": list_active_content_experiments_for_publish(),
        "app_timezone": settings.app_timezone,
        "opencli_available": opencli_status["available"],
        "opencli_status": opencli_status,
        "scheduler_health": current_scheduler_health,
        "stats": [
            {"label": "需复核", "value": need_review_count, "tone": "amber"},
            {"label": "可入队切片", "value": len(publish_items), "tone": "green"},
            {"label": "待发送", "value": ready_count, "tone": "blue"},
            {"label": "发送中", "value": sending_count, "tone": "purple"},
            {"label": "已发布", "value": published_count, "tone": "green"},
            {"label": "发送失败", "value": failed_count, "tone": "red"},
        ],
    }
