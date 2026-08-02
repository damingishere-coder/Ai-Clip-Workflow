"""现有平台 API 发布方式的显式兼容入口。"""

from __future__ import annotations

from typing import Any

from app.services.publishers.base import BasePublisher, PublishOutcome, PublishResult


class ApiCompatPublisher(BasePublisher):
    name = "api_publish"

    def __init__(self, **_: Any) -> None:
        pass

    def publish(self, job: dict[str, Any]) -> PublishResult:
        self.validate(job)
        from app.services import publish_service

        response = publish_service.execute_api_publish_job(str(job.get("id") or ""))
        if response.get("status") == "ok":
            return PublishResult(
                outcome=PublishOutcome.PUBLISHED,
                message=str(response.get("message") or "平台 API 已提交"),
                remote_video_id=str(response.get("remote_video_id") or ""),
                platform_url=str(response.get("platform_url") or ""),
                published_at=str(response.get("published_at") or ""),
                provider_response=response.get("provider_response") or response,
            )
        return PublishResult(
            outcome=PublishOutcome.FAILED,
            message=str(response.get("message") or "平台 API 发布失败"),
            error_code="api_publish_failed",
            provider_response=response,
        )
