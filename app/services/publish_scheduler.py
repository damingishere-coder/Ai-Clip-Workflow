"""SQLite 发布调度器：立即发送和定时发送共用此状态机。"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from app.core.config import settings
from app.db.database import get_connection, init_db
from app.services.publish_executor import execute_publish_job
from app.services.publish_readiness import (
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

    def run_once(self) -> dict[str, Any]:
        init_db()
        _SCHEDULER_HEALTH["scanning"] = True
        try:
            recovered = self.recover_interrupted_jobs()
            jobs = self.list_due_jobs()
            results = [self.execute_job(job["id"]) for job in jobs]
            checked_at = utc_now()
            _SCHEDULER_HEALTH["last_scan_at"] = checked_at.isoformat(timespec="seconds")
            _SCHEDULER_HEALTH["next_scan_at"] = (
                checked_at + timedelta(seconds=self.interval_seconds)
            ).isoformat(timespec="seconds")
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
        init_db()
        self._loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()
        self._wake_event = asyncio.Event()
        _ACTIVE_SCHEDULER = self
        _SCHEDULER_HEALTH["running"] = True
        try:
            while not self._stop_event.is_set():
                await asyncio.to_thread(self.run_once)
                self._wake_event.clear()
                try:
                    await asyncio.wait_for(self._wake_event.wait(), timeout=self.interval_seconds)
                except TimeoutError:
                    pass
        finally:
            if _ACTIVE_SCHEDULER is self:
                _ACTIVE_SCHEDULER = None
            _SCHEDULER_HEALTH["running"] = False

    def wake(self) -> None:
        if self._loop and self._wake_event:
            self._loop.call_soon_threadsafe(self._wake_event.set)

    def stop(self) -> None:
        if self._loop and self._stop_event:
            self._loop.call_soon_threadsafe(self._stop_event.set)
            if self._wake_event:
                self._loop.call_soon_threadsafe(self._wake_event.set)

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
            return self._mark_need_review(job_id, "risk_flags_require_review", f"内容风险标记需要人工复核：{risk_flags}")
        try:
            due_at = parse_datetime(job.get("next_attempt_at") or job.get("scheduled_at"))
        except ValueError as exc:
            return self._mark_failed(job_id, "invalid_scheduled_at", str(exc))
        if not force and due_at > utc_now():
            return {"status": "skipped", "job_id": job_id, "message": "尚未到计划发布时间"}

        max_attempts = max(1, int(job.get("max_attempts") or self.max_retry_count))
        if not force and int(job.get("attempt_count") or 0) >= max_attempts:
            return self._mark_failed(job_id, "max_retry_exceeded", "上传前安全重试次数已用完")
        if not self._claim_scheduled_job(job_id):
            return {"status": "skipped", "job_id": job_id, "message": "任务已被另一个调度器领取"}
        claimed = self.repository.get_job(job_id) or job
        try:
            raw_result = self.executor(
                job_id,
                force=force,
                runner=runner,
                repository=self.repository,
                worker_client=self.worker_client,
            )
            result = PublishResult.from_dict(raw_result)
        except PublishWorkerUnavailable as exc:
            return self._handle_worker_unavailable(claimed, exc)
        except PublishError as exc:
            if exc.needs_manual_review:
                return self._mark_need_review(job_id, exc.error_code, exc.message)
            return self._mark_failed(job_id, exc.error_code, exc.message)
        except Exception as exc:
            return self._mark_need_review(
                job_id,
                "publish_result_uncertain",
                f"执行器异常且无法确定是否已上传，请人工核对：{exc}",
            )

        if result.outcome == PublishOutcome.PUBLISHED:
            return self._mark_published(job_id, result)
        if result.outcome == PublishOutcome.EXPORTED:
            return self._mark_exported(job_id, result)
        if result.outcome == PublishOutcome.NEED_REVIEW or result.needs_manual_review:
            return self._mark_need_review(job_id, result.error_code or "manual_review_required", result.message, result)
        return self._mark_failed(job_id, result.error_code or "publish_failed", result.message, result)

    def publish_now(self, job_id: str) -> dict[str, Any]:
        job = self.repository.get_job(job_id)
        if not job:
            raise ValueError("发布任务不存在")
        if str(job.get("status") or "").upper() not in {"DRAFT", "WAITING", "SCHEDULED"}:
            raise ValueError("只有草稿、等待或已排期任务可以立即发送")
        readiness = self._require_ready_jobs([job_id], resolve_legacy=True, check_worker=True)[job_id]
        now = utc_now_iso()
        with get_connection() as connection:
            cursor = connection.execute(
                """
                UPDATE publish_jobs
                SET account_id = ?, publish_mode = ?, scheduled_at = ?, next_attempt_at = NULL,
                    timezone = ?, schedule_timezone = ?,
                    status = 'SCHEDULED', error_code = '', error_message = '', last_error = '',
                    needs_manual_review = 0, updated_at = ?
                WHERE id = ? AND status IN ('DRAFT', 'WAITING', 'SCHEDULED')
                """,
                (
                    readiness["resolved_account_id"] or job.get("account_id") or None,
                    readiness["resolved_publish_mode"] or job.get("publish_mode"),
                    now,
                    settings.app_timezone,
                    settings.app_timezone,
                    now,
                    job_id,
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

    def retry_failed(self, job_id: str, scheduled_at: str | None = None) -> dict[str, Any]:
        source = self.repository.get_job(job_id)
        if not source:
            raise ValueError("发布任务不存在")
        if str(source.get("status") or "").upper() != "FAILED":
            raise ValueError("只有明确失败的任务可以重试；需复核任务请先确认平台未发布并标记失败")
        schedule = to_utc_iso(scheduled_at, settings.app_timezone) if scheduled_at else utc_now_iso()
        if scheduled_at:
            ensure_future(scheduled_at, settings.app_timezone)
        return self._clone_job_for_retry(
            source,
            scheduled_at=schedule,
            event_type="manual_retry_created",
            event_message=f"由失败任务 {job_id} 创建",
            event_from_status="FAILED",
        )

    def repair_and_publish(self, job_id: str, account_id: str = "") -> dict[str, Any]:
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
        candidate = {**source, "account_id": account_id.strip() or source.get("account_id") or ""}
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
        result = self._clone_job_for_retry(
            source,
            scheduled_at=utc_now_iso(),
            event_type="safe_repair_created",
            event_message=f"由上传前失败任务 {job_id} 安全修复",
            event_from_status="NEED_REVIEW",
            overrides={
                "publish_mode": readiness["resolved_publish_mode"],
                "account_id": readiness["resolved_account_id"] or None,
            },
        )
        with get_connection() as connection:
            self.repository.add_event(
                job_id,
                "safe_repair_replacement_created",
                from_status="NEED_REVIEW",
                to_status="NEED_REVIEW",
                message=f"已保留原记录并创建替代任务 {result['job_id']}",
                payload={"replacement_job_id": result["job_id"]},
                connection=connection,
            )
            connection.commit()
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
    ) -> dict[str, Any]:
        source_id = str(source.get("id") or "")
        new_id = f"pub_{uuid4().hex}"
        columns_to_clear = {
            "id", "status", "scheduled_at", "next_attempt_at", "attempt_count", "retry_count",
            "claimed_at", "started_at", "finished_at", "worker_id", "execution_id", "execution_phase",
            "platform_item_id", "platform_upload_id", "remote_video_id", "platform_url", "error_code",
            "error_message", "last_error", "provider_response", "publish_result", "published_at",
            "needs_manual_review", "created_at", "updated_at", "retry_of_job_id",
        }
        with get_connection() as connection:
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
            connection.execute(
                f"INSERT INTO publish_jobs ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                [values[column] for column in columns],
            )
            self.repository.add_event(
                new_id, event_type, from_status=event_from_status, to_status="SCHEDULED",
                message=event_message, payload={"retry_of_job_id": source_id}, connection=connection,
            )
            connection.commit()
        wake_scheduler()
        return {"status": "scheduled", "job_id": new_id, "retry_of_job_id": source_id, "job": self._public_job(new_id)}

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
            if updated_at > stale_before:
                continue
            execution_id = str(job.get("execution_id") or "")
            phase = str(job.get("execution_phase") or "unknown")
            details: dict[str, Any] = {}
            if execution_id:
                try:
                    execution = self.worker_client.execution(execution_id)
                    phase = str(execution.get("phase") or phase)
                    details = execution.get("details") if isinstance(execution.get("details"), dict) else {}
                except PublishError:
                    pass
            if phase == "confirmed_success" and details:
                try:
                    self._mark_published(job["id"], PublishResult.from_dict(details))
                except Exception:
                    self._mark_need_review(job["id"], "recovery_result_uncertain", "Worker 记录成功但结果数据不完整，请人工确认")
                recovered += 1
            elif phase in {"received", "browser_opening", "browser_opened", "rejected"} and execution_id:
                self._reschedule_before_upload(job["id"], "应用重启后确认尚未开始上传，已安全重新排队")
                recovered += 1
            else:
                self._mark_need_review(
                    job["id"],
                    "interrupted_publish_uncertain",
                    "应用重启前的发布结果不确定，为避免重复投稿已停止自动重试",
                )
                recovered += 1
        return recovered

    def update_schedule(self, job_id: str, scheduled_at: str) -> dict[str, Any]:
        parsed = ensure_future(scheduled_at, settings.app_timezone)
        job = self.repository.get_job(job_id)
        if not job:
            raise ValueError("发布任务不存在")
        if str(job.get("status") or "").upper() not in {"DRAFT", "WAITING", "SCHEDULED"}:
            raise ValueError("当前状态不能修改排期；失败任务请使用重试，需复核任务请先人工确认")
        readiness = self._require_ready_jobs([job_id], resolve_legacy=True, check_worker=True)[job_id]
        stored = to_utc_iso(parsed)
        now = utc_now_iso()
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE publish_jobs SET account_id = ?, publish_mode = ?,
                    scheduled_at = ?, next_attempt_at = NULL,
                    timezone = ?, schedule_timezone = ?, status = 'SCHEDULED', updated_at = ?
                WHERE id = ?
                """,
                (
                    readiness["resolved_account_id"] or job.get("account_id") or None,
                    readiness["resolved_publish_mode"] or job.get("publish_mode"),
                    stored,
                    settings.app_timezone,
                    settings.app_timezone,
                    now,
                    job_id,
                ),
            )
            self._record_auto_target_resolution(job, readiness, connection=connection)
            self.repository.add_event(
                job_id, "schedule_updated", from_status=str(job.get("status") or ""),
                to_status="SCHEDULED", payload={"scheduled_at": stored}, connection=connection,
            )
            connection.commit()
        wake_scheduler()
        return {"status": "ok", "job": self._public_job(job_id)}

    def preview_batch_schedule(
        self,
        job_ids: list[str],
        *,
        start_at_local: str,
        timezone_name: str,
        interval_minutes: int,
        daily_start_time: str,
        daily_end_time: str,
    ) -> dict[str, Any]:
        ids = self._validate_batch_jobs(job_ids)
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

    def update_batch_schedule(
        self,
        job_ids: list[str],
        *,
        action: str,
        start_at_local: str = "",
        timezone_name: str = "Asia/Shanghai",
        interval_minutes: int = 180,
        daily_start_time: str = "09:00",
        daily_end_time: str = "21:00",
        confirmed_schedule: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        ids = self._validate_batch_jobs(job_ids)
        if action not in {"apply", "clear"}:
            raise ValueError("不支持的排期操作")
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
            rows = connection.execute(
                f"SELECT id, status, account_id, publish_mode FROM publish_jobs WHERE id IN ({','.join('?' for _ in ids)})", ids
            ).fetchall()
            row_map = {row["id"]: dict(row) for row in rows}
            for item in schedule:
                job_id = item["job_id"]
                current = row_map[job_id]
                status = str(current["status"] or "").upper()
                if status not in {"DRAFT", "WAITING", "SCHEDULED"}:
                    raise ValueError(f"任务 {job_id} 当前状态不能修改排期")
                next_status = "SCHEDULED" if action == "apply" else "WAITING"
                readiness = readiness_map.get(job_id) or {}
                connection.execute(
                    """
                    UPDATE publish_jobs SET account_id = ?, publish_mode = ?,
                        scheduled_at = ?, next_attempt_at = NULL,
                        timezone = ?, schedule_timezone = ?, status = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        readiness.get("resolved_account_id") or current.get("account_id") or None,
                        readiness.get("resolved_publish_mode") or current.get("publish_mode"),
                        item["scheduled_at_utc"], timezone_name, timezone_name, next_status, now, job_id,
                    ),
                )
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
        return self._transition_user_status(job_id, "CANCELLED", "用户取消任务")

    def skip_job(self, job_id: str) -> dict[str, Any]:
        return self.cancel_job(job_id)

    def mark_failed_manually(self, job_id: str, message: str = "人工确认平台未发布") -> dict[str, Any]:
        job = self.repository.get_job(job_id)
        if not job:
            raise ValueError("发布任务不存在")
        if str(job.get("status") or "").upper() not in {"NEED_REVIEW", "SCHEDULED", "WAITING"}:
            raise ValueError("当前任务不能人工标记失败")
        return self._mark_failed(job_id, "manually_marked_failed", message)

    def mark_published_manually(self, job_id: str, platform_url: str) -> dict[str, Any]:
        job = self.repository.get_job(job_id)
        if not job:
            raise ValueError("发布任务不存在")
        if str(job.get("status") or "").upper() != "NEED_REVIEW":
            raise ValueError("只有需复核任务可以人工标记已发布")
        url = str(platform_url or "").strip()
        expected_domain = "douyin.com" if job.get("platform") == "douyin" else "bilibili.com"
        if not url.startswith(("http://", "https://")) or expected_domain not in url.lower():
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

    def _validate_batch_jobs(self, job_ids: list[str]) -> list[str]:
        ids = list(dict.fromkeys(str(job_id).strip() for job_id in job_ids if str(job_id).strip()))
        if not ids:
            raise ValueError("至少选择一条发布任务")
        with get_connection() as connection:
            count = connection.execute(
                f"SELECT COUNT(*) FROM publish_jobs WHERE id IN ({','.join('?' for _ in ids)})", ids
            ).fetchone()[0]
        if int(count) != len(ids):
            raise ValueError("部分发布任务不存在")
        return ids

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

    def _claim_scheduled_job(self, job_id: str) -> bool:
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
        return int(cursor.rowcount or 0) == 1

    def _handle_worker_unavailable(self, job: dict[str, Any], exc: PublishWorkerUnavailable) -> dict[str, Any]:
        execution_id = str(job.get("execution_id") or "")
        if exc.request_may_have_been_received and execution_id:
            try:
                execution = self.worker_client.execution(execution_id)
                phase = str(execution.get("phase") or "unknown")
                details = execution.get("details") if isinstance(execution.get("details"), dict) else {}
                if phase == "confirmed_success" and details:
                    return self._mark_published(str(job["id"]), PublishResult.from_dict(details))
                if phase not in {"received", "browser_opening", "browser_opened", "rejected"}:
                    return self._mark_need_review(
                        str(job["id"]), "publish_worker_result_uncertain",
                        "Worker 连接中断且任务可能已经上传，请人工确认平台结果",
                    )
            except PublishError:
                return self._mark_need_review(
                    str(job["id"]), "publish_worker_result_uncertain",
                    "Worker 超时后无法读取执行阶段，为避免重复投稿已停止自动重试",
                )
        attempts = int((self.repository.get_job(str(job["id"])) or job).get("attempt_count") or 0)
        max_attempts = max(1, int(job.get("max_attempts") or self.max_retry_count))
        if attempts >= max_attempts:
            return self._mark_failed(str(job["id"]), exc.error_code, f"{exc.message}；3 次上传前安全重试已用完")
        delays = (30, 120, 300)
        delay = delays[min(max(0, attempts - 1), len(delays) - 1)]
        next_attempt = (utc_now() + timedelta(seconds=delay)).isoformat(timespec="seconds")
        now = utc_now_iso()
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE publish_jobs SET status = 'SCHEDULED', next_attempt_at = ?,
                    error_code = ?, error_message = ?, last_error = ?,
                    execution_phase = 'worker_unavailable_before_upload', updated_at = ?
                WHERE id = ? AND status = 'PUBLISHING'
                """,
                (next_attempt, exc.error_code, exc.message, exc.message, now, job["id"]),
            )
            self.repository.add_event(
                str(job["id"]), "safe_retry_scheduled", from_status="PUBLISHING", to_status="SCHEDULED",
                worker_id=self.worker_id, error_code=exc.error_code, message=exc.message,
                payload={"next_attempt_at": next_attempt, "attempt_count": attempts}, connection=connection,
            )
            connection.commit()
        return {"status": "rescheduled", "job_id": job["id"], "next_attempt_at": next_attempt, "message": exc.message}

    def _reschedule_before_upload(self, job_id: str, message: str) -> None:
        now = utc_now_iso()
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE publish_jobs SET status = 'SCHEDULED', next_attempt_at = ?,
                    execution_phase = 'recovered_before_upload', last_error = ?, error_message = ?, updated_at = ?
                WHERE id = ? AND status = 'PUBLISHING'
                """,
                (now, message, message, now, job_id),
            )
            self.repository.add_event(
                job_id, "recovered_before_upload", from_status="PUBLISHING", to_status="SCHEDULED",
                message=message, connection=connection,
            )
            connection.commit()

    def _mark_published(self, job_id: str, result: PublishResult, *, require_publishing: bool = True) -> dict[str, Any]:
        self.repository.record_provider_result(job_id, result)
        now = utc_now_iso()
        condition = "AND status = 'PUBLISHING'" if require_publishing else "AND status = 'NEED_REVIEW'"
        with get_connection() as connection:
            cursor = connection.execute(
                f"""
                UPDATE publish_jobs SET status = 'PUBLISHED', published_at = ?, finished_at = ?,
                    platform_url = ?, remote_video_id = ?, platform_item_id = ?,
                    audit_status = 'submitted', needs_manual_review = 0,
                    error_code = '', error_message = '', last_error = '',
                    execution_phase = 'confirmed_success', updated_at = ?
                WHERE id = ? {condition}
                """,
                (result.published_at or now, now, result.platform_url, result.remote_video_id,
                 result.remote_video_id, now, job_id),
            )
            if cursor.rowcount:
                self.repository.add_event(
                    job_id, "published", from_status="PUBLISHING" if require_publishing else "NEED_REVIEW",
                    to_status="PUBLISHED", worker_id=self.worker_id, payload=result.as_dict(), connection=connection,
                )
            connection.commit()
        if not cursor.rowcount:
            return {"status": "skipped", "job_id": job_id, "message": "任务状态已变化，未覆盖最新状态"}
        self._append_log(job_id, "平台已确认投稿成功")
        return {"status": "published", "job_id": job_id, "publish_result": result.as_dict()}

    def _mark_exported(self, job_id: str, result: PublishResult) -> dict[str, Any]:
        self.repository.record_provider_result(job_id, result)
        now = utc_now_iso()
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE publish_jobs SET status = 'EXPORTED', finished_at = ?, published_at = NULL,
                    audit_status = 'not_submitted', execution_phase = 'exported', updated_at = ?
                WHERE id = ? AND status = 'PUBLISHING'
                """,
                (now, now, job_id),
            )
            self.repository.add_event(
                job_id, "exported", from_status="PUBLISHING", to_status="EXPORTED",
                payload=result.as_dict(), connection=connection,
            )
            connection.commit()
        return {"status": "exported", "job_id": job_id, "publish_result": result.as_dict()}

    def _mark_failed(
        self, job_id: str, error_code: str, message: str, result: PublishResult | None = None
    ) -> dict[str, Any]:
        if result:
            self.repository.record_provider_result(job_id, result)
        now = utc_now_iso()
        with get_connection() as connection:
            row = connection.execute("SELECT status FROM publish_jobs WHERE id = ?", (job_id,)).fetchone()
            from_status = str(row["status"] if row else "")
            connection.execute(
                """
                UPDATE publish_jobs SET status = 'FAILED', finished_at = ?, error_code = ?,
                    error_message = ?, last_error = ?, needs_manual_review = 0,
                    execution_phase = 'failed', updated_at = ?
                WHERE id = ? AND status NOT IN ('PUBLISHED', 'EXPORTED', 'CANCELLED')
                """,
                (now, error_code, message, message, now, job_id),
            )
            self.repository.add_event(
                job_id, "failed", from_status=from_status, to_status="FAILED",
                worker_id=self.worker_id, error_code=error_code, message=message, connection=connection,
            )
            connection.commit()
        self._append_log(job_id, f"发布失败：{message}")
        return {"status": "failed", "job_id": job_id, "error_code": error_code, "message": message}

    def _mark_need_review(
        self, job_id: str, error_code: str, message: str, result: PublishResult | None = None
    ) -> dict[str, Any]:
        if result:
            self.repository.record_provider_result(job_id, result)
        now = utc_now_iso()
        with get_connection() as connection:
            row = connection.execute("SELECT status FROM publish_jobs WHERE id = ?", (job_id,)).fetchone()
            from_status = str(row["status"] if row else "")
            connection.execute(
                """
                UPDATE publish_jobs SET status = 'NEED_REVIEW', finished_at = ?, error_code = ?,
                    error_message = ?, last_error = ?, needs_manual_review = 1,
                    execution_phase = 'manual_review', updated_at = ?
                WHERE id = ? AND status NOT IN ('PUBLISHED', 'EXPORTED', 'CANCELLED')
                """,
                (now, error_code, message, message, now, job_id),
            )
            self.repository.add_event(
                job_id, "needs_review", from_status=from_status, to_status="NEED_REVIEW",
                worker_id=self.worker_id, error_code=error_code, message=message, connection=connection,
            )
            connection.commit()
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
            except json.JSONDecodeError:
                continue
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
            pass


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
    asyncio.create_task(scheduler.run_forever())
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
