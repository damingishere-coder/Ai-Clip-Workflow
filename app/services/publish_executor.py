"""统一发布执行入口；任务领取和最终状态由 PublishScheduler 负责。"""

from __future__ import annotations

import threading
from typing import Any, Callable

from app.services.publish_repository import PublishRepository
from app.services.publishers.registry import get_publisher


_ACTIVE_DISPATCHES: set[str] = set()
_ACTIVE_DISPATCHES_LOCK = threading.Lock()


def is_publish_dispatch_active(execution_id: str) -> bool:
    with _ACTIVE_DISPATCHES_LOCK:
        return execution_id in _ACTIVE_DISPATCHES


def execute_publish_job(
    job_id: str,
    force: bool = False,
    *,
    expected_execution_id: str | None = None,
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
    if expected_execution_id and (
        str(job.get("status") or "").upper() != "PUBLISHING"
        or str(job.get("execution_id") or "") != expected_execution_id
    ):
        from app.services.publishers.base import PublishValidationError

        raise PublishValidationError("发布任务执行代际已变化", "publish_execution_stale")
    dependencies: dict[str, Any] = {"repository": repo, "runner": runner}
    if worker_client is not None:
        dependencies["worker_client"] = worker_client
    publish_mode = str(job.get("publish_mode") or "").strip().lower()
    dispatch_reserved = False

    def reserve_dispatch() -> None:
        nonlocal dispatch_reserved
        if dispatch_reserved:
            return
        if not expected_execution_id:
            from app.services.publishers.base import PublishValidationError

            raise PublishValidationError("发布任务执行代际或状态已变化", "publish_execution_stale")
        with _ACTIVE_DISPATCHES_LOCK:
            if expected_execution_id in _ACTIVE_DISPATCHES:
                from app.services.publishers.base import PublishValidationError

                raise PublishValidationError("发布任务正在提交，请勿重复执行", "publish_execution_active")
            _ACTIVE_DISPATCHES.add(expected_execution_id)
        try:
            reserved = repo.begin_execution_dispatch(
                job_id,
                expected_execution_id,
                str(job.get("updated_at") or ""),
            )
        except Exception:
            with _ACTIVE_DISPATCHES_LOCK:
                _ACTIVE_DISPATCHES.discard(expected_execution_id)
            raise
        if not reserved:
            with _ACTIVE_DISPATCHES_LOCK:
                _ACTIVE_DISPATCHES.discard(expected_execution_id)
            from app.services.publishers.base import PublishValidationError

            raise PublishValidationError("发布任务执行代际或状态已变化", "publish_execution_stale")
        dispatch_reserved = True

    if publish_mode == "local_browser":
        # 登录态检查不产生投稿副作用；把 CAS 推迟到 Worker /publish 前一刻，
        # 避免长时间账号检查期间过早占用 dispatching 状态。
        dependencies["before_dispatch"] = reserve_dispatch
    publisher = get_publisher(
        str(job.get("platform") or ""),
        publish_mode,
        **dependencies,
    )
    if publish_mode != "local_browser":
        reserve_dispatch()
    try:
        result = publisher.publish(job)
    finally:
        if dispatch_reserved and expected_execution_id:
            with _ACTIVE_DISPATCHES_LOCK:
                _ACTIVE_DISPATCHES.discard(expected_execution_id)
    # 平台结果与最终状态由 Scheduler 在同一 fenced 事务中落库，避免旧执行
    # 先写 provider_result、再被新 execution 接管后留下半成功数据。
    return result.as_dict()
