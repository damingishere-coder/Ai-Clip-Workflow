"""明确选择时生成本地发布包，不代表平台投稿成功。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.services.publish_time import utc_now_iso
from app.services.publishers.base import BasePublisher, PublishOutcome, PublishResult, job_video_path


def _write_text(path: Path, value: str) -> None:
    path.write_text(str(value or "").strip() + "\n", encoding="utf-8")


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


class ManualExportPublisher(BasePublisher):
    name = "manual_export"

    def __init__(self, export_dir: Path | None = None, **_: Any) -> None:
        self.export_dir = Path(export_dir or settings.publish_scheduler_export_dir)

    def build_package_dir(self, job: dict[str, Any]) -> Path:
        clip_id = str(job.get("clip_id") or job.get("output_clip_id") or "unknown_clip")
        return self.export_dir / str(job.get("task_id") or "unknown_task") / clip_id

    def publish(self, job: dict[str, Any]) -> PublishResult:
        self.validate(job)
        video_path = job_video_path(job)
        package_dir = self.build_package_dir(job)
        package_dir.mkdir(parents=True, exist_ok=True)
        clip_path = package_dir / f"clip{video_path.suffix.lower()}"
        shutil.copy2(video_path, clip_path)

        payload = self.build_payload(job)
        payload.update({
            "package_dir": str(package_dir),
            "clip_file": str(clip_path),
            "exported_at": utc_now_iso(),
            "notice": "本地发布包已生成，尚未向平台投稿。",
        })
        _write_text(package_dir / "title.txt", payload["title"])
        _write_text(package_dir / "caption.txt", payload["caption"])
        _write_text(package_dir / "hashtags.txt", payload["hashtags"])
        _write_json(package_dir / "publish_plan.json", payload)
        _write_json(package_dir / "metadata.json", {
            **payload,
            "source_video_name": video_path.name,
            "source_video_size_bytes": video_path.stat().st_size,
        })
        return PublishResult(
            outcome=PublishOutcome.EXPORTED,
            message="本地发布包已生成，未向平台投稿",
            remote_video_id=f"manual_export:{job.get('id') or package_dir.name}",
            provider_response=payload,
        )
