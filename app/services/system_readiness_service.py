from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import shutil
from typing import Any

from app.core.config import settings
from app.services.database_backup_service import sqlite_diagnostic_report
from app.services.publish_scheduler import scheduler_health


def _directory_readiness(path: Path, label: str) -> dict[str, Any]:
    resolved = path.resolve()
    exists = resolved.is_dir()
    readable = exists and os.access(resolved, os.R_OK)
    writable = exists and os.access(resolved, os.W_OK)
    return {
        "label": label,
        "path": str(resolved),
        "status": "ok" if readable and writable else "error",
        "exists": exists,
        "readable": readable,
        "writable": writable,
    }


def build_system_readiness(*, deep: bool = False) -> dict[str, Any]:
    """汇总启动就绪条件；只读检查，不创建目录也不修复状态。"""
    database = sqlite_diagnostic_report(settings.database_path, deep=deep)
    storage = [
        _directory_readiness(settings.data_dir, "数据目录"),
        _directory_readiness(settings.tasks_dir, "任务存储目录"),
    ]

    scheduler: dict[str, Any]
    try:
        scheduler = scheduler_health()
        scheduler["status"] = "ok"
        if scheduler.get("enabled") and (
            not scheduler.get("running") or scheduler.get("last_error_code")
        ):
            scheduler["status"] = "degraded"
    except Exception as exc:
        scheduler = {
            "status": "error",
            "worker_available": False,
            "message": f"调度状态读取失败：{exc}",
        }

    ffmpeg_path = shutil.which("ffmpeg")
    ffmpeg = {
        "status": "ok" if ffmpeg_path else "degraded",
        "available": bool(ffmpeg_path),
        "path": ffmpeg_path or "",
    }

    critical_errors: list[str] = []
    degraded_reasons: list[str] = []
    if database["status"] != "ok":
        critical_errors.extend(str(item) for item in database["errors"])
    for item in storage:
        if item["status"] != "ok":
            critical_errors.append(f"{item['label']}不可读写")
    if scheduler["status"] == "error":
        critical_errors.append(str(scheduler.get("message") or "调度状态异常"))
    elif scheduler["status"] == "degraded":
        degraded_reasons.append("发布调度未运行或存在错误")
    if not scheduler.get("worker_available", False):
        degraded_reasons.append(
            str(scheduler.get("worker_message") or "Windows 发布 Worker 不可用")
        )
    if not ffmpeg_path:
        degraded_reasons.append("FFmpeg 不可用")

    if critical_errors:
        status = "not_ready"
    elif degraded_reasons:
        status = "degraded"
    else:
        status = "ready"

    return {
        "status": status,
        "deep": bool(deep),
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "critical_errors": critical_errors,
        "degraded_reasons": degraded_reasons,
        "checks": {
            "database": database,
            "storage": storage,
            "scheduler": scheduler,
            "ffmpeg": ffmpeg,
        },
    }
