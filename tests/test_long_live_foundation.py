from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.db.database import get_connection, init_db
from app.services import job_service
from app.services.media_preflight_service import MAX_TESTED_DURATION_SECONDS, preflight_media, probe_media
from app.services.transcript_service import (
    TranscriptChunk,
    TranscriptSegment,
    TranscriptWord,
    _segment_from_checkpoint,
)
from app.services.transcription_checkpoint_service import TranscriptionCheckpoint


def _ffprobe_payload(*, duration: float = 3600, include_audio: bool = True) -> str:
    streams = [
        {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080, "avg_frame_rate": "30/1"},
    ]
    if include_audio:
        streams.append({"codec_type": "audio", "codec_name": "aac", "channels": 2, "sample_rate": "48000"})
    return json.dumps({"streams": streams, "format": {"duration": str(duration), "size": "1048576"}})


def test_media_preflight_collects_streams_and_six_hour_warning(monkeypatch, tmp_path):
    source = tmp_path / "long.mp4"
    source.write_bytes(b"video")

    def fake_run(command, **_kwargs):
        if "-show_streams" in command:
            return SimpleNamespace(returncode=0, stdout=_ffprobe_payload(duration=MAX_TESTED_DURATION_SECONDS + 1), stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("app.services.media_preflight_service.shutil.which", lambda name: name)
    monkeypatch.setattr("app.services.media_preflight_service.subprocess.run", fake_run)
    monkeypatch.setattr(
        "app.services.media_preflight_service.shutil.disk_usage",
        lambda _path: SimpleNamespace(free=100 * 1024 ** 3),
    )
    result = preflight_media(source, total_output_limit=30)
    assert result.video_codec == "h264"
    assert result.audio_codec == "aac"
    assert result.width == 1920
    assert result.frame_rate == 30
    assert result.warnings and "超过当前 6 小时验收范围" in result.warnings[0]


def test_media_preflight_rejects_missing_audio(monkeypatch, tmp_path):
    source = tmp_path / "silent.mp4"
    source.write_bytes(b"video")
    monkeypatch.setattr("app.services.media_preflight_service.shutil.which", lambda name: name)
    monkeypatch.setattr(
        "app.services.media_preflight_service.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout=_ffprobe_payload(include_audio=False), stderr=""),
    )
    with pytest.raises(ValueError, match="没有音轨"):
        probe_media(source)


def test_transcription_checkpoint_resumes_and_invalidates_on_source_change(tmp_path):
    init_db()
    task_id = "checkpoint-foundation-test"
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source-v1")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_connection() as connection:
        connection.execute(
            """INSERT OR REPLACE INTO tasks (
               id, task_name, task_dir_name, source_type, platform, selection_profile,
               status, progress, is_deleted, created_at, updated_at
               ) VALUES (?, 'checkpoint', ?, 'upload', 'general', 'long_live_talk',
               'pending_processing', 0, 0, ?, ?)""",
            (task_id, task_id, now, now),
        )
        connection.commit()
    chunks = [TranscriptChunk(index=1, start_seconds=0, end_seconds=120)]
    segment = TranscriptSegment(
        0.125,
        1.875,
        "测试词级时间戳",
        confidence=0.91,
        words=(TranscriptWord(125, 600, "测试", 0.9),),
    )
    try:
        first = TranscriptionCheckpoint(
            task_id=task_id, source_path=source, provider="local", model="medium",
            device="cpu", compute_type="int8", chunk_seconds=120, overlap_seconds=5,
        )
        first.ensure_run(chunks)
        first.save_completed(1, [segment])

        resumed = TranscriptionCheckpoint(
            task_id=task_id, source_path=source, provider="local", model="medium",
            device="cpu", compute_type="int8", chunk_seconds=120, overlap_seconds=5,
        )
        resumed.ensure_run(chunks)
        loaded = resumed.load_completed(1, _segment_from_checkpoint)
        assert resumed.run_id == first.run_id
        assert loaded and loaded[0].words[0].start_ms == 125
        assert loaded[0].confidence == pytest.approx(0.91)

        source.write_bytes(b"source-v2-changed")
        changed = TranscriptionCheckpoint(
            task_id=task_id, source_path=source, provider="local", model="medium",
            device="cpu", compute_type="int8", chunk_seconds=120, overlap_seconds=5,
        )
        changed.ensure_run(chunks)
        assert changed.run_id != first.run_id
        assert changed.load_completed(1, _segment_from_checkpoint) is None
    finally:
        with get_connection() as connection:
            connection.execute("DELETE FROM transcription_chunks WHERE task_id = ?", (task_id,))
            connection.execute("DELETE FROM transcription_runs WHERE task_id = ?", (task_id,))
            connection.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            connection.commit()


def test_job_lease_takeover_cancel_and_retry():
    init_db()
    task_id = "job-lease-foundation-test"
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_connection() as connection:
        connection.execute(
            """INSERT OR REPLACE INTO tasks (
               id, task_name, task_dir_name, source_type, platform, selection_profile,
               status, progress, is_deleted, created_at, updated_at
               ) VALUES (?, 'lease', ?, 'upload', 'general', 'general', 'pending_video', 0, 0, ?, ?)""",
            (task_id, task_id, now, now),
        )
        connection.commit()
    try:
        job = job_service.create_job(task_id, job_service.JOB_TYPE_TRANSCRIPT)
        claimed = job_service.claim_job(job["id"], "worker-one")
        assert claimed["status"] == job_service.JOB_STATUS_RUNNING
        assert claimed["attempt_count"] == 1
        with get_connection() as connection:
            connection.execute(
                "UPDATE workflow_jobs SET lease_expires_at = '2000-01-01T00:00:00+00:00' WHERE id = ?",
                (job["id"],),
            )
            connection.commit()
        reclaimed = job_service.claim_job(job["id"], "worker-two")
        assert reclaimed["lease_owner"] == "worker-two"
        assert reclaimed["lease_token"] != claimed["lease_token"]
        assert reclaimed["attempt_count"] == 2
        assert job_service.request_job_cancel(job["id"])["cancel_requested"] == 1
        cancelled = job_service.mark_job_cancelled(
            job["id"],
            lease_owner="worker-two",
            lease_token=reclaimed["lease_token"],
        )
        assert cancelled["status"] == job_service.JOB_STATUS_CANCELLED
        assert job_service.retry_job(job["id"])["status"] == job_service.JOB_STATUS_QUEUED
    finally:
        with get_connection() as connection:
            connection.execute("DELETE FROM workflow_jobs WHERE task_id = ?", (task_id,))
            connection.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            connection.commit()
