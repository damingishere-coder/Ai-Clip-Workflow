"""SQLite 发布调度器：立即发送和定时发送共用此状态机。"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import socket
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from app.core.config import settings
from app.db.database import get_connection, init_db
from app.services.publish_executor import execute_publish_job, is_publish_dispatch_active
from app.services.publish_readiness import (
    PublishPlatformIsolationBlocked,
    SendReadinessBlocked,
    build_send_readiness,
    list_account_snapshots,
    require_worker_available,
)
from app.services.publish_repository import PublishRepository
from app.services.publish_time import (
    app_zone,
    build_schedule_times,
    ensure_future,
    local_display,
    next_allowed_schedule_time,
    parse_datetime,
    to_utc_iso,
    utc_now,
    utc_now_iso,
)
from app.services.publishers.base import (
    PublishError,
    PublishOutcome,
    PublishResult,
    PublishWorkerUnavailable,
)
from app.services.publishers.worker_client import PublishWorkerClient
from app.services.task_log_service import append_task_log


logger = logging.getLogger(__name__)


def now_iso() -> str:
    return utc_now_iso()


def build_batch_schedule_times(
    count: int,
    *,
    start_at_local: str,
    timezone_name: str,
    interval_minutes: int,
    daily_start_time: str,
    daily_end_time: str,
    reject_past: bool = True,
) -> list[str]:
    return build_schedule_times(
        count,
        start_at_local=start_at_local,
        timezone_name=timezone_name,
        interval_minutes=interval_minutes,
        daily_start_time=daily_start_time,
        daily_end_time=daily_end_time,
        reject_past=reject_past,
    )


def build_batch_schedule_preview(
    job_ids: list[str],
    *,
    start_at_local: str,
    timezone_name: str,
    interval_minutes: int,
    daily_start_time: str,
    daily_end_time: str,
    reject_past: bool = True,
) -> list[dict[str, str]]:
    utc_times = build_batch_schedule_times(
        len(job_ids),
        start_at_local=start_at_local,
        timezone_name=timezone_name,
        interval_minutes=interval_minutes,
        daily_start_time=daily_start_time,
        daily_end_time=daily_end_time,
        reject_past=reject_past,
    )
    return [
        {
            "job_id": job_id,
            "scheduled_at_utc": scheduled,
            "scheduled_at_local": parse_datetime(scheduled).astimezone(app_zone(timezone_name)).isoformat(timespec="seconds"),
            "scheduled_at_local_display": local_display(scheduled, timezone_name),
            "timezone": timezone_name,
        }
        for job_id, scheduled in zip(job_ids, utc_times, strict=True)
    ]


def get_publish_job_raw(job_id: str) -> dict[str, Any] | None:
    return PublishRepository().get_job(job_id)


_SCHEDULER_HEALTH: dict[str, Any] = {
    "running": False,
    "scanning": False,
    "last_scan_at": "",
    "next_scan_at": "",
    "last_error_code": "",
    "last_error_message": "",
    "last_error_at": "",
    "consecutive_failures": 0,
}
_ACTIVE_SCHEDULER: "PublishScheduler | None" = None


def wake_scheduler() -> bool:
    scheduler = _ACTIVE_SCHEDULER
    if not scheduler:
        return False
    scheduler.wake()
    return True


class PublishScheduler:
    def __init__(
        self,
        interval_seconds: int | None = None,
        max_retry_count: int | None = None,
        *,
        executor=execute_publish_job,
        repository: PublishRepository | None = None,
        worker_client: PublishWorkerClient | None = None,
        worker_id: str | None = None,
    ) -> None:
        self.interval_seconds = max(1, int(interval_seconds or settings.publish_scheduler_interval_seconds))
        self.max_retry_count = max(
            1,
            int(max_retry_count if max_retry_count is not None else settings.publish_scheduler_max_retry_count),
        )
        self.executor = executor
        self.repository = repository or PublishRepository()
        self.worker_client = worker_client or PublishWorkerClient()
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}"
        self._stop_event: asyncio.Event | None = None
        self._wake_event: asyncio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._background_task: asyncio.Task[None] | None = None
        self._shutdown_requested = False

    def run_once(self) -> dict[str, Any]:
        init_db()
        _SCHEDULER_HEALTH["scanning"] = True
        try:
            recovered = self.recover_interrupted_jobs()
            jobs = self.list_due_jobs()
            results: list[dict[str, Any]] = []
            for job in jobs:
                try:
                    results.append(self.execute_job(job["id"]))
                except sqlite3.Error:
                    # 数据库属于全局基础设施故障；停止本轮，交给常驻循环稍后重试。
                    raise
                except Exception:
                    logger.exception("发布任务执行出现未预期异常：%s", job.get("id"))
                    current = self.repository.get_job(str(job["id"])) or {}
                    if str(current.get("status") or "").upper() == "SCHEDULED":
                        transition = self._mark_need_review(
                            str(job["id"]),
                            "unexpected_scheduler_error",
                            "调度任务出现未预期异常，为避免重复投稿已转入人工复核",
                            expected_statuses=("SCHEDULED",),
                        )
                    else:
                        transition = {
                            "status": "skipped",
                            "job_id": str(job["id"]),
                            "message": "任务已进入发布执行，保留给代际恢复流程处理",
                        }
                    results.append(transition)
            checked_at = utc_now()
            self._record_scan_success(checked_at)
            return {
                "status": "ok",
                "checked_at": _SCHEDULER_HEALTH["last_scan_at"],
                "recovered_count": recovered,
                "matched_count": len(jobs),
                "published_count": sum(item.get("status") == "published" for item in results),
                "exported_count": sum(item.get("status") == "exported" for item in results),
                "failed_count": sum(item.get("status") == "failed" for item in results),
                "need_review_count": sum(item.get("status") == "need_review" for item in results),
                "rescheduled_count": sum(item.get("status") == "rescheduled" for item in results),
                "skipped_count": sum(item.get("status") == "skipped" for item in results),
                "results": results,
            }
        finally:
            _SCHEDULER_HEALTH["scanning"] = False

    async def run_forever(self) -> None:
        global _ACTIVE_SCHEDULER
        self._loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()
        self._wake_event = asyncio.Event()
        if self._shutdown_requested:
            self._stop_event.set()
        _ACTIVE_SCHEDULER = self
        _SCHEDULER_HEALTH["running"] = True
        try:
            while not self._stop_event.is_set():
                try:
                    await asyncio.to_thread(self.run_once)
                except Exception as exc:
                    self._record_scan_error(exc)
                    logger.exception("发布调度扫描失败，将在下一轮自动重试")
                if self._stop_event.is_set():
                    break
                self._wake_event.clear()
                try:
                    await asyncio.wait_for(self._wake_event.wait(), timeout=self.interval_seconds)
                except TimeoutError:
                    pass
        finally:
            if _ACTIVE_SCHEDULER is self:
                _ACTIVE_SCHEDULER = None
            _SCHEDULER_HEALTH["running"] = False

    def _record_scan_success(self, checked_at: datetime) -> None:
        _SCHEDULER_HEALTH["last_scan_at"] = checked_at.isoformat(timespec="seconds")
        _SCHEDULER_HEALTH["next_scan_at"] = (
            checked_at + timedelta(seconds=self.interval_seconds)
        ).isoformat(timespec="seconds")
        _SCHEDULER_HEALTH["last_error_code"] = ""
        _SCHEDULER_HEALTH["last_error_message"] = ""
        _SCHEDULER_HEALTH["last_error_at"] = ""
        _SCHEDULER_HEALTH["consecutive_failures"] = 0

    def _record_scan_error(self, exc: Exception) -> None:
        failed_at = utc_now()
        if isinstance(exc, sqlite3.Error):
            error_code = "database_unavailable"
            error_message = "数据库暂时不可用，调度器将在下一轮自动重试"
        else:
            error_code = "scheduler_scan_failed"
            error_message = "调度扫描出现异常，调度器将在下一轮自动重试"
        _SCHEDULER_HEALTH["last_error_code"] = error_code
        _SCHEDULER_HEALTH["last_error_message"] = error_message
        _SCHEDULER_HEALTH["last_error_at"] = failed_at.isoformat(timespec="seconds")
        _SCHEDULER_HEALTH["next_scan_at"] = (
            failed_at + timedelta(seconds=self.interval_seconds)
        ).isoformat(timespec="seconds")
        _SCHEDULER_HEALTH["consecutive_failures"] = (
            int(_SCHEDULER_HEALTH.get("consecutive_failures") or 0) + 1
        )

    def wake(self) -> None:
        if self._loop and self._wake_event:
            self._loop.call_soon_threadsafe(self._wake_event.set)

    def stop(self) -> None:
        self._shutdown_requested = True
        if self._loop and self._stop_event:
            self._loop.call_soon_threadsafe(self._stop_event.set)
            if self._wake_event:
                self._loop.call_soon_threadsafe(self._wake_event.set)

    async def shutdown(self) -> None:
        """停止扫描并等待当前一轮安全结束，避免应用退出时丢弃后台 Task。"""
        self.stop()
        task = self._background_task
        if task and task is not asyncio.current_task():
            await task

    def list_due_jobs(self) -> list[dict[str, Any]]:
        now = utc_now()
        due: list[dict[str, Any]] = []
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM publish_jobs
                WHERE status = 'SCHEDULED'
                ORDER BY COALESCE(next_attempt_at, scheduled_at), created_at
                """
            ).fetchall()
        for row in rows:
            job = dict(row)
            due_value = job.get("next_attempt_at") or job.get("scheduled_at")
            try:
                due_at = parse_datetime(due_value).astimezone(timezone.utc)
            except ValueError:
                due.append(job)
                continue
            if due_at <= now:
                due.append(job)
        return due

    def execute_job(self, job_id: str, *, force: bool = False, runner=None) -> dict[str, Any]:
        job = self.repository.get_job(job_id)
        if not job:
            return {"status": "failed", "job_id": job_id, "message": "发布任务不存在"}
        status = str(job.get("status") or "").upper()
        if status in {"PUBLISHED", "EXPORTED", "CANCELLED", "PUBLISHING", "NEED_REVIEW"}:
            return {"status": "skipped", "job_id": job_id, "message": f"任务状态为 {status}，不能领取"}
        if status != "SCHEDULED":
            return {"status": "skipped", "job_id": job_id, "message": f"任务状态为 {status}"}

        # 旧版 OpenCLI 排期不得由新调度器静默补发。先转入人工复核，
        # 只有用户在对应平台逐条确认后，才会创建新的 Windows Chrome 任务。
        if str(job.get("publish_mode") or "") == "opencli_publish":
            return self._mark_need_review(
                job_id,
                "legacy_schedule_requires_confirmation",
                "旧版排期已暂停，未执行上传；请选择对应平台账号后逐条转换并发送",
                expected_statuses=("SCHEDULED",),
            )

        readiness = build_send_readiness(
            job,
            accounts=list_account_snapshots(),
            resolve_legacy=False,
            validate_files=True,
        )
        if not readiness["ready"]:
            return {
                "status": "skipped",
                "job_id": job_id,
                "error_code": "send_setup_required",
                "message": readiness["message"],
                "send_readiness": readiness,
            }
        if readiness["requires_worker"]:
            try:
                require_worker_available(self.worker_client)
            except SendReadinessBlocked as exc:
                return {
                    "status": "skipped",
                    "job_id": job_id,
                    "error_code": "publish_worker_unavailable",
                    "message": str(exc),
                    "send_readiness": exc.readiness,
                }

        risk_flags = self._risk_flags(job)
        if risk_flags and not settings.publish_scheduler_allow_publish_without_review:
            return self._mark_need_review(
                job_id,
                "risk_flags_require_review",
                f"内容风险标记需要人工复核：{risk_flags}",
                expected_statuses=("SCHEDULED",),
            )
        try:
            due_at = parse_datetime(job.get("next_attempt_at") or job.get("scheduled_at"))
        except ValueError as exc:
            return self._mark_failed(
                job_id, "invalid_scheduled_at", str(exc), expected_statuses=("SCHEDULED",)
            )
        if not force and due_at > utc_now():
            return {"status": "skipped", "job_id": job_id, "message": "尚未到计划发布时间"}

        max_attempts = max(1, int(job.get("max_attempts") or self.max_retry_count))
        if not force and int(job.get("attempt_count") or 0) >= max_attempts:
            return self._mark_failed(
                job_id,
                "max_retry_exceeded",
                "上传前安全重试次数已用完",
                expected_statuses=("SCHEDULED",),
            )
        execution_id = self._claim_scheduled_job(job_id)
        if not execution_id:
            return {"status": "skipped", "job_id": job_id, "message": "任务已被另一个调度器领取"}
        claimed = self.repository.get_job(job_id) or job
        try:
            raw_result = self.executor(
                job_id,
                force=force,
                expected_execution_id=execution_id,
                runner=runner,
                repository=self.repository,
                worker_client=self.worker_client,
            )
            result = PublishResult.from_dict(raw_result)
            if (
                result.outcome == PublishOutcome.PUBLISHED
                and (not result.published_at or result.needs_manual_review)
            ):
                invalid_result = result
                result = PublishResult(
                    outcome=PublishOutcome.NEED_REVIEW,
                    message="Publisher 成功结果缺少时间证据或仍要求人工复核",
                    error_code="publish_result_inconsistent",
                    needs_manual_review=True,
                    provider_response={"invalid_result": invalid_result.as_dict()},
                )
        except PublishWorkerUnavailable as exc:
            return self._handle_worker_unavailable(claimed, exc)
        except PublishError as exc:
            if exc.needs_manual_review:
                return self._mark_need_review(
                    job_id, exc.error_code, exc.message, expected_execution_id=execution_id
                )
            return self._mark_failed(
                job_id, exc.error_code, exc.message, expected_execution_id=execution_id
            )
        except Exception as exc:
            return self._mark_need_review(
                job_id,
                "publish_result_uncertain",
                f"执行器异常且无法确定是否已上传，请人工核对：{exc}",
                expected_execution_id=execution_id,
            )

        if result.outcome == PublishOutcome.PUBLISHED:
            return self._mark_published(job_id, result, expected_execution_id=execution_id)
        if result.outcome == PublishOutcome.EXPORTED:
            return self._mark_exported(job_id, result, expected_execution_id=execution_id)
        if result.outcome == PublishOutcome.NEED_REVIEW or result.needs_manual_review:
            return self._mark_need_review(
                job_id,
                result.error_code or "manual_review_required",
                result.message,
                result,
                expected_execution_id=execution_id,
            )
        return self._mark_failed(
            job_id,
            result.error_code or "publish_failed",
            result.message,
            result,
            expected_execution_id=execution_id,
        )

    def publish_now(self, job_id: str) -> dict[str, Any]:
        job = self.repository.get_job(job_id)
        if not job:
            raise ValueError("发布任务不存在")
        status = str(job.get("status") or "").upper()
        if status not in {"DRAFT", "WAITING", "SCHEDULED"}:
            raise ValueError("只有草稿、等待或已排期任务可以立即发送")
        readiness = self._require_ready_jobs([job_id], resolve_legacy=True, check_worker=True)[job_id]
        if str(job.get("publish_mode") or "") == "opencli_publish":
            transition = self._mark_need_review(
                job_id,
                "legacy_schedule_requires_confirmation",
                "旧版任务已暂停；正在保留原记录并创建新的 Windows Chrome 投稿任务",
                expected_statuses=(status,),
                expected_updated_at=str(job.get("updated_at") or ""),
            )
            if transition.get("status") == "skipped":
                raise ValueError("任务内容或状态已变化，请刷新页面后重试")
            return self.repair_and_publish(
                job_id,
                account_id=str(readiness.get("resolved_account_id") or ""),
            )
        now = utc_now_iso()
        with get_connection() as connection:
            cursor = connection.execute(
                """
                UPDATE publish_jobs
                SET account_id = ?, publish_mode = ?, scheduled_at = ?, next_attempt_at = NULL,
                    timezone = ?, schedule_timezone = ?,
                    status = 'SCHEDULED', error_code = '', error_message = '', last_error = '',
                    needs_manual_review = 0, updated_at = ?
                WHERE id = ? AND status = ? AND updated_at = ?
                """,
                (
                    readiness["resolved_account_id"] or job.get("account_id") or None,
                    readiness["resolved_publish_mode"] or job.get("publish_mode"),
                    now,
                    settings.app_timezone,
                    settings.app_timezone,
                    now,
                    job_id,
                    str(job.get("status") or ""),
                    str(job.get("updated_at") or ""),
                ),
            )
            if cursor.rowcount:
                self._record_auto_target_resolution(job, readiness, connection=connection)
                self.repository.add_event(
                    job_id, "publish_now", from_status=str(job.get("status") or ""),
                    to_status="SCHEDULED", message="立即发送已进入统一调度队列", connection=connection,
                )
            connection.commit()
        if not cursor.rowcount:
            raise ValueError("任务状态已变化，请刷新页面后重试")
        wake_scheduler()
        return {"status": "scheduled", "job_id": job_id, "scheduled_at": now, "job": self._public_job(job_id)}

    def retry_failed(
        self,
        job_id: str,
        scheduled_at: str | None = None,
        *,
        visibility: str | None = None,
    ) -> dict[str, Any]:
        source = self.repository.get_job(job_id)
        if not source:
            raise ValueError("发布任务不存在")
        if str(source.get("status") or "").upper() != "FAILED":
            raise ValueError("只有明确失败的任务可以重试；需复核任务请先确认平台未发布并标记失败")
        schedule = to_utc_iso(scheduled_at, settings.app_timezone) if scheduled_at else utc_now_iso()
        if scheduled_at:
            ensure_future(scheduled_at, settings.app_timezone)
        resolved_visibility = str(visibility or source.get("visibility") or "public")
        if resolved_visibility not in {"public", "friends", "private"}:
            raise ValueError("可见范围只支持公开、好友可见或仅自己可见")
        readiness = self._require_ready_jobs(
            [job_id],
            resolve_legacy=True,
            check_worker=True,
        )[job_id]
        resolved_mode = str(readiness.get("resolved_publish_mode") or source.get("publish_mode") or "")
        with get_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current_row = connection.execute(
                "SELECT * FROM publish_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if not current_row or str(current_row["status"] or "").upper() != "FAILED":
                connection.rollback()
                raise ValueError("源任务状态已变化，请刷新发送中心后重试")
            if str(current_row["updated_at"] or "") != str(source.get("updated_at") or ""):
                connection.rollback()
                raise ValueError("源任务内容已变化，请刷新发送中心后重试")
            current = dict(current_row)
            active = connection.execute(
                """
                SELECT id, status FROM publish_jobs
                WHERE id <> ? AND output_clip_id = ? AND platform = ? AND publish_mode = ?
                  AND status IN ('DRAFT', 'WAITING', 'SCHEDULED', 'PUBLISHING', 'NEED_REVIEW')
                ORDER BY created_at DESC LIMIT 1
                """,
                (
                    job_id,
                    current.get("output_clip_id"),
                    current.get("platform"),
                    resolved_mode,
                ),
            ).fetchone()
            if active:
                connection.rollback()
                raise ValueError(
                    f"同一视频已有任务 {active['id']} 处于 {active['status']}；"
                    "如为人工复核，请先确认平台未发布并标记失败"
                )
            result = self._clone_job_for_retry(
                current,
                scheduled_at=schedule,
                event_type="manual_retry_created",
                event_message=f"由失败任务 {job_id} 创建",
                event_from_status="FAILED",
                overrides={
                    "visibility": resolved_visibility,
                    "publish_mode": resolved_mode,
                    "account_id": readiness.get("resolved_account_id")
                    or current.get("account_id")
                    or None,
                },
                connection=connection,
            )
            connection.commit()
        wake_scheduler()
        result["job"] = self._public_job(str(result["job_id"]))
        return result

    def repair_and_publish(
        self,
        job_id: str,
        account_id: str = "",
        visibility: str = "",
    ) -> dict[str, Any]:
        source = self.repository.get_job(job_id)
        if not source:
            raise ValueError("发布任务不存在")
        source_readiness = build_send_readiness(
            source,
            accounts=list_account_snapshots(),
            resolve_legacy=False,
        )
        if str(source.get("status") or "").upper() != "NEED_REVIEW" or not source_readiness["repairable"]:
            raise ValueError("该任务不是明确发生在上传前的旧任务，不能自动修复；请先人工核对平台结果")
        # 幂等快路径：已经成功创建替代任务时，不再重复做文件/Worker readiness。
        # 最终创建前仍会在 BEGIN IMMEDIATE 中再次检查，不能只依赖这里。
        with get_connection() as connection:
            existing = connection.execute(
                """
                SELECT id FROM publish_jobs
                WHERE retry_of_job_id = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (job_id,),
            ).fetchone()
        if existing:
            existing_id = str(existing["id"])
            return {
                "status": "already_created",
                "job_id": existing_id,
                "source_job_id": job_id,
                "job": self._public_job(existing_id),
                "message": "该旧任务已经创建过替代任务，本次没有重复创建",
            }
        resolved_visibility = visibility.strip() or str(source.get("visibility") or "public")
        if resolved_visibility not in {"public", "friends", "private"}:
            raise ValueError("可见范围只支持公开、好友可见或仅自己可见")
        candidate = {
            **source,
            "account_id": account_id.strip() or source.get("account_id") or "",
            "visibility": resolved_visibility,
        }
        readiness = build_send_readiness(
            candidate,
            accounts=list_account_snapshots(),
            resolve_legacy=True,
            validate_files=True,
        )
        if not readiness["dispatch_ready"]:
            raise SendReadinessBlocked(readiness)
        if readiness["requires_worker"]:
            require_worker_available(self.worker_client)
        existing_id = ""
        with get_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute("SELECT * FROM publish_jobs WHERE id = ?", (job_id,)).fetchone()
            existing = connection.execute(
                """
                SELECT id FROM publish_jobs
                WHERE retry_of_job_id = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (job_id,),
            ).fetchone()
            if existing:
                existing_id = str(existing["id"])
                connection.commit()
            elif not current or str(current["status"] or "").upper() != "NEED_REVIEW":
                connection.rollback()
                raise ValueError("源任务状态已变化，请刷新发送中心后重新确认")
            elif str(current["updated_at"] or "") != str(source.get("updated_at") or ""):
                connection.rollback()
                raise ValueError("源任务内容已变化，请刷新发送中心后重新确认")
            else:
                result = self._clone_job_for_retry(
                    dict(current),
                    scheduled_at=utc_now_iso(),
                    event_type="safe_repair_created",
                    event_message=f"由上传前失败任务 {job_id} 安全修复",
                    event_from_status="NEED_REVIEW",
                    overrides={
                        "publish_mode": readiness["resolved_publish_mode"],
                        "account_id": readiness["resolved_account_id"] or None,
                        "visibility": resolved_visibility,
                    },
                    connection=connection,
                )
                replacement_id = str(result["job_id"])
                self.repository.add_event(
                    job_id,
                    "safe_repair_replacement_created",
                    from_status="NEED_REVIEW",
                    to_status="NEED_REVIEW",
                    message=f"已保留原记录并创建替代任务 {replacement_id}",
                    payload={"replacement_job_id": replacement_id},
                    connection=connection,
                )
                connection.commit()
        if existing_id:
            return {
                "status": "already_created",
                "job_id": existing_id,
                "source_job_id": job_id,
                "job": self._public_job(existing_id),
                "message": "该旧任务已经创建过替代任务，本次没有重复创建",
            }
        wake_scheduler()
        result["job"] = self._public_job(str(result["job_id"]))
        result["message"] = "旧任务已保留，新的 Windows Chrome 投稿任务已进入调度器"
        result["source_job_id"] = job_id
        return result

    def _clone_job_for_retry(
        self,
        source: dict[str, Any],
        *,
        scheduled_at: str,
        event_type: str,
        event_message: str,
        event_from_status: str,
        overrides: dict[str, Any] | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any]:
        """克隆重试任务；传入连接时由调用方统一提交和唤醒。"""
        if connection is None:
            with get_connection() as owned:
                result = self._clone_job_for_retry(
                    source,
                    scheduled_at=scheduled_at,
                    event_type=event_type,
                    event_message=event_message,
                    event_from_status=event_from_status,
                    overrides=overrides,
                    connection=owned,
                )
                owned.commit()
            wake_scheduler()
            result["job"] = self._public_job(str(result["job_id"]))
            return result

        source_id = str(source.get("id") or "")
        new_id = f"pub_{uuid4().hex}"
        columns_to_clear = {
            "id", "status", "scheduled_at", "next_attempt_at", "attempt_count", "retry_count",
            "claimed_at", "started_at", "finished_at", "worker_id", "execution_id", "execution_phase",
            "platform_item_id", "platform_upload_id", "remote_video_id", "platform_url", "error_code",
            "error_message", "last_error", "provider_response", "publish_result", "published_at",
            "needs_manual_review", "created_at", "updated_at", "retry_of_job_id",
        }
        available = {row["name"] for row in connection.execute("PRAGMA table_info(publish_jobs)").fetchall()}
        values = {key: value for key, value in source.items() if key in available and key not in columns_to_clear}
        values.update({
            "id": new_id,
            "status": "SCHEDULED",
            "scheduled_at": scheduled_at,
            "timezone": source.get("timezone") or settings.app_timezone,
            "schedule_timezone": source.get("schedule_timezone") or settings.app_timezone,
            "attempt_count": 0,
            "retry_count": 0,
            "max_attempts": source.get("max_attempts") or self.max_retry_count,
            "needs_manual_review": 0,
            "retry_of_job_id": source_id,
            "created_at": utc_now_iso(),
            "updated_at": utc_now_iso(),
        })
        values.update(overrides or {})
        columns = list(values)
        try:
            connection.execute(
                f"INSERT INTO publish_jobs ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                [values[column] for column in columns],
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError(
                "同一视频已经存在等待、排期、执行中或人工复核任务；请刷新发送中心后核对"
            ) from exc
        self.repository.add_event(
            new_id, event_type, from_status=event_from_status, to_status="SCHEDULED",
            message=event_message, payload={"retry_of_job_id": source_id}, connection=connection,
        )
        return {"status": "scheduled", "job_id": new_id, "retry_of_job_id": source_id}

    def recover_interrupted_jobs(self) -> int:
        stale_before = utc_now() - timedelta(minutes=max(1, int(settings.publish_job_stale_minutes)))
        with get_connection() as connection:
            rows = connection.execute("SELECT * FROM publish_jobs WHERE status = 'PUBLISHING'").fetchall()
        recovered = 0
        for raw in rows:
            job = dict(raw)
            try:
                updated_at = parse_datetime(job.get("updated_at")).astimezone(timezone.utc)
            except ValueError:
                updated_at = datetime.min.replace(tzinfo=timezone.utc)
            execution_id = str(job.get("execution_id") or "")
            if execution_id and is_publish_dispatch_active(execution_id):
                # 同一应用进程仍位于已原子保留的真实 dispatch 临界区；终态由执行线程写回。
                continue
            phase = str(job.get("execution_phase") or "unknown")
            details: dict[str, Any] = {}
            worker_query_confirmed = False
            execution_in_progress = False
            execution_identity_valid = False
            # Worker 执行日志是跨进程恢复的唯一依据。只要有 execution_id 就主动查询，
            # 不依赖宿主 Worker 回写 SQLite，也不会因此重复调用投稿接口。
            if execution_id:
                try:
                    execution = self.worker_client.execution(execution_id)
                    worker_query_confirmed = True
                    phase = str(execution.get("phase") or phase)
                    details = execution.get("details") if isinstance(execution.get("details"), dict) else {}
                    execution_in_progress = bool(execution.get("in_progress"))
                    execution_identity_valid = self._worker_execution_matches_job(execution, job)
                except PublishError:
                    worker_query_confirmed = False
            transition: dict[str, Any] | None = None
            if execution_id and phase in {"confirmed_success", "exported", "failed", "manual_review"}:
                if not execution_identity_valid:
                    transition = self._mark_need_review(
                        job["id"],
                        "recovery_identity_mismatch",
                        "Worker 执行日志身份与当前发布任务不一致，请人工确认",
                        expected_execution_id=execution_id,
                        expected_updated_at=str(job.get("updated_at") or ""),
                    )
                else:
                    try:
                        result = self._parse_worker_terminal_result(phase, details)
                        if phase == "confirmed_success":
                            transition = self._mark_published(
                                job["id"], result, expected_execution_id=execution_id
                            )
                        elif phase == "exported":
                            transition = self._mark_exported(
                                job["id"], result, expected_execution_id=execution_id
                            )
                        elif phase == "failed":
                            transition = self._mark_failed(
                                job["id"],
                                result.error_code or "publish_failed",
                                result.message or "Worker 已确认发送失败",
                                result,
                                expected_execution_id=execution_id,
                            )
                        else:
                            transition = self._mark_need_review(
                                job["id"],
                                result.error_code or "manual_review_required",
                                result.message or "Worker 已停止自动发送，请人工确认平台结果",
                                result,
                                expected_execution_id=execution_id,
                            )
                    except (PublishError, ValueError, KeyError, TypeError):
                        transition = self._mark_need_review(
                            job["id"],
                            "recovery_result_uncertain",
                            "Worker 记录成功但结果数据不完整，请人工确认",
                            expected_execution_id=execution_id,
                        )
            elif updated_at > stale_before:
                continue
            elif worker_query_confirmed and not execution_identity_valid:
                transition = self._mark_need_review(
                    job["id"],
                    "recovery_identity_mismatch",
                    "Worker 执行日志缺少身份或与当前发布任务不一致，请人工确认",
                    expected_execution_id=execution_id,
                    expected_updated_at=str(job.get("updated_at") or ""),
                )
            elif worker_query_confirmed and execution_in_progress:
                # Worker 仍持有该 execution 的跨进程锁；HTTP 超时不等于投稿未执行。
                # 保持 PUBLISHING，等待 journal 进入明确终态，绝不并行创建新 execution。
                continue
            elif (
                worker_query_confirmed
                and phase in {"received", "browser_opening", "browser_opened", "rejected"}
                and execution_id
            ):
                transition = self._reschedule_before_upload(
                    job["id"],
                    "应用重启后确认尚未开始上传，已安全重新排队",
                    expected_execution_id=execution_id,
                    expected_updated_at=str(job.get("updated_at") or ""),
                )
            else:
                transition = self._mark_need_review(
                    job["id"],
                    "interrupted_publish_uncertain",
                    "应用重启前的发布结果不确定，为避免重复投稿已停止自动重试",
                    expected_execution_id=execution_id,
                    expected_updated_at=str(job.get("updated_at") or ""),
                )
            if transition and transition.get("status") != "skipped":
                recovered += 1
        return recovered

    def update_schedule(self, job_id: str, scheduled_at: str) -> dict[str, Any]:
        parsed = ensure_future(scheduled_at, settings.app_timezone)
        job = self.repository.get_job(job_id)
        if not job:
            raise ValueError("发布任务不存在")
        if str(job.get("status") or "").upper() not in {"DRAFT", "WAITING", "SCHEDULED"}:
            raise ValueError("当前状态不能修改排期；失败任务请使用重试，需复核任务请先人工确认")
        if str(job.get("publish_mode") or "") == "opencli_publish":
            raise ValueError("旧版任务不能直接改排期；请逐条使用“转换并发送”创建新的 Windows Chrome 任务")
        readiness = self._require_ready_jobs([job_id], resolve_legacy=True, check_worker=True)[job_id]
        source_status = str(job.get("status") or "").upper()
        stored = to_utc_iso(parsed)
        now = utc_now_iso()
        with get_connection() as connection:
            cursor = connection.execute(
                """
                UPDATE publish_jobs SET account_id = ?, publish_mode = ?,
                    scheduled_at = ?, next_attempt_at = NULL,
                    timezone = ?, schedule_timezone = ?, status = 'SCHEDULED', updated_at = ?
                WHERE id = ? AND status = ? AND updated_at = ?
                """,
                (
                    readiness["resolved_account_id"] or job.get("account_id") or None,
                    readiness["resolved_publish_mode"] or job.get("publish_mode"),
                    stored,
                    settings.app_timezone,
                    settings.app_timezone,
                    now,
                    job_id,
                    source_status,
                    job.get("updated_at"),
                ),
            )
            if cursor.rowcount:
                self._record_auto_target_resolution(job, readiness, connection=connection)
                self.repository.add_event(
                    job_id, "schedule_updated", from_status=source_status,
                    to_status="SCHEDULED", payload={"scheduled_at": stored}, connection=connection,
                )
                connection.commit()
            else:
                connection.rollback()
        if not cursor.rowcount:
            raise ValueError("任务状态已变化，请刷新页面后重试")
        wake_scheduler()
        return {"status": "ok", "job": self._public_job(job_id)}

    def preview_batch_schedule(
        self,
        job_ids: list[str],
        *,
        platform: str | None = None,
        start_at_local: str,
        timezone_name: str,
        interval_minutes: int,
        daily_start_time: str,
        daily_end_time: str,
    ) -> dict[str, Any]:
        ids = self._validate_batch_jobs(job_ids, platform)
        self._reject_legacy_schedule_apply(ids)
        self._require_ready_jobs(ids, resolve_legacy=True, check_worker=True)
        schedule = build_batch_schedule_preview(
            ids,
            start_at_local=start_at_local,
            timezone_name=timezone_name,
            interval_minutes=interval_minutes,
            daily_start_time=daily_start_time,
            daily_end_time=daily_end_time,
        )
        return {"status": "ok", "timezone": timezone_name, "schedule": schedule}

    def next_batch_schedule_start(
        self,
        job_ids: list[str],
        *,
        platform: str,
        timezone_name: str = "Asia/Shanghai",
        interval_minutes: int = 180,
        daily_start_time: str = "07:00",
        daily_end_time: str = "00:00",
    ) -> dict[str, Any]:
        ids = self._validate_batch_jobs(job_ids, platform)
        placeholders = ",".join("?" for _ in ids)
        with get_connection() as connection:
            rows = connection.execute(
                f"""
                SELECT id, status, scheduled_at
                FROM publish_jobs
                WHERE platform = ?
                  AND status IN ('WAITING', 'SCHEDULED', 'PUBLISHING')
                  AND TRIM(COALESCE(scheduled_at, '')) <> ''
                  AND id NOT IN ({placeholders})
                """,
                [platform, *ids],
            ).fetchall()

        now = utc_now()
        candidates: list[tuple[datetime, str]] = []
        for row in rows:
            try:
                scheduled = parse_datetime(row["scheduled_at"]).astimezone(timezone.utc)
            except ValueError:
                continue
            if scheduled > now:
                candidates.append((scheduled, str(row["id"])))

        if not candidates:
            return {
                "status": "empty",
                "timezone": timezone_name,
                "message": "当前平台暂无其他未来排期，请手动选择第 1 条发布时间。",
                "latest_job_id": "",
                "latest_scheduled_at_utc": "",
                "latest_scheduled_at_local_display": "",
                "next_start_at_utc": "",
                "next_start_at_local": "",
                "next_start_at_local_display": "",
            }

        latest, latest_job_id = max(candidates, key=lambda item: item[0])
        zone = app_zone(timezone_name)
        candidate = latest.astimezone(zone) + timedelta(minutes=interval_minutes)
        next_start = next_allowed_schedule_time(
            candidate,
            daily_start_time=daily_start_time,
            daily_end_time=daily_end_time,
        )
        return {
            "status": "ok",
            "timezone": timezone_name,
            "message": "已接在当前平台最晚排期后。",
            "latest_job_id": latest_job_id,
            "latest_scheduled_at_utc": to_utc_iso(latest),
            "latest_scheduled_at_local_display": local_display(latest, timezone_name),
            "next_start_at_utc": to_utc_iso(next_start),
            "next_start_at_local": next_start.strftime("%Y-%m-%dT%H:%M"),
            "next_start_at_local_display": local_display(next_start, timezone_name),
        }

    def update_batch_schedule(
        self,
        job_ids: list[str],
        *,
        platform: str | None = None,
        action: str,
        start_at_local: str = "",
        timezone_name: str = "Asia/Shanghai",
        interval_minutes: int = 180,
        daily_start_time: str = "07:00",
        daily_end_time: str = "00:00",
        confirmed_schedule: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        ids = self._validate_batch_jobs(job_ids, platform)
        with get_connection() as connection:
            expected_rows = connection.execute(
                f"SELECT id, status, updated_at FROM publish_jobs WHERE id IN ({','.join('?' for _ in ids)})",
                ids,
            ).fetchall()
        if len(expected_rows) != len(ids):
            raise ValueError("部分发布任务已不存在，请刷新后重试")
        expected_state = {
            str(row["id"]): (str(row["status"] or "").upper(), str(row["updated_at"] or ""))
            for row in expected_rows
        }
        if action not in {"apply", "clear"}:
            raise ValueError("不支持的排期操作")
        if action == "apply":
            self._reject_legacy_schedule_apply(ids)
        readiness_map = (
            self._require_ready_jobs(ids, resolve_legacy=True, check_worker=True)
            if action == "apply"
            else {}
        )
        if action == "apply" and confirmed_schedule:
            schedule_map = {str(item.get("job_id") or ""): str(item.get("scheduled_at_utc") or "") for item in confirmed_schedule}
            if set(schedule_map) != set(ids):
                raise ValueError("确认排期与所选任务不一致，请重新预览")
            schedule = []
            for job_id in ids:
                parsed = ensure_future(schedule_map[job_id], timezone_name)
                stored = to_utc_iso(parsed)
                schedule.append({
                    "job_id": job_id,
                    "scheduled_at_utc": stored,
                    "scheduled_at_local": parsed.astimezone(app_zone(timezone_name)).isoformat(timespec="seconds"),
                    "scheduled_at_local_display": local_display(parsed, timezone_name),
                    "timezone": timezone_name,
                })
        elif action == "apply":
            schedule = build_batch_schedule_preview(
                ids,
                start_at_local=start_at_local,
                timezone_name=timezone_name,
                interval_minutes=interval_minutes,
                daily_start_time=daily_start_time,
                daily_end_time=daily_end_time,
            )
        else:
            schedule = [{
                "job_id": job_id, "scheduled_at_utc": "", "scheduled_at_local": "",
                "scheduled_at_local_display": "未排期", "timezone": timezone_name,
            } for job_id in ids]
        now = utc_now_iso()
        with get_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                f"SELECT id, status, account_id, publish_mode, updated_at FROM publish_jobs WHERE id IN ({','.join('?' for _ in ids)})",
                ids,
            ).fetchall()
            if len(rows) != len(ids):
                connection.rollback()
                raise ValueError("部分发布任务已不存在，请刷新后重试")
            row_map = {row["id"]: dict(row) for row in rows}
            for item in schedule:
                job_id = item["job_id"]
                current = row_map[job_id]
                status = str(current["status"] or "").upper()
                if status not in {"DRAFT", "WAITING", "SCHEDULED"}:
                    connection.rollback()
                    raise ValueError(f"任务 {job_id} 当前状态不能修改排期")
                if expected_state.get(job_id) != (status, str(current.get("updated_at") or "")):
                    connection.rollback()
                    raise ValueError(f"任务 {job_id} 已被其他操作修改，请刷新后重试")
                next_status = "SCHEDULED" if action == "apply" else "WAITING"
                readiness = readiness_map.get(job_id) or {}
                cursor = connection.execute(
                    """
                    UPDATE publish_jobs SET account_id = ?, publish_mode = ?,
                        scheduled_at = ?, next_attempt_at = NULL,
                        timezone = ?, schedule_timezone = ?, status = ?, updated_at = ?
                    WHERE id = ? AND status = ? AND updated_at = ?
                    """,
                    (
                        readiness.get("resolved_account_id") or current.get("account_id") or None,
                        readiness.get("resolved_publish_mode") or current.get("publish_mode"),
                        item["scheduled_at_utc"], timezone_name, timezone_name, next_status, now,
                        job_id, status, current.get("updated_at"),
                    ),
                )
                if cursor.rowcount != 1:
                    connection.rollback()
                    raise ValueError(f"任务 {job_id} 状态已变化，请刷新后重试")
                if action == "apply":
                    self._record_auto_target_resolution(current, readiness, connection=connection)
                self.repository.add_event(
                    job_id, "batch_schedule_applied" if action == "apply" else "schedule_cleared",
                    from_status=status, to_status=next_status, payload=item, connection=connection,
                )
            connection.commit()
        if action == "apply":
            wake_scheduler()
        return {
            "status": "ok", "action": action, "updated_count": len(ids),
            "message": f"已保存 {len(ids)} 条任务的具体排期" if action == "apply" else f"已清除 {len(ids)} 条任务的排期",
            "schedule": schedule,
            "jobs": [self._public_job(job_id) for job_id in ids],
        }

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        job = self.repository.get_job(job_id)
        if not job:
            raise ValueError("发布任务不存在")
        source = str(job.get("status") or "").upper()
        if source not in {"DRAFT", "WAITING", "SCHEDULED"}:
            raise ValueError("只有草稿、等待或已排期任务可以取消发送并返回内容准备")
        now = utc_now_iso()
        with get_connection() as connection:
            cursor = connection.execute(
                """
                UPDATE publish_jobs
                SET status = 'WAITING', scheduled_at = '', next_attempt_at = NULL,
                    claimed_at = NULL, started_at = NULL, finished_at = NULL,
                    worker_id = NULL, execution_id = NULL, execution_phase = '',
                    error_code = '', error_message = '', last_error = '',
                    needs_manual_review = 0, updated_at = ?
                WHERE id = ? AND status = ?
                """,
                (now, job_id, source),
            )
            if cursor.rowcount:
                self.repository.add_event(
                    job_id,
                    "returned_to_preparation",
                    from_status=source,
                    to_status="WAITING",
                    message="用户取消发送并返回内容准备",
                    payload={"scheduled_at_cleared": True, "files_deleted": False},
                    connection=connection,
                )
            connection.commit()
        if not cursor.rowcount:
            raise ValueError("任务状态已变化，请刷新后重试")
        return {
            "status": "ok",
            "message": "已取消发送并返回内容准备；视频、文案和封面均已保留。",
            "job": self._public_job(job_id),
        }

    def skip_job(self, job_id: str) -> dict[str, Any]:
        return self._transition_user_status(job_id, "CANCELLED", "用户跳过任务")

    def mark_failed_manually(self, job_id: str, message: str = "人工确认平台未发布") -> dict[str, Any]:
        job = self.repository.get_job(job_id)
        if not job:
            raise ValueError("发布任务不存在")
        status = str(job.get("status") or "").upper()
        if status not in {"NEED_REVIEW", "SCHEDULED", "WAITING"}:
            raise ValueError("当前任务不能人工标记失败")
        return self._mark_failed(
            job_id,
            "manually_marked_failed",
            message,
            expected_statuses=(status,),
        )

    def mark_published_manually(self, job_id: str, platform_url: str) -> dict[str, Any]:
        job = self.repository.get_job(job_id)
        if not job:
            raise ValueError("发布任务不存在")
        if str(job.get("status") or "").upper() != "NEED_REVIEW":
            raise ValueError("只有需复核任务可以人工标记已发布")
        url = str(platform_url or "").strip()
        expected_domain = "douyin.com" if job.get("platform") == "douyin" else "bilibili.com"
        parsed_url = urlparse(url)
        hostname = str(parsed_url.hostname or "").lower().rstrip(".")
        if (
            parsed_url.scheme not in {"http", "https"}
            or not hostname
            or (hostname != expected_domain and not hostname.endswith(f".{expected_domain}"))
        ):
            raise ValueError(f"请填写有效的 {expected_domain} 作品链接")
        result = PublishResult(
            outcome=PublishOutcome.PUBLISHED,
            message="人工核对平台后标记为已发布",
            remote_video_id="",
            platform_url=url,
            published_at=utc_now_iso(),
            provider_response={"manual_confirmation": True, "platform_url": url},
        )
        return self._mark_published(job_id, result, require_publishing=False)

    def approve_review(self, job_id: str, platform_url: str = "") -> dict[str, Any]:
        return self.mark_published_manually(job_id, platform_url)

    def _validate_batch_jobs(self, job_ids: list[str], platform: str | None = None) -> list[str]:
        ids = list(dict.fromkeys(str(job_id).strip() for job_id in job_ids if str(job_id).strip()))
        if not ids:
            raise ValueError("至少选择一条发布任务")
        with get_connection() as connection:
            rows = connection.execute(
                f"SELECT id, platform FROM publish_jobs WHERE id IN ({','.join('?' for _ in ids)})", ids
            ).fetchall()
        if len(rows) != len(ids):
            raise ValueError("部分发布任务不存在")
        platforms = {str(row["platform"] or "") for row in rows}
        if len(platforms) != 1:
            raise PublishPlatformIsolationBlocked("抖音和 B站任务不能混合排期或批量操作")
        if platform and platform not in platforms:
            raise PublishPlatformIsolationBlocked("当前平台与所选任务不一致，请切换到对应平台后重试")
        return ids

    def _reject_legacy_schedule_apply(self, job_ids: list[str]) -> None:
        with get_connection() as connection:
            count = connection.execute(
                f"SELECT COUNT(*) FROM publish_jobs WHERE id IN ({','.join('?' for _ in job_ids)}) AND publish_mode = 'opencli_publish'",
                job_ids,
            ).fetchone()[0]
        if int(count):
            raise ValueError("旧版任务不能批量覆盖转换；请逐条使用“转换并发送”保留原记录")

    def _require_ready_jobs(
        self,
        job_ids: list[str],
        *,
        resolve_legacy: bool,
        check_worker: bool,
    ) -> dict[str, dict[str, Any]]:
        accounts = list_account_snapshots()
        readiness_map: dict[str, dict[str, Any]] = {}
        worker_required = False
        for job_id in job_ids:
            job = self.repository.get_job(job_id)
            if not job:
                raise ValueError("发布任务不存在")
            readiness = build_send_readiness(
                job,
                accounts=accounts,
                resolve_legacy=resolve_legacy,
                validate_files=True,
            )
            readiness_map[job_id] = readiness
            if not readiness["dispatch_ready"]:
                raise SendReadinessBlocked(readiness)
            worker_required = worker_required or bool(readiness["requires_worker"])
        if check_worker and worker_required:
            require_worker_available(self.worker_client)
        return readiness_map

    def _record_auto_target_resolution(
        self,
        job: dict[str, Any],
        readiness: dict[str, Any],
        *,
        connection,
    ) -> None:
        original_mode = str(job.get("publish_mode") or "")
        original_account = str(job.get("account_id") or "")
        resolved_mode = str(readiness.get("resolved_publish_mode") or original_mode)
        resolved_account = str(readiness.get("resolved_account_id") or original_account)
        if original_mode == resolved_mode and original_account == resolved_account:
            return
        self.repository.add_event(
            str(job.get("id") or ""),
            "send_target_auto_resolved",
            from_status=str(job.get("status") or ""),
            to_status=str(job.get("status") or ""),
            message="已自动选择唯一同平台账号并改用 Windows Chrome",
            payload={
                "from_publish_mode": original_mode,
                "to_publish_mode": resolved_mode,
                "auto_selected_account": bool(readiness.get("auto_selected_account")),
            },
            connection=connection,
        )

    def _public_job(self, job_id: str) -> dict[str, Any] | None:
        from app.services import publish_service

        return publish_service.get_publish_job(job_id) or self.repository.get_job(job_id)

    def _claim_scheduled_job(self, job_id: str) -> str | None:
        now = utc_now_iso()
        execution_id = uuid4().hex
        with get_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE publish_jobs
                SET status = 'PUBLISHING', claimed_at = ?, started_at = ?, finished_at = NULL,
                    worker_id = ?, execution_id = ?, execution_phase = 'claimed',
                    attempt_count = COALESCE(attempt_count, 0) + 1,
                    retry_count = COALESCE(retry_count, 0) + 1,
                    next_attempt_at = NULL, last_error = '', error_message = '',
                    needs_manual_review = 0, updated_at = ?
                WHERE id = ? AND status = 'SCHEDULED'
                """,
                (now, now, self.worker_id, execution_id, now, job_id),
            )
            if cursor.rowcount:
                self.repository.add_event(
                    job_id, "claimed", from_status="SCHEDULED", to_status="PUBLISHING",
                    worker_id=self.worker_id, payload={"execution_id": execution_id}, connection=connection,
                )
            connection.commit()
        return execution_id if int(cursor.rowcount or 0) == 1 else None

    def _handle_worker_unavailable(self, job: dict[str, Any], exc: PublishWorkerUnavailable) -> dict[str, Any]:
        execution_id = str(job.get("execution_id") or "")
        if exc.request_may_have_been_received and execution_id:
            try:
                execution = self.worker_client.execution(execution_id)
                if not self._worker_execution_matches_job(execution, job):
                    return self._mark_need_review(
                        str(job["id"]),
                        "publish_worker_identity_mismatch",
                        "Worker 执行日志缺少身份或与当前发布任务不一致，请人工确认",
                        expected_execution_id=execution_id,
                    )
                phase = str(execution.get("phase") or "unknown")
                details = execution.get("details") if isinstance(execution.get("details"), dict) else {}
                if phase == "confirmed_success" and details:
                    result = self._parse_worker_terminal_result(phase, details)
                    return self._mark_published(
                        str(job["id"]),
                        result,
                        expected_execution_id=execution_id,
                    )
                if phase == "manual_review" and details:
                    result = self._parse_worker_terminal_result(phase, details)
                    return self._mark_need_review(
                        str(job["id"]),
                        result.error_code or "manual_review_required",
                        result.message or "Worker 已停止自动发送，请人工确认平台结果",
                        result,
                        expected_execution_id=execution_id,
                    )
                if phase == "failed" and details:
                    result = self._parse_worker_terminal_result(phase, details)
                    return self._mark_failed(
                        str(job["id"]),
                        result.error_code or "publish_failed",
                        result.message or "Worker 已确认发送失败",
                        result,
                        expected_execution_id=execution_id,
                    )
                if phase in {
                    "received", "browser_opening", "browser_opened", "upload_started",
                    "upload_completed", "submit_clicked",
                }:
                    return {
                        "status": "pending",
                        "job_id": job["id"],
                        "message": "Worker 已接收本次执行，保持 PUBLISHING 并等待恢复扫描确认结果",
                    }
                if phase != "rejected":
                    return self._mark_need_review(
                        str(job["id"]), "publish_worker_result_uncertain",
                        "Worker 连接中断且任务可能已经上传，请人工确认平台结果",
                        expected_execution_id=execution_id,
                    )
            except (PublishError, ValueError):
                return self._mark_need_review(
                    str(job["id"]), "publish_worker_result_uncertain",
                    "Worker 超时后无法读取执行阶段，为避免重复投稿已停止自动重试",
                    expected_execution_id=execution_id,
                )
        attempts = int(job.get("attempt_count") or 0)
        max_attempts = max(1, int(job.get("max_attempts") or self.max_retry_count))
        if attempts >= max_attempts:
            return self._mark_failed(
                str(job["id"]),
                exc.error_code,
                f"{exc.message}；3 次上传前安全重试已用完",
                expected_execution_id=execution_id,
            )
        delays = (30, 120, 300)
        delay = delays[min(max(0, attempts - 1), len(delays) - 1)]
        next_attempt = (utc_now() + timedelta(seconds=delay)).isoformat(timespec="seconds")
        now = utc_now_iso()
        with get_connection() as connection:
            cursor = connection.execute(
                """
                UPDATE publish_jobs SET status = 'SCHEDULED', next_attempt_at = ?,
                    error_code = ?, error_message = ?, last_error = ?,
                    execution_phase = 'worker_unavailable_before_upload', updated_at = ?
                WHERE id = ? AND status = 'PUBLISHING' AND execution_id = ?
                """,
                (next_attempt, exc.error_code, exc.message, exc.message, now, job["id"], execution_id),
            )
            if cursor.rowcount:
                self.repository.add_event(
                    str(job["id"]), "safe_retry_scheduled", from_status="PUBLISHING", to_status="SCHEDULED",
                    worker_id=self.worker_id, error_code=exc.error_code, message=exc.message,
                    payload={"next_attempt_at": next_attempt, "attempt_count": attempts}, connection=connection,
                )
                connection.commit()
            else:
                connection.rollback()
        if not cursor.rowcount:
            return {"status": "skipped", "job_id": job["id"], "message": "发布执行代际已变化，未覆盖最新状态"}
        return {"status": "rescheduled", "job_id": job["id"], "next_attempt_at": next_attempt, "message": exc.message}

    @staticmethod
    def _worker_execution_matches_job(
        execution: dict[str, Any],
        job: dict[str, Any],
    ) -> bool:
        identity = execution.get("identity")
        if not isinstance(identity, dict):
            return False
        return identity == {
            "job_id": str(job.get("id") or ""),
            "platform": str(job.get("platform") or ""),
            "account_id": str(job.get("account_id") or ""),
        }

    @staticmethod
    def _parse_worker_terminal_result(phase: str, details: dict[str, Any]) -> PublishResult:
        expected_outcomes = {
            "confirmed_success": PublishOutcome.PUBLISHED,
            "exported": PublishOutcome.EXPORTED,
            "failed": PublishOutcome.FAILED,
            "manual_review": PublishOutcome.NEED_REVIEW,
        }
        expected = expected_outcomes.get(phase)
        if expected is None:
            raise ValueError("Worker execution phase 不是可接受终态")
        required_fields = {
            "outcome", "message", "remote_video_id", "platform_url", "published_at",
            "provider_response", "error_code", "needs_manual_review",
        }
        if (
            not required_fields.issubset(details)
            or not isinstance(details.get("provider_response"), dict)
            or not isinstance(details.get("needs_manual_review"), bool)
            or (phase == "confirmed_success" and not str(details.get("published_at") or ""))
            or (phase == "manual_review" and details.get("needs_manual_review") is not True)
            or (phase != "manual_review" and details.get("needs_manual_review") is not False)
        ):
            raise ValueError("Worker execution 终态结果不完整")
        result = PublishResult.from_dict(details)
        if result.outcome != expected:
            raise ValueError("Worker execution phase 与终态结果不一致")
        return result

    def _reschedule_before_upload(
        self,
        job_id: str,
        message: str,
        *,
        expected_execution_id: str,
        expected_updated_at: str,
    ) -> dict[str, Any]:
        now = utc_now_iso()
        with get_connection() as connection:
            cursor = connection.execute(
                """
                UPDATE publish_jobs SET status = 'SCHEDULED', next_attempt_at = ?,
                    execution_phase = 'recovered_before_upload', last_error = ?, error_message = ?, updated_at = ?
                WHERE id = ? AND status = 'PUBLISHING' AND execution_id = ?
                    AND updated_at = ?
                """,
                (now, message, message, now, job_id, expected_execution_id, expected_updated_at),
            )
            if cursor.rowcount:
                self.repository.add_event(
                    job_id, "recovered_before_upload", from_status="PUBLISHING", to_status="SCHEDULED",
                    message=message, connection=connection,
                )
                connection.commit()
            else:
                connection.rollback()
        if not cursor.rowcount:
            return {"status": "skipped", "job_id": job_id, "message": "发布执行代际已变化，未重新排队"}
        return {"status": "rescheduled", "job_id": job_id, "message": message}

    def _mark_published(
        self,
        job_id: str,
        result: PublishResult,
        *,
        require_publishing: bool = True,
        expected_execution_id: str | None = None,
    ) -> dict[str, Any]:
        now = utc_now_iso()
        if require_publishing:
            if expected_execution_id is None:
                condition = "AND status = 'PUBLISHING' AND COALESCE(execution_id, '') = ''"
                condition_params: tuple[str, ...] = ()
            else:
                condition = "AND status = 'PUBLISHING' AND COALESCE(execution_id, '') = ?"
                condition_params = (expected_execution_id,)
        else:
            condition = "AND status = 'NEED_REVIEW'"
            condition_params = ()
        with get_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            recorded = self.repository.record_provider_result(
                job_id,
                result,
                expected_execution_id=expected_execution_id if require_publishing and expected_execution_id else None,
                connection=connection,
                updated_at=now,
            )
            if not recorded:
                connection.rollback()
                return {"status": "skipped", "job_id": job_id, "message": "发布执行代际已变化，未覆盖最新状态"}
            cursor = connection.execute(
                f"""
                UPDATE publish_jobs SET status = 'PUBLISHED', published_at = ?, finished_at = ?,
                    platform_url = ?, remote_video_id = ?, platform_item_id = ?,
                    audit_status = 'submitted', needs_manual_review = 0,
                    error_code = '', error_message = '', last_error = '',
                    execution_phase = 'confirmed_success', updated_at = ?
                WHERE id = ? {condition}
                """,
                (
                    result.published_at or now,
                    now,
                    result.platform_url,
                    result.remote_video_id,
                    result.remote_video_id,
                    now,
                    job_id,
                    *condition_params,
                ),
            )
            if cursor.rowcount:
                self.repository.add_event(
                    job_id, "published", from_status="PUBLISHING" if require_publishing else "NEED_REVIEW",
                    to_status="PUBLISHED", worker_id=self.worker_id, payload=result.as_dict(), connection=connection,
                )
                connection.commit()
            else:
                connection.rollback()
        if not cursor.rowcount:
            return {"status": "skipped", "job_id": job_id, "message": "任务状态已变化，未覆盖最新状态"}
        self._append_log(job_id, "平台已确认投稿成功")
        return {"status": "published", "job_id": job_id, "publish_result": result.as_dict()}

    def _mark_exported(
        self,
        job_id: str,
        result: PublishResult,
        *,
        expected_execution_id: str,
    ) -> dict[str, Any]:
        now = utc_now_iso()
        with get_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            recorded = self.repository.record_provider_result(
                job_id,
                result,
                expected_execution_id=expected_execution_id,
                connection=connection,
                updated_at=now,
            )
            if not recorded:
                connection.rollback()
                return {"status": "skipped", "job_id": job_id, "message": "发布执行代际已变化，未覆盖最新状态"}
            cursor = connection.execute(
                """
                UPDATE publish_jobs SET status = 'EXPORTED', finished_at = ?, published_at = NULL,
                    audit_status = 'not_submitted', execution_phase = 'exported', updated_at = ?
                WHERE id = ? AND status = 'PUBLISHING' AND execution_id = ?
                """,
                (now, now, job_id, expected_execution_id),
            )
            if cursor.rowcount:
                self.repository.add_event(
                    job_id, "exported", from_status="PUBLISHING", to_status="EXPORTED",
                    payload=result.as_dict(), connection=connection,
                )
                connection.commit()
            else:
                connection.rollback()
        if not cursor.rowcount:
            return {"status": "skipped", "job_id": job_id, "message": "任务状态已变化，未覆盖最新状态"}
        return {"status": "exported", "job_id": job_id, "publish_result": result.as_dict()}

    @staticmethod
    def _expected_publish_transition(
        expected_execution_id: str | None,
        expected_statuses: tuple[str, ...] | None,
        expected_updated_at: str | None = None,
    ) -> tuple[str, tuple[str, ...]]:
        if expected_execution_id is not None:
            condition = "status = 'PUBLISHING' AND COALESCE(execution_id, '') = ?"
            params = (expected_execution_id,)
            if expected_updated_at is not None:
                condition += " AND updated_at = ?"
                params = (*params, expected_updated_at)
            return condition, params
        statuses = tuple(str(status or "").upper() for status in (expected_statuses or ()) if status)
        if not statuses:
            raise ValueError("发布状态迁移必须指定 execution_id 或明确的来源状态")
        placeholders = ", ".join("?" for _ in statuses)
        condition = f"status IN ({placeholders})"
        params = statuses
        if expected_updated_at is not None:
            condition += " AND updated_at = ?"
            params = (*params, expected_updated_at)
        return condition, params

    def _mark_failed(
        self,
        job_id: str,
        error_code: str,
        message: str,
        result: PublishResult | None = None,
        *,
        expected_execution_id: str | None = None,
        expected_statuses: tuple[str, ...] | None = None,
        expected_updated_at: str | None = None,
    ) -> dict[str, Any]:
        now = utc_now_iso()
        condition, condition_params = self._expected_publish_transition(
            expected_execution_id, expected_statuses, expected_updated_at
        )
        with get_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                f"SELECT status FROM publish_jobs WHERE id = ? AND {condition}",
                (job_id, *condition_params),
            ).fetchone()
            if not row:
                connection.rollback()
                return {"status": "skipped", "job_id": job_id, "message": "发布执行代际或状态已变化，未覆盖最新状态"}
            from_status = str(row["status"])
            if result:
                recorded = self.repository.record_provider_result(
                    job_id,
                    result,
                    expected_execution_id=expected_execution_id or None,
                    connection=connection,
                    updated_at=now,
                )
                if not recorded:
                    connection.rollback()
                    return {"status": "skipped", "job_id": job_id, "message": "发布执行代际已变化，未覆盖最新状态"}
            cursor = connection.execute(
                f"""
                UPDATE publish_jobs SET status = 'FAILED', finished_at = ?, error_code = ?,
                    error_message = ?, last_error = ?, needs_manual_review = 0,
                    execution_phase = 'failed', updated_at = ?
                WHERE id = ? AND {condition}
                """,
                (now, error_code, message, message, now, job_id, *condition_params),
            )
            if cursor.rowcount:
                self.repository.add_event(
                    job_id, "failed", from_status=from_status, to_status="FAILED",
                    worker_id=self.worker_id, error_code=error_code, message=message, connection=connection,
                )
                connection.commit()
            else:
                connection.rollback()
        if not cursor.rowcount:
            return {"status": "skipped", "job_id": job_id, "message": "任务状态已变化，未覆盖最新状态"}
        self._append_log(job_id, f"发布失败：{message}")
        return {"status": "failed", "job_id": job_id, "error_code": error_code, "message": message}

    def _mark_need_review(
        self,
        job_id: str,
        error_code: str,
        message: str,
        result: PublishResult | None = None,
        *,
        expected_execution_id: str | None = None,
        expected_statuses: tuple[str, ...] | None = None,
        expected_updated_at: str | None = None,
    ) -> dict[str, Any]:
        now = utc_now_iso()
        condition, condition_params = self._expected_publish_transition(
            expected_execution_id, expected_statuses, expected_updated_at
        )
        with get_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                f"SELECT status FROM publish_jobs WHERE id = ? AND {condition}",
                (job_id, *condition_params),
            ).fetchone()
            if not row:
                connection.rollback()
                return {"status": "skipped", "job_id": job_id, "message": "发布执行代际或状态已变化，未覆盖最新状态"}
            from_status = str(row["status"])
            if result:
                recorded = self.repository.record_provider_result(
                    job_id,
                    result,
                    expected_execution_id=expected_execution_id or None,
                    connection=connection,
                    updated_at=now,
                )
                if not recorded:
                    connection.rollback()
                    return {"status": "skipped", "job_id": job_id, "message": "发布执行代际已变化，未覆盖最新状态"}
            cursor = connection.execute(
                f"""
                UPDATE publish_jobs SET status = 'NEED_REVIEW', finished_at = ?, error_code = ?,
                    error_message = ?, last_error = ?, needs_manual_review = 1,
                    execution_phase = 'manual_review', updated_at = ?
                WHERE id = ? AND {condition}
                """,
                (now, error_code, message, message, now, job_id, *condition_params),
            )
            if cursor.rowcount:
                self.repository.add_event(
                    job_id, "needs_review", from_status=from_status, to_status="NEED_REVIEW",
                    worker_id=self.worker_id, error_code=error_code, message=message, connection=connection,
                )
                connection.commit()
            else:
                connection.rollback()
        if not cursor.rowcount:
            return {"status": "skipped", "job_id": job_id, "message": "任务状态已变化，未覆盖最新状态"}
        self._append_log(job_id, f"发布需要人工复核：{message}")
        return {"status": "need_review", "job_id": job_id, "error_code": error_code, "message": message}

    def _transition_user_status(self, job_id: str, target: str, message: str) -> dict[str, Any]:
        job = self.repository.get_job(job_id)
        if not job:
            raise ValueError("发布任务不存在")
        source = str(job.get("status") or "").upper()
        if source not in {"DRAFT", "WAITING", "SCHEDULED", "FAILED", "NEED_REVIEW"}:
            raise ValueError("当前状态不能取消")
        now = utc_now_iso()
        with get_connection() as connection:
            cursor = connection.execute(
                """
                UPDATE publish_jobs SET status = ?, finished_at = ?, last_error = ?,
                    error_message = ?, updated_at = ? WHERE id = ? AND status = ?
                """,
                (target, now, message, message, now, job_id, source),
            )
            if cursor.rowcount:
                self.repository.add_event(
                    job_id, "cancelled", from_status=source, to_status=target,
                    message=message, connection=connection,
                )
            connection.commit()
        if not cursor.rowcount:
            raise ValueError("任务状态已变化，请刷新后重试")
        return {"status": "ok", "job": self.repository.get_job(job_id)}

    @staticmethod
    def _risk_flags(job: dict[str, Any]) -> list[Any]:
        for value in (job.get("risk_flags"), job.get("provider_response")):
            if not value:
                continue
            try:
                parsed = json.loads(value) if isinstance(value, str) else value
            except (json.JSONDecodeError, TypeError):
                return ["risk_flags_invalid"]
            if isinstance(parsed, list):
                return [item for item in parsed if item]
            if isinstance(parsed, dict) and isinstance(parsed.get("risk_flags"), list):
                return [item for item in parsed["risk_flags"] if item]
        return []

    def _append_log(self, job_id: str, message: str) -> None:
        job = self.repository.get_job(job_id) or {}
        task_id = str(job.get("task_id") or "")
        if not task_id:
            return
        try:
            append_task_log(task_id, f"Publish job {job_id}: {message}")
        except Exception:
            logger.exception("写入发布任务日志失败：job_id=%s", job_id)


def queue_snapshot(task_id: str | None = None) -> dict[str, Any]:
    params: list[Any] = []
    where = ""
    if task_id:
        where = "WHERE task_id = ?"
        params.append(task_id)
    with get_connection() as connection:
        rows = connection.execute(
            f"SELECT * FROM publish_jobs {where} ORDER BY COALESCE(scheduled_at, created_at), created_at DESC",
            params,
        ).fetchall()
    jobs = [dict(row) for row in rows]
    statuses = ("DRAFT", "WAITING", "SCHEDULED", "PUBLISHING", "PUBLISHED", "EXPORTED", "FAILED", "NEED_REVIEW", "CANCELLED")
    by_status = {status: [job for job in jobs if str(job.get("status") or "").upper() == status] for status in statuses}
    today = utc_now().astimezone(app_zone()).date()
    today_jobs = []
    for job in jobs:
        try:
            if parse_datetime(job.get("scheduled_at")).astimezone(app_zone()).date() == today:
                today_jobs.append(job)
        except ValueError:
            pass
    return {
        "all": jobs,
        "pending": by_status["DRAFT"] + by_status["WAITING"],
        "scheduled": by_status["SCHEDULED"],
        "publishing": by_status["PUBLISHING"],
        "published": by_status["PUBLISHED"],
        "exported": by_status["EXPORTED"],
        "failed": by_status["FAILED"],
        "need_review": by_status["NEED_REVIEW"],
        "cancelled": by_status["CANCELLED"],
        "today": today_jobs,
        "counts": {status: len(items) for status, items in by_status.items()},
        "timezone": settings.app_timezone,
    }


def scheduler_health() -> dict[str, Any]:
    with get_connection() as connection:
        counts = connection.execute(
            """
            SELECT SUM(CASE WHEN status = 'SCHEDULED' THEN 1 ELSE 0 END) scheduled_count,
                   SUM(CASE WHEN status = 'PUBLISHING' THEN 1 ELSE 0 END) publishing_count
            FROM publish_jobs
            """
        ).fetchone()
    try:
        worker = PublishWorkerClient(timeout=2).health()
        worker_available = worker.get("status") == "ok"
        worker_message = "Windows 发布 Worker 正常"
    except PublishError as exc:
        worker_available = False
        worker_message = exc.message
    return {
        "enabled": bool(settings.publish_scheduler_enabled),
        "running": bool(_SCHEDULER_HEALTH["running"]),
        "scanning": bool(_SCHEDULER_HEALTH["scanning"]),
        "last_scan_at": _SCHEDULER_HEALTH["last_scan_at"],
        "next_scan_at": _SCHEDULER_HEALTH["next_scan_at"],
        "last_error_code": _SCHEDULER_HEALTH["last_error_code"],
        "last_error_message": _SCHEDULER_HEALTH["last_error_message"],
        "last_error_at": _SCHEDULER_HEALTH["last_error_at"],
        "consecutive_failures": int(_SCHEDULER_HEALTH["consecutive_failures"]),
        "interval_seconds": int(settings.publish_scheduler_interval_seconds),
        "scheduled_count": int(counts["scheduled_count"] or 0),
        "publishing_count": int(counts["publishing_count"] or 0),
        "worker_available": worker_available,
        "worker_message": worker_message,
        "timezone": settings.app_timezone,
    }


async def start_scheduler_background() -> PublishScheduler | None:
    if not settings.publish_scheduler_enabled:
        return None
    scheduler = PublishScheduler()
    scheduler._background_task = asyncio.create_task(
        scheduler.run_forever(), name="niuma-publish-scheduler"
    )
    return scheduler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="NiuMa Studio publish scheduler")
    parser.add_argument("command", choices=["run", "run-once", "snapshot"])
    args = parser.parse_args(argv)
    scheduler = PublishScheduler()
    if args.command == "run-once":
        print(json.dumps(scheduler.run_once(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "snapshot":
        init_db()
        print(json.dumps(queue_snapshot(), ensure_ascii=False, indent=2))
        return 0
    asyncio.run(scheduler.run_forever())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
