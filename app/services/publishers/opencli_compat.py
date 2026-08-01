"""旧 opencli 的显式兼容 Publisher；状态仍由统一 Scheduler 管理。"""

from __future__ import annotations

from typing import Any

from app.services.publishers.base import BasePublisher, PublishOutcome, PublishResult


class OpenCliCompatPublisher(BasePublisher):
    name = "opencli_publish"

    def __init__(self, *, runner=None, **_: Any) -> None:
        self.runner = runner

    def publish(self, job: dict[str, Any]) -> PublishResult:
        self.validate(job)
        from app.services import publish_service

        response = publish_service.execute_opencli_send_job(str(job.get("id") or ""), runner=self.runner)
        if response.get("status") == "ok":
            current = response.get("job") or {}
            url = str(current.get("platform_url") or "")
            remote_id = str(current.get("remote_video_id") or current.get("platform_item_id") or "")
            # 旧 opencli 只有在明确返回平台证据时才算成功；否则避免误判为已发布。
            if url or remote_id or response.get("confirmed") is True:
                return PublishResult(
                    outcome=PublishOutcome.PUBLISHED,
                    message=str(response.get("message") or "opencli 已确认投稿成功"),
                    remote_video_id=remote_id,
                    platform_url=url,
                    provider_response=response,
                )
            return PublishResult(
                outcome=PublishOutcome.NEED_REVIEW,
                message="opencli 已执行，但没有取得作品链接或稿件 ID，请人工确认",
                error_code="opencli_result_uncertain",
                needs_manual_review=True,
                provider_response=response,
            )
        return PublishResult(
            outcome=PublishOutcome.FAILED,
            message=str(response.get("message") or "opencli 执行失败"),
            error_code="opencli_publish_failed",
            provider_response=response,
        )
