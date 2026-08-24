"""明确选择时生成本地发布包，不代表平台投稿成功。"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.core.config import settings
from app.services.publish_time import utc_now_iso
from app.services.publishers.base import (
    BasePublisher,
    PublishOutcome,
    PublishResult,
    PublishValidationError,
    job_video_path,
)


logger = logging.getLogger(__name__)


def _write_text(path: Path, value: str) -> None:
    path.write_text(str(value or "").strip() + "\n", encoding="utf-8")


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _safe_package_component(value: Any, fallback: str) -> str:
    component = str(value or fallback)
    windows_reserved = {
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
    }
    if (
        not component
        or component != component.strip()
        or component in {".", ".."}
        or Path(component).is_absolute()
        or "/" in component
        or "\\" in component
        or any(character in component for character in '<>:"|?*')
        or any(ord(character) < 32 for character in component)
        or component.endswith((".", " "))
        or component.split(".", 1)[0].upper() in windows_reserved
    ):
        raise PublishValidationError("发布包标识包含不安全路径", "unsafe_export_identifier")
    return component


class ManualExportPublisher(BasePublisher):
    name = "manual_export"

    def __init__(self, export_dir: Path | None = None, **_: Any) -> None:
        self.export_dir = Path(export_dir or settings.publish_scheduler_export_dir)

    def build_package_dir(self, job: dict[str, Any]) -> Path:
        task_id = _safe_package_component(job.get("task_id"), "unknown_task")
        clip_id = _safe_package_component(
            job.get("clip_id") or job.get("output_clip_id"), "unknown_clip"
        )
        root = self.export_dir.resolve()
        package_dir = (root / task_id / clip_id).resolve()
        if root not in package_dir.parents:
            raise PublishValidationError("发布包路径超出导出目录", "unsafe_export_path")
        return package_dir

    def publish(self, job: dict[str, Any]) -> PublishResult:
        self.validate(job)
        video_path = job_video_path(job)
        package_dir = self.build_package_dir(job)
        package_dir.parent.mkdir(parents=True, exist_ok=True)
        nonce = uuid4().hex
        staging_dir = package_dir.with_name(f".{package_dir.name}.staging-{nonce}")
        backup_dir = package_dir.with_name(f".{package_dir.name}.backup-{nonce}")
        final_clip_path = package_dir / f"clip{video_path.suffix.lower()}"
        payload = self.build_payload(job)
        payload.update({
            "package_dir": str(package_dir),
            "clip_file": str(final_clip_path),
            "exported_at": utc_now_iso(),
            "notice": "本地发布包已生成，尚未向平台投稿。",
        })
        try:
            staging_dir.mkdir()
            shutil.copy2(video_path, staging_dir / final_clip_path.name)
            _write_text(staging_dir / "title.txt", payload["title"])
            _write_text(staging_dir / "caption.txt", payload["caption"])
            _write_text(staging_dir / "hashtags.txt", payload["hashtags"])
            _write_json(staging_dir / "publish_plan.json", payload)
            _write_json(staging_dir / "metadata.json", {
                **payload,
                "source_video_name": video_path.name,
                "source_video_size_bytes": video_path.stat().st_size,
            })
            if package_dir.exists():
                package_dir.replace(backup_dir)
            try:
                staging_dir.replace(package_dir)
            except Exception:
                if backup_dir.exists() and not package_dir.exists():
                    backup_dir.replace(package_dir)
                raise
        finally:
            if staging_dir.exists():
                shutil.rmtree(staging_dir, ignore_errors=True)
        if backup_dir.exists():
            try:
                shutil.rmtree(backup_dir)
            except OSError:
                logger.warning("旧发布包备份清理失败，保留供人工检查：%s", backup_dir)
        return PublishResult(
            outcome=PublishOutcome.EXPORTED,
            message="本地发布包已生成，未向平台投稿",
            remote_video_id=f"manual_export:{job.get('id') or package_dir.name}",
            provider_response=payload,
        )
