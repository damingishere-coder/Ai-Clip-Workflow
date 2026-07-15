"""本地浏览器执行模式：校验、登录态预检、调用 Worker、写回原始结果。"""

from __future__ import annotations

from typing import Any

from app.services.publishers.base import (
    BasePublisher,
    PublishOutcome,
    PublishResult,
    PublishValidationError,
    job_cover_path,
    job_hashtags,
)
from app.services.publishers.worker_client import PublishWorkerClient


class LocalBrowserPublisher(BasePublisher):
    name = "local_browser"

    def __init__(
        self,
        *,
        platform: str,
        worker_client: PublishWorkerClient | None = None,
        repository: Any | None = None,
        **_: Any,
    ) -> None:
        self.platform = str(platform or "").lower()
        self.worker_client = worker_client or PublishWorkerClient()
        self.repository = repository

    def validate(self, job: dict[str, Any]) -> None:
        super().validate(job)
        if str(job.get("platform") or "").lower() != self.platform:
            raise PublishValidationError("Publisher 与任务平台不匹配", "platform_mismatch")
        if not str(job.get("account_id") or "").strip():
            raise PublishValidationError("请选择发布账号", "missing_account")
        if not job_hashtags(job):
            raise PublishValidationError("话题或标签不能为空", "missing_hashtags")
        job_cover_path(job, required=True)
        from app.services.publishers.registry import get_platform_publisher_class

        platform_class = get_platform_publisher_class(self.platform)
        # 平台字段校验不需要启动浏览器。
        platform_class.validate_job_data(job)

    def publish(self, job: dict[str, Any]) -> PublishResult:
        self.validate(job)
        account_id = str(job.get("account_id") or "")
        login = self.worker_client.check_account(self.platform, account_id)
        login_status = str(login.get("login_status") or login.get("status") or "unknown").lower()
        if self.repository is not None:
            self.repository.update_account_status(
                account_id,
                "normal" if login_status == "normal" else "invalid",
                str(login.get("message") or ""),
                logged_in=login_status == "normal",
            )
        if login_status != "normal":
            result = PublishResult(
                outcome=PublishOutcome.NEED_REVIEW,
                message=str(login.get("message") or "账号登录失效，请重新登录"),
                error_code="account_login_required",
                needs_manual_review=True,
                provider_response={"login_status": login_status},
            )
            self._record(job, result)
            return result
        result = self.worker_client.publish(self.build_payload(job))
        self._record(job, result)
        return result

    def _record(self, job: dict[str, Any], result: PublishResult) -> None:
        if self.repository is not None:
            self.repository.record_provider_result(str(job.get("id") or ""), result)
