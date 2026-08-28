"""SQLite 转写分块 checkpoint。

每个块成功后独立提交；进程失败或重启时只读取同一源指纹和同一运行配置下的成功块。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.db.database import get_connection
from app.services import job_service


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _assert_current_lease(connection) -> None:
    active = job_service.current_job_lease()
    if active is None:
        return
    job_id, lease_owner, lease_token = active
    row = connection.execute(
        """
        SELECT 1 FROM workflow_jobs
        WHERE id = ? AND status = 'running' AND lease_owner = ? AND lease_token = ?
          AND lease_expires_at > ?
        """,
        (job_id, lease_owner, lease_token, _now_iso()),
    ).fetchone()
    if not row:
        raise job_service.JobLeaseLostError(f"Workflow Job 租约已失效：{job_id}")


def fingerprint_file(path_value: str | Path) -> str:
    path = Path(path_value).resolve()
    digest = hashlib.sha256()
    size = path.stat().st_size
    digest.update(str(size).encode("ascii"))
    with path.open("rb") as source:
        digest.update(source.read(1024 * 1024))
        if size > 1024 * 1024:
            source.seek(max(0, size - 1024 * 1024))
            digest.update(source.read(1024 * 1024))
    return digest.hexdigest()


class TranscriptionCheckpoint:
    def __init__(
        self,
        *,
        task_id: str,
        source_path: str | Path,
        provider: str,
        model: str,
        device: str,
        compute_type: str,
        chunk_seconds: int,
        overlap_seconds: int,
        allow_uncertain_retry: bool = False,
    ) -> None:
        self.task_id = task_id
        self.source_fingerprint = fingerprint_file(source_path)
        self.provider = provider
        self.model = model
        self.device = device
        self.compute_type = compute_type
        self.chunk_seconds = chunk_seconds
        self.overlap_seconds = overlap_seconds
        self.allow_uncertain_retry = allow_uncertain_retry
        self.run_id = ""

    def ensure_run(self, chunks) -> str:
        if self.run_id:
            return self.run_id
        now = _now_iso()
        with get_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            _assert_current_lease(connection)
            uncertain = connection.execute(
                """
                SELECT id FROM transcription_runs
                WHERE task_id = ? AND source_fingerprint = ? AND provider = ? AND model = ?
                  AND device = ? AND compute_type = ? AND chunk_seconds = ? AND overlap_seconds = ?
                  AND status = 'uncertain'
                ORDER BY updated_at DESC LIMIT 1
                """,
                (
                    self.task_id, self.source_fingerprint, self.provider, self.model,
                    self.device, self.compute_type, self.chunk_seconds, self.overlap_seconds,
                ),
            ).fetchone()
            if uncertain and not self.allow_uncertain_retry:
                connection.rollback()
                raise RemoteTranscriptionResultUncertainError(
                    "上次远程转写请求可能已计费，但结果未可靠保存；"
                    "普通任务重试不会再次请求。请由用户明确选择重新生成转写。"
                )
            row = connection.execute(
                """
                SELECT id FROM transcription_runs
                WHERE task_id = ? AND source_fingerprint = ? AND provider = ? AND model = ?
                  AND device = ? AND compute_type = ? AND chunk_seconds = ? AND overlap_seconds = ?
                  AND status IN ('processing', 'failed', 'completed')
                ORDER BY updated_at DESC LIMIT 1
                """,
                (
                    self.task_id, self.source_fingerprint, self.provider, self.model,
                    self.device, self.compute_type, self.chunk_seconds, self.overlap_seconds,
                ),
            ).fetchone()
            connection.execute(
                "UPDATE transcription_runs SET is_active = 0 WHERE task_id = ?",
                (self.task_id,),
            )
            if row:
                self.run_id = row["id"]
                connection.execute(
                    "UPDATE transcription_runs SET status = 'processing', is_active = 1, error_message = NULL, total_chunks = ?, updated_at = ? WHERE id = ?",
                    (len(chunks), now, self.run_id),
                )
            else:
                self.run_id = uuid4().hex
                connection.execute(
                    """
                    INSERT INTO transcription_runs (
                        id, task_id, source_fingerprint, provider, model, device, compute_type,
                        chunk_seconds, overlap_seconds, status, total_chunks, completed_chunks,
                        is_active, error_message, created_at, updated_at, completed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'processing', ?, 0, 1, NULL, ?, ?, NULL)
                    """,
                    (
                        self.run_id, self.task_id, self.source_fingerprint, self.provider,
                        self.model, self.device, self.compute_type, self.chunk_seconds,
                        self.overlap_seconds, len(chunks), now, now,
                    ),
                )
            for chunk in chunks:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO transcription_chunks (
                        id, run_id, task_id, chunk_index, start_ms, end_ms, status,
                        attempt_count, result_json, result_checksum, error_message, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'queued', 0, NULL, NULL, NULL, ?, ?)
                    """,
                    (
                        uuid4().hex, self.run_id, self.task_id, chunk.index,
                        round(chunk.start_seconds * 1000), round(chunk.end_seconds * 1000), now, now,
                    ),
                )
            connection.commit()
        return self.run_id

    def load_completed(self, chunk_index: int, segment_factory) -> list | None:
        with get_connection() as connection:
            _assert_current_lease(connection)
            row = connection.execute(
                "SELECT result_json, result_checksum FROM transcription_chunks WHERE run_id = ? AND chunk_index = ? AND status = 'completed'",
                (self.run_id, chunk_index),
            ).fetchone()
        if not row or not row["result_json"]:
            return None
        raw = str(row["result_json"])
        if hashlib.sha256(raw.encode("utf-8")).hexdigest() != str(row["result_checksum"] or ""):
            self._invalidate_completed_chunk(chunk_index, "checkpoint checksum 不一致，已重新排队")
            return None
        try:
            payload = json.loads(raw)
            if not isinstance(payload, list):
                raise ValueError("checkpoint 顶层不是数组")
            return [segment_factory(item) for item in payload if isinstance(item, dict)]
        except (json.JSONDecodeError, TypeError, ValueError, KeyError) as exc:
            self._invalidate_completed_chunk(chunk_index, f"checkpoint 内容损坏，已重新排队：{exc}")
            return None

    def _invalidate_completed_chunk(self, chunk_index: int, error: str) -> None:
        with get_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            _assert_current_lease(connection)
            connection.execute(
                """
                UPDATE transcription_chunks
                SET status = 'queued', result_json = NULL, result_checksum = NULL,
                    error_message = ?, updated_at = ?
                WHERE run_id = ? AND chunk_index = ? AND status = 'completed'
                """,
                (error[:2000], _now_iso(), self.run_id, chunk_index),
            )
            connection.commit()

    def prepare_remote_request(self, chunk_index: int) -> str:
        """在远程副作用前落账；遗留 requesting 表示上次结果不确定，禁止自动重发。"""
        now = _now_iso()
        request_id = hashlib.sha256(
            f"{self.run_id}:{self.source_fingerprint}:{chunk_index}".encode("utf-8")
        ).hexdigest()[:32]
        with get_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            _assert_current_lease(connection)
            row = connection.execute(
                "SELECT status FROM transcription_chunks WHERE run_id = ? AND chunk_index = ?",
                (self.run_id, chunk_index),
            ).fetchone()
            if not row:
                connection.rollback()
                raise RuntimeError(f"找不到远程转写分片 checkpoint：{chunk_index}")
            if row["status"] in {"requesting", "uncertain"}:
                connection.rollback()
                raise RemoteTranscriptionResultUncertainError(
                    f"第 {chunk_index} 段上次远程请求已发出但没有可靠结果；"
                    "本次未自动重发，请由用户明确重新生成转写。"
                )
            connection.execute(
                """
                UPDATE transcription_chunks
                SET status = 'requesting', attempt_count = attempt_count + 1,
                    error_message = ?, updated_at = ?
                WHERE run_id = ? AND chunk_index = ?
                """,
                (f"request_id={request_id}", now, self.run_id, chunk_index),
            )
            connection.commit()
        return request_id

    def save_completed(self, chunk_index: int, segments, *, attempt_already_counted: bool = False) -> None:
        now = _now_iso()
        raw = json.dumps([asdict(segment) for segment in segments], ensure_ascii=False, separators=(",", ":"))
        checksum = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        with get_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            _assert_current_lease(connection)
            connection.execute(
                """
                UPDATE transcription_chunks
                    SET status = 'completed', attempt_count = attempt_count + ?, result_json = ?,
                    result_checksum = ?, error_message = NULL, updated_at = ?
                WHERE run_id = ? AND chunk_index = ?
                """,
                (0 if attempt_already_counted else 1, raw, checksum, now, self.run_id, chunk_index),
            )
            completed = connection.execute(
                "SELECT COUNT(*) FROM transcription_chunks WHERE run_id = ? AND status = 'completed'",
                (self.run_id,),
            ).fetchone()[0]
            connection.execute(
                "UPDATE transcription_runs SET completed_chunks = ?, updated_at = ? WHERE id = ?",
                (completed, now, self.run_id),
            )
            connection.commit()

    def save_failed(self, chunk_index: int, error: str, *, attempt_already_counted: bool = False) -> None:
        now = _now_iso()
        with get_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            _assert_current_lease(connection)
            connection.execute(
                """UPDATE transcription_chunks SET status = 'failed', attempt_count = attempt_count + ?,
                   error_message = ?, updated_at = ? WHERE run_id = ? AND chunk_index = ?""",
                (0 if attempt_already_counted else 1, error[:2000], now, self.run_id, chunk_index),
            )
            connection.execute(
                "UPDATE transcription_runs SET status = 'failed', error_message = ?, updated_at = ? WHERE id = ?",
                (error[:2000], now, self.run_id),
            )
            connection.commit()

    def save_uncertain(self, chunk_index: int, error: str) -> None:
        now = _now_iso()
        with get_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            _assert_current_lease(connection)
            connection.execute(
                """
                UPDATE transcription_chunks
                SET status = 'uncertain', error_message = ?, updated_at = ?
                WHERE run_id = ? AND chunk_index = ?
                """,
                (error[:2000], now, self.run_id, chunk_index),
            )
            connection.execute(
                """
                UPDATE transcription_runs
                SET status = 'uncertain', error_message = ?, updated_at = ?
                WHERE id = ?
                """,
                (error[:2000], now, self.run_id),
            )
            connection.commit()

    def complete(self) -> None:
        now = _now_iso()
        with get_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            _assert_current_lease(connection)
            connection.execute(
                """UPDATE transcription_runs SET status = 'completed', is_active = 1,
                   error_message = NULL, completed_at = ?, updated_at = ? WHERE id = ?""",
                (now, now, self.run_id),
            )
            connection.commit()


class RemoteTranscriptionResultUncertainError(RuntimeError):
    """远程调用已发出但结果未可靠持久化，必须停止自动重试。"""
