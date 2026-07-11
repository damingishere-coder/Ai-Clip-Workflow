from __future__ import annotations

import json
import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.services.storage_service import resolve_video_file_path


class PublishValidationError(ValueError):
    def __init__(self, message: str, error_code: str = "validation_failed") -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code


@dataclass(frozen=True)
class PublishResult:
    ok: bool
    payload: dict[str, Any]
    remote_video_id: str = ""


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _parse_json_dict(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {"raw": value}
    return parsed if isinstance(parsed, dict) else {"data": parsed}


def _json_dump(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _text_dump(path: Path, value: str) -> None:
    path.write_text((value or "").strip() + "\n", encoding="utf-8")


def _job_clip_id(job: dict[str, Any]) -> str:
    return str(job.get("clip_id") or job.get("output_clip_id") or "unknown_clip").strip()


def _job_caption(job: dict[str, Any]) -> str:
    return str(job.get("caption") or job.get("description") or "").strip()


def _job_hashtags(job: dict[str, Any]) -> str:
    return str(job.get("hashtags") or job.get("tags") or "").strip()


def _job_cover_text(job: dict[str, Any]) -> str:
    provider_payload = _parse_json_dict(job.get("provider_response"))
    result_payload = _parse_json_dict(job.get("publish_result"))
    return str(
        job.get("cover_text")
        or provider_payload.get("cover_text")
        or result_payload.get("cover_text")
        or job.get("title")
        or ""
    ).strip()


def _job_video_path(job: dict[str, Any]) -> Path:
    raw_path = str(job.get("video_path") or job.get("video_file_path") or "").strip()
    if not raw_path:
        raise PublishValidationError("video_path is empty", "missing_video_path")
    resolved = resolve_video_file_path(raw_path) or Path(raw_path).expanduser()
    if not resolved.exists() or not resolved.is_file():
        raise PublishValidationError(f"video file does not exist: {raw_path}", "video_not_found")
    return resolved


class BasePublisher(ABC):
    name = "base"

    def validate(self, job: dict[str, Any]) -> None:
        _job_video_path(job)
        if not str(job.get("title") or "").strip():
            raise PublishValidationError("title is empty", "missing_title")
        if not _job_caption(job):
            raise PublishValidationError("caption is empty", "missing_caption")

    def build_payload(self, job: dict[str, Any]) -> dict[str, Any]:
        return {
            "job_id": job.get("id") or "",
            "task_id": job.get("task_id") or "",
            "clip_id": _job_clip_id(job),
            "platform": job.get("platform") or "",
            "account_id": job.get("account_id") or "",
            "scheduled_at": job.get("scheduled_at") or "",
            "title": str(job.get("title") or "").strip(),
            "caption": _job_caption(job),
            "hashtags": _job_hashtags(job),
            "cover_text": _job_cover_text(job),
            "video_path": str(_job_video_path(job)),
            "publisher": self.name,
        }

    @abstractmethod
    def publish(self, job: dict[str, Any]) -> PublishResult:
        raise NotImplementedError


class ManualExportPublisher(BasePublisher):
    name = "manual_export"

    def __init__(self, export_dir: Path | None = None) -> None:
        self.export_dir = Path(export_dir or settings.publish_scheduler_export_dir)

    def build_package_dir(self, job: dict[str, Any]) -> Path:
        return self.export_dir / str(job.get("task_id") or "unknown_task") / _job_clip_id(job)

    def publish(self, job: dict[str, Any]) -> PublishResult:
        self.validate(job)
        video_path = _job_video_path(job)
        package_dir = self.build_package_dir(job)
        package_dir.mkdir(parents=True, exist_ok=True)

        clip_path = package_dir / "clip.mp4"
        shutil.copy2(video_path, clip_path)

        payload = self.build_payload(job)
        payload.update(
            {
                "package_dir": str(package_dir),
                "clip_file": str(clip_path),
                "exported_at": _now_iso(),
            }
        )

        _text_dump(package_dir / "title.txt", payload["title"])
        _text_dump(package_dir / "caption.txt", payload["caption"])
        _text_dump(package_dir / "hashtags.txt", payload["hashtags"])
        _text_dump(package_dir / "cover_text.txt", payload["cover_text"])
        _json_dump(package_dir / "publish_plan.json", payload)
        _json_dump(
            package_dir / "metadata.json",
            {
                **payload,
                "source_video_name": video_path.name,
                "source_video_size_bytes": video_path.stat().st_size,
            },
        )

        return PublishResult(
            ok=True,
            payload=payload,
            remote_video_id=f"manual_export:{job.get('id') or package_dir.name}",
        )


class LocalBrowserPublisher(BasePublisher):
    name = "local_browser"

    def publish(self, job: dict[str, Any]) -> PublishResult:
        self.validate(job)
        raise PublishValidationError(
            "local browser publisher is reserved but not implemented in v1.4.0",
            "local_browser_not_implemented",
        )


def publisher_for_job(job: dict[str, Any]) -> BasePublisher:
    publish_mode = str(job.get("publish_mode") or "").strip().lower()
    if publish_mode == "local_browser":
        return LocalBrowserPublisher()
    if publish_mode == "manual_export":
        return ManualExportPublisher()
    raise PublishValidationError(
        f"publish_mode={publish_mode or '(empty)'} 不能由本地发布包适配器执行",
        "unsupported_publish_mode",
    )
