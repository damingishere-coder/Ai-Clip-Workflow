"""统一发布执行入口。状态抢占由 PublishScheduler 负责。"""

from __future__ import annotations

from typing import Any, Callable

from app.services.publish_adapters import ManualExportPublisher, PublishValidationError


def execute_publish_job(
    job_id: str,
    force: bool = False,
    *,
    runner: Callable[[list[str]], Any] | None = None,
) -> dict[str, Any]:
    """按 publish_mode 执行任务，禁止未知类型静默降级。"""
    from app.services import publish_service
    from app.services.publish_scheduler import get_publish_job_raw

    job = get_publish_job_raw(job_id)
    if not job:
        raise PublishValidationError("发布任务不存在", "publish_job_not_found")
    publish_mode = str(job.get("publish_mode") or "").strip().lower()

    if publish_mode == "opencli_publish":
        result = publish_service.execute_opencli_send_job(job_id, runner=runner)
        return {
            "status": "published" if result.get("status") == "ok" else "failed",
            "job_id": job_id,
            "message": result.get("message") or "",
            "job": result.get("job"),
        }
    if publish_mode == "manual_export":
        result = ManualExportPublisher().publish(job)
        return {
            "status": "exported",
            "job_id": job_id,
            "payload": result.payload,
            "remote_video_id": result.remote_video_id,
        }
    if publish_mode == "api_publish":
        result = publish_service.execute_api_publish_job(job_id)
        return {
            "status": "published" if result.get("status") == "ok" else "failed",
            "job_id": job_id,
            "message": result.get("message") or "",
            "job": result.get("job"),
        }
    if publish_mode == "local_browser":
        raise PublishValidationError(
            "local_browser 当前未实现，任务不会降级为发布包导出",
            "local_browser_not_implemented",
        )
    raise PublishValidationError(
        f"不支持的 publish_mode：{publish_mode or '(empty)'}",
        "unsupported_publish_mode",
    )
