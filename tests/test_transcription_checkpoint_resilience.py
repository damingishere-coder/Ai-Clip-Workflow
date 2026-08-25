from __future__ import annotations

from datetime import datetime, timezone
import hashlib

import pytest

from app.db.database import get_connection, init_db
from app.services.transcript_service import TranscriptChunk, TranscriptSegment, _segment_from_checkpoint
from app.services.transcription_checkpoint_service import (
    RemoteTranscriptionResultUncertainError,
    TranscriptionCheckpoint,
)


def _create_task(task_id: str) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO tasks (
                id, task_name, task_dir_name, source_type, platform, selection_profile,
                status, progress, is_deleted, created_at, updated_at
            ) VALUES (?, 'checkpoint resilience', ?, 'upload', 'general', 'general',
                      'pending_processing', 0, 0, ?, ?)
            """,
            (task_id, task_id, now, now),
        )
        connection.commit()


def _cleanup(task_id: str) -> None:
    with get_connection() as connection:
        connection.execute("DELETE FROM transcription_chunks WHERE task_id = ?", (task_id,))
        connection.execute("DELETE FROM transcription_runs WHERE task_id = ?", (task_id,))
        connection.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        connection.commit()


def _checkpoint(task_id: str, source, provider: str = "local") -> TranscriptionCheckpoint:
    return TranscriptionCheckpoint(
        task_id=task_id,
        source_path=source,
        provider=provider,
        model="test-model",
        device="remote" if provider == "volcengine" else "cpu",
        compute_type="mp3" if provider == "volcengine" else "int8",
        chunk_seconds=120,
        overlap_seconds=5,
    )


def test_corrupted_completed_chunk_is_invalidated_and_recomputed(tmp_path):
    init_db()
    task_id = "test-checkpoint-corruption"
    source = tmp_path / "audio.wav"
    source.write_bytes(b"audio")
    _create_task(task_id)
    try:
        checkpoint = _checkpoint(task_id, source)
        checkpoint.ensure_run([TranscriptChunk(1, 0, 120)])
        checkpoint.save_completed(1, [TranscriptSegment(0, 1, "ok")])
        corrupt = "{not-json"
        checksum = hashlib.sha256(corrupt.encode("utf-8")).hexdigest()
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE transcription_chunks
                SET result_json = ?, result_checksum = ?
                WHERE run_id = ? AND chunk_index = 1
                """,
                (corrupt, checksum, checkpoint.run_id),
            )
            connection.commit()
        assert checkpoint.load_completed(1, _segment_from_checkpoint) is None
        with get_connection() as connection:
            row = connection.execute(
                "SELECT status, result_json FROM transcription_chunks WHERE run_id = ? AND chunk_index = 1",
                (checkpoint.run_id,),
            ).fetchone()
        assert row["status"] == "queued"
        assert row["result_json"] is None
    finally:
        _cleanup(task_id)


def test_unfinished_remote_request_requires_explicit_new_run(tmp_path):
    init_db()
    task_id = "test-checkpoint-remote-uncertain"
    source = tmp_path / "audio.mp3"
    source.write_bytes(b"remote-audio")
    chunks = [TranscriptChunk(1, 0, 120)]
    _create_task(task_id)
    try:
        first = _checkpoint(task_id, source, provider="volcengine")
        first.ensure_run(chunks)
        request_id = first.prepare_remote_request(1)
        assert request_id

        resumed = _checkpoint(task_id, source, provider="volcengine")
        resumed.ensure_run(chunks)
        assert resumed.run_id == first.run_id
        with pytest.raises(RemoteTranscriptionResultUncertainError, match="没有可靠结果"):
            resumed.prepare_remote_request(1)

        resumed.save_uncertain(1, "结果不确定")
        blocked_retry = _checkpoint(task_id, source, provider="volcengine")
        with pytest.raises(RemoteTranscriptionResultUncertainError, match="普通任务重试不会再次请求"):
            blocked_retry.ensure_run(chunks)

        explicit_retry = TranscriptionCheckpoint(
            task_id=task_id,
            source_path=source,
            provider="volcengine",
            model="test-model",
            device="remote",
            compute_type="mp3",
            chunk_seconds=120,
            overlap_seconds=5,
            allow_uncertain_retry=True,
        )
        explicit_retry.ensure_run(chunks)
        assert explicit_retry.run_id != first.run_id
    finally:
        _cleanup(task_id)
