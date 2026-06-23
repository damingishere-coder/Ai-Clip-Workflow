from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime
from typing import Any

from app.core.config import settings
from app.db.database import get_connection, init_db
from app.services.publish_adapters import PublishValidationError, publisher_for_job
from app.services.task_log_service import append_task_log


PUBLISH_STATUSES = {
    "DRAFT",
    "SCHEDULED",
    "WAITING",
    "PUBLISHING",
    "PUBLISHED",
    "FAILED",
    "CANCELLED",
    "NEED_REVIEW",
}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def parse_datetime(value: str | None) -> datetime:
    text = (value or "").strip()
    if not text:
        raise ValueError("scheduled_at is empty")
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.astimezone()
    return parsed.astimezone()


def _row_to_dict(row) -> dict[str, Any] | None:
    return dict(row) if row else None


def _parse_json(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _risk_flags(job: dict[str, Any]) -> list[Any]:
    direct = _parse_json(job.get("risk_flags"))
    if isinstance(direct, list):
        return [item for item in direct if item]
    if isinstance(direct, str) and direct.strip():
        return [direct.strip()]
    provider = _parse_json(job.get("provider_response"))
    if isinstance(provider, dict) and isinstance(provider.get("risk_flags"), list):
        return [item for item in provider["risk_flags"] if item]
    return []


def get_publish_job_raw(job_id: str) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM publish_jobs WHERE id = ?", (job_id,)).fetchone()
    return _row_to_dict(row)


class PublishScheduler:
    def __init__(
        self,
        interval_seconds: int | None = None,
        max_retry_count: int | None = None,
    ) -> None:
        self.interval_seconds = max(1, int(interval_seconds or settings.publish_scheduler_interval_seconds))
        self.max_retry_count = max(0, int(max_retry_count if max_retry_count is not None else settings.publish_scheduler_max_retry_count))
        self._stop_event: asyncio.Event | None = None

    def run_once(self) -> dict[str, Any]:
        init_db()
        self.recover_interrupted_jobs()
        jobs = self.list_due_jobs()
        results = [self.execute_job(job["id"]) for job in jobs]
        return {
            "status": "ok",
            "checked_at": now_iso(),
            "matched_count": len(jobs),
            "published_count": sum(1 for item in results if item.get("status") == "published"),
            "failed_count": sum(1 for item in results if item.get("status") == "failed"),
            "skipped_count": sum(1 for item in results if item.get("status") == "skipped"),
            "results": results,
        }

    async def run_forever(self) -> None:
        init_db()
        self._stop_event = asyncio.Event()
        while not self._stop_event.is_set():
            self.run_once()
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.interval_seconds)
            except TimeoutError:
                continue

    def stop(self) -> None:
        if self._stop_event:
            self._stop_event.set()

    def recover_interrupted_jobs(self) -> int:
        now = now_iso()
        with get_connection() as connection:
            cursor = connection.execute(
                """
                UPDATE publish_jobs
                SET status = 'SCHEDULED',
                    last_error = COALESCE(NULLIF(last_error, ''), 'Recovered from interrupted PUBLISHING state'),
                    error_message = COALESCE(NULLIF(error_message, ''), 'Recovered from interrupted PUBLISHING state'),
                    updated_at = ?
                WHERE status = 'PUBLISHING' AND published_at IS NULL
                """,
                (now,),
            )
            connection.commit()
        return int(cursor.rowcount or 0)

    def list_due_jobs(self) -> list[dict[str, Any]]:
        current = datetime.now().astimezone()
        due: list[dict[str, Any]] = []
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM publish_jobs
                WHERE status = 'SCHEDULED'
                ORDER BY scheduled_at ASC, created_at ASC
                """
            ).fetchall()
        for row in rows:
            job = dict(row)
            try:
                scheduled_at = parse_datetime(job.get("scheduled_at"))
            except ValueError as exc:
                due.append({**job, "_invalid_schedule_error": str(exc)})
                continue
            if scheduled_at <= current:
                due.append(job)
        return due

    def execute_job(self, job_id: str, *, force: bool = False, allow_republish: bool = False) -> dict[str, Any]:
        job = get_publish_job_raw(job_id)
        if not job:
            return {"status": "failed", "job_id": job_id, "message": "publish job not found"}

        status = str(job.get("status") or "").upper()
        if status == "PUBLISHED" and not allow_republish:
            return {"status": "skipped", "job_id": job_id, "message": "already published"}
        if status in {"CANCELLED", "NEED_REVIEW"}:
            return {"status": "skipped", "job_id": job_id, "message": f"status is {status}"}
        if status not in {"SCHEDULED", "FAILED", "PUBLISHING"} and not force:
            return {"status": "skipped", "job_id": job_id, "message": f"status is {status}"}

        if _risk_flags(job) and not settings.publish_scheduler_allow_publish_without_review:
            self._mark_need_review(job_id, _risk_flags(job))
            return {"status": "skipped", "job_id": job_id, "message": "risk flags require review"}

        try:
            scheduled_at = parse_datetime(job.get("scheduled_at"))
        except ValueError as exc:
            return self._mark_failed(job_id, "invalid_scheduled_at", str(exc))
        if not force and scheduled_at > datetime.now().astimezone():
            return {"status": "skipped", "job_id": job_id, "message": "scheduled_at is in the future"}

        attempts = int(job.get("attempt_count") or job.get("retry_count") or 0)
        if not force and attempts >= self.max_retry_count:
            return self._mark_failed(job_id, "max_retry_exceeded", "max retry count exceeded")

        self._mark_publishing(job_id)
        job = get_publish_job_raw(job_id) or job
        try:
            result = publisher_for_job(job).publish(job)
        except PublishValidationError as exc:
            return self._mark_failed(job_id, exc.error_code, exc.message)
        except Exception as exc:
            return self._mark_failed(job_id, "publish_failed", str(exc) or exc.__class__.__name__)

        return self._mark_published(job_id, result.payload, result.remote_video_id)

    def publish_now(self, job_id: str, *, allow_republish: bool = False) -> dict[str, Any]:
        self._set_schedule_to_now(job_id)
        return self.execute_job(job_id, force=True, allow_republish=allow_republish)

    def retry_failed(self, job_id: str) -> dict[str, Any]:
        job = get_publish_job_raw(job_id)
        if not job:
            raise ValueError("publish job not found")
        if str(job.get("status") or "").upper() != "FAILED":
            raise ValueError("only FAILED publish jobs can be retried")
        self._set_schedule_to_now(job_id)
        return self.execute_job(job_id, force=True)

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        return self._update_status(job_id, "CANCELLED", "cancelled manually")

    def skip_job(self, job_id: str) -> dict[str, Any]:
        return self._update_status(job_id, "CANCELLED", "skipped manually", {"action": "skip"})

    def approve_review(self, job_id: str) -> dict[str, Any]:
        job = get_publish_job_raw(job_id)
        if not job:
            raise ValueError("publish job not found")
        if str(job.get("status") or "").upper() != "NEED_REVIEW":
            raise ValueError("only NEED_REVIEW jobs can be approved")
        now = now_iso()
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE publish_jobs
                SET status = 'SCHEDULED', risk_flags = '', last_error = '',
                    error_message = '', updated_at = ?
                WHERE id = ?
                """,
                (now, job_id),
            )
            connection.commit()
        return {"status": "ok", "job": get_publish_job_raw(job_id)}

    def update_schedule(self, job_id: str, scheduled_at: str) -> dict[str, Any]:
        parsed = parse_datetime(scheduled_at)
        job = get_publish_job_raw(job_id)
        if not job:
            raise ValueError("publish job not found")
        if str(job.get("status") or "").upper() == "PUBLISHED":
            raise ValueError("published jobs cannot be rescheduled")
        now = now_iso()
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE publish_jobs
                SET scheduled_at = ?, status = 'SCHEDULED', updated_at = ?
                WHERE id = ?
                """,
                (parsed.isoformat(timespec="seconds"), now, job_id),
            )
            connection.commit()
        return {"status": "ok", "job": get_publish_job_raw(job_id)}

    def _set_schedule_to_now(self, job_id: str) -> None:
        now = now_iso()
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE publish_jobs
                SET scheduled_at = ?, status = CASE
                    WHEN status = 'PUBLISHED' THEN status
                    WHEN status = 'CANCELLED' THEN status
                    WHEN status = 'NEED_REVIEW' THEN status
                    ELSE 'SCHEDULED'
                END,
                updated_at = ?
                WHERE id = ?
                """,
                (now, now, job_id),
            )
            connection.commit()

    def _mark_publishing(self, job_id: str) -> None:
        now = now_iso()
        payload = json.dumps({"publisher": "started", "started_at": now}, ensure_ascii=False)
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE publish_jobs
                SET status = 'PUBLISHING',
                    attempt_count = COALESCE(attempt_count, 0) + 1,
                    retry_count = COALESCE(retry_count, 0) + 1,
                    last_error = '',
                    error_message = '',
                    publish_result = ?,
                    provider_response = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (payload, payload, now, job_id),
            )
            connection.commit()

    def _mark_published(self, job_id: str, payload: dict[str, Any], remote_video_id: str) -> dict[str, Any]:
        now = now_iso()
        publish_result = json.dumps(payload, ensure_ascii=False)
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE publish_jobs
                SET status = 'PUBLISHED',
                    publish_result = ?,
                    provider_response = ?,
                    remote_video_id = ?,
                    platform_item_id = ?,
                    published_at = ?,
                    last_error = '',
                    error_message = '',
                    error_code = '',
                    audit_status = 'submitted',
                    updated_at = ?
                WHERE id = ?
                """,
                (publish_result, publish_result, remote_video_id, remote_video_id, now, now, job_id),
            )
            connection.commit()
        job = get_publish_job_raw(job_id) or {"task_id": ""}
        self._append_log(job.get("task_id") or "", f"Publish job {job_id} completed by manual_export")
        return {"status": "published", "job_id": job_id, "publish_result": payload}

    def _mark_failed(self, job_id: str, error_code: str, message: str) -> dict[str, Any]:
        now = now_iso()
        payload = json.dumps(
            {"error_code": error_code, "message": message, "failed_at": now},
            ensure_ascii=False,
        )
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE publish_jobs
                SET status = 'FAILED',
                    error_code = ?,
                    error_message = ?,
                    last_error = ?,
                    publish_result = ?,
                    provider_response = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (error_code, message, message, payload, payload, now, job_id),
            )
            connection.commit()
        job = get_publish_job_raw(job_id) or {"task_id": ""}
        self._append_log(job.get("task_id") or "", f"Publish job {job_id} failed: {message}")
        return {"status": "failed", "job_id": job_id, "error_code": error_code, "message": message}

    def _mark_need_review(self, job_id: str, risk_flags: list[Any]) -> None:
        now = now_iso()
        message = f"risk flags require manual review: {risk_flags}"
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE publish_jobs
                SET status = 'NEED_REVIEW', last_error = ?, error_message = ?,
                    risk_flags = ?, updated_at = ?
                WHERE id = ?
                """,
                (message, message, json.dumps(risk_flags, ensure_ascii=False), now, job_id),
            )
            connection.commit()

    def _update_status(
        self,
        job_id: str,
        status: str,
        message: str = "",
        result_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        job = get_publish_job_raw(job_id)
        if not job:
            raise ValueError("publish job not found")
        if str(job.get("status") or "").upper() == "PUBLISHED" and status != "PUBLISHED":
            raise ValueError("published jobs cannot be changed by this operation")
        now = now_iso()
        payload = json.dumps(result_payload or {"message": message, "updated_at": now}, ensure_ascii=False)
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE publish_jobs
                SET status = ?, last_error = ?, error_message = ?,
                    publish_result = ?, provider_response = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, message, message, payload, payload, now, job_id),
            )
            connection.commit()
        return {"status": "ok", "job": get_publish_job_raw(job_id)}

    def _append_log(self, task_id: str, message: str) -> None:
        if not task_id:
            return
        try:
            append_task_log(task_id, message)
        except Exception:
            return


def queue_snapshot(task_id: str | None = None) -> dict[str, Any]:
    params: list[Any] = []
    where = ""
    if task_id:
        where = "WHERE task_id = ?"
        params.append(task_id)
    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT *
            FROM publish_jobs
            {where}
            ORDER BY scheduled_at ASC, created_at DESC
            """,
            params,
        ).fetchall()
    jobs = [dict(row) for row in rows]
    by_status = {status: [job for job in jobs if str(job.get("status") or "").upper() == status] for status in PUBLISH_STATUSES}
    today = datetime.now().astimezone().date()
    today_jobs = []
    for job in jobs:
        try:
            if parse_datetime(job.get("scheduled_at")).date() == today:
                today_jobs.append(job)
        except ValueError:
            continue
    return {
        "all": jobs,
        "pending": by_status["SCHEDULED"] + by_status["WAITING"],
        "publishing": by_status["PUBLISHING"],
        "published": by_status["PUBLISHED"],
        "failed": by_status["FAILED"],
        "need_review": by_status["NEED_REVIEW"],
        "cancelled": by_status["CANCELLED"],
        "today": today_jobs,
        "counts": {status: len(items) for status, items in by_status.items()},
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

    async def _runner() -> None:
        await scheduler.run_forever()

    asyncio.run(_runner())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
