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
    ) -> None:
        self.task_id = task_id
        self.source_fingerprint = fingerprint_file(source_path)
        self.provider = provider
        self.model = model
        self.device = device
        self.compute_type = compute_type
        self.chunk_seconds = chunk_seconds
        self.overlap_seconds = overlap_seconds
        self.run_id = ""

    def ensure_run(self, chunks) -> str:
        if self.run_id:
            return self.run_id
        now = _now_iso()
        with get_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            _assert_current_lease(connection)
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
            return None
        payload = json.loads(raw)
        return [segment_factory(item) for item in payload]

    def save_completed(self, chunk_index: int, segments) -> None:
        now = _now_iso()
        raw = json.dumps([asdict(segment) for segment in segments], ensure_ascii=False, separators=(",", ":"))
        checksum = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        with get_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            _assert_current_lease(connection)
            connection.execute(
                """
                UPDATE transcription_chunks
                SET status = 'completed', attempt_count = attempt_count + 1, result_json = ?,
                    result_checksum = ?, error_message = NULL, updated_at = ?
                WHERE run_id = ? AND chunk_index = ?
                """,
                (raw, checksum, now, self.run_id, chunk_index),
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

    def save_failed(self, chunk_index: int, error: str) -> None:
        now = _now_iso()
        with get_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            _assert_current_lease(connection)
            connection.execute(
                """UPDATE transcription_chunks SET status = 'failed', attempt_count = attempt_count + 1,
                   error_message = ?, updated_at = ? WHERE run_id = ? AND chunk_index = ?""",
                (error[:2000], now, self.run_id, chunk_index),
            )
            connection.execute(
                "UPDATE transcription_runs SET status = 'failed', error_message = ?, updated_at = ? WHERE id = ?",
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
