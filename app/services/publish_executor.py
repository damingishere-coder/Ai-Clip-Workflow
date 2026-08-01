"""统一发布执行入口；任务领取和最终状态由 PublishScheduler 负责。"""

from __future__ import annotations

from typing import Any, Callable

from app.services.publish_repository import PublishRepository
from app.services.publishers.registry import get_publisher


def execute_publish_job(
    job_id: str,
    force: bool = False,
    *,
    runner: Callable[[list[str]], Any] | None = None,
    repository: PublishRepository | None = None,
    worker_client=None,
) -> dict[str, Any]:
    del force  # 是否允许领取由 Scheduler 处理，Publisher 不绕过状态机。
    repo = repository or PublishRepository()
    job = repo.get_job(job_id)
    if not job:
        from app.services.publishers.base import PublishValidationError

        raise PublishValidationError("发布任务不存在", "publish_job_not_found")
    dependencies: dict[str, Any] = {"repository": repo, "runner": runner}
    if worker_client is not None:
        dependencies["worker_client"] = worker_client
    publisher = get_publisher(
        str(job.get("platform") or ""),
        str(job.get("publish_mode") or ""),
        **dependencies,
    )
    result = publisher.publish(job)
    # LocalBrowserPublisher 已即时记录 Worker 原始结果；其他模式在这里统一补写。
    if str(job.get("publish_mode") or "") != "local_browser":
        repo.record_provider_result(job_id, result)
    return result.as_dict()
