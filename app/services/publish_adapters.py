"""v1.4 兼容导入层。

新代码应从 ``app.services.publishers`` 导入；保留本文件避免旧脚本和测试立即失效。
"""

from __future__ import annotations

from typing import Any

from app.services.publishers.base import (
    BasePublisher,
    PublishError,
    PublishOutcome,
    PublishResult,
    PublishValidationError,
    PublishWorkerUnavailable,
)
from app.services.publishers.local_browser import LocalBrowserPublisher
from app.services.publishers.manual_export import ManualExportPublisher
from app.services.publishers.registry import get_publisher


def publisher_for_job(job: dict[str, Any]) -> BasePublisher:
    return get_publisher(
        str(job.get("platform") or ""),
        str(job.get("publish_mode") or ""),
    )


__all__ = [
    "BasePublisher",
    "LocalBrowserPublisher",
    "ManualExportPublisher",
    "PublishError",
    "PublishOutcome",
    "PublishResult",
    "PublishValidationError",
    "PublishWorkerUnavailable",
    "publisher_for_job",
]
