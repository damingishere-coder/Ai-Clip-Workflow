"""发布任务持久化边界，集中处理脱敏结果和状态事件。"""

from __future__ import annotations

import json
from typing import Any

from app.db.database import get_connection
from app.services.publish_time import utc_now_iso
from app.services.publishers.base import PublishResult, sanitize_provider_response


class PublishRepository:
    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with get_connection() as connection:
            row = connection.execute("SELECT * FROM publish_jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row else None

    def record_provider_result(
        self,
        job_id: str,
        result: PublishResult,
        *,
        connection=None,
        updated_at: str | None = None,
    ) -> None:
        """保存脱敏平台结果；复用连接时由调用方统一提交事务。"""

        now = updated_at or utc_now_iso()
        provider_json = json.dumps(
            sanitize_provider_response(result.provider_response), ensure_ascii=False
        )
        result_json = json.dumps(result.as_dict(), ensure_ascii=False)
        values = (
            result.remote_video_id,
            result.remote_video_id,
            result.platform_url,
            provider_json,
            result_json,
            result.published_at or None,
            result.error_code,
            result.message if result.error_code else "",
            result.message if result.error_code else "",
            int(result.needs_manual_review),
            now,
            job_id,
        )
        sql = """
            UPDATE publish_jobs
            SET remote_video_id = ?, platform_item_id = ?, platform_url = ?,
                provider_response = ?, publish_result = ?, published_at = ?,
                error_code = ?, last_error = ?, error_message = ?,
                needs_manual_review = ?, updated_at = ?
            WHERE id = ?
        """
        if connection is not None:
            connection.execute(sql, values)
            return
        with get_connection() as owned:
            owned.execute(sql, values)
            owned.commit()

    def update_execution_phase(
        self,
        job_id: str,
        phase: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """同步 Worker 的实时阶段；不会在这里改变任务最终状态。"""

        values = sanitize_provider_response(details or {})
        message = str(values.get("message") or "") if isinstance(values, dict) else ""
        now = utc_now_iso()
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE publish_jobs
                SET execution_phase = ?,
                    last_error = CASE WHEN ? <> '' THEN ? ELSE last_error END,
                    error_message = CASE WHEN ? <> '' THEN ? ELSE error_message END,
                    updated_at = ?
                WHERE id = ? AND status = 'PUBLISHING'
                """,
                (phase, message, message, message, message, now, job_id),
            )
            connection.commit()

    def add_event(
        self,
        job_id: str,
        event_type: str,
        *,
        from_status: str = "",
        to_status: str = "",
        worker_id: str = "",
        error_code: str = "",
        message: str = "",
        payload: dict[str, Any] | None = None,
        connection=None,
    ) -> None:
        values = (
            job_id,
            event_type,
            from_status,
            to_status,
            worker_id,
            error_code,
            message,
            json.dumps(sanitize_provider_response(payload or {}), ensure_ascii=False),
            utc_now_iso(),
        )
        sql = """
            INSERT INTO publish_job_events (
                job_id, event_type, from_status, to_status, worker_id,
                error_code, message, payload, occurred_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        if connection is not None:
            connection.execute(sql, values)
            return
        with get_connection() as owned:
            owned.execute(sql, values)
            owned.commit()

    def list_events(self, job_id: str) -> list[dict[str, Any]]:
        with get_connection() as connection:
            rows = connection.execute(
                "SELECT * FROM publish_job_events WHERE job_id = ? ORDER BY occurred_at, id",
                (job_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_account_status(
        self,
        account_id: str,
        login_status: str,
        message: str = "",
        *,
        logged_in: bool = False,
    ) -> None:
        now = utc_now_iso()
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE publish_accounts
                SET login_status = ?, login_message = ?, login_checked_at = ?,
                    last_login_at = CASE WHEN ? THEN ? ELSE last_login_at END,
                    authorization_status = CASE WHEN ? THEN 'authorized' ELSE authorization_status END,
                    updated_at = ?
                WHERE id = ?
                """,
                (login_status, message, now, int(logged_in), now, int(logged_in), now, account_id),
            )
            connection.commit()
