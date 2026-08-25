"""Workflow Job 租约代际隔离回归测试。"""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.db.database import get_connection, init_db
from app.services import job_service, job_worker


def _create_task_and_job(*, max_attempts: int = 3) -> tuple[str, dict]:
    init_db()
    task_id = f"fencing-{uuid4().hex}"
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO tasks (
                id, task_name, task_dir_name, source_type, platform, selection_profile,
                status, progress, is_deleted, created_at, updated_at
            ) VALUES (?, 'fencing', ?, 'upload', 'general', 'general',
                'pending_video', 0, 0, ?, ?)
            """,
            (task_id, task_id, now, now),
        )
        connection.commit()
    job = job_service.create_job(task_id, job_service.JOB_TYPE_VIDEO_CUT)
    if max_attempts != 3:
        with get_connection() as connection:
            connection.execute(
                "UPDATE workflow_jobs SET max_attempts = ? WHERE id = ?",
                (max_attempts, job["id"]),
            )
            connection.commit()
    return task_id, job


def _expire(job_id: str) -> None:
    with get_connection() as connection:
        connection.execute(
            "UPDATE workflow_jobs SET lease_expires_at = '2000-01-01T00:00:00+00:00' WHERE id = ?",
            (job_id,),
        )
        connection.commit()


def _cleanup(task_id: str) -> None:
    with get_connection() as connection:
        connection.execute("DELETE FROM workflow_jobs WHERE task_id = ?", (task_id,))
        connection.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        connection.commit()


def test_stale_worker_cannot_overwrite_new_claim() -> None:
    task_id, created = _create_task_and_job()
    try:
        first = job_service.claim_job(created["id"], "worker-a")
        assert first and first["lease_token"]
        _expire(created["id"])
        second = job_service.claim_job(created["id"], "worker-b")
        assert second and second["lease_token"] != first["lease_token"]

        stale_operations = (
            lambda: job_service.update_job_progress(
                created["id"], 90, "stale", lease_owner="worker-a", lease_token=first["lease_token"]
            ),
            lambda: job_service.update_job_checkpoint(
                created["id"], {"stale": True}, lease_owner="worker-a", lease_token=first["lease_token"]
            ),
            lambda: job_service.heartbeat_job(created["id"], "worker-a", first["lease_token"]),
            lambda: job_service.mark_job_completed(
                created["id"], lease_owner="worker-a", lease_token=first["lease_token"]
            ),
            lambda: job_service.mark_job_failed(
                created["id"], "stale", lease_owner="worker-a", lease_token=first["lease_token"]
            ),
            lambda: job_service.mark_job_cancelled(
                created["id"], lease_owner="worker-a", lease_token=first["lease_token"]
            ),
        )
        for operation in stale_operations:
            with pytest.raises(job_service.JobLeaseLostError):
                operation()
        assert job_service.release_job_lease(created["id"], "worker-a", first["lease_token"]) is False

        current = job_service.get_job(created["id"])
        assert current["status"] == job_service.JOB_STATUS_RUNNING
        assert current["lease_owner"] == "worker-b"
        assert current["lease_token"] == second["lease_token"]
        assert current["progress"] == 10
        assert current["checkpoint_json"] == {}

        job_service.update_job_progress(
            created["id"], 35, "current", lease_owner="worker-b", lease_token=second["lease_token"]
        )
        assert job_service.heartbeat_job(created["id"], "worker-b", second["lease_token"])
    finally:
        _cleanup(task_id)


def test_expired_lease_cannot_heartbeat_or_start_claimed_subprocess(monkeypatch) -> None:
    task_id, created = _create_task_and_job()
    called = False
    try:
        claimed = job_service.claim_job(created["id"], "worker-a")
        _expire(created["id"])
        with pytest.raises(job_service.JobLeaseLostError):
            job_service.heartbeat_job(created["id"], "worker-a", claimed["lease_token"])

        def forbidden_handler(_task_id: str):
            nonlocal called
            called = True
            return {}

        monkeypatch.setattr(job_worker.task_service, "process_task_video_cuts", forbidden_handler)
        with pytest.raises(job_service.JobLeaseLostError):
            job_worker.execute_job(
                created["id"],
                lease_owner="worker-a",
                lease_token=claimed["lease_token"],
                already_claimed=True,
            )
        assert called is False
    finally:
        _cleanup(task_id)


def test_worker_marks_job_failed_when_subprocess_cannot_start(monkeypatch) -> None:
    task_id, created = _create_task_and_job()
    runner = job_worker.WorkflowJobRunner()
    try:
        claimed = job_service.claim_job(created["id"], runner.owner)
        assert claimed and claimed["lease_token"]
        monkeypatch.setattr(
            job_worker,
            "popen_process_group",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("spawn denied")),
        )

        runner._run_job_subprocess(created["id"])

        failed = job_service.get_job(created["id"])
        assert failed["status"] == job_service.JOB_STATUS_FAILED
        assert "无法启动 Job 子进程：spawn denied" in failed["error_message"]
        assert failed["lease_owner"] is None
        assert failed["lease_token"] is None
    finally:
        _cleanup(task_id)


def test_claim_next_does_not_fail_live_max_attempt_worker() -> None:
    active_task_id, active_created = _create_task_and_job(max_attempts=1)
    queued_task_id, queued_created = _create_task_and_job()
    try:
        with get_connection() as connection:
            connection.execute(
                "UPDATE workflow_jobs SET created_at = '1900-01-01T00:00:00+00:00' WHERE id = ?",
                (queued_created["id"],),
            )
            connection.commit()
        active = job_service.claim_job(active_created["id"], "worker-a")
        assert active and active["attempt_count"] == 1

        claimed_next = job_service.claim_next_job("worker-b")
        assert claimed_next and claimed_next["id"] == queued_created["id"]
        still_active = job_service.get_job(active_created["id"])
        assert still_active["status"] == job_service.JOB_STATUS_RUNNING
        assert still_active["lease_owner"] == "worker-a"
    finally:
        _cleanup(active_task_id)
        _cleanup(queued_task_id)


def test_expired_max_attempt_job_is_failed_before_next_claim() -> None:
    task_id, created = _create_task_and_job(max_attempts=1)
    try:
        claimed = job_service.claim_job(created["id"], "worker-a")
        assert claimed and claimed["attempt_count"] == 1
        _expire(created["id"])

        assert job_service.claim_next_job("worker-b") is None
        failed = job_service.get_job(created["id"])
        assert failed["status"] == job_service.JOB_STATUS_FAILED
        assert failed["lease_owner"] is None
        assert failed["lease_token"] is None
    finally:
        _cleanup(task_id)


def test_retry_invalidates_previous_lease_token() -> None:
    task_id, created = _create_task_and_job()
    try:
        claimed = job_service.claim_job(created["id"], "worker-a")
        job_service.mark_job_failed(
            created["id"],
            "first failure",
            lease_owner="worker-a",
            lease_token=claimed["lease_token"],
        )
        retried = job_service.retry_job(created["id"])
        assert retried["status"] == job_service.JOB_STATUS_QUEUED

        with pytest.raises(job_service.JobLeaseLostError):
            job_service.mark_job_completed(
                created["id"],
                lease_owner="worker-a",
                lease_token=claimed["lease_token"],
            )
        current = job_service.get_job(created["id"])
        assert current["status"] == job_service.JOB_STATUS_QUEUED
        assert current["lease_token"] is None
    finally:
        _cleanup(task_id)


def test_workflow_job_schema_contains_lease_token() -> None:
    init_db()
    with get_connection() as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(workflow_jobs)").fetchall()}
    assert "lease_token" in columns


def test_unfenced_running_job_blocks_startup() -> None:
    from app.db.database import _guard_unfenced_running_workflow_jobs

    task_id, created = _create_task_and_job()
    try:
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE workflow_jobs
                SET status = 'running', lease_owner = 'old-worker', lease_token = NULL
                WHERE id = ?
                """,
                (created["id"],),
            )
            connection.commit()
            with pytest.raises(RuntimeError, match="未带 lease_token"):
                _guard_unfenced_running_workflow_jobs(connection)
    finally:
        _cleanup(task_id)


def test_workflow_job_column_migration_is_concurrent_safe(tmp_path) -> None:
    from app.db.database import _migrate_workflow_jobs_table

    database_path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE workflow_jobs (id TEXT PRIMARY KEY)")
        connection.commit()

    def migrate() -> None:
        with sqlite3.connect(database_path, timeout=10) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 5000")
            _migrate_workflow_jobs_table(connection)
            connection.commit()

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(lambda _index: migrate(), range(2)))

    with sqlite3.connect(database_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(workflow_jobs)").fetchall()}
    assert "lease_token" in columns
    assert "checkpoint_json" in columns


def test_pipeline_lease_loss_does_not_write_failed_task_state(monkeypatch) -> None:
    from app.models.task import TaskStatus
    from app.services.pipeline_engine import PipelineEngine
    from app.services import pipeline_engine

    state_updates: list[TaskStatus] = []
    logs: list[str] = []
    engine = PipelineEngine()
    monkeypatch.setattr(
        engine,
        "_get_task",
        lambda _task_id: {"status": TaskStatus.CREATED.value, "auto_mode": 1},
    )
    monkeypatch.setattr(pipeline_engine.task_service, "update_task_status", lambda _task_id, status, *_args: state_updates.append(status))
    monkeypatch.setattr(pipeline_engine, "append_task_log", lambda _task_id, message: logs.append(message))
    monkeypatch.setattr(pipeline_engine.job_service, "is_cancel_requested", lambda _job_id: False)
    monkeypatch.setattr(pipeline_engine.job_service, "update_job_progress", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        pipeline_engine.job_service,
        "heartbeat_job",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(job_service.JobLeaseLostError("lost")),
    )

    with pytest.raises(job_service.JobLeaseLostError):
        engine.run(
            "task-fencing",
            start_step=TaskStatus.PREPARING_SOURCE,
            job_id="job-fencing",
        )

    assert state_updates == []
    assert not any("流水线失败" in message for message in logs)


def test_audio_lease_loss_does_not_mark_task_failed(monkeypatch, tmp_path) -> None:
    from app.services import task_service, transcript_workflow_service

    source = tmp_path / "source.mp4"
    audio = tmp_path / "audio.mp3"
    source.write_bytes(b"source")
    status_updates = []
    logs: list[str] = []
    monkeypatch.setattr(task_service, "get_task", lambda _task_id: {"id": "task-fencing"})
    monkeypatch.setattr(task_service, "update_task_status", lambda _task_id, status, *_args: status_updates.append(status))
    monkeypatch.setattr(transcript_workflow_service, "get_source_video_path", lambda _task: source)
    monkeypatch.setattr(transcript_workflow_service, "validate_source_video_path", lambda _path: (True, ""))
    monkeypatch.setattr(transcript_workflow_service, "get_artifact_paths", lambda _task_id: {"audio_path": audio})
    monkeypatch.setattr(transcript_workflow_service, "append_task_log", lambda _task_id, message: logs.append(message))
    monkeypatch.setattr(
        transcript_workflow_service,
        "run_ffmpeg_audio_extract",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(job_service.JobLeaseLostError("lost")),
    )

    with pytest.raises(job_service.JobLeaseLostError):
        transcript_workflow_service.process_task_audio("task-fencing", job_id="job-fencing")

    assert status_updates[-1].value == "audio_extracting"
    assert not any("音频提取失败" in message for message in logs)


def test_subtitle_lease_loss_does_not_try_fallback_encoder(monkeypatch, tmp_path) -> None:
    from app.services import subtitle_workflow_service

    calls = 0

    def lose_lease(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise job_service.JobLeaseLostError("lost")

    monkeypatch.setattr(subtitle_workflow_service, "_ffmpeg_has_encoder", lambda _name: False)
    monkeypatch.setattr(subtitle_workflow_service, "_run_ffmpeg_progress", lose_lease)

    with pytest.raises(job_service.JobLeaseLostError):
        subtitle_workflow_service._render_with_fallback(
            tmp_path / "input.mp4",
            tmp_path / "subtitle.ass",
            tmp_path / "output.part.mp4",
            workflow_job_id="job-fencing",
            duration_seconds=60,
            has_audio=True,
            source_audio_codec="aac",
            progress_start=0,
            progress_end=100,
        )
    assert calls == 1


def test_stale_transcription_checkpoint_cannot_overwrite_new_claim(tmp_path) -> None:
    from app.services.transcript_service import TranscriptChunk, TranscriptSegment
    from app.services.transcription_checkpoint_service import TranscriptionCheckpoint

    task_id, created = _create_task_and_job()
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    checkpoint = TranscriptionCheckpoint(
        task_id=task_id,
        source_path=source,
        provider="local",
        model="medium",
        device="cpu",
        compute_type="int8",
        chunk_seconds=120,
        overlap_seconds=5,
    )
    chunks = [TranscriptChunk(index=1, start_seconds=0, end_seconds=120)]
    segments = [TranscriptSegment(0, 1, "current")]
    try:
        first = job_service.claim_job(created["id"], "worker-a")
        with job_service.job_lease_context(created["id"], "worker-a", first["lease_token"]):
            checkpoint.ensure_run(chunks)
        _expire(created["id"])
        second = job_service.claim_job(created["id"], "worker-b")

        with job_service.job_lease_context(created["id"], "worker-a", first["lease_token"]):
            with pytest.raises(job_service.JobLeaseLostError):
                checkpoint.save_completed(1, [TranscriptSegment(0, 1, "stale")])

        with job_service.job_lease_context(created["id"], "worker-b", second["lease_token"]):
            checkpoint.save_completed(1, segments)
        with get_connection() as connection:
            row = connection.execute(
                "SELECT status, result_json FROM transcription_chunks WHERE run_id = ? AND chunk_index = 1",
                (checkpoint.run_id,),
            ).fetchone()
        assert row["status"] == "completed"
        assert "current" in row["result_json"]
        assert "stale" not in row["result_json"]
    finally:
        with get_connection() as connection:
            connection.execute("DELETE FROM transcription_chunks WHERE task_id = ?", (task_id,))
            connection.execute("DELETE FROM transcription_runs WHERE task_id = ?", (task_id,))
            connection.commit()
        _cleanup(task_id)


def test_transcription_provider_does_not_wrap_lease_loss(monkeypatch, tmp_path) -> None:
    from app.services import transcript_service

    monkeypatch.setattr(
        transcript_service,
        "transcribe_audio_with_provider",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(job_service.JobLeaseLostError("lost")),
    )
    with pytest.raises(job_service.JobLeaseLostError):
        transcript_service.transcribe_audio_with_configured_provider(
            tmp_path / "audio.mp3",
            tmp_path,
            tmp_path / "progress.json",
            provider="local",
            allow_fallback=True,
        )
