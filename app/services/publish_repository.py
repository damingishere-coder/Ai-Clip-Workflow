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
        expected_execution_id: str | None = None,
        connection=None,
        updated_at: str | None = None,
    ) -> bool:
        """保存脱敏平台结果；可用 execution_id 拒绝旧执行写回。"""

        now = updated_at or utc_now_iso()
        provider_json = json.dumps(
            sanitize_provider_response(result.provider_response), ensure_ascii=False
        )
        result_json = json.dumps(sanitize_provider_response(result.as_dict()), ensure_ascii=False)
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
            *((expected_execution_id,) if expected_execution_id else ()),
        )
        execution_condition = (
            " AND status = 'PUBLISHING' AND execution_id = ?"
            if expected_execution_id
            else ""
        )
        sql = f"""
            UPDATE publish_jobs
            SET remote_video_id = ?, platform_item_id = ?, platform_url = ?,
                provider_response = ?, publish_result = ?, published_at = ?,
                error_code = ?, last_error = ?, error_message = ?,
                needs_manual_review = ?, updated_at = ?
            WHERE id = ?{execution_condition}
        """
        if connection is not None:
            cursor = connection.execute(sql, values)
            return int(cursor.rowcount or 0) == 1
        with get_connection() as owned:
            cursor = owned.execute(sql, values)
            owned.commit()
        return int(cursor.rowcount or 0) == 1

    def update_execution_phase(
        self,
        job_id: str,
        phase: str,
        details: dict[str, Any] | None = None,
        *,
        expected_execution_id: str | None = None,
    ) -> bool:
        """同步 Worker 实时阶段；提供 execution 时拒绝旧执行写回。"""

        values = sanitize_provider_response(details or {})
        message = str(values.get("message") or "") if isinstance(values, dict) else ""
        now = utc_now_iso()
        execution_condition = " AND execution_id = ?" if expected_execution_id else ""
        params = (
            phase,
            message,
            message,
            message,
            message,
            now,
            job_id,
            *((expected_execution_id,) if expected_execution_id else ()),
        )
        with get_connection() as connection:
            cursor = connection.execute(
                f"""
                UPDATE publish_jobs
                SET execution_phase = ?,
                    last_error = CASE WHEN ? <> '' THEN ? ELSE last_error END,
                    error_message = CASE WHEN ? <> '' THEN ? ELSE error_message END,
                    updated_at = ?
                WHERE id = ? AND status = 'PUBLISHING'{execution_condition}
                """,
                params,
            )
            connection.commit()
        return int(cursor.rowcount or 0) == 1

    def begin_execution_dispatch(
        self,
        job_id: str,
        expected_execution_id: str,
        expected_updated_at: str,
    ) -> bool:
        """在外部投稿前原子保留 dispatch 权；与恢复扫描的快照写回互斥。"""

        now = utc_now_iso()
        with get_connection() as connection:
            cursor = connection.execute(
                """
                UPDATE publish_jobs
                SET execution_phase = 'dispatching', updated_at = ?
                WHERE id = ? AND status = 'PUBLISHING' AND execution_id = ?
                    AND updated_at = ?
                """,
                (now, job_id, expected_execution_id, expected_updated_at),
            )
            connection.commit()
        return int(cursor.rowcount or 0) == 1

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
