"""Publisher 的稳定输入、输出与异常类型。"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from app.services.publish_time import utc_now_iso
from app.services.storage_service import resolve_video_file_path


class PublishOutcome(str, Enum):
    PUBLISHED = "PUBLISHED"
    EXPORTED = "EXPORTED"
    FAILED = "FAILED"
    NEED_REVIEW = "NEED_REVIEW"


class PublishError(RuntimeError):
    def __init__(
        self,
        message: str,
        error_code: str = "publish_failed",
        *,
        needs_manual_review: bool = False,
        safe_to_retry: bool = False,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.needs_manual_review = needs_manual_review
        self.safe_to_retry = safe_to_retry


class PublishValidationError(PublishError, ValueError):
    pass


class PublishWorkerUnavailable(PublishError):
    def __init__(
        self,
        message: str = "Windows 发布 Worker 当前不可用",
        *,
        request_may_have_been_received: bool = False,
    ) -> None:
        super().__init__(message, "publish_worker_unavailable", safe_to_retry=True)
        self.request_may_have_been_received = request_may_have_been_received


class PublishNeedsReview(PublishError):
    def __init__(self, message: str, error_code: str = "manual_review_required") -> None:
        super().__init__(message, error_code, needs_manual_review=True)


@dataclass(frozen=True)
class PublishResult:
    outcome: PublishOutcome
    message: str = ""
    remote_video_id: str = ""
    platform_url: str = ""
    published_at: str = ""
    provider_response: dict[str, Any] = field(default_factory=dict)
    error_code: str = ""
    needs_manual_review: bool = False

    @property
    def ok(self) -> bool:
        return self.outcome in {PublishOutcome.PUBLISHED, PublishOutcome.EXPORTED}

    @property
    def payload(self) -> dict[str, Any]:
        """兼容 v1.4 调用方。"""
        return self.provider_response

    def as_dict(self) -> dict[str, Any]:
        payload = sanitize_provider_response(asdict(self))
        payload["outcome"] = self.outcome.value
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PublishResult":
        raw_outcome = str(payload.get("outcome") or payload.get("status") or "FAILED").upper()
        aliases = {"SUCCESS": "PUBLISHED", "PUBLISHED": "PUBLISHED", "EXPORTED": "EXPORTED"}
        outcome = PublishOutcome(aliases.get(raw_outcome, raw_outcome))
        return cls(
            outcome=outcome,
            message=str(payload.get("message") or ""),
            remote_video_id=str(payload.get("remote_video_id") or ""),
            platform_url=str(payload.get("platform_url") or ""),
            published_at=str(payload.get("published_at") or ""),
            provider_response=sanitize_provider_response(payload.get("provider_response") or payload),
            error_code=str(payload.get("error_code") or ""),
            needs_manual_review=bool(payload.get("needs_manual_review")) or outcome == PublishOutcome.NEED_REVIEW,
        )


_SENSITIVE_KEY_PARTS = {
    "accesskey", "accesstoken", "apikey", "apisecret", "authorization", "authtoken",
    "bearer", "clientsecret",
    "cookie", "cookies", "credential", "credentials", "csrftoken", "password", "privatekey",
    "idtoken", "jwt", "refreshtoken", "secret", "secretkey", "sessiontoken", "storagestate",
    "token", "tokenvalue",
}


def _is_sensitive_key(key: Any) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
    return normalized in _SENSITIVE_KEY_PARTS or any(
        normalized.endswith(part) for part in _SENSITIVE_KEY_PARTS
    )


def _sanitize_sensitive_text(value: str) -> str:
    cleaned = re.sub(
        r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}",
        "Bearer [REDACTED]",
        value,
    )
    return re.sub(
        r"(?i)\b(authorization|access[_-]?token|refresh[_-]?token|api[_-]?key|client[_-]?secret|cookie)"
        r"\s*[:=]\s*['\"]?[^'\"\s,;}]+'?",
        r"\1=[REDACTED]",
        cleaned,
    )


def sanitize_provider_response(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]"
            if _is_sensitive_key(key)
            else sanitize_provider_response(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize_provider_response(item) for item in value]
    if isinstance(value, str):
        cleaned = _sanitize_sensitive_text(value)
        if len(cleaned) > 20000:
            return cleaned[:20000] + "…"
        return cleaned
    return value


def parse_json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except (json.JSONDecodeError, TypeError):
        return {"raw": str(value)}
    return parsed if isinstance(parsed, dict) else {"data": parsed}


def parse_public_json_dict(value: Any) -> dict[str, Any]:
    """解析供 API/UI 返回的 Provider JSON；损坏正文不回显。"""
    if isinstance(value, dict):
        return sanitize_provider_response(value)
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except (json.JSONDecodeError, TypeError):
        return {"invalid_payload": True}
    if not isinstance(parsed, dict):
        return {"invalid_payload": True}
    return sanitize_provider_response(parsed)


def job_video_path(job: dict[str, Any]) -> Path:
    raw_path = str(job.get("video_path") or job.get("video_file_path") or "").strip()
    if not raw_path:
        raise PublishValidationError("视频文件路径为空", "missing_video_path")
    resolved = resolve_video_file_path(raw_path) or Path(raw_path).expanduser()
    if not resolved.exists() or not resolved.is_file():
        raise PublishValidationError(f"视频文件不存在：{raw_path}", "video_not_found")
    if resolved.suffix.lower() not in {".mp4", ".mov", ".mkv", ".avi", ".flv", ".webm", ".m4v"}:
        raise PublishValidationError("不支持的视频格式", "unsupported_video_format")
    return resolved.resolve()


def job_caption(job: dict[str, Any]) -> str:
    return str(job.get("caption") or job.get("description") or "").strip()


def job_hashtags(job: dict[str, Any]) -> str:
    return str(job.get("hashtags") or job.get("tags") or "").strip()


def split_hashtags(value: str) -> list[str]:
    return [part for part in re.split(r"[,，\s#]+", str(value or "")) if part]


def job_cover_path(job: dict[str, Any], *, required: bool = False) -> Path | None:
    raw_path = str(job.get("cover_file_path") or "").strip()
    if not raw_path:
        if required:
            raise PublishValidationError("请选择或生成发布封面", "missing_cover")
        return None
    resolved = resolve_video_file_path(raw_path) or Path(raw_path).expanduser()
    if not resolved.exists() or not resolved.is_file():
        raise PublishValidationError(f"封面文件不存在：{raw_path}", "cover_not_found")
    if resolved.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
        raise PublishValidationError("封面必须是 JPG、PNG 或 WebP 图片", "unsupported_cover_format")
    return resolved.resolve()


class BasePublisher(ABC):
    name = "base"

    def validate(self, job: dict[str, Any]) -> None:
        job_video_path(job)
        if not str(job.get("title") or "").strip():
            raise PublishValidationError("标题不能为空", "missing_title")
        if not job_caption(job):
            raise PublishValidationError("正文或简介不能为空", "missing_caption")

    def build_payload(self, job: dict[str, Any]) -> dict[str, Any]:
        cover_path = job_cover_path(job)
        return {
            "job_id": str(job.get("id") or ""),
            "execution_id": str(job.get("execution_id") or ""),
            "task_id": str(job.get("task_id") or ""),
            "clip_id": str(job.get("clip_id") or job.get("output_clip_id") or ""),
            "platform": str(job.get("platform") or ""),
            "account_id": str(job.get("account_id") or ""),
            "scheduled_at": str(job.get("scheduled_at") or ""),
            "title": str(job.get("title") or "").strip(),
            "caption": job_caption(job),
            "hashtags": job_hashtags(job),
            "video_path": str(job_video_path(job)),
            "cover_file_path": str(cover_path or ""),
            "visibility": str(job.get("visibility") or "public"),
            "allow_download": bool(job.get("allow_download", True)),
            "bilibili_tid": str(job.get("bilibili_tid") or ""),
            "bilibili_copyright": str(job.get("bilibili_copyright") or "original"),
            "bilibili_source": str(job.get("bilibili_source") or ""),
            "publisher": self.name,
        }

    @abstractmethod
    def publish(self, job: dict[str, Any]) -> PublishResult:
        raise NotImplementedError


class BasePlatformPublisher(BasePublisher):
    platform = ""
    creator_url = ""

    def validate(self, job: dict[str, Any]) -> None:
        super().validate(job)
        if str(job.get("platform") or "").lower() != self.platform:
            raise PublishValidationError("Publisher 与任务平台不匹配", "platform_mismatch")
        if not str(job.get("account_id") or "").strip():
            raise PublishValidationError("请选择发布账号", "missing_account")

    @abstractmethod
    def check_login(self, account_id: str) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def open_login(self, account_id: str) -> dict[str, Any]:
        raise NotImplementedError

    def published_result(
        self,
        *,
        message: str,
        remote_video_id: str = "",
        platform_url: str = "",
        provider_response: dict[str, Any] | None = None,
    ) -> PublishResult:
        return PublishResult(
            outcome=PublishOutcome.PUBLISHED,
            message=message,
            remote_video_id=remote_video_id,
            platform_url=platform_url,
            published_at=utc_now_iso(),
            provider_response=provider_response or {},
        )
