from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.db.database import get_connection, init_db
from app.main import app
from app.models.task import TaskStatus
from app.services import job_service
from app.services.auto_publish_service import create_auto_publish_jobs
from app.services.pipeline_engine import PipelineEngine
from app.services.publish_readiness import build_send_readiness
from app.services.publish_service import sync_task_publish_jobs
from app.services.subtitle_ai_service import generate_subtitle_suggestions
from app.services.subtitle_auto_workflow_service import (
    cleanup_interrupted_subtitle_job,
    enqueue_task_subtitle_render,
    execute_subtitle_render_job,
    prepare_task_subtitle_review,
    skip_task_subtitles_and_resume,
)
from app.services.subtitle_data_service import (
    accept_suggestion_revision,
    approve_revision,
    ensure_clip_track,
    get_revision,
    get_track,
)
from app.services.subtitle_workflow_service import (
    _create_subtitle_job,
    _build_ffmpeg_render_command,
    _probe_media,
    _render_with_fallback,
    _validate_rendered_media,
    render_subtitles_for_output_clip,
)
from app.services.storage_service import get_artifact_paths
from app.services.task_service import get_task


PREFIX = "test-subtitle-auto-"


def _headers() -> dict[str, str]:
    if settings.local_admin_token:
        return {"Authorization": f"Bearer {settings.local_admin_token}"}
    return {}


@pytest.fixture(autouse=True)
def subtitle_auto_database():
    init_db()
    _cleanup()
    yield
    _cleanup()


def _cleanup() -> None:
    with get_connection() as connection:
        publish_rows = connection.execute(
            "SELECT id FROM publish_jobs WHERE task_id LIKE ?", (f"{PREFIX}%",)
        ).fetchall()
        if publish_rows:
            placeholders = ",".join("?" for _ in publish_rows)
            connection.execute(
                f"DELETE FROM publish_job_events WHERE job_id IN ({placeholders})",
                [row["id"] for row in publish_rows],
            )
        connection.execute("DELETE FROM publish_jobs WHERE task_id LIKE ?", (f"{PREFIX}%",))
        connection.execute("DELETE FROM subtitle_jobs WHERE task_id LIKE ?", (f"{PREFIX}%",))
        connection.execute("DELETE FROM workflow_jobs WHERE task_id LIKE ?", (f"{PREFIX}%",))
        connection.execute(
            "DELETE FROM subtitle_cues WHERE revision_id IN (SELECT id FROM subtitle_revisions WHERE track_id IN (SELECT id FROM subtitle_tracks WHERE task_id LIKE ?))",
            (f"{PREFIX}%",),
        )
        connection.execute(
            "DELETE FROM subtitle_revisions WHERE track_id IN (SELECT id FROM subtitle_tracks WHERE task_id LIKE ?)",
            (f"{PREFIX}%",),
        )
        connection.execute("DELETE FROM subtitle_tracks WHERE task_id LIKE ?", (f"{PREFIX}%",))
        connection.execute("DELETE FROM output_clip WHERE task_id LIKE ?", (f"{PREFIX}%",))
        connection.execute("DELETE FROM transcription_chunks WHERE task_id LIKE ?", (f"{PREFIX}%",))
        connection.execute("DELETE FROM transcription_runs WHERE task_id LIKE ?", (f"{PREFIX}%",))
        connection.execute("DELETE FROM tasks WHERE id LIKE ?", (f"{PREFIX}%",))
        connection.commit()


def _create_task(tmp_path: Path, *, status: str = "VIDEO_CUTTING") -> tuple[str, str, Path]:
    task_id = f"{PREFIX}{uuid4().hex[:8]}"
    output_id = f"out-{uuid4().hex[:8]}"
    source_path = tmp_path / f"{task_id}-source.mp4"
    output_path = tmp_path / f"{output_id}.mp4"
    source_path.write_bytes(b"source")
    output_path.write_bytes(b"clip")
    now = "2026-08-24T00:00:00+00:00"
    segments = [
        {"start_seconds": 0.1, "end_seconds": 1.2, "text": "大家好", "confidence": 0.95},
        {"start_seconds": 1.3, "end_seconds": 2.8, "text": "这是直播高光", "confidence": 0.93},
    ]
    result_json = json.dumps(segments, ensure_ascii=False)
    result_checksum = hashlib.sha256(result_json.encode("utf-8")).hexdigest()
    run_id = f"run-{uuid4().hex[:8]}"
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO tasks (
                id, task_name, source_type, platform, original_video_path,
                selection_profile, auto_mode, auto_config_json, status,
                created_at, updated_at
            ) VALUES (?, '字幕自动流程测试', 'upload', 'general', ?, 'general', 1, '{}', ?, ?, ?)
            """,
            (task_id, str(source_path), status, now, now),
        )
        connection.execute(
            """
            INSERT INTO output_clip (
                id, task_id, output_file_path, output_file_name, status, is_active,
                source_start_ms, source_end_ms, source_duration_ms,
                source_fingerprint, snapshot_source, created_at, updated_at
            ) VALUES (?, ?, ?, 'clip.mp4', 'completed', 1, 0, 5000, 5000,
                      'source-v1', 'cut_commit', ?, ?)
            """,
            (output_id, task_id, str(output_path), now, now),
        )
        connection.execute(
            """
            INSERT INTO transcription_runs (
                id, task_id, source_fingerprint, provider, model, device, compute_type,
                chunk_seconds, overlap_seconds, status,
                total_chunks, completed_chunks, is_active, created_at, updated_at, completed_at
            ) VALUES (?, ?, 'source-v1', 'local', 'small', 'cpu', 'int8',
                      120, 5, 'completed', 1, 1, 1, ?, ?, ?)
            """,
            (run_id, task_id, now, now, now),
        )
        connection.execute(
            """
            INSERT INTO transcription_chunks (
                id, run_id, task_id, chunk_index, start_ms, end_ms, status,
                result_json, result_checksum, created_at, updated_at
            ) VALUES (?, ?, ?, 1, 0, 5000, 'completed', ?, ?, ?, ?)
            """,
            (f"chunk-{uuid4().hex[:8]}", run_id, task_id, result_json, result_checksum, now, now),
        )
        connection.commit()
    return task_id, output_id, output_path


def test_schema_contains_async_render_validation_fields():
    with get_connection() as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(subtitle_jobs)")}
    assert {
        "workflow_job_id",
        "revision_id",
        "validation_status",
        "validation_json",
        "encoder",
        "verified_at",
    } <= columns


@pytest.mark.parametrize("width,height", [(360, 640), (640, 360), (480, 480)])
def test_real_ffmpeg_render_supports_three_aspect_ratios(tmp_path: Path, width: int, height: int):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("本机没有 FFmpeg/FFprobe")
    task_id, output_id, output_path = _create_task(tmp_path)
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"color=c=navy:s={width}x{height}:r=25:d=3",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:sample_rate=44100:duration=3",
                "-shortest",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                str(output_path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        prepare_task_subtitle_review(task_id)
        track = ensure_clip_track(task_id, output_id)
        approve_revision(track["id"], track["active_revision_id"])

        result = render_subtitles_for_output_clip(task_id, output_id)
        rendered_path = Path(result["job"]["output_file_path"])
        probe = _probe_media(rendered_path)

        assert result["job"]["validation_status"] == "verified"
        assert result["job"]["revision_id"] == track["active_revision_id"]
        assert rendered_path.is_file()
        assert probe["video_codec"] == "h264"
        assert probe["pixel_format"] == "yuv420p"
        assert probe["has_audio"] is True
        assert probe["duration"] == pytest.approx(3.0, abs=1.0)
    finally:
        shutil.rmtree(get_artifact_paths(task_id)["task_dir"], ignore_errors=True)


def test_prepare_review_creates_clip_draft_and_pauses(tmp_path: Path):
    task_id, output_id, _ = _create_task(tmp_path)
    result = prepare_task_subtitle_review(task_id)
    task = get_task(task_id, include_video_probe=False)
    track = ensure_clip_track(task_id, output_id)
    revision = get_revision(track["active_revision_id"], include_cues=True)
    assert result["status"] == "pending_subtitle_review"
    assert task["status"] == TaskStatus.PENDING_SUBTITLE_REVIEW.value
    assert revision["status"] == "draft"
    assert revision["cue_count"] == 2

    init_db()
    assert get_task(task_id, include_video_probe=False)["status"] == TaskStatus.PENDING_SUBTITLE_REVIEW.value


def test_review_page_and_api_enqueue_batch_job(tmp_path: Path):
    task_id, _, _ = _create_task(tmp_path)
    prepare_task_subtitle_review(task_id)
    with TestClient(app) as client:
        page = client.get(f"/subtitles/{task_id}", headers=_headers())
        task_page = client.get(f"/tasks/{task_id}", headers=_headers())
        live = client.get(f"/api/tasks/{task_id}/live-status", headers=_headers())
        queued = client.post(
            f"/api/subtitles/tasks/{task_id}/approve-and-render",
            json={"approve_active_revisions": True},
            headers=_headers(),
        )
        jobs = client.get(f"/api/subtitles/tasks/{task_id}/jobs", headers=_headers())

    assert page.status_code == 200
    assert 'id="subtitle-review-gate"' in page.text
    assert 'id="subtitle-batch-approve-render"' in page.text
    assert 'id="subtitle-skip-and-resume"' in page.text
    assert 'id="subtitle-ai-suggest"' in page.text
    assert 'data-sync-publish-task' not in page.text
    assert task_page.status_code == 200
    assert f'href="/subtitles/{task_id}"' in task_page.text
    assert live.status_code == 200
    assert live.json()["actions"]["primary"] == "subtitle_review"
    assert live.json()["should_poll"] is False
    assert queued.status_code == 200
    assert queued.json()["job"]["job_type"] == job_service.JOB_TYPE_SUBTITLE
    assert jobs.status_code == 200
    assert jobs.json()["count"] == 1


def test_skip_api_persists_original_delivery_decision(tmp_path: Path):
    task_id, _, _ = _create_task(tmp_path)
    prepare_task_subtitle_review(task_id)
    with TestClient(app) as client:
        response = client.post(
            f"/api/subtitles/tasks/{task_id}/skip-and-resume",
            headers=_headers(),
        )
    assert response.status_code == 200, response.text
    with get_connection() as connection:
        raw_config = connection.execute(
            "SELECT auto_config_json FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()[0]
    assert json.loads(raw_config)["subtitle_delivery_mode"] == "original"


def test_pipeline_stops_before_metadata_and_publish(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    task_id, _, _ = _create_task(tmp_path, status="CREATED")
    engine = PipelineEngine()
    for name in ("_prepare_source", "_transcribe_or_read_text", "_run_ai_analysis", "_select_clips", "_cut_video"):
        monkeypatch.setattr(engine, name, Mock(return_value={"ok": True}))

    def pause(task_id_value: str, _context: dict) -> dict:
        from app.services import task_service

        task_service.update_task_status(task_id_value, TaskStatus.PENDING_SUBTITLE_REVIEW)
        return {"status": "pending_subtitle_review"}

    monkeypatch.setattr(engine, "_prepare_subtitle_drafts", pause)
    metadata = Mock(return_value={})
    monkeypatch.setattr(engine, "_generate_metadata", metadata)
    result = engine.run(task_id)
    assert result["status"] == "pending_subtitle_review"
    metadata.assert_not_called()
    with get_connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM publish_jobs WHERE task_id = ?", (task_id,)).fetchone()[0] == 0


def test_batch_approval_pins_revisions_and_records_delivery_mode(tmp_path: Path):
    task_id, output_id, _ = _create_task(tmp_path)
    prepare_task_subtitle_review(task_id)
    result = enqueue_task_subtitle_render(
        task_id,
        approve_active_revisions=True,
        continue_pipeline=True,
    )
    item = result["job"]["payload_json"]["items"][0]
    revision = get_revision(item["revision_id"])
    with get_connection() as connection:
        config = json.loads(connection.execute("SELECT auto_config_json FROM tasks WHERE id = ?", (task_id,)).fetchone()[0])
    assert item["output_clip_id"] == output_id
    assert revision["status"] == "approved"
    assert config["subtitle_delivery_mode"] == "subtitled"


def test_skip_is_explicit_and_queues_metadata_resume(tmp_path: Path):
    task_id, _, _ = _create_task(tmp_path)
    prepare_task_subtitle_review(task_id)
    result = skip_task_subtitles_and_resume(task_id)
    with get_connection() as connection:
        config = json.loads(connection.execute("SELECT auto_config_json FROM tasks WHERE id = ?", (task_id,)).fetchone()[0])
    assert config["subtitle_delivery_mode"] == "original"
    assert result["job"]["job_type"] == job_service.JOB_TYPE_AUTO_PIPELINE
    assert result["job"]["payload_json"]["start_step"] == TaskStatus.METADATA_GENERATING.value


def test_skip_rejects_active_subtitle_job(tmp_path: Path):
    task_id, _, _ = _create_task(tmp_path)
    prepare_task_subtitle_review(task_id)
    enqueue_task_subtitle_render(task_id, approve_active_revisions=True, continue_pipeline=True)
    with pytest.raises(ValueError, match="仍在运行"):
        skip_task_subtitles_and_resume(task_id)


def test_pending_subtitle_review_cannot_sync_publish_center(tmp_path: Path):
    task_id, _, _ = _create_task(tmp_path)
    prepare_task_subtitle_review(task_id)
    with pytest.raises(ValueError, match="等待字幕审核"):
        sync_task_publish_jobs(task_id)


def test_batch_execution_checkpoints_and_queues_resume(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    task_id, output_id, _ = _create_task(tmp_path)
    prepare_task_subtitle_review(task_id)
    queued = enqueue_task_subtitle_render(task_id, approve_active_revisions=True, continue_pipeline=True)
    renderer = Mock(
        return_value={
            "job": {
                "id": "subtitle-render-1",
                "output_file_path": str(tmp_path / "verified.mp4"),
            }
        }
    )
    monkeypatch.setattr(
        "app.services.subtitle_auto_workflow_service.render_subtitles_for_output_clip",
        renderer,
    )
    claimed = job_service.claim_job(queued["job_id"], "subtitle-test-worker")
    with job_service.job_lease_context(queued["job_id"], "subtitle-test-worker", claimed["lease_token"]):
        result = execute_subtitle_render_job(queued["job_id"], task_id, queued["job"]["payload_json"])
    checkpoint = job_service.get_job(queued["job_id"])["checkpoint_json"]
    assert result["completed_count"] == 1
    assert checkpoint["completed"][output_id]["revision_id"]
    assert result["resume_job_id"]


def test_cancel_cleanup_is_precise_and_retry_keeps_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    task_id, output_id, _ = _create_task(tmp_path)
    prepare_task_subtitle_review(task_id)
    queued = enqueue_task_subtitle_render(task_id, approve_active_revisions=True, continue_pipeline=True)
    workflow_job_id = queued["job_id"]
    revision_id = queued["job"]["payload_json"]["items"][0]["revision_id"]
    child = _create_subtitle_job(
        task_id,
        output_id,
        "processing",
        revision_id=revision_id,
        workflow_job_id=workflow_job_id,
        is_active=0,
    )
    directory = tmp_path / "subtitled"
    directory.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "app.services.subtitle_auto_workflow_service.get_artifact_paths",
        lambda _task_id: {"subtitled_dir": directory},
    )
    owned_temp = directory / f".clip.{workflow_job_id}.part.mp4"
    unrelated_temp = directory / ".clip.other-job.part.mp4"
    owned_temp.write_bytes(b"partial")
    unrelated_temp.write_bytes(b"keep")

    stale_claim = job_service.claim_job(workflow_job_id, "cleanup-test-worker-old")
    with get_connection() as connection:
        connection.execute(
            "UPDATE workflow_jobs SET lease_expires_at = '2000-01-01T00:00:00+00:00' WHERE id = ?",
            (workflow_job_id,),
        )
        connection.commit()
    claimed = job_service.claim_job(workflow_job_id, "cleanup-test-worker")
    assert cleanup_interrupted_subtitle_job(
        workflow_job_id,
        lease_owner="cleanup-test-worker-old",
        lease_token=stale_claim["lease_token"],
        status="cancelled",
        message="旧 Worker 不得清理",
    ) is False
    assert owned_temp.exists() is True
    assert cleanup_interrupted_subtitle_job(
        workflow_job_id,
        lease_owner="cleanup-test-worker",
        lease_token=claimed["lease_token"],
        status="cancelled",
        message="用户已取消字幕烧录",
    ) is True
    with get_connection() as connection:
        child_status = connection.execute(
            "SELECT status FROM subtitle_jobs WHERE id = ?",
            (child["id"],),
        ).fetchone()[0]
    assert child_status == "cancelled"
    assert owned_temp.exists() is False
    assert unrelated_temp.exists() is True

    checkpoint = {"completed": {output_id: {"revision_id": revision_id}}}
    with job_service.job_lease_context(workflow_job_id, claimed["lease_owner"], claimed["lease_token"]):
        job_service.update_job_checkpoint(workflow_job_id, checkpoint)
        job_service.mark_job_cancelled(workflow_job_id)
    retried = job_service.retry_job(workflow_job_id)
    assert retried["status"] == job_service.JOB_STATUS_QUEUED
    assert retried["checkpoint_json"] == checkpoint


def test_ffmpeg_command_maps_optional_audio_and_forces_compatible_video(tmp_path: Path):
    command = _build_ffmpeg_render_command(
        tmp_path / "input.mp4",
        tmp_path / "subtitle.ass",
        tmp_path / "output.mp4",
        encoder="libx264",
        audio_mode="aac",
    )
    assert command[command.index("-map") + 1] == "0:v:0"
    assert "0:a:0?" in command
    assert ["-c:v", "libx264"] == command[command.index("-c:v") : command.index("-c:v") + 2]
    assert "yuv420p" in command
    assert "-progress" in command


def test_nvenc_failure_falls_back_to_libx264(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    calls = []
    monkeypatch.setattr("app.services.subtitle_workflow_service._ffmpeg_has_encoder", lambda _name: True)

    def run(command: list[str], **_kwargs) -> None:
        calls.append(command)
        if "h264_nvenc" in command:
            raise RuntimeError("NVENC unavailable")

    monkeypatch.setattr("app.services.subtitle_workflow_service._run_ffmpeg_progress", run)
    encoder, audio_mode = _render_with_fallback(
        tmp_path / "input.mp4",
        tmp_path / "subtitle.ass",
        tmp_path / "output.part.mp4",
        workflow_job_id="job",
        duration_seconds=3,
        has_audio=True,
        source_audio_codec="aac",
        progress_start=5,
        progress_end=95,
    )
    assert encoder == "libx264"
    assert audio_mode == "copy"
    assert any("h264_nvenc" in command for command in calls)
    assert any("libx264" in command for command in calls)


def test_ffprobe_validation_rejects_missing_audio_and_bad_pixel_format(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    output = tmp_path / "render.part.mp4"
    output.write_bytes(b"video")
    monkeypatch.setattr(
        "app.services.subtitle_workflow_service._probe_media",
        lambda _path: {
            "duration": 3.0,
            "video_codec": "h264",
            "pixel_format": "yuv444p",
            "has_audio": False,
            "audio_codec": "",
        },
    )
    with pytest.raises(RuntimeError, match="yuv420p"):
        _validate_rendered_media(output, source_duration=3, source_has_audio=True)


class _SuggestionProvider:
    name = "test-ai"

    def __init__(self, cue_ids: list[str]):
        self.cue_ids = cue_ids

    def generate_json(self, _prompt: str) -> str:
        return json.dumps(
            {
                "suggestions": [
                    {"cue_id": cue_id, "suggested_text": f"建议{index + 1}", "reason": "错别字"}
                    for index, cue_id in enumerate(self.cue_ids)
                ]
            },
            ensure_ascii=False,
        )


def test_ai_suggestion_is_inactive_until_selected_diff_is_accepted(tmp_path: Path):
    task_id, output_id, _ = _create_task(tmp_path)
    prepare_task_subtitle_review(task_id)
    track = ensure_clip_track(task_id, output_id)
    base_revision = get_revision(track["active_revision_id"], include_cues=True)
    cue_ids = [cue["id"] for cue in base_revision["cues"]]
    suggestion = generate_subtitle_suggestions(
        track["id"],
        revision_id=base_revision["id"],
        cue_ids=cue_ids,
        provider=_SuggestionProvider(cue_ids),
    )
    assert get_track(track["id"])["active_revision_id"] == base_revision["id"]
    assert suggestion["revision"]["status"] == "suggested"
    accepted = accept_suggestion_revision(
        track["id"],
        suggestion_revision_id=suggestion["revision"]["id"],
        base_revision_id=base_revision["id"],
        cue_ids=[cue_ids[0]],
    )
    assert accepted["origin"] == "manual"
    assert accepted["cues"][0]["text"] == "建议1"
    assert accepted["cues"][1]["text"] == base_revision["cues"][1]["text"]
    assert accepted["cues"][0]["start_ms"] == base_revision["cues"][0]["start_ms"]


def test_publish_readiness_blocks_unverified_subtitle_evidence():
    job = {
        "status": "WAITING",
        "platform": "douyin",
        "publish_mode": "manual_export",
        "video_source": "subtitled",
        "video_file_path": "clip.mp4",
        "video_path": "clip.mp4",
        "title": "标题",
        "caption": "正文",
        "provider_payload": {"subtitle_delivery_mode": "subtitled"},
    }
    readiness = build_send_readiness(job, accounts=[])
    assert readiness["dispatch_ready"] is False
    assert any(issue["code"] == "subtitle_not_verified" for issue in readiness["issues"])


def test_auto_publish_uses_verified_approved_subtitle(tmp_path: Path):
    task_id, output_id, output_path = _create_task(tmp_path)
    subtitled_path = tmp_path / "approved-subtitled.mp4"
    cover_path = tmp_path / "cover.jpg"
    subtitled_path.write_bytes(b"subtitled")
    cover_path.write_bytes(b"cover")
    result = create_auto_publish_jobs(
        {"id": task_id, "platform": "general"},
        [
            {
                "output_clip": {
                    "id": output_id,
                    "output_file_path": str(output_path),
                    "subtitle_status": "completed",
                    "subtitled_output_file_path": str(subtitled_path),
                    "subtitle_revision_id": "approved-revision",
                    "subtitle_revision_status": "approved",
                    "subtitle_validation_status": "verified",
                    "subtitle_verified_at": "2026-08-24T00:00:00+00:00",
                },
                "cover": {"cover_file_path": str(cover_path), "cover_time_seconds": 1},
                "metadata": {
                    "platform": "douyin",
                    "title": "标题",
                    "caption": "正文",
                    "hashtags": ["测试"],
                    "risk_flags": [],
                },
                "scheduled_at": "",
            }
        ],
        subtitle_delivery_mode="subtitled",
    )
    assert result["created"][0]["video_source"] == "subtitled"
    assert result["created"][0]["video_file_path"] == str(subtitled_path)
    issue_codes = {issue["code"] for issue in result["created"][0]["send_readiness"]["issues"]}
    assert "subtitle_not_verified" not in issue_codes
    assert "subtitle_review_required" not in issue_codes
